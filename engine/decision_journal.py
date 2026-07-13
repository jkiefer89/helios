"""Trade-decision journal — the OPERATOR's calls, scored against outcomes.

The signal journal records what the ENGINE said; this journal records what the
operator actually decided (agree or override, with rationale) and measures both
over forward windows so the "who adds value where" question has evidence:

    hit-rate when agreeing vs overriding, average alpha per bucket, and — for
    overrides — whether the operator's call beat the engine's on the same
    forward window ("override won" / "engine won").

Decisions are append-only facts; only outcome fields are ever updated. Outcomes
use the same paper-hit rule as the Evidence Lab / Signal Journal (alpha when a
benchmark result exists, else the raw forward return), and demo/sample targets
are honestly marked not-measurable rather than scored against synthetic prices.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from . import costs, data, persistence, portfolio, provenance
from ._common import avg as _avg, clean_close as _clean_close, paper_hit as _paper_hit, pct as _pct
from .signal_journal import MODEL_BENCHMARKS

OUTCOME_HORIZONS_D = (21, 63, 252)
VALID_ACTIONS = {"BUY", "ADD", "HOLD", "TRIM", "SELL"}
# Direction buckets used both for agreement classification and the hit rule.
_BUCKET = {"BUY": "BUY", "ADD": "BUY", "HOLD": "HOLD", "TRIM": "SELL", "SELL": "SELL"}


def _bucket(action: str) -> str:
    return _BUCKET.get(str(action or "").upper(), "HOLD")


def classify_agreement(engine_action: str, my_action: str) -> str:
    """agree = same direction bucket as the engine; override = different."""
    return "agree" if _bucket(engine_action) == _bucket(my_action) else "override"


def _target_series(target_kind: str, target_id: str) -> tuple[pd.Series, str, str, str]:
    """(close, display name, data_mode, mandate) for an instrument or model."""
    if target_kind == "model":
        mdl = portfolio.get(target_id)
        if mdl is None:
            raise ValueError(f"Unknown model '{target_id}'.")
        ps = portfolio.build_series(mdl)
        # build_series' raw provenance carries no data_mode key — reading it
        # directly left EVERY model decision permanently not_measurable
        # (review finding). provenance.portfolio() derives the mode the same
        # way every other consumer does: real / mixed / invalid_for_research.
        prov = provenance.portfolio(ps.provenance or {})
        return _clean_close(ps.close), mdl.name, str(prov.get("data_mode") or ""), mdl.mandate_key
    inst = data.get(target_id)
    if inst is None:
        raise ValueError(f"Unknown ticker '{target_id}'.")
    close = _clean_close(inst.df["close"] if "close" in inst.df else None)
    prov = provenance.instrument(inst.source, len(close))
    return close, inst.name, str(prov.get("data_mode") or ""), ""


def record_decision(
    *,
    target_kind: str,
    target_id: str,
    my_action: str,
    rationale: str = "",
    signal: dict[str, Any] | None = None,
    mandate: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record one operator decision with a compact engine snapshot."""
    kind = (target_kind or "instrument").strip().lower()
    if kind not in {"instrument", "model"}:
        raise ValueError("target_kind must be 'instrument' or 'model'.")
    action = str(my_action or "").strip().upper()
    if action not in VALID_ACTIONS:
        raise ValueError(f"my_action must be one of {sorted(VALID_ACTIONS)}.")
    close, name, data_mode, model_mandate = _target_series(kind, target_id)
    if close.empty:
        raise ValueError("Target has no usable price history to anchor the decision.")
    sig = signal or {}
    engine_action = str(sig.get("action") or "").upper()
    mandate_key = (mandate or model_mandate or "").lower()
    benchmark = MODEL_BENCHMARKS.get(mandate_key, "SPY")
    entry = {
        "decision_id": f"dec-{uuid.uuid4().hex[:20]}",
        "target_kind": kind,
        "target_id": str(target_id).upper() if kind == "instrument" else str(target_id),
        "target_name": name,
        "mandate": mandate_key,
        "benchmark": benchmark,
        "engine_action": engine_action,
        "engine_score": sig.get("score"),
        "tactical_action": str((sig.get("tactical") or {}).get("action") or ""),
        "strategic_action": str((sig.get("strategic") or {}).get("action") or ""),
        "my_action": action,
        "agreement": classify_agreement(engine_action, action) if engine_action else "",
        "rationale": str(rationale or "").strip(),
        "decision_date": str(close.index[-1].date()),
        "decision_price": float(close.iloc[-1]),
        "data_mode": data_mode,
        "context": dict(context or {}),
        "outcome_status": "pending" if data_mode == "real" else "not_measurable",
        "outcomes": {},
    }
    result = persistence.get_store().record_decision(entry)
    return result.get("entry") or result


def _forward_return(close: pd.Series, start_date: str, horizon_days: int) -> dict[str, Any] | None:
    """Forward pct return over `horizon_days` TRADING days after start_date,
    or None while not enough post-decision history exists yet."""
    if close.empty:
        return None
    try:
        start_ts = pd.Timestamp(start_date)
    except Exception:
        return None
    after = close[close.index > start_ts]
    if len(after) < horizon_days:
        return None
    start_px = close[close.index <= start_ts]
    if start_px.empty:
        return None
    end_px = float(after.iloc[horizon_days - 1])
    base = float(start_px.iloc[-1])
    if base <= 0:
        return None
    return {
        "end_date": str(after.index[horizon_days - 1].date()),
        "return_pct": round((end_px / base - 1.0) * 100.0, 4),
    }


def _return_over_span(close: pd.Series, start_date: str, end_date: str) -> float | None:
    """Benchmark pct return over the SAME calendar window as the target's
    forward window (last bar at/before each date). Counting `horizon`
    benchmark BARS instead compared mismatched periods whenever the target
    trades a different calendar — crypto weeks, international holidays,
    sparse uploads (review finding). Requires the series to extend through
    end_date so the window is covered, not truncated by stale data."""
    if close.empty:
        return None
    try:
        start_ts, end_ts = pd.Timestamp(start_date), pd.Timestamp(end_date)
    except Exception:
        return None
    if close.index.max() < end_ts:
        return None
    start_px = close[close.index <= start_ts]
    end_px = close[close.index <= end_ts]
    if start_px.empty or end_px.empty:
        return None
    base = float(start_px.iloc[-1])
    if base <= 0:
        return None
    return round((float(end_px.iloc[-1]) / base - 1.0) * 100.0, 4)


def evaluate_outcomes(entry: dict[str, Any]) -> dict[str, Any]:
    """Fill in any newly-measurable horizon outcomes for one decision."""
    if entry.get("outcome_status") == "not_measurable":
        return entry
    # Decisions recorded against a stale price history must not be scored as
    # if made at that stale date — later backfilled bars would grant the
    # decision hindsight it never had (review finding). The decision date must
    # sit at/after the record date minus a small settlement window.
    created = str(entry.get("created_at") or "")[:10]
    decision_date = str(entry.get("decision_date") or "")
    if created and decision_date:
        try:
            lag = (date.fromisoformat(created) - date.fromisoformat(decision_date)).days
        except ValueError:
            lag = None
        if lag is not None and lag > 7:
            persistence.get_store().update_decision_outcomes(
                entry["decision_id"], {}, "not_measurable",
                datetime.now(timezone.utc).isoformat())
            return {**entry, "outcome_status": "not_measurable",
                    "outcomes": {},
                    "not_measurable_reason": (f"price history was {lag} days stale at record "
                                              "time — scoring it would grant hindsight")}
    try:
        close, _, data_mode, _ = _target_series(entry["target_kind"], entry["target_id"])
    except ValueError:
        return entry  # target was deleted; leave the decision as recorded
    # Provenance re-check at evaluation time: if the target's data is no longer
    # real (e.g. replaced by sample/demo history), never measure against it.
    if data_mode != "real":
        return entry
    bench_close = pd.Series(dtype=float)
    try:
        bench = data.get(str(entry.get("benchmark") or ""))
        # Alpha only against a REAL benchmark series — measuring against bundled
        # sample data would be a fabricated comparison.
        if bench is not None and provenance.is_real_source(bench.source):
            bench_close = _clean_close(bench.df["close"])
    except Exception:
        pass
    # Score from the RECORD date, not the (possibly up to 7 days older)
    # last-bar date: the operator records AFTER observing the price action
    # between the two, and the engine's snapshot could not react intraday —
    # scoring from the stale anchor asymmetrically inflated the operator's
    # hit rate (review finding). ISO dates compare lexicographically.
    score_anchor = str(entry.get("decision_date") or "")
    if created and created > score_anchor:
        score_anchor = created
    outcomes = dict(entry.get("outcomes") or {})
    changed = False
    for horizon in OUTCOME_HORIZONS_D:
        key = str(horizon)
        if key in outcomes:
            continue
        fwd = _forward_return(close, score_anchor, horizon)
        if fwd is None:
            continue
        row: dict[str, Any] = {"end_date": fwd["end_date"], "target_return_pct": fwd["return_pct"]}
        bench_ret = _return_over_span(bench_close, score_anchor, fwd["end_date"])
        if bench_ret is not None:
            row["benchmark_return_pct"] = bench_ret
            row["alpha_pct"] = round(fwd["return_pct"] - bench_ret, 4)
        row["hit"] = _paper_hit(_bucket(entry.get("my_action")), fwd["return_pct"], row.get("alpha_pct"))
        engine_action = entry.get("engine_action") or ""
        if engine_action:
            row["engine_hit"] = _paper_hit(_bucket(engine_action), fwd["return_pct"], row.get("alpha_pct"))
        outcomes[key] = row
        changed = True
    if changed:
        status = "measured" if len(outcomes) == len(OUTCOME_HORIZONS_D) else "partial"
        evaluated_at = datetime.now(timezone.utc).isoformat()
        persistence.get_store().update_decision_outcomes(
            entry["decision_id"], outcomes, status, evaluated_at)
        entry = {**entry, "outcomes": outcomes, "outcome_status": status, "evaluated_at": evaluated_at}
    return entry


def list_decisions(limit: int = 200, refresh_outcomes: bool = True) -> list[dict[str, Any]]:
    entries = persistence.get_store().decision_journal(limit=limit)
    if not refresh_outcomes:
        return entries
    return [evaluate_outcomes(e) if e.get("outcome_status") in {"pending", "partial"} else e
            for e in entries]


def refresh_pending_outcomes(limit: int = 250) -> int:
    """Score pending/partial decisions against the latest price history.

    Runs at data-refresh time (upload, live refresh) beside the signal
    journal's forward-result refresh — never from GET handlers. Outcomes only
    change when new price bars arrive, so measuring on data arrival keeps
    reads pure without ever leaving a measurable decision unmeasured.
    Returns the number of entries whose outcomes advanced.
    """
    refreshed = 0
    try:
        entries = persistence.get_store().decision_journal(limit=limit)
    except Exception:
        return 0
    for entry in entries:
        if entry.get("outcome_status") not in {"pending", "partial"}:
            continue
        try:
            if evaluate_outcomes(entry) is not entry:
                refreshed += 1
        except Exception:
            continue
    return refreshed


# --------------------------------------------------------------------------- #
# Scoreboard
# --------------------------------------------------------------------------- #
def _primary_outcome(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Longest measured horizon (most decision-relevant window available)."""
    outcomes = entry.get("outcomes") or {}
    for horizon in sorted((int(k) for k in outcomes), reverse=True):
        row = outcomes.get(str(horizon))
        if row and row.get("hit") is not None:
            return {**row, "horizon_days": horizon}
    return None


def _bucket_stats(entries: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [(e, _primary_outcome(e)) for e in entries]
    measured = [(e, o) for e, o in scored if o is not None]
    hits = [o["hit"] for _, o in measured]
    # Per-horizon breakdown: the primary-outcome headline mixes 21-day returns
    # (young decisions) with 252-day returns (old ones) in one average —
    # incomparable windows (review finding). Each horizon row aggregates only
    # its own window; the headline stays as the labeled mixed-window view.
    by_horizon: dict[str, Any] = {}
    for horizon in OUTCOME_HORIZONS_D:
        rows = [o for o in ((e.get("outcomes") or {}).get(str(horizon)) for e in entries)
                if o and o.get("hit") is not None]
        if not rows:
            continue
        by_horizon[str(horizon)] = {
            "measured_count": len(rows),
            "hit_count": sum(1 for o in rows if o.get("hit")),
            "hit_rate_pct": _pct(sum(1 for o in rows if o.get("hit")), len(rows)),
            "avg_target_return_pct": _avg(o.get("target_return_pct") for o in rows),
            "avg_alpha_pct": _avg(o.get("alpha_pct") for o in rows),
            "avg_alpha_after_default_costs_pct": costs.net_of_default_costs(
                _avg(o.get("alpha_pct") for o in rows)),
        }
    return {
        "count": len(entries),
        "measured_count": len(measured),
        "hit_count": sum(1 for h in hits if h),
        "hit_rate_pct": _pct(sum(1 for h in hits if h), len(hits)),
        "avg_target_return_pct": _avg(o.get("target_return_pct") for _, o in measured),
        "avg_alpha_pct": _avg(o.get("alpha_pct") for _, o in measured),
        "avg_alpha_after_default_costs_pct": costs.net_of_default_costs(
            _avg(o.get("alpha_pct") for _, o in measured)),
        "alpha_basis": costs.GROSS_LABEL,
        "alpha_cost_basis": costs.NET_ASSUMPTION_LABEL,
        "headline_basis": "longest measured horizon per decision (mixed windows) — "
                          "use by_horizon for like-for-like comparisons",
        "by_horizon": by_horizon,
    }


def scoreboard(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Agree-vs-override evidence: whose calls are actually earning alpha."""
    agree = [e for e in entries if e.get("agreement") == "agree"]
    override = [e for e in entries if e.get("agreement") == "override"]
    override_won = engine_won = tied = 0
    for e in override:
        o = _primary_outcome(e)
        if o is None or o.get("engine_hit") is None:
            continue
        mine, engine = bool(o.get("hit")), bool(o.get("engine_hit"))
        if mine and not engine:
            override_won += 1
        elif engine and not mine:
            engine_won += 1
        else:
            tied += 1
    by_mandate: dict[str, Any] = {}
    for key in sorted({e.get("mandate") or "unspecified" for e in entries}):
        subset = [e for e in entries if (e.get("mandate") or "unspecified") == key]
        by_mandate[key] = _bucket_stats(subset)
    return {
        "total": _bucket_stats(entries),
        "agree": _bucket_stats(agree),
        "override": _bucket_stats(override),
        "override_vs_engine": {
            "override_won": override_won,
            "engine_won": engine_won,
            "tied": tied,
            "note": "Overrides where both your call and the engine's call have a measured "
                    "primary outcome; 'won' means one hit while the other missed.",
        },
        "by_mandate": by_mandate,
        "not_measurable_count": sum(1 for e in entries if e.get("outcome_status") == "not_measurable"),
        "disclaimer": "Paper outcomes from stored decision dates and later prices — evidence for "
                      "learning, not a performance record of executed trades.",
    }

"""Signal journal and paper-performance tracking.

The journal records deterministic Helios signal outputs with the exact input
window used at the time. It never stores raw price histories; it stores compact
audit facts plus a pending/measured forward result when later data exists.

Recording is explicit and idempotent per (target, input_end, horizon, action,
score): viewing analysis is read-only, while POST signal-recording actions
capture prospective evidence. Measured results receive a separate immutable
evidence envelope before the journal row advances. Evidence aggregation counts
each forward window once (see _dedupe_forward_windows); raw rows are the audit
trail.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from . import costs, data, evidence, persistence, portfolio, provenance
from ._common import (
    avg as _avg,
    clean_close as _clean_close,
    finite as _finite,
    journal_hold_preserved as _hold_preserved,
    journal_paper_hit as _paper_hit,
    pct as _pct,
)

MODEL_BENCHMARKS = {
    "pure_growth": "QQQ",
    "balanced": "SPY",
    "income": "BND",
    "capital_preservation": "BND",
    "cd_alternative": "SGOV",
}

# Signals recorded from demo/sample data are never measured against later
# prices (real or synthetic); the status stays within persistence's 16-char cap.
NOT_MEASURABLE_STATUS = "not_measurable"


def benchmark_for_model(model: portfolio.Model) -> str:
    return MODEL_BENCHMARKS.get(model.mandate_key, "SPY")


def record_signal(
    *,
    target_kind: str,
    target_id: str,
    target_name: str,
    close: pd.Series,
    input_close: pd.Series,
    signal: dict[str, Any],
    horizon_days: int,
    benchmark: str,
    source_counts: dict[str, int],
    eligible_for_real_research: bool,
    data_mode: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_input = _clean_close(input_close)
    full_close = _clean_close(close)
    if clean_input.empty:
        return {"recorded": False, "warning": "No input history available for signal journal."}
    input_start = str(clean_input.index.min().date())
    input_end = str(clean_input.index.max().date())
    score = float(signal.get("score") or 0.0)
    action = str(signal.get("action") or "HOLD").upper()
    real_eligible = bool(eligible_for_real_research)
    # Settlement guard (mirrors the decision journal's 7-day rule): a signal
    # recorded TODAY on a price window ending months ago blends today's
    # sentiment/macro with stale prices, then scores a forward window whose
    # bars were already knowable at record time (review finding).
    lag_days = (datetime.now(timezone.utc).date() - clean_input.index.max().date()).days
    # Bounded on BOTH sides: negative lag means the input window ends in the
    # FUTURE (fabricated bars — review finding, reproduced settling +2%
    # against prices that don't exist). -1 tolerates same-day bars from
    # markets ahead of UTC, mirroring the ingestion cutoff.
    fresh_enough = -1 <= lag_days <= 7
    measurable = real_eligible and fresh_enough
    evidence_id = evidence.new_id("signal")
    event_metadata = dict(metadata or {})
    event_metadata["evidence"] = evidence.reference(evidence_id)
    try:
        from . import trials
        link_metadata = {**event_metadata, "input_end_date": input_end}
        trial_id, recording_source = trials.active_link(target_kind, target_id, link_metadata)
        if link_metadata.get("trial_link_warning"):
            event_metadata["trial_link_warning"] = link_metadata["trial_link_warning"]
        if trial_id:
            trial = persistence.get_store().prospective_trial(trial_id) or {}
            expected_horizon = int((trial.get("protocol") or {}).get("horizon_days") or 0)
            if expected_horizon and int(horizon_days) != expected_horizon:
                event_metadata["trial_link_warning"] = (
                    f"Scheduled snapshot horizon {int(horizon_days)} did not match "
                    f"preregistered horizon {expected_horizon}; trial linkage withheld."
                )
                trial_id, recording_source = "", "exploratory"
    except Exception:
        trial_id, recording_source = "", (
            "scheduled" if event_metadata.get("endpoint") == "auto_snapshot" else "exploratory"
        )
    event = {
        "dedupe_key": _dedupe_key(target_kind, target_id, input_end, horizon_days, action, score),
        "target_kind": target_kind,
        "target_id": target_id,
        "target_name": target_name,
        "benchmark": benchmark,
        "input_start_date": input_start,
        "input_end_date": input_end,
        "input_rows": int(clean_input.shape[0]),
        "horizon_days": int(horizon_days),
        "score": round(score, 6),
        "action_label": action,
        "data_mode": data_mode,
        "eligible_for_real_research": real_eligible,
        "source_counts": source_counts,
        "metadata": event_metadata,
        "trial_id": trial_id,
        "recording_source": recording_source,
        **(_forward_result(full_close, input_end, horizon_days) if measurable
           else _not_measurable_result()),
    }
    if real_eligible and not fresh_enough:
        event["metadata"]["not_measurable_reason"] = (
            f"Input window ends {-lag_days} days in the FUTURE — fabricated bars "
            "cannot anchor a forward measurement."
            if lag_days < -1 else
            f"Price history ends {lag_days} days before the signal was recorded; "
            "scoring a forward window that was already observable grants hindsight.")
    if measurable:
        benchmark_result = _benchmark_result(benchmark, input_end, horizon_days,
                                             target_end_date=event.get("forward_end_date"))
        if benchmark_result.get("benchmark_result_pct") is not None:
            event.update(benchmark_result)
            if event.get("forward_result_pct") is not None:
                event["alpha_pct"] = round(event["forward_result_pct"] - benchmark_result["benchmark_result_pct"], 4)
        elif benchmark_result.get("benchmark_note"):
            event["metadata"]["benchmark_note"] = benchmark_result["benchmark_note"]
    store = persistence.get_store()
    if not store.available:
        return store.record_signal_event(event)
    with store.transaction(durable=True):
        result = store.record_signal_event(event)
        if not result.get("recorded") and not result.get("duplicate"):
            raise RuntimeError(result.get("warning") or "Signal journal entry could not be persisted.")
        entry = result.get("entry") or result
        if result.get("recorded") and isinstance(entry, dict):
            captured = evidence.capture(
                evidence_id=evidence_id,
                artifact_kind="signal",
                target_kind=target_kind,
                target_id=target_id,
                input_payload={
                    "input_series": evidence.series_payload(clean_input),
                    "available_series": evidence.series_payload(full_close),
                    "benchmark_series": _benchmark_series_payload(benchmark),
                    "signal": signal,
                    "horizon_days": int(horizon_days),
                    "benchmark": benchmark,
                    "source_counts": source_counts,
                    "eligible_for_real_research": real_eligible,
                    "data_mode": data_mode,
                    "trial_id": trial_id,
                    "recording_source": recording_source,
                },
                output_payload=entry,
                evidence_manifest=evidence.manifest(
                    series=clean_input,
                    source=_primary_source(source_counts),
                    provider=str(event_metadata.get("provider") or ""),
                    retrieved_at=str(event_metadata.get("retrieved_at") or ""),
                    transformations=("clean_close", "deterministic_signal"),
                    extra={"source_counts": source_counts, "horizon_days": int(horizon_days)},
                ),
            )
            if not captured.get("recorded"):
                raise RuntimeError(captured.get("warning") or "Signal evidence could not be persisted.")
    return entry


def _primary_source(source_counts: dict[str, int]) -> str:
    if not source_counts:
        return ""
    return max(source_counts.items(), key=lambda item: (int(item[1]), item[0]))[0]


def _benchmark_series_payload(benchmark: str) -> dict[str, Any]:
    inst = data.get(benchmark)
    if inst is None or not provenance.is_real_source(inst.source) or "close" not in inst.df:
        return evidence.series_payload(pd.Series(dtype=float))
    return evidence.series_payload(_clean_close(inst.df["close"]))


def list_entries(limit: int = 100) -> list[dict[str, Any]]:
    """Read-only journal view. Pending forward results are refreshed after
    data refreshes (see data.ensure_live_symbols), never on the read path."""
    return persistence.get_store().signal_journal(limit=limit)


def _dedupe_forward_windows(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """One observation per forward window for EVIDENCE aggregation.

    Re-viewing a ticker intraday drifts the score (sentiment/macro/price move)
    just enough to mint a new dedupe key over the IDENTICAL forward window, so
    frequently-viewed names accrued pseudo-replicated weight in hit-rate/alpha
    statistics (review finding). Collapse by (target, input_end, horizon)
    keeping the latest created_at; raw rows stay in the store as the audit
    trail. Returns (collapsed entries, superseded count)."""
    latest: dict[tuple, dict[str, Any]] = {}
    for entry in entries:
        key = (entry.get("target_kind"), entry.get("target_id"),
               entry.get("input_end_date"), entry.get("horizon_days"))
        held = latest.get(key)
        if held is None or str(entry.get("created_at") or "") >= str(held.get("created_at") or ""):
            latest[key] = entry
    collapsed = list(latest.values())
    return collapsed, len(entries) - len(collapsed)


def dashboard_payload(entries: list[dict[str, Any]]) -> dict[str, Any]:
    unique, superseded = _dedupe_forward_windows(entries)
    return {
        "summary": summarize_entries(unique),
        "benchmark_comparison": benchmark_comparison(unique),
        "model_evidence": model_evidence(unique),
        "track_evidence": track_evidence(unique),
        "drift": drift_series(unique),
        # Visible, never silent: how many near-duplicate window observations
        # were collapsed out of the evidence statistics.
        "superseded_count": superseded,
        "dedupe_basis": ("Evidence statistics count each (target, input-end, horizon) "
                         "forward window once — latest recording wins; raw journal rows "
                         "are preserved as the audit trail."),
    }


_TRACK_EVIDENCE_MIN_N = 10


def track_evidence(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Out-of-sample evidence for the STRATEGIC (fundamentals) track.

    Prospective by design: entries record the strategic gap at signal time
    (metadata.strategic_gap_pp) and are scored here once their forward window
    measures. No point-in-time fundamentals exist for a retrospective backtest,
    so below the minimum sample this reports "insufficient evidence" honestly
    instead of a noise statistic.
    """
    scored = []
    for entry in entries:
        meta = entry.get("metadata") or {}
        gap = _finite(meta.get("strategic_gap_pp"))
        fwd = _finite(entry.get("forward_result_pct"))
        if gap is None or fwd is None or entry.get("forward_status") != "measured":
            continue
        if not _real_eligible(entry):
            continue
        scored.append({"gap_pp": gap, "forward_pct": fwd,
                       "strategic_action": str(meta.get("strategic_action") or "")})
    n = len(scored)
    out: dict[str, Any] = {
        "measured_count": n,
        "min_required": _TRACK_EVIDENCE_MIN_N,
        "sufficient": n >= _TRACK_EVIDENCE_MIN_N,
        "note": ("Prospective validation: strategic gaps recorded at signal time, scored "
                 "as forward windows measure. Retrospective backtests are impossible "
                 "without point-in-time fundamentals and are not faked here."),
    }
    if n < _TRACK_EVIDENCE_MIN_N:
        return out
    # Directional agreement: did positive gaps precede positive forward returns?
    agree = sum(1 for s in scored if (s["gap_pp"] > 0) == (s["forward_pct"] > 0))
    pos = [s["forward_pct"] for s in scored if s["gap_pp"] > 0]
    neg = [s["forward_pct"] for s in scored if s["gap_pp"] <= 0]
    avg_pos, avg_neg = _avg(pos), _avg(neg)
    out.update({
        "direction_agreement_pct": _pct(agree, n),
        "avg_forward_when_gap_positive_pct": avg_pos,
        "avg_forward_when_gap_negative_pct": avg_neg,
        "spread_pp": (round(avg_pos - avg_neg, 2)
                      if avg_pos is not None and avg_neg is not None else None),
    })
    return out


def summarize_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    # Headline evidence aggregates only real-eligible signals recorded before
    # outcomes. Ineligible legacy entries remain visible but never score.
    real = [entry for entry in entries if _real_eligible(entry)]
    ineligible = [entry for entry in entries if not _real_eligible(entry)]
    outcomes = [entry for entry in real if entry.get("forward_status") == "measured"]
    pending = [entry for entry in real if entry.get("forward_status") != "measured"]
    ineligible_measured = [entry for entry in ineligible if entry.get("forward_status") == "measured"]
    hit_flags = [_paper_hit(entry) for entry in outcomes]
    hit_flags = [flag for flag in hit_flags if flag is not None]
    hold_flags = [_hold_preserved(entry) for entry in outcomes]
    hold_flags = [flag for flag in hold_flags if flag is not None]
    directional_alphas = [
        costs.directional_alpha(entry.get("action_label"), entry.get("alpha_pct"))
        for entry in outcomes
    ]
    return {
        "total_count": len(entries),
        "outcome_count": len(outcomes),
        "measured_count": len(hit_flags),
        "pending_count": len(pending),
        "hit_count": sum(1 for flag in hit_flags if flag),
        "hit_rate_pct": _pct(sum(1 for flag in hit_flags if flag), len(hit_flags)),
        "hold_measured_count": len(hold_flags),
        "hold_preserved_count": sum(1 for flag in hold_flags if flag),
        "hold_preservation_rate_pct": _pct(sum(1 for flag in hold_flags if flag), len(hold_flags)),
        "avg_score": _avg(entry.get("score") for entry in entries),
        "avg_forward_result_pct": _avg(entry.get("forward_result_pct") for entry in outcomes),
        "avg_benchmark_result_pct": _avg(entry.get("benchmark_result_pct") for entry in outcomes),
        "avg_alpha_pct": _avg(directional_alphas),
        "avg_alpha_after_default_costs_pct": costs.net_of_default_costs(_avg(directional_alphas)),
        "alpha_basis": costs.GROSS_LABEL,
        "alpha_cost_basis": costs.NET_ASSUMPTION_LABEL,
        "model_count": len({entry.get("target_id") for entry in entries if entry.get("target_kind") == "model"}),
        "instrument_count": len({entry.get("target_id") for entry in entries if entry.get("target_kind") == "instrument"}),
        "research_ready_count": len(real),
        "ineligible_count": len(ineligible),
        "ineligible_measured_count": len(ineligible_measured),
        "ineligible_pending_count": len(ineligible) - len(ineligible_measured),
        "measurement_basis": "Hit rate uses only BUY/SELL calls with benchmark alpha; HOLD preservation is reported separately.",
    }


def benchmark_comparison(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    ineligible_measured: dict[str, int] = {}
    for entry in entries:
        benchmark = str(entry.get("benchmark") or "").strip()
        if not benchmark or entry.get("forward_status") != "measured":
            continue
        if _real_eligible(entry):
            groups.setdefault(benchmark, []).append(entry)
        else:
            # Ineligible legacy measurements never feed benchmark statistics.
            ineligible_measured[benchmark] = ineligible_measured.get(benchmark, 0) + 1
    rows = []
    for benchmark, rows_for_benchmark in groups.items():
        hit_flags = [_paper_hit(entry) for entry in rows_for_benchmark]
        hit_flags = [flag for flag in hit_flags if flag is not None]
        directional_alphas = [
            costs.directional_alpha(entry.get("action_label"), entry.get("alpha_pct"))
            for entry in rows_for_benchmark
        ]
        rows.append({
            "benchmark": benchmark,
            "outcome_count": len(rows_for_benchmark),
            "measured_count": len(hit_flags),
            "avg_forward_result_pct": _avg(entry.get("forward_result_pct") for entry in rows_for_benchmark),
            "avg_benchmark_result_pct": _avg(entry.get("benchmark_result_pct") for entry in rows_for_benchmark),
            "avg_alpha_pct": _avg(directional_alphas),
            "avg_alpha_after_default_costs_pct": costs.net_of_default_costs(_avg(directional_alphas)),
            "hit_rate_pct": _pct(sum(1 for flag in hit_flags if flag), len(hit_flags)),
            "ineligible_measured_count": ineligible_measured.pop(benchmark, 0),
        })
    for benchmark, count in ineligible_measured.items():
        rows.append({
            "benchmark": benchmark,
            "measured_count": 0,
            "avg_forward_result_pct": None,
            "avg_benchmark_result_pct": None,
            "avg_alpha_pct": None,
            "avg_alpha_after_default_costs_pct": None,
            "hit_rate_pct": None,
            "ineligible_measured_count": count,
        })
    return sorted(rows, key=lambda row: (row["measured_count"], row["benchmark"]), reverse=True)


def model_evidence(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        if entry.get("target_kind") == "model":
            groups.setdefault(str(entry.get("target_id") or ""), []).append(entry)
    rows = []
    for target_id, model_entries in groups.items():
        ordered = sorted(model_entries, key=lambda entry: str(entry.get("created_at") or ""), reverse=True)
        real_entries = [entry for entry in model_entries if _real_eligible(entry)]
        outcomes = [entry for entry in real_entries if entry.get("forward_status") == "measured"]
        directional_alphas = [
            costs.directional_alpha(entry.get("action_label"), entry.get("alpha_pct"))
            for entry in outcomes
        ]
        hit_flags = [_paper_hit(entry) for entry in outcomes]
        hit_flags = [flag for flag in hit_flags if flag is not None]
        hold_flags = [_hold_preserved(entry) for entry in outcomes]
        hold_flags = [flag for flag in hold_flags if flag is not None]
        latest = ordered[0] if ordered else {}
        source_counts = _merge_source_counts(model_entries)
        rows.append({
            "target_id": target_id,
            "target_name": latest.get("target_name") or target_id,
            "signal_count": len(model_entries),
            "outcome_count": len(outcomes),
            "measured_count": len(hit_flags),
            "pending_count": len(real_entries) - len(outcomes),
            "hit_rate_pct": _pct(sum(1 for flag in hit_flags if flag), len(hit_flags)),
            "hold_preservation_rate_pct": _pct(sum(1 for flag in hold_flags if flag), len(hold_flags)),
            "avg_score": _avg(entry.get("score") for entry in model_entries),
            "avg_alpha_pct": _avg(directional_alphas),
            "avg_alpha_after_default_costs_pct": costs.net_of_default_costs(_avg(directional_alphas)),
            "latest_action_label": latest.get("action_label") or "",
            "latest_score": latest.get("score"),
            "latest_input_end_date": latest.get("input_end_date") or "",
            "benchmark": latest.get("benchmark") or "",
            "data_modes": sorted({str(entry.get("data_mode") or "unknown") for entry in model_entries}),
            "source_counts": source_counts,
            "research_ready_count": len(real_entries),
            "ineligible_count": len(model_entries) - len(real_entries),
        })
    return sorted(rows, key=lambda row: (row["pending_count"], row["signal_count"], row["target_name"]), reverse=True)


def drift_series(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(entries, key=lambda entry: (str(entry.get("input_end_date") or ""), str(entry.get("created_at") or ""), str(entry.get("dedupe_key") or "")))
    out = []
    measured_count = 0
    hit_count = 0
    hold_measured_count = 0
    hold_preserved_count = 0
    alpha_values: list[float] = []
    for index, entry in enumerate(ordered, start=1):
        hit = _paper_hit(entry)
        hold = _hold_preserved(entry)
        # Cumulative headline evidence only accrues from real-eligible signals.
        if hit is not None and _real_eligible(entry):
            measured_count += 1
            if hit:
                hit_count += 1
        if hold is not None and _real_eligible(entry):
            hold_measured_count += 1
            if hold:
                hold_preserved_count += 1
        if entry.get("forward_status") == "measured" and _real_eligible(entry):
            directional_alpha = costs.directional_alpha(
                entry.get("action_label"), entry.get("alpha_pct"),
            )
            if directional_alpha is not None:
                alpha_values.append(float(directional_alpha))
        out.append({
            "index": index,
            "created_at": entry.get("created_at"),
            "input_end_date": entry.get("input_end_date"),
            "target_kind": entry.get("target_kind"),
            "target_id": entry.get("target_id"),
            "target_name": entry.get("target_name"),
            "action_label": entry.get("action_label"),
            "score": entry.get("score"),
            "data_mode": entry.get("data_mode"),
            "eligible_for_real_research": _real_eligible(entry),
            "forward_status": entry.get("forward_status"),
            "forward_result_pct": entry.get("forward_result_pct"),
            "benchmark_result_pct": entry.get("benchmark_result_pct"),
            "alpha_pct": entry.get("alpha_pct"),
            "paper_hit": hit,
            "hold_preserved": hold,
            "cumulative_measured_count": measured_count,
            "cumulative_hit_rate_pct": _pct(hit_count, measured_count),
            "cumulative_hold_measured_count": hold_measured_count,
            "cumulative_hold_preservation_rate_pct": _pct(hold_preserved_count, hold_measured_count),
            "cumulative_avg_alpha_pct": _avg(alpha_values),
            "cumulative_avg_alpha_net_pct": costs.net_of_default_costs(_avg(alpha_values)),
        })
    return out


def refresh_forward_results(limit: int = 250) -> None:
    store = persistence.get_store()
    # Pending-only, oldest first: the newest-250 listing starved long-horizon
    # (63/252d) entries out of the refresh window forever (review finding).
    for entry in store.pending_signal_entries(limit=limit):
        if entry["forward_status"] == "measured":
            continue
        if not _real_eligible(entry):
            # Demo-time signals must never be scored against later live prices.
            if entry["forward_status"] != NOT_MEASURABLE_STATUS:
                result = _not_measurable_result()
                with store.transaction(durable=True):
                    captured = _capture_forward_result(
                        entry, pd.Series(dtype=float), result, pd.Series(dtype=float),
                    )
                    if not captured.get("recorded"):
                        raise RuntimeError(captured.get("warning") or "Signal result evidence could not be persisted.")
                    if not store.update_signal_forward_result(entry["dedupe_key"], result):
                        raise RuntimeError(store.warning or "Signal result could not be advanced.")
            continue
        close = _series_for_entry(entry)
        result = _forward_result(close, entry["input_end_date"], entry["horizon_days"])
        benchmark_close = _benchmark_close(entry["benchmark"])
        benchmark_result = _benchmark_result(entry["benchmark"], entry["input_end_date"],
                                             entry["horizon_days"],
                                             target_end_date=result.get("forward_end_date"))
        if benchmark_result.get("benchmark_result_pct") is not None:
            result.update(benchmark_result)
        if result.get("forward_result_pct") is not None and result.get("benchmark_result_pct") is not None:
            result["alpha_pct"] = round(result["forward_result_pct"] - result["benchmark_result_pct"], 4)
        if result["forward_status"] == "measured":
            with store.transaction(durable=True):
                captured = _capture_forward_result(entry, close, result, benchmark_close)
                if not captured.get("recorded"):
                    raise RuntimeError(captured.get("warning") or "Signal result evidence could not be persisted.")
                if not store.update_signal_forward_result(entry["dedupe_key"], result):
                    raise RuntimeError(store.warning or "Signal result could not be advanced.")


def _capture_forward_result(
    entry: dict[str, Any],
    close: pd.Series,
    result: dict[str, Any],
    benchmark_close: pd.Series,
) -> dict[str, Any]:
    """Seal each forward-status transition before mutating the journal row."""
    original_ref = ((entry.get("metadata") or {}).get("evidence") or {})
    evidence_id = evidence.new_id("signal-result")
    return evidence.capture(
        evidence_id=evidence_id,
        artifact_kind="signal_forward_result",
        target_kind=str(entry.get("target_kind") or ""),
        target_id=str(entry.get("target_id") or ""),
        input_payload={
            "signal_evidence": original_ref,
            "dedupe_key": entry.get("dedupe_key"),
            "input_end_date": entry.get("input_end_date"),
            "horizon_days": entry.get("horizon_days"),
            "prior_forward_status": entry.get("forward_status"),
            "available_series": evidence.series_payload(close),
            "benchmark": entry.get("benchmark"),
            "benchmark_series": evidence.series_payload(benchmark_close),
        },
        output_payload=result,
        evidence_manifest=evidence.manifest(
            series=close if not close.empty else None,
            source=_primary_source(entry.get("source_counts") or {}),
            transformations=("forward_return", "benchmark_alpha"),
            extra={"parent_evidence_id": original_ref.get("evidence_id", "")},
        ),
    )


def _real_eligible(entry: dict[str, Any]) -> bool:
    """Was this entry recorded from real-eligible (live/uploaded) data?"""
    return bool(entry.get("eligible_for_real_research"))


def _not_measurable_result() -> dict[str, Any]:
    return {
        "forward_status": NOT_MEASURABLE_STATUS,
        "forward_start_date": None,
        "forward_end_date": None,
        "forward_result_pct": None,
        "evaluated_at": None,
    }


def _series_for_entry(entry: dict[str, Any]) -> pd.Series:
    try:
        if entry["target_kind"] == "instrument":
            inst = data.get(entry["target_id"])
            if inst is None or not provenance.is_real_source(inst.source):
                # Mirrors the model/benchmark guards: if the symbol currently
                # resolves to a bundled sample/simulated series, a real-eligible
                # entry must stay pending — never measured against synthetic
                # prices and frozen as "measured" (review finding).
                return pd.Series(dtype=float)
            return inst.df["close"]
        if entry["target_kind"] == "model":
            model = portfolio.get(entry["target_id"])
            if model is None:
                return pd.Series(dtype=float)
            # Forward results must come from real prices only: a permissive
            # rebuild would silently measure the signal on sample/simulated
            # series, so failures leave the entry pending instead.
            return portfolio.build_series(model, allow_sample=False, allow_simulated=False).close
    except Exception:
        return pd.Series(dtype=float)
    return pd.Series(dtype=float)


def _benchmark_result(benchmark: str, input_end_date: str, horizon_days: int,
                      target_end_date: str | None = None) -> dict[str, Any]:
    close = _benchmark_close(benchmark)
    if close.empty:
        return {"benchmark_note": (
            f"Benchmark {benchmark} has no eligible live/uploaded price history; "
            "benchmark result and alpha are withheld."
        )}
    try:
        if target_end_date:
            # Align to the TARGET's realized calendar window: counting
            # `horizon` benchmark bars compared mismatched periods whenever
            # the target trades a different calendar (review finding).
            if close.empty or close.index.max() < pd.Timestamp(target_end_date):
                return {}
            start_px = close[close.index <= pd.Timestamp(input_end_date)]
            end_px = close[close.index <= pd.Timestamp(target_end_date)]
            if start_px.empty or end_px.empty or float(start_px.iloc[-1]) <= 0:
                return {}
            ret = (float(end_px.iloc[-1]) / float(start_px.iloc[-1]) - 1.0) * 100.0
            return {"benchmark_result_pct": round(ret, 4)}
        result = _forward_result(close, input_end_date, horizon_days)
        if result.get("forward_result_pct") is None:
            return {}
        return {"benchmark_result_pct": result["forward_result_pct"]}
    except Exception:
        return {}


def _benchmark_close(benchmark: str) -> pd.Series:
    try:
        inst = data.get(benchmark)
        if inst is None or not provenance.is_real_source(inst.source):
            return pd.Series(dtype=float)
        return _clean_close(inst.df["close"])
    except Exception:
        return pd.Series(dtype=float)


def _forward_result(close: pd.Series, input_end_date: str, horizon_days: int) -> dict[str, Any]:
    clean = _clean_close(close)
    if clean.empty:
        return {"forward_status": "pending", "forward_start_date": None, "forward_end_date": None, "forward_result_pct": None, "evaluated_at": None}
    input_end = pd.Timestamp(input_end_date)
    history = clean[clean.index <= input_end]
    future = clean[clean.index > input_end]
    if history.empty or len(future) < int(horizon_days):
        return {"forward_status": "pending", "forward_start_date": None, "forward_end_date": None, "forward_result_pct": None, "evaluated_at": None}
    start_price = float(history.iloc[-1])
    end_date = future.index[int(horizon_days) - 1]
    end_price = float(future.iloc[int(horizon_days) - 1])
    result_pct = (end_price / start_price - 1.0) * 100.0 if start_price else None
    return {
        "forward_status": "measured",
        "forward_start_date": str(input_end.date()),
        "forward_end_date": str(end_date.date()),
        "forward_result_pct": round(float(result_pct), 4) if result_pct is not None else None,
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _dedupe_key(target_kind: str, target_id: str, input_end: str, horizon_days: int, action: str, score: float) -> str:
    # `+ 0.0` normalizes negative zero: -0.00004 and +0.00004 both format as
    # "0.0000" instead of minting two keys via "-0.0000" vs "0.0000".
    score = round(float(score), 4) + 0.0
    raw = f"{target_kind}|{target_id}|{input_end}|{horizon_days}|{action}|{score:.4f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _merge_source_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for entry in entries:
        for source, count in dict(entry.get("source_counts") or {}).items():
            merged[str(source)] = merged.get(str(source), 0) + int(count or 0)
    return merged

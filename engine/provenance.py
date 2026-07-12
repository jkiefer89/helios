"""Data-provenance gates for Helios Pro research features."""
from __future__ import annotations

import datetime

REAL_SOURCES = {"live", "upload"}
DEMO_SOURCES = {"sample", "simulated"}
BLOCKED_SOURCES = {"missing", "excluded", "unknown"}

DEMO_ACTION = "Connect live data or upload real price/model files to generate real research."


def is_real_source(source: str | None) -> bool:
    return (source or "").lower() in REAL_SOURCES


def is_demo_source(source: str | None) -> bool:
    return (source or "").lower() in DEMO_SOURCES


def universe(instruments) -> dict:
    counts: dict[str, int] = {}
    for inst in instruments:
        counts[inst.source] = counts.get(inst.source, 0) + 1
    real_count = sum(counts.get(src, 0) for src in REAL_SOURCES)
    demo_count = sum(counts.get(src, 0) for src in DEMO_SOURCES)
    if real_count and demo_count:
        mode = "mixed"
        label = "Mixed Data Warning"
        reason = "Some instruments are live/uploaded, while bundled sample data remains present."
    elif real_count:
        mode = "real"
        label = "Real Research Mode"
        reason = "Research rankings are based on live or user-uploaded price history."
    elif demo_count:
        mode = "demo"
        label = "Demo Mode — Not Real Market Evidence"
        reason = "Only bundled synthetic sample data is available."
    else:
        mode = "invalid_for_research"
        label = "Data Quality Blocked"
        reason = "No eligible live or uploaded market history is available."
    return {
        "data_mode": mode,
        "display_label": label,
        "eligible_for_real_research": bool(real_count),
        "reason": reason,
        "required_action": "" if real_count else DEMO_ACTION,
        "source_counts": counts,
        "warnings": [] if real_count else [reason],
    }


def instrument(source: str, history_days: int, min_history: int = 60) -> dict:
    source = source or "unknown"
    enough_history = history_days >= min_history
    eligible = is_real_source(source) and enough_history
    if is_real_source(source):
        mode = "real"
        label = "Real Research Mode"
        reason = "" if enough_history else f"Need at least {min_history} sessions of real price history."
    elif is_demo_source(source):
        mode = "demo"
        label = "Demo sample — not real market data"
        reason = "Bundled sample/synthetic data is for UI demonstration only."
    else:
        mode = "invalid_for_research"
        label = "Data Quality Blocked"
        reason = "No eligible live or uploaded price history is available."
    return {
        "data_mode": mode,
        "display_label": label,
        "eligible_for_real_research": eligible,
        "reason": reason,
        "required_action": "" if eligible else DEMO_ACTION,
        "source_counts": {source: 1},
        "history_days": history_days,
        "warnings": [] if eligible else [reason],
    }


def _as_of_age_days(coverage: dict, now: "datetime.date | None") -> int | None:
    newest = (coverage.get("as_of_range") or {}).get("newest", "")
    if not newest:
        return None
    try:
        d = datetime.date.fromisoformat(str(newest)[:10])
    except ValueError:
        return None
    today = now or datetime.date.today()
    return (today - d).days


def forward_research(coverage: dict, ready_pct: float = 80.0, partial_pct: float = 40.0,
                     now: "datetime.date | None" = None,
                     warn_days: int = 135, stale_days: int = 270, block_days: int = 450) -> dict:
    """Second provenance axis: forward-data (holdings look-through) sufficiency.

    The existing :func:`portfolio` / :func:`instrument` gates answer "is there
    real PRICE history?" — a backward-looking question. A newly-launched fund
    fails that gate yet can be richly evaluated forward from what it HOLDS. This
    gate answers the orthogonal question: "do we know the model's composition
    well enough to run holdings-based forward research?" so a no-price-history
    fund is correctly ``invalid_for_price`` yet can still be ``valid_for_forward``.

    ``coverage`` is the block returned by ``holdings.model_lookthrough``. The
    decision is driven by composition coverage = look-through + transparent
    leaves; opaque (unresolved) fund weight is what blocks readiness.
    """
    looked = float(coverage.get("looked_through_pct", 0.0))
    leaf = float(coverage.get("leaf_pct", 0.0))
    uncovered = float(coverage.get("uncovered_pct", 0.0))
    composition = float(coverage.get("composition_coverage_pct", looked + leaf))
    n_holdings = int(coverage.get("n_holdings", 0) or 0)

    if n_holdings == 0:
        mode, label = "unavailable", "Forward Research Blocked"
        reason = "Model has no holdings to look through."
        eligible = False
    elif composition >= ready_pct:
        mode, label = "ready", "Forward Research Ready"
        reason = "Holdings look-through covers enough model weight for composition-based forward research."
        eligible = True
    elif composition >= partial_pct:
        mode, label = "partial", "Forward Research — Partial Coverage"
        reason = "Some holdings could not be looked through; forward research is based on partial composition."
        eligible = False
    else:
        mode, label = "unavailable", "Forward Research Blocked"
        reason = "Too little of the model's composition could be resolved for forward research."
        eligible = False

    unresolved = [u.get("ticker", "") for u in (coverage.get("unresolved") or []) if u.get("ticker")]
    warnings = []
    if uncovered > 0 and unresolved:
        warnings.append("Could not look through: " + ", ".join(unresolved))

    # Staleness: N-PORT is quarterly with a ~60-day public lag, so even fresh
    # filings describe a portfolio that has since drifted. Past a quarter+lag we
    # warn; past two quarters we stop calling it "ready"; past a year we block.
    age = _as_of_age_days(coverage, now)
    newest = (coverage.get("as_of_range") or {}).get("newest", "")
    if n_holdings > 0:
        if age is None and composition > 0:
            warnings.append("Holdings as-of date is unknown; composition recency cannot be confirmed.")
        elif age is not None and age > block_days:
            mode, label, eligible = "unavailable", "Forward Research Blocked", False
            reason = f"Holdings as of {newest} are {age} days old — too stale for forward research."
            warnings.append(reason)
        elif age is not None and age > stale_days:
            if mode == "ready":
                mode, label = "partial", "Forward Research — Stale Holdings"
            eligible = False
            warnings.append(f"Holdings as of {newest} are {age} days old; composition may have drifted materially.")
        elif age is not None and age > warn_days:
            warnings.append(f"Holdings as of {newest} are {age} days old (quarterly N-PORT lag); treat composition as indicative.")

    required_action = "" if eligible else (
        "Provide SEC look-through or a holdings CSV for: " + ", ".join(unresolved)
        if unresolved else "Refresh holdings: the latest available composition is too stale or thin."
    )
    return {
        "forward_mode": mode,
        "display_label": label,
        "eligible_for_forward_research": eligible,
        "reason": reason,
        "required_action": required_action,
        "composition_coverage_pct": round(composition, 1),
        "looked_through_pct": round(looked, 1),
        "leaf_pct": round(leaf, 1),
        "uncovered_pct": round(uncovered, 1),
        "as_of_days": age,
        "unresolved_tickers": unresolved,
        "as_of_range": coverage.get("as_of_range", {"oldest": "", "newest": ""}),
        "warnings": warnings,
    }


def portfolio(prov: dict, min_real_weight_pct: float = 99.9) -> dict:
    weights = prov.get("source_weight_pct", {}) or {}
    real_weight = float(weights.get("live", 0.0) + weights.get("upload", 0.0))
    sample_weight = float(weights.get("sample", 0.0))
    simulated_weight = float(weights.get("simulated", 0.0))
    missing_weight = float(weights.get("missing", 0.0) + weights.get("excluded", 0.0))
    non_real = sample_weight + simulated_weight + missing_weight
    eligible = real_weight >= min_real_weight_pct and non_real <= (100.0 - min_real_weight_pct + 1e-9)
    if eligible:
        mode = "real"
        label = "Real Research Mode"
        reason = "All analyzed model weight uses live or uploaded price history."
    elif real_weight > 0:
        mode = "mixed"
        label = "Mixed Data Warning"
        reason = "Some model weight lacks live/uploaded price history."
    else:
        mode = "invalid_for_research"
        label = "Data Quality Blocked"
        reason = "Model has no eligible live or uploaded price history."
    missing = list(prov.get("missing_symbols") or [])
    warnings = [] if eligible else [reason]
    if sample_weight:
        warnings.append(f"{sample_weight:.1f}% of model weight uses bundled sample data.")
    if simulated_weight:
        warnings.append(f"{simulated_weight:.1f}% of model weight would require simulated prices.")
    if missing:
        warnings.append("Upload real price history for: " + ", ".join(missing))
    return {
        "data_mode": mode,
        "display_label": label,
        "eligible_for_real_research": eligible,
        "reason": reason,
        "required_action": "" if eligible else DEMO_ACTION,
        "source_weight_pct": weights,
        "real_weight_pct": round(real_weight, 1),
        "non_real_weight_pct": round(non_real, 1),
        "missing_tickers": missing,
        "warnings": warnings,
        # Qualifies even "Real Research Mode": real DATA, constructed SERIES.
        "series_basis": prov.get("series_basis", ""),
        "series_basis_note": prov.get("series_basis_note", ""),
    }


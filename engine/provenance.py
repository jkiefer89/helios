"""Data-provenance gates for Helios Pro research features."""
from __future__ import annotations

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
    }


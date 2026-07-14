"""Data-provenance gates for Helios Pro research features."""
from __future__ import annotations

import datetime

REAL_SOURCES = {"live", "upload"}
INELIGIBLE_SOURCES = {"sample", "simulated"}
BLOCKED_SOURCES = {"missing", "excluded", "unknown"}

RESEARCH_DATA_ACTION = "Connect live data or upload real price/model files to generate real research."
# Compatibility aliases for older callers and retained database rows. Neither
# alias creates a runtime data mode.
DEMO_SOURCES = INELIGIBLE_SOURCES
DEMO_ACTION = RESEARCH_DATA_ACTION


def is_real_source(source: str | None) -> bool:
    return (source or "").lower() in REAL_SOURCES


def is_demo_source(source: str | None) -> bool:
    return (source or "").lower() in INELIGIBLE_SOURCES


def universe(instruments) -> dict:
    counts: dict[str, int] = {}
    eligibility = []
    for inst in instruments:
        counts[inst.source] = counts.get(inst.source, 0) + 1
        close = inst.df["close"].dropna() if "close" in inst.df else []
        eligibility.append(instrument(
            inst.source,
            len(close),
            price_provider=getattr(inst, "price_provider", ""),
        ))
    real_count = sum(1 for row in eligibility if row["eligible_for_real_research"])
    ineligible_count = sum(counts.get(src, 0) for src in INELIGIBLE_SOURCES)
    if real_count and ineligible_count:
        mode = "mixed"
        label = "Mixed Data Warning"
        reason = "Some instruments are live/uploaded, while ineligible histories remain present."
    elif real_count:
        mode = "real"
        label = "Real Research Mode"
        reason = "Research rankings are based on live or user-uploaded price history."
    elif ineligible_count:
        mode = "invalid_for_research"
        label = "Research Blocked - Ineligible Data"
        reason = "No eligible live or uploaded market history is available."
    else:
        mode = "invalid_for_research"
        label = "Data Quality Blocked"
        reason = "No eligible live or uploaded market history is available."
    return {
        "data_mode": mode,
        "display_label": label,
        "eligible_for_real_research": bool(real_count),
        "reason": reason,
        "required_action": "" if real_count else RESEARCH_DATA_ACTION,
        "source_counts": counts,
        "warnings": [] if real_count else [reason],
    }


def instrument(
    source: str,
    history_days: int,
    min_history: int = 60,
    price_provider: str = "",
) -> dict:
    source = source or "unknown"
    enough_history = history_days >= min_history
    provider_control = {
        "passed": True,
        "state": "not_applicable",
        "provider": price_provider or "not_applicable",
        "detail": "Uploaded histories are governed by their retained provenance record.",
        "required_action": "",
    }
    if source == "live":
        try:
            from . import provider_registry

            provider_control = provider_registry.provider_usage_readiness(price_provider, "prices")
        except Exception as exc:
            provider_control = {
                "passed": False,
                "state": "blocked",
                "provider": price_provider or "unknown",
                "detail": f"Provider control could not be verified: {exc}",
                "required_action": "Restore provider-control evidence before institutional research use.",
            }
    eligible = is_real_source(source) and enough_history and bool(provider_control["passed"])
    if is_real_source(source):
        if not enough_history:
            mode = "invalid_for_research"
            label = "Data Quality Blocked"
            reason = f"Need at least {min_history} sessions of real price history."
        elif not provider_control["passed"]:
            mode = "invalid_for_research"
            label = "Institutional Provider Gate Blocked"
            reason = str(provider_control.get("detail") or "Provider controls are incomplete.")
        else:
            mode = "real"
            label = "Real Research Mode"
            reason = ""
    elif is_demo_source(source):
        mode = "invalid_for_research"
        label = "Research Blocked - Ineligible Data"
        reason = "This history is not an eligible live or uploaded research source."
    else:
        mode = "invalid_for_research"
        label = "Data Quality Blocked"
        reason = "No eligible live or uploaded price history is available."
    return {
        "data_mode": mode,
        "display_label": label,
        "eligible_for_real_research": eligible,
        "reason": reason,
        "required_action": (
            "" if eligible else str(provider_control.get("required_action") or RESEARCH_DATA_ACTION)
        ),
        "source_counts": {source: 1},
        "history_days": history_days,
        "warnings": [] if eligible else [reason],
        "provider_control": provider_control,
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
    source_eligible = real_weight >= min_real_weight_pct and non_real <= (100.0 - min_real_weight_pct + 1e-9)
    provider_failures = []
    try:
        from . import provider_registry

        if provider_registry.controls_required():
            source_by_symbol = prov.get("source_by_symbol") or {}
            for ticker, provider in (prov.get("price_providers") or {}).items():
                if source_by_symbol.get(ticker) != "live":
                    continue
                control = provider_registry.provider_usage_readiness(str(provider or ""), "prices")
                if not control.get("passed"):
                    provider_failures.append({
                        "ticker": ticker,
                        "provider": control.get("provider") or "unknown",
                        "detail": control.get("detail") or "Provider control failed.",
                    })
    except Exception as exc:
        provider_failures.append({"ticker": "workspace", "provider": "unknown", "detail": str(exc)})
    eligible = source_eligible and not provider_failures
    if eligible:
        mode = "real"
        label = "Real Research Mode"
        reason = "All analyzed model weight uses live or uploaded price history."
    elif source_eligible and provider_failures:
        mode = "invalid_for_research"
        label = "Institutional Provider Gate Blocked"
        reason = "One or more live holding histories are outside the approved provider cutover."
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
        warnings.append(f"{sample_weight:.1f}% of model weight uses an ineligible fixture source.")
    if simulated_weight:
        warnings.append(f"{simulated_weight:.1f}% of model weight lacks retained market prices.")
    if missing:
        warnings.append("Upload real price history for: " + ", ".join(missing))
    if provider_failures:
        warnings.append("Provider control blocked: " + ", ".join(
            f"{row['ticker']} ({row['provider']})" for row in provider_failures
        ))
    return {
        "data_mode": mode,
        "display_label": label,
        "eligible_for_real_research": eligible,
        "reason": reason,
        "required_action": (
            "" if eligible else
            "Refresh live holdings through the approved primary or backup provider."
            if provider_failures else RESEARCH_DATA_ACTION
        ),
        "source_weight_pct": weights,
        "real_weight_pct": round(real_weight, 1),
        "non_real_weight_pct": round(non_real, 1),
        "missing_tickers": missing,
        "warnings": warnings,
        "provider_failures": provider_failures,
        # Qualifies even "Real Research Mode": real DATA, constructed SERIES.
        "series_basis": prov.get("series_basis", ""),
        "series_basis_note": prov.get("series_basis_note", ""),
    }

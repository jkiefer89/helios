"""Portfolio Clinic diagnostics and hypothetical rebalance suggestions."""
from __future__ import annotations

import numpy as np

from . import indicators, mandate, portfolio, provenance


def analyze_clinic(model: portfolio.Model) -> dict:
    try:
        ps = portfolio.build_series(model, allow_sample=False, allow_simulated=False)
    except ValueError as e:
        return _blocked_clinic(model, str(e), [h.ticker for h in model.holdings])
    p = provenance.portfolio(ps.provenance)
    if not p["eligible_for_real_research"]:
        return _blocked_clinic(model, p["reason"], p["missing_tickers"], ps.provenance, p["warnings"])
    m = mandate.get(model.mandate_key)
    metrics = indicators.metrics_summary(ps.close)
    cap = float(m["single_name_cap"])
    before_weights = {h["ticker"]: float(h["weight"]) for h in ps.holdings if not h.get("excluded")}
    after_weights, cap_warning = _cap_weights(before_weights, cap)
    suggestions = _suggestions(model, ps, metrics, m, before_weights, after_weights)
    diagnostics = _diagnostics(ps, metrics, m)
    warnings = list(ps.warnings)
    refusals = []
    sim_weight = ps.provenance.get("simulated_weight_pct", 0)
    if sim_weight:
        warnings.append(f"{sim_weight:.1f}% of analyzed weight uses simulated prices; clinic output is illustrative.")
    if sim_weight >= 50:
        refusals.append("Refusing precise rebalance optimization because simulated data dominates the analyzed model.")
    if cap_warning:
        warnings.append(cap_warning)

    return {
        "id": model.id,
        "name": model.name,
        "data_mode": p["data_mode"],
        "display_label": p["display_label"],
        "eligible_for_real_research": p["eligible_for_real_research"],
        "reason": p["reason"],
        "required_action": p["required_action"],
        "missing_tickers": p["missing_tickers"],
        "data_provenance": p,
        "mandate": {"key": model.mandate_key, **m},
        "constraints": {
            "long_only": True,
            "single_name_cap": cap,
            "no_short_weights": True,
        },
        "diagnostics": diagnostics,
        "risk_contributions": _risk_contributions(ps),
        "suggestions": suggestions,
        "before": {
            "weights": before_weights,
            "estimates": {
                "annual_vol_pct": metrics["annual_vol_pct"],
                "max_drawdown_pct": metrics["max_drawdown_pct"],
                "hhi": ps.hhi,
                "n_eff": ps.n_eff,
            },
        },
        "after": {
            "weights": after_weights,
            "estimates": _after_estimates(metrics, before_weights, after_weights),
        },
        "provenance": ps.provenance,
        "warnings": _dedupe(warnings),
        "refusals": refusals,
        "explanation": (
            "Suggestions are hypothetical research prompts. They respect long-only weights and configured "
            "single-name caps where feasible; they are not orders or investment advice."
        ),
    }


def _diagnostics(ps: portfolio.PortfolioSeries, metrics: dict, m: dict) -> dict:
    vol_gap = float(metrics["annual_vol_pct"] - m["target_vol_pct"])
    dd_gap = float(abs(metrics["max_drawdown_pct"]) - m["max_drawdown_tolerance_pct"])
    high_hhi = mandate.hhi_thresholds(m.get("key", ""))[1] if "key" in m else 0.25
    return {
        "hhi": ps.hhi,
        "effective_holdings": ps.n_eff,
        "mean_correlation": ps.corr_mean,
        "top_weight_pct": max((h["weight"] for h in ps.holdings if not h.get("excluded")), default=0) * 100,
        "volatility_gap_pct": max(0.0, vol_gap),
        "drawdown_gap_pct": max(0.0, dd_gap),
        "concentration_flag": ps.hhi >= high_hhi,
        "mandate_mismatch": vol_gap > 0 or dd_gap > 0,
        "data_quality_issue": ps.provenance.get("simulated_weight_pct", 0) > 0 or ps.provenance.get("n_excluded", 0) > 0,
    }


def _blocked_clinic(
    model: portfolio.Model,
    reason: str,
    missing_tickers: list[str],
    raw_provenance: dict | None = None,
    warnings: list[str] | None = None,
) -> dict:
    m = mandate.get(model.mandate_key)
    warn = list(warnings or [])
    if not warn:
        warn.append(reason)
    if missing_tickers:
        warn.append("Upload real price history for: " + ", ".join(missing_tickers))
    return {
        "id": model.id,
        "name": model.name,
        "data_mode": "invalid_for_research",
        "display_label": "Data Quality Blocked",
        "eligible_for_real_research": False,
        "reason": reason,
        "required_action": provenance.DEMO_ACTION,
        "missing_tickers": missing_tickers,
        "data_provenance": raw_provenance or {"missing_tickers": missing_tickers},
        "mandate": {"key": model.mandate_key, **m},
        "constraints": {
            "long_only": True,
            "single_name_cap": float(m["single_name_cap"]),
            "no_short_weights": True,
        },
        "diagnostics": {
            "hhi": 0.0,
            "effective_holdings": 0.0,
            "mean_correlation": 0.0,
            "top_weight_pct": 0.0,
            "volatility_gap_pct": 0.0,
            "drawdown_gap_pct": 0.0,
            "concentration_flag": False,
            "mandate_mismatch": False,
            "data_quality_issue": True,
        },
        "risk_contributions": [],
        "suggestions": [],
        "before": {"weights": {}, "estimates": {}},
        "after": {"weights": {}, "estimates": {}},
        "provenance": raw_provenance or {"missing_symbols": missing_tickers},
        "warnings": _dedupe(warn),
        "refusals": ["Portfolio Clinic requires live or uploaded price history for every analyzed holding."],
        "explanation": (
            "Data quality is blocked, so Helios will not fabricate risk, concentration, "
            "or rebalance evidence from sample or simulated prices."
        ),
    }


def _suggestions(model, ps, metrics, m, before, after) -> list[dict]:
    suggestions = []
    cap = float(m["single_name_cap"])
    for ticker, weight in sorted(before.items(), key=lambda kv: kv[1], reverse=True):
        suggested = after.get(ticker, weight)
        if weight > cap and suggested < weight and suggested <= cap + 1e-9:
            suggestions.append({
                "type": "trim",
                "ticker": ticker,
                "current_weight": weight,
                "suggested_weight": suggested,
                "rationale": f"Position exceeds the {cap:.0%} single-name cap for {m['label']}.",
            })

    vol_gap = metrics["annual_vol_pct"] - m["target_vol_pct"]
    dd_gap = abs(metrics["max_drawdown_pct"]) - m["max_drawdown_tolerance_pct"]
    low_risk_mandate = model.mandate_key in {"capital_preservation", "cd_alternative"}
    if low_risk_mandate and (vol_gap > 0 or dd_gap > 0):
        top_risk = sorted(_risk_contributions(ps), key=lambda h: h["mrc_pct"], reverse=True)[:3]
        suggestions.append({
            "type": "de_risk",
            "ticker": ", ".join(h["ticker"] for h in top_risk),
            "current_weight": sum(before.get(h["ticker"], 0) for h in top_risk),
            "suggested_weight": sum(after.get(h["ticker"], 0) for h in top_risk),
            "rationale": (
                f"{m['label']} risk budget is exceeded: vol gap {max(vol_gap, 0):.1f}%, "
                f"drawdown gap {max(dd_gap, 0):.1f}%."
            ),
        })

    if ps.provenance.get("simulated_weight_pct", 0) > 0:
        suggestions.append({
            "type": "data_quality",
            "ticker": ", ".join(ps.provenance.get("simulated_symbols", [])),
            "current_weight": ps.provenance.get("simulated_weight_pct", 0) / 100,
            "suggested_weight": 0.0,
            "rationale": "Replace simulated prices with client/live history before treating estimates as decision-grade.",
        })

    return suggestions or _research_hypotheses(ps, metrics, m, before)


def _research_hypotheses(ps, metrics, m, before) -> list[dict]:
    holdings = [h for h in ps.holdings if not h.get("excluded")]
    if len(holdings) < 2:
        return []
    cap = float(m["single_name_cap"])
    step = min(0.03, max(0.01, 1.0 / len(holdings) * 0.10))
    out: list[dict] = []

    top_return = max(holdings, key=lambda h: h.get("window_return_pct") or 0.0)
    top_return_weight = float(before.get(top_return["ticker"], 0.0))
    if top_return_weight < cap - 1e-9:
        out.append({
            "type": "test_tilt",
            "ticker": top_return["ticker"],
            "current_weight": top_return_weight,
            "suggested_weight": round(min(cap, top_return_weight + step), 6),
            "rationale": (
                f"Test a modest tilt toward {top_return['ticker']} because it has the strongest trailing "
                f"holding return in this real-data model ({top_return['window_return_pct']:.1f}%). "
                "Hypothetical research prompt only; confirm in Strategy Lab and Advisor Report before use."
            ),
        })

    top_risk = max(holdings, key=lambda h: h.get("mrc_pct") or 0.0)
    top_risk_weight = float(before.get(top_risk["ticker"], 0.0))
    if top_risk.get("mrc_pct", 0.0) > top_risk_weight * 100 + 5:
        out.append({
            "type": "test_risk_trim",
            "ticker": top_risk["ticker"],
            "current_weight": top_risk_weight,
            "suggested_weight": round(max(0.0, top_risk_weight - step), 6),
            "rationale": (
                f"Test trimming {top_risk['ticker']} because its marginal risk contribution "
                f"({top_risk['mrc_pct']:.1f}%) is above its portfolio weight. Hypothetical only; "
                "review tax, mandate, and client suitability outside Helios."
            ),
        })

    if not out and ps.n_eff < max(4.0, len(holdings) * 0.6):
        largest = max(holdings, key=lambda h: h.get("weight") or 0.0)
        largest_weight = float(before.get(largest["ticker"], 0.0))
        out.append({
            "type": "test_diversification",
            "ticker": largest["ticker"],
            "current_weight": largest_weight,
            "suggested_weight": round(max(0.0, largest_weight - step), 6),
            "rationale": (
                f"Test reducing {largest['ticker']} and adding lower-correlated real-data coverage because "
                f"effective holdings are {ps.n_eff:.1f}. Hypothetical research prompt only; do not treat "
                "this as an order."
            ),
        })

    if not out:
        top = sorted(holdings, key=lambda h: h.get("mrc_pct") or 0.0, reverse=True)[:3]
        out.append({
            "type": "test_stress_review",
            "ticker": ", ".join(h["ticker"] for h in top),
            "current_weight": sum(float(before.get(h["ticker"], 0.0)) for h in top),
            "suggested_weight": sum(float(before.get(h["ticker"], 0.0)) for h in top),
            "rationale": (
                f"Test stress scenarios for the top risk contributors before changing this {m['label']} "
                f"model; current volatility is {metrics['annual_vol_pct']:.1f}% and max drawdown is "
                f"{metrics['max_drawdown_pct']:.1f}%. Hypothetical research prompt only."
            ),
        })

    return out[:3]


def _cap_weights(weights: dict[str, float], cap: float) -> tuple[dict[str, float], str | None]:
    if not weights:
        return {}, None
    if cap * len(weights) < 1.0 - 1e-9:
        return dict(weights), "Single-name cap is infeasible for this number of analyzed holdings."
    capped = {k: max(0.0, float(v)) for k, v in weights.items()}
    total = sum(capped.values()) or 1.0
    capped = {k: v / total for k, v in capped.items()}
    for _ in range(12):
        excess = sum(max(0.0, v - cap) for v in capped.values())
        for k, v in list(capped.items()):
            if v > cap:
                capped[k] = cap
        if excess <= 1e-12:
            break
        receivers = {k: cap - v for k, v in capped.items() if v < cap - 1e-12}
        capacity = sum(receivers.values())
        if capacity <= 1e-12:
            break
        for k, room in receivers.items():
            capped[k] += excess * (room / capacity)
    total = sum(capped.values()) or 1.0
    return {k: round(max(0.0, v / total), 10) for k, v in capped.items()}, None


def _risk_contributions(ps: portfolio.PortfolioSeries) -> list[dict]:
    out = []
    for h in ps.holdings:
        if h.get("excluded"):
            continue
        out.append({
            "ticker": h["ticker"],
            "weight": float(h["weight"]),
            "source": h["source"],
            "mrc_pct": float(h.get("mrc_pct") or 0.0),
            "window_return_pct": float(h.get("window_return_pct") or 0.0),
        })
    return out


def _after_estimates(metrics: dict, before: dict[str, float], after: dict[str, float]) -> dict:
    before_hhi = sum(v * v for v in before.values()) or 1.0
    after_hhi = sum(v * v for v in after.values()) or before_hhi
    concentration_ratio = float(np.sqrt(after_hhi / before_hhi)) if before_hhi > 0 else 1.0
    est_vol = metrics["annual_vol_pct"] * min(1.0, concentration_ratio)
    est_dd = metrics["max_drawdown_pct"] * min(1.0, concentration_ratio)
    return {
        "annual_vol_pct": est_vol,
        "max_drawdown_pct": est_dd,
        "hhi": after_hhi,
        "n_eff": (1.0 / after_hhi) if after_hhi > 0 else 0.0,
    }


def _dedupe(items: list[str]) -> list[str]:
    out = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out

"""Portfolio-level risk and exposure analytics.

This module is deterministic and analysis-only. It summarizes risk evidence from
the same live/uploaded price histories used by the portfolio engine; it does not
create recommendations, execute trades, or alter signal/forecast methodology.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd

from . import assumptions, data, indicators, mandate, portfolio, provenance, signal_journal
from ._common import dedupe as _dedupe

_SECTOR_MAP = {
    "AAPL": ("Technology", "mega-cap platform"),
    "MSFT": ("Technology", "cloud/software"),
    "NVDA": ("Technology", "semiconductors"),
    "AMD": ("Technology", "semiconductors"),
    "AVGO": ("Technology", "semiconductors"),
    "TSM": ("Technology", "semiconductors"),
    "SMH": ("Technology", "semiconductors ETF"),
    "VRT": ("Industrials", "data-center infrastructure"),
    "AMZN": ("Consumer Discretionary", "cloud/platform"),
    "GOOGL": ("Communication Services", "digital platforms"),
    "META": ("Communication Services", "digital platforms"),
    "TSLA": ("Consumer Discretionary", "electric vehicles"),
    "QQQ": ("Technology", "growth ETF"),
    "SPY": ("Broad Market", "core equity ETF"),
    "IWM": ("Broad Market", "small-cap ETF"),
    "DIA": ("Broad Market", "large-cap ETF"),
    "JPM": ("Financials", "banking"),
    "XOM": ("Energy", "integrated energy"),
    "XLE": ("Energy", "energy ETF"),
    "UNH": ("Healthcare", "managed care"),
    "LLY": ("Healthcare", "pharma"),
    "XLV": ("Healthcare", "healthcare ETF"),
    "VHT": ("Healthcare", "healthcare ETF"),
    "IHI": ("Healthcare", "medtech ETF"),
    "IBB": ("Healthcare", "biotech ETF"),
    "ISRG": ("Healthcare", "medtech"),
    "TMO": ("Healthcare", "life-science tools"),
    "SYK": ("Healthcare", "medtech"),
    "ABBV": ("Healthcare", "pharma"),
    "LMT": ("Industrials", "defense"),
    "RTX": ("Industrials", "defense"),
    "NOC": ("Industrials", "defense"),
    "GD": ("Industrials", "defense"),
    "ITA": ("Industrials", "defense ETF"),
    "XAR": ("Industrials", "aerospace ETF"),
    "CIBR": ("Technology", "cybersecurity ETF"),
    "PANW": ("Technology", "cybersecurity"),
    "CRWD": ("Technology", "cybersecurity"),
    "FTNT": ("Technology", "cybersecurity"),
    "VST": ("Utilities", "power generation"),
    "CEG": ("Utilities", "power generation"),
    "XLU": ("Utilities", "utilities ETF"),
    "ETN": ("Industrials", "electrical equipment"),
    "PAVE": ("Industrials", "infrastructure ETF"),
    "NEE": ("Utilities", "renewable utility"),
    "GRID": ("Utilities", "grid infrastructure ETF"),
    "GE": ("Industrials", "industrial infrastructure"),
    "GLD": ("Commodities", "gold"),
    "TIP": ("Fixed Income", "inflation-linked bonds"),
    "BND": ("Fixed Income", "core bonds"),
    "TLT": ("Fixed Income", "long duration bonds"),
    "SGOV": ("Cash", "treasury bills"),
    "USMV": ("Broad Market", "minimum volatility ETF"),
    "VIG": ("Broad Market", "dividend quality ETF"),
    "QUAL": ("Broad Market", "quality ETF"),
    "BTC-USD": ("Crypto", "bitcoin"),
}

# Hand-assigned taxonomy loadings and ADV proxies: owned and dated in
# engine/assumptions.py so every payload labels them instead of presenting
# them as observed market data (review finding).
_FACTOR_MAP = assumptions.FACTOR_MAP["values"]
_FACTOR_MAP_AS_OF = assumptions.FACTOR_MAP["as_of"]
_FACTOR_MAP_METHODOLOGY = assumptions.FACTOR_MAP["methodology"]

_LIQUIDITY_AVG_DAILY_DOLLAR_VOLUME = assumptions.LIQUIDITY_ADV_PROXIES["values"]
_ADV_PROXY_AS_OF = assumptions.LIQUIDITY_ADV_PROXIES["as_of"]


def analyze_model_risk(model: portfolio.Model, aum_usd: float | None = None) -> dict[str, Any]:
    try:
        ps = portfolio.build_series(model, allow_sample=False, allow_simulated=False)
    except ValueError as exc:
        return _blocked_payload(model, str(exc), [h.ticker for h in model.holdings])
    p = provenance.portfolio(ps.provenance)
    if not p["eligible_for_real_research"]:
        return _blocked_payload(model, p["reason"], p["missing_tickers"], ps.provenance, p["warnings"])

    returns = _holding_returns(model)
    portfolio_returns = indicators.daily_returns(ps.close).dropna()
    benchmark_symbol = signal_journal.benchmark_for_model(model)
    benchmark = _benchmark_relative(portfolio_returns, benchmark_symbol)
    metrics = indicators.metrics_summary(ps.close)
    factor_exposure = _factor_exposure(model)
    vol_budget = _volatility_budget(metrics, model)
    liquidity = _liquidity_flags(model)
    capacity = _capacity_analysis(model, liquidity.get("items") or [], aum_usd)
    income_bucket = _income_bucket(model, aum_usd)
    concentration = _concentration(model, ps)
    drawdown = _drawdown_stress(ps.close)
    scenario_shocks = _scenario_shocks(model, factor_exposure)
    benchmark_relative = benchmark
    sector_exposure = _weighted_exposure(model, kind="sector")
    theme_exposure = _weighted_exposure(model, kind="theme")
    volatility_contribution = _volatility_contribution(ps)
    correlation_clusters = _correlation_clusters(returns)
    warnings = list(ps.warnings)
    if benchmark["status"] != "available":
        warnings.append(benchmark["message"])

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
        "mandate": {"key": model.mandate_key, **mandate.get(model.mandate_key)},
        "benchmark": {"symbol": benchmark_symbol, "status": benchmark["status"]},
        "sector_exposure": sector_exposure,
        "theme_exposure": theme_exposure,
        "factor_exposure": factor_exposure,
        "single_name_concentration": concentration,
        "volatility_budget": vol_budget,
        "volatility_contribution": volatility_contribution,
        "correlation_clusters": correlation_clusters,
        "drawdown_stress": drawdown,
        "scenario_shocks": scenario_shocks,
        "liquidity_flags": liquidity,
        "capacity": capacity,
        "income_bucket": income_bucket,
        "benchmark_relative": benchmark_relative,
        "client_risk_pack": _client_risk_pack(
            capacity=capacity,
            income_bucket=income_bucket,
            model=model,
            close=ps.close,
            data_provenance=p,
            benchmark_symbol=benchmark_symbol,
            benchmark_relative=benchmark_relative,
            concentration=concentration,
            sector_exposure=sector_exposure,
            factor_exposure=factor_exposure,
            volatility_budget=vol_budget,
            drawdown_stress=drawdown,
            scenario_shocks=scenario_shocks,
            liquidity=liquidity,
            correlation_clusters=correlation_clusters,
        ),
        "provenance": ps.provenance,
        "warnings": _dedupe(warnings),
        "methodology": {
            "analysis_only": True,
            "uses_live_or_uploaded_prices_only": True,
            "no_trade_execution": True,
            "classification_source": "static Helios public ticker taxonomy with unclassified fallback",
            "scenario_basis": "deterministic factor/asset-class shocks, not forecasts",
            # Factor exposure derives from NO price history at all (review
            # finding: the UI implied otherwise) — label it at the source.
            "factor_exposure_basis": (
                f"{_FACTOR_MAP_METHODOLOGY} (as of {_FACTOR_MAP_AS_OF})"),
            "assumptions_version": assumptions.ASSUMPTIONS_VERSION,
        },
        "disclaimer": "Analysis only. Risk analytics are estimates from available price history and do not guarantee outcomes.",
    }


def _blocked_payload(
    model: portfolio.Model,
    reason: str,
    missing_tickers: list[str],
    raw_provenance: dict | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    p = provenance.portfolio(raw_provenance or {"missing_symbols": missing_tickers}) if raw_provenance else {
        "data_mode": "invalid_for_research",
        "display_label": "Data Quality Blocked",
        "eligible_for_real_research": False,
        "reason": reason,
        "required_action": "Upload real price history for every model holding before running portfolio risk analytics.",
        "missing_tickers": missing_tickers,
        "warnings": [],
    }
    return {
        "id": model.id,
        "name": model.name,
        "data_mode": p["data_mode"],
        "display_label": p["display_label"],
        "eligible_for_real_research": False,
        "reason": reason,
        "required_action": "Upload real price history for every model holding before running portfolio risk analytics.",
        "missing_tickers": missing_tickers,
        "risk_exposure_unavailable": True,
        "data_provenance": p,
        "mandate": {"key": model.mandate_key, **mandate.get(model.mandate_key)},
        "sector_exposure": [],
        "theme_exposure": [],
        "factor_exposure": {},
        "single_name_concentration": {},
        "volatility_budget": {},
        "volatility_contribution": [],
        "correlation_clusters": [],
        "drawdown_stress": {},
        "scenario_shocks": [],
        "liquidity_flags": {"items": [], "summary": {"flagged_count": 0}},
        "benchmark_relative": {"status": "blocked", "benchmark_symbol": signal_journal.benchmark_for_model(model)},
        "client_risk_pack": _blocked_client_risk_pack(model, reason, missing_tickers),
        "warnings": _dedupe(list(warnings or []) + [reason]),
        "methodology": {
            "analysis_only": True,
            "uses_live_or_uploaded_prices_only": True,
            "no_trade_execution": True,
        },
        "disclaimer": "Analysis only. Risk analytics require eligible real price history.",
    }


def _blocked_client_risk_pack(model: portfolio.Model, reason: str, missing_tickers: list[str]) -> dict[str, Any]:
    return {
        "available": False,
        "summary": {
            "model_id": model.id,
            "model_name": model.name,
            "risk_posture": "blocked",
            "benchmark_symbol": signal_journal.benchmark_for_model(model),
        },
        "stress_scenarios": [],
        "historical_stress_replay": [],
        "benchmark_relative_drawdown": {
            "status": "blocked",
            "benchmark_symbol": signal_journal.benchmark_for_model(model),
            "interpretation": "Benchmark-relative drawdown requires eligible model and benchmark history.",
        },
        "concentration_warnings": [],
        "liquidity_flags": {"items": [], "summary": {"flagged_count": 0, "basis": "Unavailable until real model histories pass provenance checks."}},
        "correlation_clusters": [],
        "what_would_break_this_model": [{
            "driver": "Real history gate",
            "language": reason or "Eligible live or uploaded price history is required before client-grade risk language is shown.",
            "severity": "blocked",
        }],
        "required_action": "Upload real price history for every model holding before running the client-grade risk pack.",
        "missing_tickers": missing_tickers,
        "methodology": _risk_pack_methodology(),
        "disclaimer": "Analysis only. Client-grade risk packs require eligible real price history and do not guarantee outcomes.",
    }


def _client_risk_pack(
    *,
    capacity: dict[str, Any],
    income_bucket: dict[str, Any] | None,
    model: portfolio.Model,
    close: pd.Series,
    data_provenance: dict[str, Any],
    benchmark_symbol: str,
    benchmark_relative: dict[str, Any],
    concentration: dict[str, Any],
    sector_exposure: list[dict[str, Any]],
    factor_exposure: dict[str, float],
    volatility_budget: dict[str, Any],
    drawdown_stress: dict[str, Any],
    scenario_shocks: list[dict[str, Any]],
    liquidity: dict[str, Any],
    correlation_clusters: list[dict[str, Any]],
) -> dict[str, Any]:
    stress = _stress_pack_rows(scenario_shocks)
    historical = _historical_stress_replay(close)
    concentration_warnings = _concentration_warnings(concentration, sector_exposure, volatility_budget)
    liquidity_watchlist = _liquidity_watchlist(liquidity)
    clusters = _cluster_pack_rows(correlation_clusters)
    breakpoints = _break_model_language(
        model=model,
        income_bucket=income_bucket,
        stress_scenarios=stress,
        concentration_warnings=concentration_warnings,
        liquidity_watchlist=liquidity_watchlist,
        benchmark_relative=benchmark_relative,
        factor_exposure=factor_exposure,
        capacity=capacity,
    )
    risk_posture = _risk_posture(stress, concentration_warnings, liquidity_watchlist,
                                 volatility_budget, capacity)
    return {
        "available": True,
        "capacity": capacity,
        "income_bucket": income_bucket,
        "summary": {
            "model_id": model.id,
            "model_name": model.name,
            "risk_posture": risk_posture,
            "benchmark_symbol": benchmark_symbol,
            "data_mode": data_provenance.get("data_mode"),
            "source_counts": data_provenance.get("source_counts") or {},
        },
        "stress_scenarios": stress,
        "historical_stress_replay": historical,
        "benchmark_relative_drawdown": _benchmark_drawdown_pack(benchmark_relative, benchmark_symbol),
        "concentration_warnings": concentration_warnings,
        "liquidity_flags": {
            "items": liquidity_watchlist,
            "summary": liquidity.get("summary") or {"flagged_count": len(liquidity_watchlist)},
        },
        "correlation_clusters": clusters,
        "what_would_break_this_model": breakpoints,
        "methodology": _risk_pack_methodology(),
        "disclaimer": "Analysis only. Stress scenarios and breakpoints are deterministic review language, not forecasts, recommendations, or guarantees.",
    }


def _stress_pack_rows(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for scenario in sorted(scenarios, key=lambda row: float(row.get("portfolio_impact_pct") or 0.0))[:6]:
        impact = float(scenario.get("portfolio_impact_pct") or 0.0)
        rows.append({
            "scenario": scenario.get("scenario") or "Scenario shock",
            "portfolio_impact_pct": round(impact, 2),
            "severity": "high" if impact <= -8 else "medium" if impact <= -4 else "watch" if impact < 0 else "offset",
            "basis": scenario.get("basis") or "deterministic exposure shock",
            "what_it_tests": _scenario_language(str(scenario.get("scenario") or "")),
        })
    return rows


def _historical_stress_replay(close: pd.Series) -> list[dict[str, Any]]:
    clean = close.dropna()
    returns = indicators.daily_returns(clean).dropna()
    rows = []
    for window in (21, 63):
        rolling = (1 + returns).rolling(window).apply(np.prod, raw=True) - 1
        rolling = rolling.dropna()
        if rolling.empty:
            continue
        end_date = rolling.idxmin()
        end_loc = clean.index.get_indexer([end_date], method="nearest")[0]
        start_loc = max(0, end_loc - window + 1)
        start_date = clean.index[start_loc]
        value = float(rolling.loc[end_date] * 100)
        rows.append({
            "scenario": f"Worst observed {window}d window",
            "window_days": window,
            "portfolio_impact_pct": round(value, 2),
            "start_date": str(pd.Timestamp(start_date).date()),
            "end_date": str(pd.Timestamp(end_date).date()),
            "basis": "observed_model_history",
            "severity": "high" if value <= -8 else "medium" if value <= -4 else "watch" if value < 0 else "offset",
            "what_it_tests": "Replays the model's actual worst realized rolling return on eligible real history.",
        })
    return sorted(rows, key=lambda row: float(row.get("portfolio_impact_pct") or 0.0))


def _scenario_language(name: str) -> str:
    lower = name.lower()
    if "growth" in lower:
        return "Tests sensitivity to valuation compression in growth-heavy holdings."
    if "rates" in lower:
        return "Tests rate-sensitive duration, utilities, fixed-income, and growth exposure."
    if "liquidity" in lower:
        return "Tests whether lower-liquidity holdings could pressure exits during stress."
    if "defensive" in lower:
        return "Tests whether a rotation away from growth and cyclicals would hurt the model."
    if "equity" in lower:
        return "Tests broad equity beta during a market drawdown."
    return "Tests deterministic exposure sensitivity."


def _benchmark_drawdown_pack(benchmark: dict[str, Any], benchmark_symbol: str) -> dict[str, Any]:
    status = benchmark.get("status") or "unavailable"
    out = {
        "status": status,
        "benchmark_symbol": benchmark.get("benchmark_symbol") or benchmark_symbol,
        "relative_drawdown_pct": benchmark.get("relative_drawdown_pct"),
        "beta": benchmark.get("beta"),
        "correlation": benchmark.get("correlation"),
        "tracking_error_pct": benchmark.get("tracking_error_pct"),
        "overlap_days": benchmark.get("overlap_days"),
    }
    if status == "available":
        out["interpretation"] = "Shows model drawdown versus benchmark on overlapping real history."
    else:
        out["interpretation"] = benchmark.get("message") or "Benchmark-relative drawdown is unavailable until benchmark history overlaps the model."
    return out


def _concentration_warnings(
    concentration: dict[str, Any],
    sector_exposure: list[dict[str, Any]],
    volatility_budget: dict[str, Any],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    top = concentration.get("top_holding") or {}
    top_weight = float(top.get("weight_pct") or 0.0)
    if top_weight >= 20:
        warnings.append({
            "type": "single_name",
            "severity": "high" if top_weight >= 30 else "medium",
            "title": "Single-name concentration",
            "detail": f"{top.get('ticker') or 'Top holding'} is {top_weight:.1f}% of the model.",
        })
    top_5 = float(concentration.get("top_5_weight_pct") or 0.0)
    if top_5 >= 60:
        warnings.append({
            "type": "top_5",
            "severity": "medium",
            "title": "Top-five concentration",
            "detail": f"Top five holdings are {top_5:.1f}% of the model.",
        })
    if concentration.get("status") == "concentrated":
        warnings.append({
            "type": "hhi",
            "severity": "medium",
            "title": "HHI concentration",
            "detail": f"HHI is {concentration.get('hhi')}; effective holdings are {concentration.get('effective_holdings')}.",
        })
    for sector in sector_exposure:
        weight = float(sector.get("weight_pct") or 0.0)
        if weight >= 45:
            warnings.append({
                "type": "sector",
                "severity": "medium",
                "title": "Sector concentration",
                "detail": f"{sector.get('name')} is {weight:.1f}% of the model.",
            })
    if volatility_budget.get("status") == "over_budget":
        warnings.append({
            "type": "volatility_budget",
            "severity": "high",
            "title": "Volatility budget breach",
            "detail": f"Annualized volatility is {volatility_budget.get('annual_vol_pct')}% versus target {volatility_budget.get('target_vol_pct')}%.",
        })
    if not warnings:
        warnings.append({
            "type": "review",
            "severity": "info",
            "title": "No concentration breach",
            "detail": "No concentration threshold was triggered by the current model weights.",
        })
    return warnings


def _liquidity_watchlist(liquidity: dict[str, Any]) -> list[dict[str, Any]]:
    items = liquidity.get("items") if isinstance(liquidity.get("items"), list) else []
    flagged = [item for item in items if item.get("flag") != "normal"]
    watchlist = flagged if flagged else items[:3]
    return [
        {
            "ticker": item.get("ticker"),
            "weight_pct": item.get("weight_pct"),
            "liquidity_score": item.get("liquidity_score"),
            "flag": item.get("flag"),
            "estimated_adv_usd": item.get("estimated_adv_usd"),
            "observed_adv_usd": item.get("observed_adv_usd"),
            "adv_source": item.get("adv_source"),
            "adv_observation_days": item.get("adv_observation_days"),
            "language": "Observed live/uploaded dollar volume is thin; review implementation capacity outside Helios."
            if item.get("adv_source") == "observed_60d_dollar_volume" and item.get("flag") != "normal"
            else "Verify liquidity with current provider/venue data before any implementation."
            if item.get("flag") != "normal"
            else "Observed live/uploaded dollar volume supports normal liquidity review; execution capacity remains outside Helios."
            if item.get("adv_source") == "observed_60d_dollar_volume"
            else "Large-cap liquidity proxy is normal; still verify before execution outside Helios.",
        }
        for item in watchlist
    ]


def _cluster_pack_rows(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for cluster in clusters:
        pairs = cluster.get("pairs") if isinstance(cluster.get("pairs"), list) else []
        out.append({
            "name": cluster.get("name"),
            "type": cluster.get("type"),
            "average_correlation": cluster.get("average_correlation"),
            "pairs": pairs[:5],
            "language": "High-correlation clusters can reduce diversification when stress is broad."
            if cluster.get("type") == "high_correlation"
            else "Diversifier pairs may help, but correlations can rise during market stress."
            if cluster.get("type") == "diversifier"
            else "Correlation mix is moderate on available history.",
        })
    return out


def _break_model_language(
    *,
    model: portfolio.Model,
    income_bucket: dict[str, Any] | None = None,
    stress_scenarios: list[dict[str, Any]],
    concentration_warnings: list[dict[str, Any]],
    liquidity_watchlist: list[dict[str, Any]],
    benchmark_relative: dict[str, Any],
    factor_exposure: dict[str, float],
    capacity: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if income_bucket and income_bucket.get("status") == "below_thesis_floor":
        rows.append({
            "driver": "Income bucket below thesis floor",
            "severity": "high",
            "trigger": "Low-vol bucket covers fewer months than the operator's stated band",
            "evidence": (f"{income_bucket.get('months_of_coverage')} months of the stated "
                         f"${income_bucket.get('monthly_draw_usd'):,.0f}/mo draw vs a "
                         f"{income_bucket.get('thesis_band_months')} month thesis band."),
            "language": ("An extended downturn would force selling volatile holdings for income "
                         "— the exact scenario this model's thesis exists to avoid. Rebuild the "
                         "low-vol bucket toward the stated band."),
        })
    if capacity and capacity.get("status") == "capacity_constrained":
        worst_days = capacity.get("max_days_to_liquidate_10pct")
        rows.append({
            "driver": "Implementation capacity",
            "severity": "high",
            "trigger": "A holding needs >20 trading days to exit at 10% ADV participation",
            "evidence": (f"At ${capacity.get('aum_usd'):,.0f} AUM the slowest holding needs "
                         f"{worst_days} days to liquidate at a 10% participation cap."),
            "language": ("This model does not exit cleanly at the stated AUM — the slowest "
                         f"position needs {worst_days} trading days at a 10% participation cap; "
                         "size down or diversify the illiquid sleeve."),
        })
    growth = float(factor_exposure.get("growth") or 0.0)
    if growth >= 30:
        rows.append({
            "driver": "Growth factor unwind",
            "severity": "high" if growth >= 50 else "medium",
            "trigger": "Growth factor exposure >= 30%",
            "evidence": f"Current growth exposure is {growth:.1f}%.",
            "language": f"A valuation reset in growth assets would pressure this model because growth exposure is {growth:.1f}%.",
        })
    if stress_scenarios:
        worst = stress_scenarios[0]
        rows.append({
            "driver": str(worst.get("scenario") or "Stress scenario"),
            "severity": str(worst.get("severity") or "watch"),
            "trigger": "Largest client risk-pack stress scenario",
            "evidence": f"{worst.get('scenario')} impact is {worst.get('portfolio_impact_pct')}%.",
            "language": f"The largest modeled stress is {worst.get('scenario')} at {worst.get('portfolio_impact_pct')}%.",
        })
    top_warning = next((warning for warning in concentration_warnings if warning.get("type") == "single_name"), None)
    if top_warning:
        rows.append({
            "driver": "Top holding failure",
            "severity": str(top_warning.get("severity") or "medium"),
            "trigger": "Single-name concentration warning",
            "evidence": str(top_warning.get("detail") or "A single holding drives concentrated model risk."),
            "language": str(top_warning.get("detail") or "A single holding drives concentrated model risk."),
        })
    if benchmark_relative.get("status") == "available":
        rel_dd = benchmark_relative.get("relative_drawdown_pct")
        beta = benchmark_relative.get("beta")
        rows.append({
            "driver": "Benchmark-relative drawdown",
            "severity": "medium",
            "trigger": "Observed benchmark-relative drawdown and beta",
            "evidence": f"Relative drawdown {rel_dd}% and beta {beta} versus {benchmark_relative.get('benchmark_symbol')}.",
            "language": f"Relative drawdown versus {benchmark_relative.get('benchmark_symbol')} is {rel_dd}% with beta {beta}; watch for persistent benchmark divergence.",
        })
    else:
        rows.append({
            "driver": "Benchmark evidence gap",
            "severity": "medium",
            "trigger": "Benchmark history unavailable or insufficient overlap",
            "evidence": str(benchmark_relative.get("message") or "Benchmark overlap is unavailable."),
            "language": str(benchmark_relative.get("message") or "Benchmark overlap is unavailable, so relative drawdown should be verified before client use."),
        })
    flagged = [item for item in liquidity_watchlist if item.get("flag") != "normal"]
    if flagged:
        tickers = ", ".join(str(item.get("ticker")) for item in flagged[:4])
        rows.append({
            "driver": "Liquidity stress",
            "severity": "medium",
            "trigger": "Liquidity watchlist contains non-normal flags",
            "evidence": f"Flagged liquidity tickers: {tickers}.",
            "language": f"Liquidity proxies require review for {tickers}; verify with current provider data before implementation.",
        })
    if not rows:
        rows.append({
            "driver": "Risk review",
            "severity": "info",
            "trigger": "No material deterministic break trigger",
            "evidence": "Current pack did not trigger concentration, liquidity, benchmark, or factor breakpoints.",
            "language": f"{model.name} has no material deterministic break trigger, but advisor review is still required.",
        })
    return rows


def _risk_posture(
    stress_scenarios: list[dict[str, Any]],
    concentration_warnings: list[dict[str, Any]],
    liquidity_watchlist: list[dict[str, Any]],
    volatility_budget: dict[str, Any],
    capacity: dict[str, Any] | None = None,
) -> str:
    if volatility_budget.get("status") == "over_budget":
        return "elevated"
    if any(row.get("severity") == "high" for row in stress_scenarios + concentration_warnings):
        return "elevated"
    if capacity and capacity.get("status") == "capacity_constrained":
        return "watch"
    if any(row.get("flag") != "normal" for row in liquidity_watchlist):
        return "watch"
    return "review_ready"


def _risk_pack_methodology() -> dict[str, bool]:
    return {
        "analysis_only": True,
        "uses_live_or_uploaded_prices_only": True,
        "no_trade_execution": True,
        "scenario_stress_not_forecast": True,
        "uses_observed_volume_when_available": True,
        "includes_historical_stress_replay": True,
        "does_not_change_analytics": True,
    }


def _holding_returns(model: portfolio.Model) -> pd.DataFrame:
    frames = {}
    for holding in model.holdings:
        inst = data.get(holding.ticker)
        if inst is None or not provenance.is_real_source(inst.source):
            continue
        close = inst.df["close"].dropna() if "close" in inst.df else pd.Series(dtype=float)
        if len(close) >= 3:
            frames[holding.ticker] = close.pct_change()
    return pd.DataFrame(frames).dropna(how="all")


def _weighted_exposure(model: portfolio.Model, *, kind: str) -> list[dict[str, Any]]:
    weights: dict[str, float] = {}
    members: dict[str, list[str]] = {}
    for holding in model.holdings:
        sector, theme = _classify(holding.ticker)
        label = sector if kind == "sector" else theme
        weights[label] = weights.get(label, 0.0) + float(holding.weight)
        members.setdefault(label, []).append(holding.ticker)
    return [
        {
            "name": name,
            "weight_pct": round(weight * 100, 2),
            "tickers": members.get(name, []),
        }
        for name, weight in sorted(weights.items(), key=lambda item: item[1], reverse=True)
    ]


def _factor_exposure(model: portfolio.Model) -> dict[str, float]:
    exposures: dict[str, float] = {}
    for holding in model.holdings:
        sector, _ = _classify(holding.ticker)
        for factor, score in _FACTOR_MAP.get(sector, {"unclassified": 1.0}).items():
            exposures[factor] = exposures.get(factor, 0.0) + float(holding.weight) * float(score)
    return {key: round(value * 100, 2) for key, value in sorted(exposures.items())}


def _concentration(model: portfolio.Model, ps: portfolio.PortfolioSeries) -> dict[str, Any]:
    top = max(model.holdings, key=lambda holding: holding.weight, default=None)
    high_threshold = mandate.hhi_thresholds(model.mandate_key)[1]
    return {
        "hhi": round(ps.hhi, 4),
        "effective_holdings": round(ps.n_eff, 2),
        "status": "concentrated" if ps.hhi >= high_threshold else "diversified",
        "top_holding": {
            "ticker": top.ticker if top else "",
            "weight_pct": round(float(top.weight) * 100, 2) if top else 0.0,
        },
        "top_5_weight_pct": round(sum(float(h.weight) for h in sorted(model.holdings, key=lambda h: h.weight, reverse=True)[:5]) * 100, 2),
    }


def _volatility_budget(metrics: dict[str, Any], model: portfolio.Model) -> dict[str, Any]:
    target = float(mandate.get(model.mandate_key)["target_vol_pct"])
    actual = float(metrics.get("annual_vol_pct") or 0.0)
    return {
        "annual_vol_pct": round(actual, 2),
        "target_vol_pct": round(target, 2),
        "gap_pct": round(actual - target, 2),
        "status": "over_budget" if actual > target else "within_budget",
    }


def _volatility_contribution(ps: portfolio.PortfolioSeries) -> list[dict[str, Any]]:
    rows = []
    for holding in ps.holdings:
        if holding.get("excluded"):
            continue
        rows.append({
            "ticker": holding["ticker"],
            "weight_pct": round(float(holding["weight"]) * 100, 2),
            "mrc_pct": round(float(holding.get("mrc_pct") or 0.0), 2),
            "source": holding.get("source") or "",
        })
    return sorted(rows, key=lambda row: row["mrc_pct"], reverse=True)


def _correlation_clusters(returns: pd.DataFrame) -> list[dict[str, Any]]:
    if returns.empty or len(returns.columns) < 2:
        return []
    corr = returns.corr(min_periods=20)
    high_pairs = []
    diversifiers = []
    for left, right in combinations(corr.columns, 2):
        value = corr.loc[left, right]
        if not np.isfinite(value):
            continue
        item = {"tickers": [left, right], "correlation": round(float(value), 3)}
        if value >= 0.75:
            high_pairs.append(item)
        elif value <= 0.2:
            diversifiers.append(item)
    out = []
    if high_pairs:
        out.append({"name": "High correlation pairs", "type": "high_correlation", "pairs": high_pairs[:8]})
    if diversifiers:
        out.append({"name": "Diversifiers", "type": "diversifier", "pairs": diversifiers[:8]})
    if not out:
        avg = corr.where(~np.eye(len(corr), dtype=bool)).stack().mean()
        out.append({"name": "Moderate correlation mix", "type": "moderate", "average_correlation": round(float(avg), 3) if np.isfinite(avg) else None, "pairs": []})
    return out


def _drawdown_stress(close: pd.Series) -> dict[str, Any]:
    dd = indicators.max_drawdown(close) * 100
    returns = indicators.daily_returns(close).dropna()
    rolling_21 = (1 + returns).rolling(21).apply(np.prod, raw=True) - 1
    rolling_63 = (1 + returns).rolling(63).apply(np.prod, raw=True) - 1
    return {
        "max_drawdown_pct": round(float(dd), 2),
        "worst_21d_pct": round(float(rolling_21.min() * 100), 2) if rolling_21.notna().any() else None,
        "worst_63d_pct": round(float(rolling_63.min() * 100), 2) if rolling_63.notna().any() else None,
        "current_drawdown_pct": round(float((close.iloc[-1] / close.cummax().iloc[-1] - 1) * 100), 2) if len(close) else 0.0,
    }


def _scenario_shocks(model: portfolio.Model, factors: dict[str, float]) -> list[dict[str, Any]]:
    weights = {holding.ticker: float(holding.weight) for holding in model.holdings}
    sector = {row["name"]: row["weight_pct"] / 100 for row in _weighted_exposure(model, kind="sector")}
    scenarios = [
        ("Equity shock -10%", {"Technology": -0.10, "Communication Services": -0.10, "Consumer Discretionary": -0.10, "Broad Market": -0.10, "Financials": -0.08, "Industrials": -0.08, "Healthcare": -0.04, "Utilities": -0.03, "Fixed Income": 0.01, "Cash": 0.0, "Commodities": 0.02, "Crypto": -0.20}),
        ("Rates shock +100 bps", {"Fixed Income": -0.06, "Utilities": -0.04, "Technology": -0.03, "Broad Market": -0.025, "Cash": 0.01, "Commodities": 0.01}),
        ("Growth multiple compression", {"Technology": -0.12, "Communication Services": -0.09, "Consumer Discretionary": -0.08, "Crypto": -0.15, "Healthcare": -0.03}),
        ("Defensive rotation", {"Utilities": 0.04, "Healthcare": 0.03, "Fixed Income": 0.02, "Cash": 0.0, "Technology": -0.04, "Consumer Discretionary": -0.04, "Financials": -0.02}),
        # Observed-first, like the flags surface: this scenario used to score
        # from the static ADV proxy even when 60d observed dollar volume
        # existed — shocking tickers by stale assumption (review finding).
        # Broadly missed earnings -> multiple compression across equities:
        # deterministic exposure shock (the earnings-breadth monitor supplies
        # the OBSERVATION of whether the season is actually deteriorating).
        ("Broad earnings de-rating", {"Technology": -0.08, "Communication Services": -0.07,
                                      "Consumer Discretionary": -0.07, "Broad Market": -0.06,
                                      "Financials": -0.05, "Industrials": -0.05,
                                      "Healthcare": -0.04, "Utilities": -0.02, "Energy": -0.04,
                                      "Crypto": -0.10, "Fixed Income": 0.01, "Cash": 0.0}),
        ("Liquidity stress", {ticker: (-0.08 if _effective_liquidity_score(ticker) < 50 else -0.02)
                              for ticker in weights}),
    ]
    out = []
    for name, shocks in scenarios:
        impact = 0.0
        if name == "Liquidity stress":
            impact = sum(weights.get(ticker, 0.0) * shocks.get(ticker, 0.0) for ticker in weights)
        else:
            impact = sum(sector.get(sec, 0.0) * shocks.get(sec, 0.0) for sec in sector)
        out.append({
            "scenario": name,
            "portfolio_impact_pct": round(impact * 100, 2),
            "basis": "deterministic exposure shock",
        })
    factor_growth = (factors.get("growth", 0.0) or 0.0) / 100
    if factor_growth > 0.4:
        out.append({"scenario": "High growth-factor unwind", "portfolio_impact_pct": round(-12.0 * factor_growth, 2), "basis": "growth factor exposure"})
    return out


def _effective_liquidity_score(ticker: str) -> int:
    """Observed 60-day dollar volume first; the dated static proxy only as a
    fallback — the same precedence the liquidity flags surface uses."""
    observed = _observed_liquidity(ticker)
    return int(observed.get("liquidity_score") or _liquidity_score(ticker))


# Days-to-liquidate participation caps: 10% of ADV is a conservative
# institutional exit pace; 20% is aggressive.
_CAPACITY_CAPS = (0.10, 0.20)
_CAPACITY_WATCH_DAYS = 5.0
_CAPACITY_CONSTRAINED_DAYS = 20.0


def _daily_sigma(ticker: str) -> float | None:
    """Recent daily return volatility from REAL history (impact scaling)."""
    inst = data.get(ticker)
    if inst is None or not provenance.is_real_source(inst.source) or "close" not in inst.df:
        return None
    rets = inst.df["close"].dropna().pct_change().tail(120).dropna()
    if len(rets) < 30:
        return None
    return float(rets.std())


def _capacity_analysis(model: portfolio.Model, liquidity_items: list[dict[str, Any]],
                       aum_usd: float | None) -> dict[str, Any]:
    """Dollar-sized implementation capacity: can this book actually be traded?

    BUY/HOLD/SELL labels never became governed dollar exposure, and liquidity
    analysis had no AUM or trade size — it could not say whether a strategy
    works at $1B (review finding). Participation, days-to-liquidate at 10%/20%
    ADV caps, and a square-root impact estimate per holding; every number is
    an order-of-magnitude ESTIMATE, never execution advice.
    """
    if aum_usd is None or aum_usd <= 0:
        return {
            "status": "aum_not_set",
            "note": ("Pass ?aum=<dollars> (or set HELIOS_MODEL_AUM_USD) to size positions "
                     "against real liquidity — Helios never assumes an AUM silently."),
        }
    by_ticker = {str(item.get("ticker")): item for item in liquidity_items}
    rows: list[dict[str, Any]] = []
    for holding in model.holdings:
        item = by_ticker.get(holding.ticker, {})
        position_usd = float(holding.weight) * float(aum_usd)
        adv = item.get("estimated_adv_usd")
        row: dict[str, Any] = {
            "ticker": holding.ticker,
            "weight_pct": round(float(holding.weight) * 100, 2),
            "position_usd": round(position_usd, 0),
            "adv_source": item.get("adv_source") or "unavailable",
            "proxy_as_of": item.get("proxy_as_of"),
        }
        if not adv or float(adv) <= 0:
            row["status"] = "adv_unavailable"
            rows.append(row)
            continue
        ratio = position_usd / float(adv)
        row.update({
            "status": "ok",
            "estimated_adv_usd": float(adv),
            "one_day_participation_pct": round(ratio * 100, 2),
            "days_to_liquidate_10pct": round(ratio / _CAPACITY_CAPS[0], 2),
            "days_to_liquidate_20pct": round(ratio / _CAPACITY_CAPS[1], 2),
        })
        sigma = _daily_sigma(holding.ticker)
        if sigma is not None:
            # Square-root market-impact heuristic: sigma_daily * sqrt(size/ADV).
            row["impact_estimate_bps"] = round(10000 * sigma * (ratio ** 0.5), 1)
        rows.append(row)
    sized = [r for r in rows if r["status"] == "ok"]
    unsized = [r for r in rows if r["status"] != "ok"]
    if not sized:
        return {"status": "adv_unavailable", "aum_usd": float(aum_usd), "holdings": rows,
                "note": "No holding has an ADV estimate; capacity cannot be sized."}
    days10 = [r["days_to_liquidate_10pct"] for r in sized]
    sized_w = sum(r["weight_pct"] for r in sized) or 1.0
    weighted_days = sum(r["days_to_liquidate_10pct"] * r["weight_pct"] for r in sized) / sized_w
    over_watch = [r["ticker"] for r in sized if r["days_to_liquidate_10pct"] > _CAPACITY_WATCH_DAYS]
    over_hard = [r["ticker"] for r in sized if r["days_to_liquidate_10pct"] > _CAPACITY_CONSTRAINED_DAYS]
    status = ("capacity_constrained" if over_hard
              else "watch" if over_watch else "within_capacity")
    return {
        "status": status,
        "aum_usd": float(aum_usd),
        "holdings": rows,
        "max_days_to_liquidate_10pct": round(max(days10), 2),
        "weighted_days_to_liquidate_10pct": round(weighted_days, 2),
        "holdings_over_5d": over_watch,
        "holdings_over_20d": over_hard,
        "unsized_count": len(unsized),
        "proxy_based_count": sum(1 for r in sized if r["adv_source"] == "static_public_proxy"),
        "basis": ("Position $ vs average daily dollar volume; days-to-liquidate at 10%/20% "
                  "participation caps; square-root impact scaled by observed daily vol. "
                  "Order-of-magnitude estimates from available data — not execution advice."),
    }


_LOW_VOL_ANNUAL = 0.08   # <8% annualized: cash/bill/short-duration territory


def _income_bucket(model: portfolio.Model, aum_usd: float | None) -> dict[str, Any] | None:
    """The operator's income-model thesis, operationalized: how many months of
    the stated draw does the low-volatility bucket actually cover?

    Thesis: extended downturns are ridden out by drawing from 12-24 months of
    income needs held in cash / money market / very-low-vol holdings. This
    check measures the bucket against the STATED draw — it only exists when
    the model carries thesis_params.income_monthly_draw_usd (absence of a
    thesis adds nothing), and it needs AUM to convert weights to dollars.
    """
    params = getattr(model, "thesis_params", None) or {}
    draw = params.get("income_monthly_draw_usd")
    if not draw:
        return None
    min_months = float(params.get("income_bucket_min_months") or 12.0)
    max_months = float(params.get("income_bucket_max_months") or 24.0)
    if aum_usd is None or aum_usd <= 0:
        return {"status": "aum_not_set",
                "note": "Set the model AUM to size the income bucket against the stated draw."}
    bucket_weight = 0.0
    rows: list[dict[str, Any]] = []
    unclassified: list[str] = []
    for holding in model.holdings:
        sector = _SECTOR_MAP.get(holding.ticker, (None, None))[0]
        vol = None
        inst = data.get(holding.ticker)
        if inst is not None and provenance.is_real_source(inst.source) and "close" in inst.df:
            rets = inst.df["close"].dropna().pct_change().tail(180).dropna()
            if len(rets) >= 40:
                vol = float(rets.std() * np.sqrt(252))
        low_vol = (sector in {"Cash", "Fixed Income"}
                   or (vol is not None and vol < _LOW_VOL_ANNUAL))
        if sector is None and vol is None:
            unclassified.append(holding.ticker)
            continue
        if low_vol:
            bucket_weight += float(holding.weight)
            rows.append({"ticker": holding.ticker,
                         "weight_pct": round(float(holding.weight) * 100, 2),
                         "observed_vol_pct": round(vol * 100, 1) if vol is not None else None,
                         "sector": sector or "unclassified"})
    bucket_usd = bucket_weight * float(aum_usd)
    months = bucket_usd / float(draw) if draw else 0.0
    status = ("below_thesis_floor" if months < min_months
              else "above_thesis_band" if months > max_months else "within_thesis_band")
    return {
        "status": status,
        "months_of_coverage": round(months, 1),
        "thesis_band_months": [min_months, max_months],
        "monthly_draw_usd": float(draw),
        "bucket_usd": round(bucket_usd, 0),
        "bucket_weight_pct": round(bucket_weight * 100, 2),
        "bucket_holdings": rows,
        "unclassified": unclassified,
        "basis": ("Low-vol bucket = Cash/Fixed Income taxonomy or observed annualized "
                  f"vol < {_LOW_VOL_ANNUAL:.0%} on real history; coverage = bucket $ / "
                  "stated monthly draw. Measures the model against the OPERATOR'S "
                  "thesis, not a generic standard."),
    }


def _liquidity_flags(model: portfolio.Model) -> dict[str, Any]:
    items = []
    for holding in model.holdings:
        observed = _observed_liquidity(holding.ticker)
        score = int(observed.get("liquidity_score") or _liquidity_score(holding.ticker))
        flag = "unknown_liquidity" if score < 45 else "watch" if score < 65 else "normal"
        fallback_adv = _LIQUIDITY_AVG_DAILY_DOLLAR_VOLUME.get(holding.ticker)
        adv_source = observed.get("adv_source") or ("static_public_proxy" if fallback_adv is not None else "unavailable")
        items.append({
            "ticker": holding.ticker,
            "weight_pct": round(float(holding.weight) * 100, 2),
            "liquidity_score": score,
            "flag": flag,
            "estimated_adv_usd": observed.get("observed_adv_usd") or fallback_adv,
            "observed_adv_usd": observed.get("observed_adv_usd"),
            "adv_source": adv_source,
            "proxy_as_of": _ADV_PROXY_AS_OF if adv_source == "static_public_proxy" else None,
            "adv_observation_days": observed.get("adv_observation_days") or 0,
        })
    flagged = [item for item in items if item["flag"] != "normal"]
    observed_count = sum(1 for item in items if item.get("adv_source") == "observed_60d_dollar_volume")
    proxy_count = sum(1 for item in items if item.get("adv_source") == "static_public_proxy")
    return {
        "items": sorted(items, key=lambda item: (item["flag"] == "normal", -item["weight_pct"])),
        "summary": {
            "flagged_count": len(flagged),
            "observed_count": observed_count,
            "proxy_count": proxy_count,
            "basis": "observed live/uploaded dollar volume where available; static public proxy only when volume is absent",
        },
    }


def _observed_liquidity(ticker: str) -> dict[str, Any]:
    inst = data.get(ticker)
    if inst is None or not provenance.is_real_source(inst.source):
        return {}
    if "close" not in inst.df or "volume" not in inst.df:
        return {}
    frame = inst.df[["close", "volume"]].dropna().tail(60)
    if len(frame) < 20:
        return {}
    dollar_volume = pd.to_numeric(frame["close"], errors="coerce") * pd.to_numeric(frame["volume"], errors="coerce")
    dollar_volume = dollar_volume.replace([np.inf, -np.inf], np.nan).dropna()
    if len(dollar_volume) < 20:
        return {}
    observed_adv = float(dollar_volume.mean())
    return {
        "observed_adv_usd": round(observed_adv, 2),
        "adv_observation_days": int(len(dollar_volume)),
        "adv_source": "observed_60d_dollar_volume",
        "liquidity_score": _liquidity_score_from_adv(observed_adv),
    }


def _benchmark_relative(portfolio_returns: pd.Series, benchmark_symbol: str) -> dict[str, Any]:
    inst = data.get(benchmark_symbol)
    if inst is None or not provenance.is_real_source(inst.source) or "close" not in inst.df:
        return {
            "status": "benchmark_unavailable",
            "benchmark_symbol": benchmark_symbol,
            "message": f"Benchmark {benchmark_symbol} needs live or uploaded history for relative risk.",
        }
    benchmark_returns = inst.df["close"].dropna().pct_change()
    aligned = pd.concat({"portfolio": portfolio_returns, "benchmark": benchmark_returns}, axis=1, sort=False).dropna()
    if len(aligned) < 30:
        return {
            "status": "insufficient_overlap",
            "benchmark_symbol": benchmark_symbol,
            "message": f"Benchmark {benchmark_symbol} has insufficient overlap with the model history.",
        }
    cov = aligned.cov()
    bench_var = float(cov.loc["benchmark", "benchmark"])
    beta = float(cov.loc["portfolio", "benchmark"] / bench_var) if bench_var > 1e-12 else 0.0
    active = aligned["portfolio"] - aligned["benchmark"]
    return {
        "status": "available",
        "benchmark_symbol": benchmark_symbol,
        "beta": round(beta, 3),
        "correlation": round(float(aligned["portfolio"].corr(aligned["benchmark"])), 3),
        "active_vol_pct": round(float(active.std() * np.sqrt(252) * 100), 2),
        "tracking_error_pct": round(float(active.std() * np.sqrt(252) * 100), 2),
        "relative_drawdown_pct": round(float(indicators.max_drawdown((1 + active).cumprod()) * 100), 2),
        "overlap_days": int(len(aligned)),
        "message": "",
    }


def _classify(ticker: str) -> tuple[str, str]:
    return _SECTOR_MAP.get(str(ticker).upper(), ("Unclassified", "unclassified"))


def _liquidity_score(ticker: str) -> int:
    adv = _LIQUIDITY_AVG_DAILY_DOLLAR_VOLUME.get(str(ticker).upper())
    if adv is None:
        return 40
    return _liquidity_score_from_adv(float(adv))


def _liquidity_score_from_adv(adv: float) -> int:
    if adv >= 5_000_000_000:
        return 95
    if adv >= 1_000_000_000:
        return 85
    if adv >= 250_000_000:
        return 70
    return 55

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

from . import data, indicators, mandate, portfolio, provenance, signal_journal

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

_FACTOR_MAP = {
    "Technology": {"growth": 0.75, "quality": 0.35},
    "Communication Services": {"growth": 0.55, "quality": 0.25},
    "Consumer Discretionary": {"growth": 0.55, "cyclical": 0.35},
    "Industrials": {"cyclical": 0.35, "defensive": 0.20},
    "Utilities": {"defensive": 0.65, "income": 0.25},
    "Healthcare": {"defensive": 0.45, "quality": 0.35, "growth": 0.15},
    "Financials": {"value": 0.45, "cyclical": 0.35},
    "Energy": {"value": 0.35, "cyclical": 0.35, "real_assets": 0.25},
    "Commodities": {"real_assets": 0.75, "defensive": 0.15},
    "Fixed Income": {"income": 0.65, "defensive": 0.45},
    "Cash": {"defensive": 0.80, "income": 0.35},
    "Broad Market": {"quality": 0.25, "growth": 0.25, "value": 0.20, "defensive": 0.15},
    "Crypto": {"growth": 0.55, "cyclical": 0.55, "real_assets": 0.20},
}

_LIQUIDITY_AVG_DAILY_DOLLAR_VOLUME = {
    "AAPL": 7_000_000_000,
    "MSFT": 6_000_000_000,
    "NVDA": 12_000_000_000,
    "AMZN": 5_000_000_000,
    "GOOGL": 3_000_000_000,
    "META": 4_000_000_000,
    "SPY": 40_000_000_000,
    "QQQ": 20_000_000_000,
    "IWM": 7_000_000_000,
    "BND": 1_000_000_000,
    "TLT": 2_000_000_000,
    "GLD": 1_500_000_000,
    "SGOV": 700_000_000,
    "BTC-USD": 20_000_000_000,
}


def analyze_model_risk(model: portfolio.Model) -> dict[str, Any]:
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
        "sector_exposure": _weighted_exposure(model, kind="sector"),
        "theme_exposure": _weighted_exposure(model, kind="theme"),
        "factor_exposure": factor_exposure,
        "single_name_concentration": _concentration(model, ps),
        "volatility_budget": vol_budget,
        "volatility_contribution": _volatility_contribution(ps),
        "correlation_clusters": _correlation_clusters(returns),
        "drawdown_stress": _drawdown_stress(ps.close),
        "scenario_shocks": _scenario_shocks(model, factor_exposure),
        "liquidity_flags": liquidity,
        "benchmark_relative": benchmark,
        "provenance": ps.provenance,
        "warnings": _dedupe(warnings),
        "methodology": {
            "analysis_only": True,
            "uses_live_or_uploaded_prices_only": True,
            "no_trade_execution": True,
            "classification_source": "static Helios public ticker taxonomy with unclassified fallback",
            "scenario_basis": "deterministic factor/asset-class shocks, not forecasts",
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
        "warnings": _dedupe(list(warnings or []) + [reason]),
        "methodology": {
            "analysis_only": True,
            "uses_live_or_uploaded_prices_only": True,
            "no_trade_execution": True,
        },
        "disclaimer": "Analysis only. Risk analytics require eligible real price history.",
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
        ("Liquidity stress", {ticker: (-0.08 if _liquidity_score(ticker) < 50 else -0.02) for ticker in weights}),
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


def _liquidity_flags(model: portfolio.Model) -> dict[str, Any]:
    items = []
    for holding in model.holdings:
        score = _liquidity_score(holding.ticker)
        flag = "unknown_liquidity" if score < 45 else "watch" if score < 65 else "normal"
        items.append({
            "ticker": holding.ticker,
            "weight_pct": round(float(holding.weight) * 100, 2),
            "liquidity_score": score,
            "flag": flag,
            "estimated_adv_usd": _LIQUIDITY_AVG_DAILY_DOLLAR_VOLUME.get(holding.ticker),
        })
    flagged = [item for item in items if item["flag"] != "normal"]
    return {
        "items": sorted(items, key=lambda item: (item["flag"] == "normal", -item["weight_pct"])),
        "summary": {
            "flagged_count": len(flagged),
            "basis": "static public-liquidity proxy; verify with current venue/provider data before trading",
        },
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
    if adv >= 5_000_000_000:
        return 95
    if adv >= 1_000_000_000:
        return 85
    if adv >= 250_000_000:
        return 70
    return 55


def _dedupe(items: list[str]) -> list[str]:
    out = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out

"""Market-regime classification for the Helios Command Center.

The regime view is intentionally price-only. It uses the broad-market proxy
already present in the local data store, preferring SPY when available, and it
does not invent macro, rates, earnings, or sentiment inputs.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import provenance


def classify_regime(close: pd.Series, symbol: str = "SPY", source: str = "unknown") -> dict:
    """Classify a price series as risk-on, neutral, or risk-off.

    Returns a stable, explainable 0-100 score plus the concrete drivers used in
    the classification. Positive drivers lift the score; negative drivers lower
    it. The function only uses information available at the end of the supplied
    series.
    """
    px = close.dropna().astype(float)
    if len(px) < 60:
        return {
            "status": "ok",
            "label": "neutral",
            "score": 50,
            "symbol": symbol,
            "source": source,
            "drivers": [],
            "summary": "Not enough benchmark history to classify the regime with confidence.",
            "warnings": ["Need at least 60 sessions of benchmark history for regime classification."],
        }

    last = float(px.iloc[-1])
    sma50 = float(px.rolling(50).mean().iloc[-1]) if len(px) >= 50 else np.nan
    sma200 = float(px.rolling(200).mean().iloc[-1]) if len(px) >= 200 else np.nan
    ret21 = _return_pct(px, 21)
    ret63 = _return_pct(px, 63)
    returns = px.pct_change().dropna()
    vol = float(returns.tail(63).std() * np.sqrt(252) * 100) if len(returns) else 0.0
    drawdown = float((last / px.cummax().iloc[-1] - 1.0) * 100)

    score = 50.0
    drivers: list[dict] = []

    def add(name: str, value: str, impact: float, detail: str) -> None:
        nonlocal score
        score += impact
        drivers.append({
            "name": name,
            "value": value,
            "impact": round(impact, 1),
            "detail": detail,
        })

    if np.isfinite(sma200):
        above_200 = last >= sma200
        add("Long-term trend", f"{last / sma200 - 1.0:+.1%} vs SMA200",
            16 if above_200 else -18,
            "Price is above the 200-day trend line." if above_200 else "Price is below the 200-day trend line.")
    else:
        add("Long-term trend", "SMA200 unavailable", -4, "Less than 200 sessions limits regime confidence.")

    if np.isfinite(sma50) and np.isfinite(sma200):
        cross_ok = sma50 >= sma200
        add("Intermediate trend", f"{sma50 / sma200 - 1.0:+.1%} SMA50/SMA200",
            14 if cross_ok else -16,
            "SMA50 is above SMA200." if cross_ok else "SMA50 is below SMA200.")

    add("63-day return", f"{ret63:+.1f}%", 11 if ret63 >= 0 else -13,
        "Three-month price momentum is positive." if ret63 >= 0 else "Three-month price momentum is negative.")
    add("21-day return", f"{ret21:+.1f}%", 7 if ret21 >= 0 else -8,
        "One-month price momentum supports risk appetite." if ret21 >= 0 else "One-month price momentum is defensive.")

    if vol <= 18:
        add("Realized volatility", f"{vol:.1f}%", 6, "Recent volatility is contained.")
    elif vol >= 30:
        add("Realized volatility", f"{vol:.1f}%", -9, "Recent volatility is elevated.")
    else:
        add("Realized volatility", f"{vol:.1f}%", 0, "Recent volatility is in a middle band.")

    if drawdown >= -5:
        add("Drawdown", f"{drawdown:.1f}%", 6, "Benchmark is close to its recent high.")
    elif drawdown <= -15:
        add("Drawdown", f"{drawdown:.1f}%", -11, "Benchmark drawdown is large enough to demand caution.")
    else:
        add("Drawdown", f"{drawdown:.1f}%", -2, "Benchmark is below its high but not in stress territory.")

    final_score = int(round(float(np.clip(score, 0, 100))))
    label = "risk-on" if final_score >= 65 else "risk-off" if final_score <= 35 else "neutral"
    return {
        "status": "ok",
        "label": label,
        "score": final_score,
        "symbol": symbol,
        "source": source,
        "drivers": drivers,
        "summary": _summary(label, symbol, final_score, drivers),
        "warnings": [],
    }


def _unavailable_regime() -> dict:
    """No benchmark data means NO regime number — a hard-coded neutral 50/100
    rendered as numeric state with zero data loaded (review finding)."""
    return {
        "status": "unavailable",
        "label": "unavailable",
        "score": None,
        "symbol": None,
        "source": "none",
        "drivers": [],
        "summary": "No benchmark price history loaded — regime unavailable.",
        "warnings": ["Fetch live or upload benchmark history (SPY preferred) "
                     "to classify the market regime."],
    }


def market_regime(instruments) -> dict:
    """Choose the broad-market proxy and classify its current regime."""
    insts = list(instruments)
    if not insts:
        return _unavailable_regime()

    proxy = next((inst for inst in insts if inst.symbol.upper() == "SPY"), None)
    warnings: list[str] = []
    if proxy is None:
        proxy = max(insts, key=lambda inst: len(inst.df["close"].dropna()))
        warnings.append(f"SPY was not available; using {proxy.symbol} as the broad-market proxy.")
    if proxy.df.get("close") is None or proxy.df["close"].dropna().empty:
        return _unavailable_regime()
    if not provenance.is_real_source(proxy.source):
        unavailable = _unavailable_regime()
        unavailable["symbol"] = proxy.symbol
        unavailable["source"] = proxy.source
        unavailable["warnings"] = warnings + [
            f"Regime proxy {proxy.symbol} is not eligible market evidence. "
            "Load approved live or uploaded benchmark history."
        ]
        return unavailable

    result = classify_regime(proxy.df["close"], symbol=proxy.symbol, source=proxy.source)
    result["warnings"] = warnings + result.get("warnings", [])
    return result


def _return_pct(close: pd.Series, periods: int) -> float:
    if len(close) <= periods:
        return 0.0
    start = float(close.iloc[-periods - 1])
    if start == 0:
        return 0.0
    return float((float(close.iloc[-1]) / start - 1.0) * 100)


def _summary(label: str, symbol: str, score: int, drivers: list[dict]) -> str:
    ordered = sorted(drivers, key=lambda d: abs(d["impact"]), reverse=True)
    lead = ordered[0]["detail"] if ordered else "Insufficient driver detail."
    if label == "risk-on":
        tone = "Risk appetite is constructive"
    elif label == "risk-off":
        tone = "Risk appetite is defensive"
    else:
        tone = "Risk appetite is mixed"
    return f"{tone} for {symbol} (score {score}/100). {lead}"

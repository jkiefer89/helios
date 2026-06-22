"""Composite trade-signal generation.

Blends four transparent components into one score in [-1, 1]:
    trend      - price vs SMA50/200, MACD histogram sign
    momentum   - RSI position (mean-reverting at extremes)
    forecast   - expected return over the horizon (Ridge model, capped)
    sentiment  - aggregate news-headline sentiment

A volatility penalty shrinks conviction when realized vol is high. The score
maps to BUY / HOLD / SELL with a conviction percentage and a full per-component
breakdown so the recommendation is fully explainable -- analysis only, never
order execution.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind

WEIGHTS = {"trend": 0.30, "momentum": 0.20, "forecast": 0.30, "sentiment": 0.20}


def _trend_score(f: pd.DataFrame, i: int) -> float:
    row = f.iloc[i]
    score = 0.0
    if np.isfinite(row["sma50"]) and np.isfinite(row["sma200"]):
        score += 0.5 if row["sma50"] > row["sma200"] else -0.5  # golden/death cross
    if np.isfinite(row["close"]) and np.isfinite(row["sma50"]):
        score += 0.25 if row["close"] > row["sma50"] else -0.25
    if np.isfinite(row["macd_hist"]):
        score += 0.25 * np.sign(row["macd_hist"])
    return float(np.clip(score, -1, 1))


def _momentum_score(f: pd.DataFrame, i: int) -> float:
    rsi = f.iloc[i]["rsi"]
    if not np.isfinite(rsi):
        return 0.0
    # Oversold -> bullish, overbought -> bearish, with a mild pro-trend tilt mid-range.
    if rsi < 30:
        return float((30 - rsi) / 30)
    if rsi > 70:
        return float(-(rsi - 70) / 30)
    return float((rsi - 50) / 50 * 0.4)


def _forecast_score(expected_return_pct: float) -> float:
    # ~+/-5% over the horizon saturates the component.
    return float(np.clip(expected_return_pct / 5.0, -1, 1))


def _sentiment_score(agg: float) -> float:
    return float(np.clip(agg, -1, 1))


def _to_action(score: float) -> str:
    if score > 0.25:
        return "BUY"
    if score < -0.25:
        return "SELL"
    return "HOLD"


def _conviction_band(conviction_pct: float) -> str:
    if conviction_pct < 20:
        return "low"
    if conviction_pct < 45:
        return "moderate"
    if conviction_pct < 70:
        return "high"
    return "very high"


def evaluate(close: pd.Series, forecast_result: dict, sentiment_result: dict,
             mandate_key: str | None = None, portfolio_meta: dict | None = None,
             history_days: int | None = None, data_honesty: dict | None = None) -> dict:
    """Composite signal with mandate-aware weights and a numbers-backed rationale.

    Every threshold quoted in the rationale is the value actually used in the
    math, so the explanation can never drift from the computation.
    """
    from . import mandate as mnd

    f = ind.indicator_frame(close)
    i = len(f) - 1
    eff_w = mnd.weights(mandate_key) if mandate_key else dict(WEIGHTS)
    mlabel = mnd.get(mandate_key)["label"] if mandate_key else None

    raw = {
        "trend": _trend_score(f, i),
        "momentum": _momentum_score(f, i),
        "forecast": _forecast_score(forecast_result.get("expected_return_pct", 0.0)),
        "sentiment": _sentiment_score(sentiment_result.get("aggregate_score", 0.0)),
    }
    contributions = {k: eff_w[k] * raw[k] for k in raw}
    base_composite = sum(contributions.values())

    vol = ind.annualized_vol(close)
    vol_penalty = float(np.clip(1.0 - max(0.0, vol - 0.35) * 0.5, 0.6, 1.0))

    # Mandate-fit: dampen conviction when realized vol exceeds the mandate budget.
    mandate_fit = 1.0
    if mandate_key:
        tgt_vol = mnd.get(mandate_key)["target_vol_pct"] / 100.0
        if vol > tgt_vol > 0:
            mandate_fit = float(np.clip(tgt_vol / vol, 0.5, 1.0))

    score = float(np.clip(base_composite * vol_penalty * mandate_fit, -1, 1))
    action = _to_action(score)
    conviction_pct = round(abs(score) * 100, 1)
    band = _conviction_band(conviction_pct)

    clauses = _component_clauses(f, i, raw, eff_w, contributions, forecast_result, sentiment_result)
    components = [
        {"name": k, "raw": round(raw[k], 3), "base_weight": WEIGHTS[k],
         "effective_weight": round(eff_w[k], 3), "contribution": round(contributions[k], 3),
         "clause": clauses[k]}
        for k in ("trend", "momentum", "forecast", "sentiment")
    ]
    # Lead with the dominant drivers.
    ordered = sorted(components, key=lambda c: abs(c["contribution"]), reverse=True)

    caveats = _caveats(forecast_result, history_days, data_honesty)
    headline = _headline(action, conviction_pct, band, ordered, vol, vol_penalty,
                         mandate_fit, mandate_key, mlabel, portfolio_meta, caveats)

    return {
        "action": action,
        "score": round(score, 3),
        "conviction_pct": conviction_pct,
        "conviction_band": band,
        "vol_penalty": round(vol_penalty, 3),
        "mandate_fit": round(mandate_fit, 3),
        "mandate": mandate_key,
        "components": components,
        "headline_rationale": headline,
        "caveats": caveats,
    }


def _component_clauses(f, i, raw, eff_w, contrib, fc, sent) -> dict:
    row = f.iloc[i]
    fin = lambda *vals: all(np.isfinite(v) for v in vals)  # noqa: E731
    cross = ("SMA50 vs SMA200 n/a (short history)" if not fin(row["sma50"], row["sma200"])
             else "SMA50 > SMA200 (golden cross)" if row["sma50"] > row["sma200"]
             else "SMA50 < SMA200 (death cross)")
    px = ("price vs SMA50 n/a" if not fin(row["close"], row["sma50"])
          else "price above SMA50" if row["close"] > row["sma50"] else "price below SMA50")
    macd = ("MACD n/a" if not fin(row["macd_hist"])
            else "MACD histogram positive" if row["macd_hist"] >= 0 else "MACD histogram negative")
    rsi = row["rsi"] if np.isfinite(row["rsi"]) else 50.0
    mom_state = "oversold" if rsi < 30 else "overbought" if rsi > 70 else "neutral"
    er = fc.get("expected_return_pct", 0.0)
    q = fc.get("quality", {}) or {}
    da = q.get("directional_accuracy")
    da_txt = f"; OOS directional accuracy {da*100:.0f}% on {q.get('n_test', 0)}d" if da is not None else ""
    return {
        "trend": (f"Trend {'up' if raw['trend']>0 else 'down' if raw['trend']<0 else 'flat'} "
                  f"(raw {raw['trend']:+.2f}, eff. wt {eff_w['trend']:.0%}, contrib {contrib['trend']:+.2f}): "
                  f"{cross}, {px}, {macd}."),
        "momentum": (f"Momentum {mom_state} (RSI {rsi:.0f} vs 30/70 bands, raw {raw['momentum']:+.2f}, "
                     f"eff. wt {eff_w['momentum']:.0%}, contrib {contrib['momentum']:+.2f})."),
        "forecast": (f"Model projects {er:+.1f}% over {fc.get('horizon_days', 0)}d "
                     f"(p(up)={fc.get('prob_up', 0)*100:.0f}%, raw {raw['forecast']:+.2f}, "
                     f"eff. wt {eff_w['forecast']:.0%}, contrib {contrib['forecast']:+.2f}{da_txt})."),
        "sentiment": (f"News {sent.get('aggregate_label', 'neutral')} (aggregate "
                      f"{sent.get('aggregate_score', 0):+.2f} across {sent.get('count', 0)} headlines, "
                      f"raw {raw['sentiment']:+.2f}, eff. wt {eff_w['sentiment']:.0%}, "
                      f"contrib {contrib['sentiment']:+.2f})."),
    }


def _caveats(fc, history_days, data_honesty) -> list:
    out = []
    q = fc.get("quality", {}) or {}
    da = q.get("directional_accuracy")
    if da is not None and da <= 0.50:
        out.append(f"Forecast shown but the model has no measured edge — directional accuracy "
                   f"{da*100:.0f}% ≤ coin-flip; treat the forecast contribution skeptically.")
    if history_days is not None and history_days < 252:
        out.append(f"Only {history_days} aligned trading days of history (<1y); estimates carry wide uncertainty.")
    if data_honesty and data_honesty.get("simulated_weight_pct", 0) > 0:
        out.append(f"{data_honesty['simulated_weight_pct']:.0f}% of weight uses simulated prices — "
                   f"this signal is illustrative, not market-calibrated.")
    return out


def _headline(action, conviction_pct, band, ordered, vol, vol_penalty, mandate_fit,
              mandate_key, mlabel, pmeta, caveats) -> str:
    verb = {"BUY": "Constructive", "SELL": "Cautious", "HOLD": "Balanced"}[action]
    parts = []
    if pmeta:
        parts.append(f"This model ({pmeta.get('n_holdings', 0)} holdings, top weight "
                     f"{pmeta.get('top_weight', 0):.0%} in {pmeta.get('top_ticker', '?')}).")
    parts.append(f"{verb} — {conviction_pct:.0f}% conviction ({band}).")
    parts.append(ordered[0]["clause"])
    if len(ordered) > 1 and abs(ordered[1]["contribution"]) > 0.02:
        parts.append(ordered[1]["clause"])
    if vol_penalty < 0.999:
        parts.append(f"Conviction scaled ×{vol_penalty:.2f} (annualized vol {vol:.0%} above the 35% penalty line).")
    if mandate_fit < 0.999 and mlabel:
        parts.append(f"Further ×{mandate_fit:.2f} — {mlabel} target vol exceeded.")
    if caveats:
        parts.append("Caveat: " + caveats[0])
    return " ".join(parts)


def historical_signals(close: pd.Series) -> pd.Series:
    """Per-day trend+momentum score over history (for chart markers & backtest).

    Uses only causally-available indicators (no forward-looking forecast) so it
    is honest for backtesting.
    """
    f = ind.indicator_frame(close)
    scores = np.zeros(len(f))
    for i in range(len(f)):
        scores[i] = 0.6 * _trend_score(f, i) + 0.4 * _momentum_score(f, i)
    return pd.Series(scores, index=close.index)

"""Composite trade-signal generation.

Blends up to five transparent components into one score in [-1, 1]:
    trend        - price vs SMA50/200, MACD histogram sign
    momentum     - RSI position (mean-reverting at extremes)
    forecast     - expected return over the horizon (Ridge model, capped)
    sentiment    - aggregate news-headline sentiment
    fundamentals - building-block CMA expected return vs the mandate anchor
                   (annualized, so it does NOT move with the chart horizon) —
                   included only when usable fundamentals exist, never fabricated

The output is DUAL-TRACK: a ``tactical`` score (the four price/news components,
horizon-sensitive by design) and a ``strategic`` score (fundamentals-only,
horizon-free), plus the blended headline action. A volatility penalty shrinks
conviction when realized vol is high. The score maps to BUY / HOLD / SELL with
a conviction percentage and a full per-component breakdown so the
recommendation is fully explainable -- analysis only, never order execution.
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
    # Oversold -> bullish, overbought -> bearish, with a mild pro-trend tilt
    # mid-range. The extreme legs anchor to the mid-range endpoints (±0.16 at
    # RSI 30/70) so the score is CONTINUOUS at the bands — the old piecewise
    # jumped +0.159 -> -0.003 crossing 70, letting a 0.2-point RSI move flip a
    # borderline rating and whipsaw backtest positions (review finding).
    if rsi < 30:
        return float(-0.16 + (30 - rsi) / 30 * 1.16)   # -0.16 at 30 -> +1.0 at 0
    if rsi > 70:
        return float(0.16 - (rsi - 70) / 30 * 1.16)    # +0.16 at 70 -> -1.0 at 100
    return float((rsi - 50) / 50 * 0.4)


def _forecast_score(expected_return_pct: float, horizon_days: int = 21) -> float:
    # The Ridge E[r] compounds ~linearly with the horizon, so a FIXED +/-5%
    # saturation made the component's magnitude (and often the action) a
    # mechanical function of the slider (review finding). Scale the saturation
    # with the horizon instead: +/-5% at 21d, ~+/-21% at 90d, ~+/-1.2% at 5d.
    saturation = 5.0 * max(int(horizon_days or 21), 1) / 21.0
    return float(np.clip(expected_return_pct / saturation, -1, 1))


def _sentiment_score(agg: float) -> float:
    return float(np.clip(agg, -1, 1))


# The forecast component's weight is EARNED by measured out-of-sample edge.
# Full weight only at >=55% walk-forward directional accuracy, ZERO at <=50%
# (coin-flip): a measured below-chance model previously kept its full weight
# and could flip a HOLD to BUY while a caveat politely mentioned the missing
# edge (review finding, reproduced — DA=42% on n=111 still moved the action).
_EDGE_MIN_TEST_N = 40


def _forecast_edge_multiplier(quality: dict | None) -> float | None:
    """[0, 1] weight multiplier from measured directional accuracy, or None
    when the edge is UNMEASURED (small/absent test window — keep the weight
    and say so; absence of measurement is not evidence of no edge)."""
    if not quality:
        return None
    da = quality.get("directional_accuracy")
    n_test = quality.get("n_test")
    if da is None or not isinstance(n_test, (int, float)) or n_test < _EDGE_MIN_TEST_N:
        return None
    return float(np.clip((float(da) - 0.50) / 0.05, 0.0, 1.0))


# A gap of +/-6 percentage points (annualized) between the building-block CMA
# return and the mandate anchor saturates the fundamentals component. The gap is
# an annualized figure, so this score is the same at a 5-day and a 90-day chart
# horizon — it is the horizon-independent leg of the rating.
_FUNDAMENTALS_GAP_SATURATION_PP = 6.0

# Macro event-risk damper bounds (multiplies conviction like the vol penalty —
# it never flips a direction, it shrinks confidence ahead of known event risk).
_FOMC_DAMPER = 0.90            # decision within FOMC_IMMINENT_DAYS of a meeting
_GPR_MAX_DAMPER = 0.25         # up to -25% conviction at geopolitical index 1.0
_EARNINGS_BREADTH_DAMPER = 0.92   # <40% of a real report sample beating consensus
_EVENT_DAMPER_FLOOR = 0.70


def _event_risk_damper(macro_context: dict | None) -> tuple[float, list[str]]:
    """(multiplier, reasons) from the macro event-risk block. Transparent and
    bounded: unknown/unavailable macro state means NO damper — the absence of
    data is never treated as risk (or as calm)."""
    if not macro_context:
        return 1.0, []
    damper, reasons = 1.0, []
    if macro_context.get("fomc_imminent"):
        damper *= _FOMC_DAMPER
        days = macro_context.get("fomc_days_until")
        reasons.append(f"FOMC decision {days}d away — rate-path repricing risk "
                       f"(conviction ×{_FOMC_DAMPER:.2f})")
    gpr = macro_context.get("gpr_index")
    if isinstance(gpr, (int, float)) and gpr >= 0.6:
        cut = _GPR_MAX_DAMPER * (min(float(gpr), 1.0) - 0.6) / 0.4
        damper *= (1.0 - cut)
        reasons.append(f"Geopolitical risk index {gpr:.2f} (elevated) — "
                       f"conviction ×{1.0 - cut:.2f}")
    if macro_context.get("earnings_breadth_deteriorating"):
        # Reported beats/misses across the real book, not a forecast: when the
        # season is broadly missing (<40% beats on a sufficient sample), every
        # rating carries a little less conviction.
        damper *= _EARNINGS_BREADTH_DAMPER
        breadth = macro_context.get("earnings_breadth_pct")
        reasons.append(
            f"Earnings breadth weak ({breadth}% of {macro_context.get('earnings_reports')} "
            f"reports beat) — conviction ×{_EARNINGS_BREADTH_DAMPER:.2f}")
    return max(damper, _EVENT_DAMPER_FLOOR), reasons


def _fundamentals_score(fundamental_result: dict | None) -> float | None:
    """Score in [-1, 1] from the CMA gap vs anchor, or None when unusable."""
    if not fundamental_result or not fundamental_result.get("usable"):
        return None
    gap = fundamental_result.get("gap_vs_anchor_pct")
    if gap is None:
        return None
    return float(np.clip(gap / _FUNDAMENTALS_GAP_SATURATION_PP, -1, 1))


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
             history_days: int | None = None, data_honesty: dict | None = None,
             fundamental_result: dict | None = None,
             macro_context: dict | None = None) -> dict:
    """Composite signal with mandate-aware weights and a numbers-backed rationale.

    Every threshold quoted in the rationale is the value actually used in the
    math, so the explanation can never drift from the computation.

    ``fundamental_result`` (from ``cma.instrument_forward``) adds the fifth,
    horizon-independent component and the ``strategic`` track. Without it (or
    when it reports ``usable=False``) the output is the legacy four-component
    technical blend, and says so in a caveat.
    """
    from . import mandate as mnd

    f = ind.indicator_frame(close)
    i = len(f) - 1
    tech_w = mnd.weights(mandate_key) if mandate_key else dict(WEIGHTS)
    mlabel = mnd.get(mandate_key)["label"] if mandate_key else None

    raw = {
        "trend": _trend_score(f, i),
        "momentum": _momentum_score(f, i),
        "forecast": _forecast_score(forecast_result.get("expected_return_pct", 0.0),
                                    forecast_result.get("horizon_days", 21)),
        "sentiment": _sentiment_score(sentiment_result.get("aggregate_score", 0.0)),
    }
    fund_raw = _fundamentals_score(fundamental_result)
    has_fundamentals = fund_raw is not None

    # Effective weights: with usable fundamentals the four technical weights
    # scale down by (1 - share) and fundamentals takes the mandate's share;
    # without them the legacy technical weights apply unchanged (and a caveat
    # says the rating is technicals-only). Weight is never spent on missing data.
    if has_fundamentals:
        fshare = mnd.fundamentals_share(mandate_key or mnd.DEFAULT)
        eff_w = {k: v * (1.0 - fshare) for k, v in tech_w.items()}
        eff_w["fundamentals"] = fshare
        raw["fundamentals"] = fund_raw
    else:
        eff_w = dict(tech_w)

    edge_mult = _forecast_edge_multiplier(forecast_result.get("quality"))
    if edge_mult is not None:
        # Weight is EARNED, never redistributed: a gated forecast simply
        # contributes less (or nothing), it does not inflate the other legs.
        eff_w["forecast"] = eff_w["forecast"] * edge_mult

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

    # Macro event risk (FOMC proximity, geopolitical stress) shrinks conviction
    # transparently — the direction of the evidence is never altered.
    event_damper, event_reasons = _event_risk_damper(macro_context)

    score = float(np.clip(base_composite * vol_penalty * mandate_fit * event_damper, -1, 1))
    action = _to_action(score)
    conviction_pct = round(abs(score) * 100, 1)
    band = _conviction_band(conviction_pct)

    # -- Dual tracks ------------------------------------------------------- #
    # Tactical: the four price/news components under the mandate's technical
    # weights — this is the horizon-sensitive leg (the Ridge forecast is over
    # the user's chart horizon by construction).
    tactical_w = dict(tech_w)
    if edge_mult is not None:
        tactical_w["forecast"] = tactical_w["forecast"] * edge_mult
    tactical_composite = sum(tactical_w[k] * raw[k] for k in ("trend", "momentum", "forecast", "sentiment"))
    tactical_score = float(np.clip(tactical_composite * vol_penalty * mandate_fit * event_damper, -1, 1))
    tactical = {
        "action": _to_action(tactical_score),
        "score": round(tactical_score, 3),
        "conviction_pct": round(abs(tactical_score) * 100, 1),
        "basis": "trend/momentum/model-forecast/sentiment — sensitive to the selected horizon",
    }
    # Strategic: fundamentals only, annualized — does not move with the slider.
    if has_fundamentals:
        strategic = {
            "usable": True,
            "action": _to_action(fund_raw),
            "score": round(fund_raw, 3),
            "conviction_pct": round(abs(fund_raw) * 100, 1),
            "expected_return_pct": fundamental_result.get("expected_return_pct"),
            "anchor_pct": fundamental_result.get("anchor_pct"),
            "gap_vs_anchor_pct": fundamental_result.get("gap_vs_anchor_pct"),
            "blocks_pct": fundamental_result.get("blocks_pct", {}),
            "source": fundamental_result.get("source"),
            "analyst": fundamental_result.get("analyst"),
            "quality": fundamental_result.get("quality"),
            "basis": "building-block CMA (yield + growth + valuation reversion), annualized — "
                     "independent of the selected horizon",
        }
    else:
        strategic = {
            "usable": False,
            "reason": (fundamental_result or {}).get("basis", "no usable fundamentals"),
        }

    clauses = _component_clauses(f, i, raw, eff_w, contributions, forecast_result, sentiment_result)
    names = ("trend", "momentum", "forecast", "sentiment") + (("fundamentals",) if has_fundamentals else ())
    if has_fundamentals:
        clauses["fundamentals"] = _fundamentals_clause(raw, eff_w, contributions, fundamental_result)
    components = [
        {"name": k, "raw": round(raw[k], 3),
         "base_weight": WEIGHTS.get(k, round(eff_w[k], 3)),
         "effective_weight": round(eff_w[k], 3), "contribution": round(contributions[k], 3),
         "clause": clauses[k]}
        for k in names
    ]
    # Lead with the dominant drivers.
    ordered = sorted(components, key=lambda c: abs(c["contribution"]), reverse=True)

    caveats = _caveats(forecast_result, history_days, data_honesty)
    if not has_fundamentals:
        caveats.append("No usable fundamentals for this instrument — rating is the "
                       "technical blend only (no strategic track).")
    caveats.extend(event_reasons)
    # Sector-level policy pressure is context (never scored): flag it honestly.
    sector_policy = (macro_context or {}).get("sector_policy")
    if sector_policy:
        caveats.append(
            f"White House policy activity touching {sector_policy.get('sector')} "
            f"({sector_policy.get('n_actions')} recent action(s); themes: "
            f"{', '.join(sector_policy.get('themes') or [])}) — headline/policy risk.")
    headline = _headline(action, conviction_pct, band, ordered, vol, vol_penalty,
                         mandate_fit, mandate_key, mlabel, portfolio_meta, caveats,
                         tactical=tactical, strategic=strategic)

    result = {
        "action": action,
        "score": round(score, 3),
        "conviction_pct": conviction_pct,
        "conviction_band": band,
        "vol_penalty": round(vol_penalty, 3),
        "mandate_fit": round(mandate_fit, 3),
        "mandate": mandate_key,
        "components": components,
        "tactical": tactical,
        "strategic": strategic,
        "headline_rationale": headline,
        "caveats": caveats,
    }
    if macro_context is not None:
        result["event_risk_damper"] = round(event_damper, 3)
        result["macro"] = {k: macro_context.get(k) for k in
                           ("fomc_imminent", "fomc_days_until", "gpr_index",
                            "fed_stance_label", "fed_stance_score") if k in macro_context}
    return result


def _fundamentals_clause(raw, eff_w, contrib, fr: dict) -> str:
    er = fr.get("expected_return_pct")
    anchor = fr.get("anchor_pct")
    gap = fr.get("gap_vs_anchor_pct") or 0.0
    stance = "above" if gap > 0 else "below" if gap < 0 else "at"
    blocks = fr.get("blocks_pct") or {}
    detail = ", ".join(f"{k.replace('_', ' ')} {v:+.1f}%" for k, v in blocks.items())
    analyst = fr.get("analyst") or {}
    extra = ""
    if analyst.get("implied_upside_pct") is not None:
        extra = (f" Analyst consensus target implies {analyst['implied_upside_pct']:+.1f}% "
                 f"({analyst.get('n_analysts') or '?'} analysts; context only, not scored).")
    return (f"Fundamentals: forward E[r] {er:+.1f}%/yr is {abs(gap):.1f}pp {stance} the {anchor:.1f}% "
            f"mandate anchor ({detail}; raw {raw['fundamentals']:+.2f}, eff. wt "
            f"{eff_w['fundamentals']:.0%}, contrib {contrib['fundamentals']:+.2f}; annualized — "
            f"does not change with the chart horizon).{extra}")


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
    if da is not None and da <= 0.50:
        forecast_clause = (f"Forecast transparency check: estimate {er:+.1f}% over "
                           f"{fc.get('horizon_days', 0)}d is not measured edge "
                           f"(p(up)={fc.get('prob_up', 0)*100:.0f}%, raw {raw['forecast']:+.2f}, "
                           f"eff. wt {eff_w['forecast']:.0%}, contrib {contrib['forecast']:+.2f}{da_txt}).")
    else:
        forecast_clause = (f"Model projects {er:+.1f}% over {fc.get('horizon_days', 0)}d "
                           f"(p(up)={fc.get('prob_up', 0)*100:.0f}%, raw {raw['forecast']:+.2f}, "
                           f"eff. wt {eff_w['forecast']:.0%}, contrib {contrib['forecast']:+.2f}{da_txt}).")
    return {
        "trend": (f"Trend {'up' if raw['trend']>0 else 'down' if raw['trend']<0 else 'flat'} "
                  f"(raw {raw['trend']:+.2f}, eff. wt {eff_w['trend']:.0%}, contrib {contrib['trend']:+.2f}): "
                  f"{cross}, {px}, {macd}."),
        "momentum": (f"Momentum {mom_state} (RSI {rsi:.0f} vs 30/70 bands, raw {raw['momentum']:+.2f}, "
                     f"eff. wt {eff_w['momentum']:.0%}, contrib {contrib['momentum']:+.2f})."),
        "forecast": forecast_clause,
        "sentiment": (f"News {sent.get('aggregate_label', 'neutral')} (aggregate "
                      f"{sent.get('aggregate_score', 0):+.2f} across {sent.get('count', 0)} headlines, "
                      f"raw {raw['sentiment']:+.2f}, eff. wt {eff_w['sentiment']:.0%}, "
                      f"contrib {contrib['sentiment']:+.2f})."),
    }


def _caveats(fc, history_days, data_honesty) -> list:
    out = []
    q = fc.get("quality", {}) or {}
    da = q.get("directional_accuracy")
    n_test = q.get("n_test")
    edge_mult = _forecast_edge_multiplier(q)
    if edge_mult is not None and edge_mult < 1.0:
        out.append(
            f"Forecast weight gated to {edge_mult:.0%} of the mandate weight — measured "
            f"directional accuracy {da*100:.0f}% on {int(n_test)} test windows "
            f"({'no' if edge_mult == 0 else 'thin'} out-of-sample edge; full weight requires ≥55%).")
    elif da is not None and da <= 0.50 and edge_mult is None:
        out.append(f"Forecast shown but its edge is UNMEASURED (test window under "
                   f"{_EDGE_MIN_TEST_N}) and in-sample accuracy is {da*100:.0f}% — "
                   "treat the forecast contribution skeptically.")
    if history_days is not None and history_days < 252:
        out.append(f"Only {history_days} analyzed trading days of history (<1y); estimates carry wide uncertainty.")
    if data_honesty and data_honesty.get("simulated_weight_pct", 0) > 0:
        out.append(f"{data_honesty['simulated_weight_pct']:.0f}% of weight uses simulated prices — "
                   f"this signal is illustrative, not market-calibrated.")
    return out


def _headline(action, conviction_pct, band, ordered, vol, vol_penalty, mandate_fit,
              mandate_key, mlabel, pmeta, caveats, tactical=None, strategic=None) -> str:
    verb = {"BUY": "Constructive", "SELL": "Cautious", "HOLD": "Neutral evidence"}[action]
    parts = []
    if pmeta:
        parts.append(f"This model ({pmeta.get('n_holdings', 0)} holdings, top weight "
                     f"{pmeta.get('top_weight', 0):.0%} in {pmeta.get('top_ticker', '?')}).")
    parts.append(f"{verb} — {conviction_pct:.0f}% conviction ({band}).")
    if (tactical and strategic and strategic.get("usable")
            and tactical["action"] != strategic["action"]):
        parts.append(f"Tracks diverge: tactical (price action, horizon-sensitive) reads "
                     f"{tactical['action']}, strategic (fundamentals, horizon-free) reads "
                     f"{strategic['action']} — the blend weighs both.")
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
    is honest for backtesting. Vectorized, and exactly equivalent to the
    per-row reference `_historical_signals_loop` (asserted in tests).
    """
    f = ind.indicator_frame(close)
    n = len(f)
    if n == 0:
        return pd.Series(np.zeros(0), index=close.index)
    px = f["close"].to_numpy(dtype=float)
    sma50 = f["sma50"].to_numpy(dtype=float)
    sma200 = f["sma200"].to_numpy(dtype=float)
    macd_hist = f["macd_hist"].to_numpy(dtype=float)
    rsi = f["rsi"].to_numpy(dtype=float)

    trend = np.zeros(n)
    both_smas = np.isfinite(sma50) & np.isfinite(sma200)
    trend[both_smas] += np.where(sma50[both_smas] > sma200[both_smas], 0.5, -0.5)
    px_vs_sma = np.isfinite(px) & np.isfinite(sma50)
    trend[px_vs_sma] += np.where(px[px_vs_sma] > sma50[px_vs_sma], 0.25, -0.25)
    has_macd = np.isfinite(macd_hist)
    trend[has_macd] += 0.25 * np.sign(macd_hist[has_macd])
    trend = np.clip(trend, -1.0, 1.0)

    momentum = np.zeros(n)
    has_rsi = np.isfinite(rsi)
    r = rsi[has_rsi]
    # Mirrors _momentum_score exactly — continuous at the 30/70 bands.
    momentum[has_rsi] = np.where(
        r < 30, -0.16 + (30 - r) / 30 * 1.16,
        np.where(r > 70, 0.16 - (r - 70) / 30 * 1.16, (r - 50) / 50 * 0.4),
    )
    return pd.Series(0.6 * trend + 0.4 * momentum, index=close.index)


def _historical_signals_loop(close: pd.Series) -> pd.Series:
    """Per-row reference implementation of `historical_signals`.

    Kept only as the regression oracle for the vectorized version; the two
    must return frame-equal results on any input.
    """
    f = ind.indicator_frame(close)
    scores = np.zeros(len(f))
    for i in range(len(f)):
        scores[i] = 0.6 * _trend_score(f, i) + 0.4 * _momentum_score(f, i)
    return pd.Series(scores, index=close.index)


def latest_signal_score(close: pd.Series) -> float:
    """Trend+momentum score for the most recent session only.

    Equals `historical_signals(close).iloc[-1]` without materializing the full
    per-day history (used where only the latest signal is needed).
    """
    f = ind.indicator_frame(close)
    if not len(f):
        raise ValueError("Need at least one price row for a signal score.")
    i = len(f) - 1
    return float(0.6 * _trend_score(f, i) + 0.4 * _momentum_score(f, i))

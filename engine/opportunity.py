"""Conservative Opportunity Radar scoring.

The radar ranks review candidates, not trades. Scores are deliberately
penalized for simulated/sample data, weak out-of-sample forecast quality,
high volatility/drawdown, weak backtest evidence, and risk-off regimes.
"""
from __future__ import annotations

import numpy as np

from . import (
    analytics_cache, cma, data, forecast, fundamentals, indicators, macro_events, mandate,
    portfolio, provenance, regime as regime_mod, sec_events, sentiment, signals, strategy,
)
from ._common import dedupe as _dedupe


def score_candidate(candidate: dict, regime: dict | None = None) -> dict:
    regime = regime or {"label": "neutral"}
    source = candidate.get("source", "sample")
    # Non-finite (NaN/inf/non-numeric) analytics inputs are coerced to
    # conservative fallbacks so they can never inflate — or NaN-poison — the
    # composite score. Fallback rationale per input:
    #   signal_score     0.0   neutral signal, no conviction credited
    #   expected_return  0.0   no upside credited
    #   expected_vol     60.0  assume elevated volatility -> risk penalty applies
    #   max_drawdown    -40.0  assume deep drawdown risk -> risk penalty applies
    #   backtest returns 0.0   no historical alpha credited
    #   sim weight     100.0   assume fully simulated -> data-quality floor
    bad_inputs: list[str] = []
    signal_score = _finite(candidate.get("signal_score", 0.0), 0.0, "signal_score", bad_inputs)
    expected_return = _finite(candidate.get("expected_return_pct", 0.0), 0.0, "expected_return_pct", bad_inputs)
    expected_vol = _finite(candidate.get("expected_vol_pct", 0.0), 60.0, "expected_vol_pct", bad_inputs)
    max_drawdown = _finite(candidate.get("max_drawdown_pct", 0.0), -40.0, "max_drawdown_pct", bad_inputs)
    action = candidate.get("action") or _action_from_signal(signal_score)

    backtest = candidate.get("backtest") or {}
    strategy_return = _finite(backtest.get("strategy_return_pct", 0.0), 0.0, "strategy_return_pct", bad_inputs)
    benchmark_return = _finite(backtest.get("benchmark_return_pct", 0.0), 0.0, "benchmark_return_pct", bad_inputs)
    sim_weight = _finite(candidate.get("simulated_weight_pct") or 0.0, 100.0, "simulated_weight_pct", bad_inputs)
    data_quality = _data_quality(source, sim_weight)
    forecast_quality = _forecast_quality(candidate.get("forecast_quality") or {})
    sharpe = _finite(backtest.get("sharpe", 0.0), 0.0, "sharpe", bad_inputs)
    backtest_quality = _backtest_quality(strategy_return, benchmark_return, sharpe)
    risk_score = float(np.clip(expected_vol * 1.05 + abs(max_drawdown) * 0.85, 0, 100))
    signal_quality = float(np.clip(50 + signal_score * 45, 0, 100))
    upside_quality = float(np.clip(50 + expected_return * 3.0, 0, 100))
    evidence_score = float(np.clip(
        forecast_quality * 0.32 + backtest_quality * 0.36 + data_quality * 0.20 + signal_quality * 0.12,
        0,
        100,
    ))
    score = (
        evidence_score * 0.42
        + upside_quality * 0.26
        + signal_quality * 0.18
        + data_quality * 0.14
        - max(risk_score - 35, 0) * 0.50
    )

    warnings = list(candidate.get("warnings") or [])
    if bad_inputs:
        warnings.append(
            "Data integrity: non-numeric analytics inputs ("
            + ", ".join(bad_inputs)
            + ") were replaced with conservative fallbacks; verify source data before relying on this score.")
    if source in {"sample", "simulated"} or sim_weight:
        warnings.append("Sample or simulated data lowers confidence; verify with client/live price history.")
        score -= 12
    if forecast_quality < 50:
        warnings.append("Weak forecast quality; out-of-sample directional evidence is below the review threshold.")
        score -= 8
    if backtest_quality < 45:
        warnings.append("Weak backtest quality; historical signal evidence did not clear the benchmark.")
        score -= 7
    if risk_score >= 65:
        warnings.append("Risk is elevated; volatility or drawdown could invalidate the setup.")
        score -= 10
    # The regime penalty only applies when the regime itself was classified
    # from REAL data: a bundled sample SPY once became the broad-market proxy
    # on offline starts and its synthetic risk-off label docked every real
    # candidate 8 points (review finding).
    regime_source = str(regime.get("source") or "")
    regime_is_real = not regime_source or provenance.is_real_source(regime_source)
    if regime.get("label") == "risk-off" and evidence_score < 70 and regime_is_real:
        warnings.append("Risk-off regime requires stronger evidence before a constructive review.")
        score -= 8

    # Final guard: a non-finite composite must never be ranked or serialized.
    if not np.isfinite(score):
        warnings.append(
            "Data integrity: the composite score could not be computed from the inputs; "
            "scored 0 and blocked from ranking above reviewed candidates.")
        score = 0.0

    scored = {
        "id": candidate.get("id") or f"{candidate.get('kind', 'instrument')}:{candidate.get('symbol', '')}",
        "kind": candidate.get("kind", "instrument"),
        "name": candidate.get("name", candidate.get("symbol", "")),
        "symbol": candidate.get("symbol", ""),
        "model_id": candidate.get("model_id"),
        "source": source,
        "action": action,
        "opportunity_score": round(float(np.clip(score, 0, 100)), 1),
        "risk_score": round(risk_score, 1),
        "evidence_score": round(evidence_score, 1),
        "expected_return_pct": round(expected_return, 2),
        "expected_vol_pct": round(expected_vol, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "strategy_return_pct": round(strategy_return, 2),
        "benchmark_return_pct": round(benchmark_return, 2),
        "beat_benchmark": strategy_return > benchmark_return,
        "reason": candidate.get("reason", ""),
        "tactical": candidate.get("tactical"),
        "strategic": candidate.get("strategic"),
        "insider_signal": candidate.get("insider_signal"),
        "insider_signal_note": candidate.get("insider_signal_note"),
        "forecast_quality": round(forecast_quality, 1),
        "backtest_quality": round(backtest_quality, 1),
        "data_quality": round(data_quality, 1),
        "top_positive_drivers": _positive_drivers(expected_return, signal_score, forecast_quality, backtest_quality),
        "top_negative_drivers": _negative_drivers(source, risk_score, forecast_quality, backtest_quality, regime),
        "plain_english_summary": _summary(candidate, evidence_score, risk_score, action),
        "recommended_next_step": _next_step(action),
        "warnings": _dedupe(warnings)[:5],
        "eligible_for_real_research": provenance.is_real_source(source) and not sim_weight,
        "data_mode": "real" if provenance.is_real_source(source) else "demo",
    }
    return scored


def rank_candidates(candidates: list[dict], regime: dict | None = None) -> list[dict]:
    # Defensive sort key: a non-finite score must never float to the top of the
    # review queue (NaN comparisons would leave it wherever it started).
    def _key(item: dict) -> tuple[float, float]:
        score, evidence = float(item["opportunity_score"]), float(item["evidence_score"])
        return (score if np.isfinite(score) else -1.0,
                evidence if np.isfinite(evidence) else -1.0)

    return sorted(
        [score_candidate(candidate, regime=regime) for candidate in candidates],
        key=_key,
        reverse=True,
    )


def opportunities(
    instruments: list[data.Instrument] | None = None,
    models: list[portfolio.Model] | None = None,
    kind: str = "all",
    include_hold: bool = True,
    min_score: float = 0.0,
    limit: int = 25,
) -> dict:
    instruments = data.all_instruments() if instruments is None else instruments
    models = portfolio.all_models() if models is None else models
    uprov = provenance.universe(instruments)
    market = regime_mod.market_regime(instruments)
    raw: list[dict] = []
    blocked: list[dict] = []
    if kind in {"all", "instrument"}:
        raw.extend(_instrument_candidates(instruments))
    if kind in {"all", "model"}:
        model_candidates, blocked_models = _model_candidates(models)
        raw.extend(model_candidates)
        blocked.extend(blocked_models)
    ranked = rank_candidates(raw, regime=market)
    ranked = [item for item in ranked if item.get("eligible_for_real_research")]
    if not include_hold:
        ranked = [item for item in ranked if item["action"] != "HOLD"]
    ranked = [item for item in ranked if item["opportunity_score"] >= min_score]
    return {
        "regime": market,
        "macro": macro_events.compact_summary(macro_events.snapshot_cached()),
        "items": ranked[:limit],
        "blocked_items": blocked,
        "count": len(ranked[:limit]),
        "total_candidates": len(raw),
        "data_mode": "real" if ranked else uprov["data_mode"],
        "display_label": "Real Research Mode" if ranked else uprov["display_label"],
        "eligible_for_real_research": bool(ranked),
        "reason": "" if ranked else uprov["reason"],
        "required_action": "" if ranked else uprov["required_action"],
        "data_provenance": {"source_counts": uprov["source_counts"], "warnings": uprov["warnings"]},
        "methodology": {
            "scoring": "Evidence, upside, signal strength, data quality, risk penalty, and regime compatibility.",
            "analysis_only": True,
        },
    }


def instrument_candidate(inst: data.Instrument) -> dict | None:
    """Raw scoring candidate for one instrument, memoized per price series.

    This is the single source of the forecast/signal/backtest evidence that
    both the Opportunity Radar and the Command Center score via
    :func:`score_candidate`. Returns None when the instrument lacks the 60
    rows of history the forecast and strategy engines require.
    """
    close = inst.df["close"].dropna()
    if len(close) < 60:
        return None
    cache_key = analytics_cache.series_key(f"{inst.symbol}:{inst.source}", close)
    cached = analytics_cache.get("opportunity_candidate", cache_key)
    if cached is not None:
        return cached
    fc = forecast.forecast(close, horizon=21, n_paths=700)
    sent = sentiment.score_headlines(inst.headlines)
    # Fundamentals give the radar its horizon-independent strategic leg; the
    # fetch is TTL-cached in engine.fundamentals and empty/offline results are
    # honestly reported as a technicals-only rating.
    fwd = cma.instrument_forward(inst.symbol, fundamentals.fetch(inst.symbol))
    # Macro context: cached-only (the radar never fetches feeds in-request; the
    # background loop keeps the snapshot warm). No snapshot -> no damper.
    macro_ctx = macro_events.build_macro_context(fwd.get("sector") or "")
    sig = signals.evaluate(close, fc, sent, history_days=len(close),
                           fundamental_result=fwd, macro_context=macro_ctx)
    st = strategy.analyze_strategy(close)
    metrics = indicators.metrics_summary(close)
    candidate = {
        "id": f"instrument:{inst.symbol}",
        "kind": "instrument",
        "name": inst.name,
        "symbol": inst.symbol,
        "source": inst.source,
        "action": sig["action"],
        "signal_score": sig["score"],
        "tactical": sig.get("tactical"),
        "strategic": sig.get("strategic"),
        "expected_return_pct": fc["expected_return_pct"],
        "expected_vol_pct": fc["expected_vol_pct"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "forecast_quality": fc.get("quality", {}),
        "backtest": {
            "strategy_return_pct": st["strategy"]["total_return_pct"],
            "benchmark_return_pct": st["benchmark"]["total_return_pct"],
            "sharpe": st["strategy"]["sharpe"],
        },
        "reason": sig.get("headline_rationale", ""),
        "warnings": sig.get("caveats", []),
    }
    # Earnings proximity — a rating computed days before a report carries event
    # risk the price history can't see yet; flag it rather than hide it.
    earnings = (fwd or {}).get("earnings") or {}
    if earnings.get("imminent"):
        candidate["warnings"] = list(candidate["warnings"]) + [
            f"Earnings scheduled {earnings['next_date']} ({earnings['days_until']}d away) — "
            "expect an event-driven move this rating cannot anticipate."]
        candidate["next_earnings"] = earnings.get("next_date")
    # Regulatory event flags — cached-only read (the radar never waits on EDGAR;
    # the cache is warmed by the analyze path and the background refresh loop).
    events = sec_events.events_cached(inst.symbol)
    if events and events.get("available"):
        if events.get("notable_8k"):
            latest = next((e for e in events.get("eight_ks", []) if e.get("notable")), None)
            if latest:
                candidate["warnings"] = list(candidate["warnings"]) + [
                    f"Notable 8-K {latest['filing_date']}: {'; '.join(latest['labels'][:3])}."]
        insider = events.get("insider") or {}
        if insider.get("net_signal") in {"buying", "selling"}:
            candidate["insider_signal"] = insider["net_signal"]
            # Carry the coverage caveat with the signal: net_signal may come
            # from a 5-filing parse budget over a much larger window, and
            # dropping the "Parsed N of M" context presented a thin sample as
            # the full insider picture (review finding).
            in_window = int(insider.get("filings_in_window") or 0)
            parsed_n = len(insider.get("parsed") or [])
            if in_window > parsed_n:
                candidate["insider_signal_note"] = (
                    f"Based on {parsed_n} of {in_window} Form 4 filings in the window.")
    analytics_cache.put("opportunity_candidate", cache_key, candidate)
    return candidate


def _instrument_candidates(instruments: list[data.Instrument]) -> list[dict]:
    out = []
    for inst in instruments:
        if not provenance.is_real_source(inst.source):
            continue
        try:
            candidate = instrument_candidate(inst)
        except Exception:
            continue
        if candidate is not None:
            out.append(candidate)
    return out


def _model_candidates(models: list[portfolio.Model]) -> tuple[list[dict], list[dict]]:
    out = []
    blocked = []
    for mdl in models:
        try:
            ps = portfolio.build_series(mdl, allow_sample=False, allow_simulated=False)
            p = provenance.portfolio(ps.provenance)
            if not p["eligible_for_real_research"]:
                blocked.append(_blocked_model(mdl, p))
                continue
            fc = forecast.forecast(ps.close, horizon=21, n_paths=700)
            sig = signals.evaluate(ps.close, fc, {"aggregate_score": 0, "aggregate_label": "neutral", "count": 0},
                                   mandate_key=mdl.mandate_key, history_days=ps.n_days, data_honesty=ps.provenance)
            st = strategy.analyze_strategy(ps.close)
            metrics = indicators.metrics_summary(ps.close)
            m = mandate.get(mdl.mandate_key)
        except Exception as exc:
            blocked.append(_blocked_model(mdl, {
                "data_mode": "invalid_for_research",
                "display_label": "Data Quality Blocked",
                "eligible_for_real_research": False,
                "reason": str(exc),
                "required_action": provenance.DEMO_ACTION,
                "missing_tickers": [h.ticker for h in mdl.holdings],
                "warnings": [str(exc)],
            }))
            continue
        out.append({
            "id": f"model:{mdl.id}",
            "kind": "model",
            "name": mdl.name,
            "symbol": mdl.id,
            "model_id": mdl.id,
            "source": "simulated" if ps.provenance.get("simulated_weight_pct", 0) else "upload",
            "simulated_weight_pct": ps.provenance.get("simulated_weight_pct", 0),
            "action": sig["action"],
            "signal_score": sig["score"],
            "expected_return_pct": fc["expected_return_pct"],
            "expected_vol_pct": fc["expected_vol_pct"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "forecast_quality": fc.get("quality", {}),
            "backtest": {
                "strategy_return_pct": st["strategy"]["total_return_pct"],
                "benchmark_return_pct": st["benchmark"]["total_return_pct"],
                "sharpe": st["strategy"]["sharpe"],
            },
            "reason": sig.get("headline_rationale", ""),
            "warnings": ps.warnings + sig.get("caveats", []),
            "mandate_label": m["label"],
        })
    return out, blocked


def _blocked_model(mdl: portfolio.Model, p: dict) -> dict:
    return {
        "id": f"model:{mdl.id}",
        "kind": "model",
        "name": mdl.name,
        "symbol": mdl.id,
        "model_id": mdl.id,
        "action": "REVIEW",
        "opportunity_score": 0.0,
        "risk_score": 0.0,
        "evidence_score": 0.0,
        "eligible_for_real_research": False,
        "data_mode": p.get("data_mode", "invalid_for_research"),
        "display_label": p.get("display_label", "Data Quality Blocked"),
        "reason": p.get("reason", "Model data quality is blocked."),
        "required_action": p.get("required_action", provenance.DEMO_ACTION),
        "missing_tickers": p.get("missing_tickers", []),
        "warnings": p.get("warnings", []),
    }


def _finite(value, fallback: float, name: str = "", bad_inputs: list[str] | None = None) -> float:
    """Coerce a metric to a finite float, recording the field when it is not."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = float("nan")
    if not np.isfinite(v):
        if bad_inputs is not None and name:
            bad_inputs.append(name)
        return fallback
    return v


def _data_quality(source: str, sim_weight: float) -> float:
    if sim_weight:
        return max(20.0, 75.0 - sim_weight * 0.7)
    return {"live": 92.0, "upload": 82.0, "sample": 42.0, "simulated": 28.0}.get(source, 55.0)


def _forecast_quality(quality: dict) -> float:
    da = quality.get("directional_accuracy")
    n_test = int(_finite(quality.get("n_test"), 0.0))
    if da is None:
        base = 42.0
    else:
        # Unmeasurable accuracy scores the same as unmeasured: no demonstrated edge.
        base = 50.0 + (_finite(da, 0.50) - 0.50) * 180.0
    if n_test < 30:
        base -= 12
    elif n_test < 60:
        base -= 5
    return float(np.clip(base, 0, 100))


def _backtest_quality(strategy_return: float, benchmark_return: float, sharpe: float) -> float:
    alpha = strategy_return - benchmark_return
    return float(np.clip(50 + alpha * 1.1 + sharpe * 13, 0, 100))


def _positive_drivers(expected_return: float, signal_score: float, forecast_quality: float, backtest_quality: float) -> list[str]:
    drivers = []
    if expected_return > 0:
        drivers.append(f"Forecast expects {expected_return:+.1f}% over the tactical horizon.")
    if signal_score > 0.25:
        drivers.append(f"Composite signal is constructive ({signal_score:+.2f}).")
    if forecast_quality >= 60:
        drivers.append(f"Forecast quality score is {forecast_quality:.0f}/100.")
    if backtest_quality >= 60:
        drivers.append(f"Backtest quality score is {backtest_quality:.0f}/100.")
    return drivers[:3] or ["No strong positive driver cleared the review threshold."]


def _negative_drivers(source: str, risk_score: float, forecast_quality: float, backtest_quality: float, regime: dict) -> list[str]:
    drivers = []
    if source in {"sample", "simulated"}:
        drivers.append("Data quality is penalized because the source is sample or simulated.")
    if risk_score >= 55:
        drivers.append(f"Risk score is elevated at {risk_score:.0f}/100.")
    if forecast_quality < 50:
        drivers.append("Forecast quality is below the evidence threshold.")
    if backtest_quality < 45:
        drivers.append("Backtest quality is weak versus benchmark evidence.")
    if regime.get("label") == "risk-off":
        drivers.append("Risk-off regime raises the evidence bar.")
    return drivers[:3] or ["No major negative driver dominated the score."]


def _summary(candidate: dict, evidence_score: float, risk_score: float, action: str) -> str:
    return (
        f"{candidate.get('symbol', '')} is a {action} review candidate with evidence "
        f"{evidence_score:.0f}/100 and risk {risk_score:.0f}/100. "
        "Use this as a research queue item, not an instruction to trade."
    )


def _next_step(action: str) -> str:
    if action == "BUY":
        return "Verify data provenance, inspect Strategy Lab, and document invalidating risks before any recommendation."
    if action == "SELL":
        return "Verify the risk drivers and review whether exposure should be watched or reduced in a model context."
    return "Verify whether stronger evidence emerges before escalating beyond hold/review."


def _action_from_signal(signal_score: float) -> str:
    if signal_score > 0.25:
        return "BUY"
    if signal_score < -0.25:
        return "SELL"
    return "HOLD"

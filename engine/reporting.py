"""Printable Advisor Report composition."""
from __future__ import annotations

from datetime import datetime, timezone

from . import (
    cma, forecast, fundamentals, indicators, opportunity, persistence, portfolio,
    portfolio_clinic, provenance, risk_exposure, sentiment, signals, strategy,
)
from ._common import dedupe as _dedupe

DISCLAIMER = (
    "Analysis only — Helios provides research evidence and does not provide investment advice, "
    "brokerage services, order execution, or return guarantees."
)


def instrument_report(inst) -> dict:
    close = inst.df["close"].dropna()
    p = provenance.instrument(inst.source, len(close))
    fc = forecast.forecast(close, horizon=21, n_paths=700)
    sent = sentiment.score_headlines(inst.headlines)
    fwd = cma.instrument_forward(inst.symbol, fundamentals.fetch(inst.symbol))
    sig = signals.evaluate(close, fc, sent, history_days=len(close), fundamental_result=fwd)
    try:
        st = strategy.analyze_strategy(close)
        strategy_note = ""
    except ValueError as exc:
        # Short history (< 60 rows): degrade to an honest strategy-unavailable
        # section instead of failing the whole report.
        st = None
        strategy_note = str(exc)
    metrics = indicators.metrics_summary(close)
    scored = opportunity.score_candidate({
        "id": f"instrument:{inst.symbol}",
        "kind": "instrument",
        "name": inst.name,
        "symbol": inst.symbol,
        "source": inst.source,
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
        } if st else {},
        "warnings": sig.get("caveats", []),
    })
    warnings = list(scored["warnings"])
    if strategy_note:
        warnings.append(strategy_note)
    if inst.source == "sample":
        warnings.append("This report uses bundled sample data; verify with current client/live data before use.")
    return _base_report(
        kind="instrument",
        identity={"symbol": inst.symbol, "name": inst.name, "source": inst.source},
        sections={
            "executive_summary": {
                "headline": f"{inst.symbol}: {sig['action']} review candidate",
                "summary": scored["plain_english_summary"],
            },
            "action": {
                "action": sig["action"],
                "conviction_pct": sig["conviction_pct"],
                "signal_score": sig["score"],
                "rationale": sig["headline_rationale"],
            },
            "evidence": {
                "opportunity_score": scored["opportunity_score"],
                "evidence_score": scored["evidence_score"],
                "positive_drivers": scored["top_positive_drivers"],
                "negative_drivers": scored["top_negative_drivers"],
            },
            "risk": {
                "risk_score": scored["risk_score"],
                "annual_vol_pct": metrics["annual_vol_pct"],
                "max_drawdown_pct": metrics["max_drawdown_pct"],
                "warnings": warnings,
            },
            "forecast": {
                "expected_return_pct": fc["expected_return_pct"],
                "expected_vol_pct": fc["expected_vol_pct"],
                "prob_up": fc["prob_up"],
                "quality": fc.get("quality", {}),
            },
            "strategy": _strategy_summary(st) if st else _strategy_unavailable(strategy_note),
            "provenance": _instrument_provenance(inst, close, p),
            "data_quality": p,
            "assumptions": _assumptions(),
        },
        warnings=warnings,
        data_quality=p,
    )


def model_report(model: portfolio.Model) -> dict:
    clinic = portfolio_clinic.analyze_clinic(model)
    if not clinic.get("eligible_for_real_research"):
        p = {
            "data_mode": clinic.get("data_mode", "invalid_for_research"),
            "display_label": clinic.get("display_label", "Data Quality Blocked"),
            "eligible_for_real_research": False,
            "reason": clinic.get("reason", "Model data quality is blocked."),
            "required_action": clinic.get("required_action", provenance.DEMO_ACTION),
            "missing_tickers": clinic.get("missing_tickers", []),
            "warnings": clinic.get("warnings", []),
            "source_weight_pct": clinic.get("data_provenance", {}).get("source_weight_pct", {}),
        }
        return _base_report(
            kind="model",
            identity={"id": model.id, "name": model.name, "mandate": model.mandate_key},
            sections={
                "executive_summary": {
                    "headline": "Data quality blocked",
                    "summary": "Helios will not generate advisor evidence from sample, simulated, or missing prices.",
                },
                "data_quality": p,
                "clinic": {
                    "diagnostics": clinic["diagnostics"],
                    "suggestions": clinic["suggestions"],
                    "refusals": clinic["refusals"],
                },
                "provenance": clinic.get("provenance", {}),
                "assumptions": _assumptions(),
            },
            warnings=p["warnings"],
            data_quality=p,
        )

    ps = portfolio.build_series(model, allow_sample=False, allow_simulated=False)
    model_source = _model_source(ps)
    p = _series_data_quality(
        ps.close,
        provenance.portfolio(ps.provenance),
        source=model_source,
        source_counts=ps.sources,
    )
    fc = forecast.forecast(ps.close, horizon=21, n_paths=700)
    sig = signals.evaluate(
        ps.close,
        fc,
        {"aggregate_score": 0, "aggregate_label": "neutral", "count": 0},
        mandate_key=model.mandate_key,
        history_days=ps.n_days,
        data_honesty=ps.provenance,
    )
    st = strategy.analyze_strategy(ps.close)
    metrics = indicators.metrics_summary(ps.close)
    risk = risk_exposure.analyze_model_risk(model)
    warnings = list(ps.warnings) + list(clinic.get("warnings", [])) + list(sig.get("caveats", []))
    if ps.provenance.get("simulated_weight_pct", 0):
        warnings.append(
            f"{ps.provenance['simulated_weight_pct']:.1f}% of analyzed model weight uses simulated prices."
        )
    return _base_report(
        kind="model",
        identity={"id": model.id, "name": model.name, "mandate": model.mandate_key, "source": model_source},
        sections={
            "executive_summary": {
                "headline": f"{model.name}: {sig['action']} model review",
                "summary": sig["headline_rationale"],
            },
            "action": {
                "action": sig["action"],
                "conviction_pct": sig["conviction_pct"],
                "signal_score": sig["score"],
                "rationale": sig["headline_rationale"],
            },
            "evidence": {
                "strategy_beat_benchmark": st["beat_benchmark"],
                "strategy_sharpe": st["strategy"]["sharpe"],
                "forecast_quality": fc.get("quality", {}),
            },
            "risk": {
                "annual_vol_pct": metrics["annual_vol_pct"],
                "max_drawdown_pct": metrics["max_drawdown_pct"],
                "warnings": warnings,
            },
            "client_risk_pack": risk.get("client_risk_pack", {}),
            "forecast": {
                "expected_return_pct": fc["expected_return_pct"],
                "expected_vol_pct": fc["expected_vol_pct"],
                "prob_up": fc["prob_up"],
                "quality": fc.get("quality", {}),
            },
            "strategy": _strategy_summary(st),
            "clinic": {
                "diagnostics": clinic["diagnostics"],
                "suggestions": clinic["suggestions"],
                "refusals": clinic["refusals"],
            },
            "provenance": _model_provenance(ps, p),
            "data_quality": p,
            "assumptions": _assumptions(),
        },
        warnings=warnings,
        data_quality=p,
    )


def _base_report(kind: str, identity: dict, sections: dict, warnings: list[str], data_quality: dict) -> dict:
    title = _title(kind, identity, data_quality)
    return {
        "kind": kind,
        "title": title,
        **identity,
        "data_mode": data_quality.get("data_mode"),
        "display_label": data_quality.get("display_label"),
        "eligible_for_real_research": data_quality.get("eligible_for_real_research", False),
        "data_provenance": data_quality,
        "sections": sections,
        "warnings": _dedupe(warnings),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "disclaimer": DISCLAIMER,
    }


def _instrument_provenance(inst, close, data_quality: dict) -> dict:
    return {
        "source": inst.source,
        "last_refresh": _last_refresh(inst.symbol),
        "eligible_for_real_research": data_quality.get("eligible_for_real_research", False),
        **_series_window(close),
    }


def _model_provenance(ps: portfolio.PortfolioSeries, data_quality: dict) -> dict:
    return {
        **ps.provenance,
        "source": data_quality.get("source"),
        "source_counts": data_quality.get("source_counts", {}),
        "history_days": data_quality.get("history_days"),
        "row_count": data_quality.get("row_count"),
        "first_date": data_quality.get("first_date"),
        "last_date": data_quality.get("last_date"),
        "last_refresh": None,
        "eligible_for_real_research": data_quality.get("eligible_for_real_research", False),
        "binding_ticker": ps.binding_ticker,
    }


def _series_data_quality(close, data_quality: dict, source: str, source_counts: dict) -> dict:
    return {
        **data_quality,
        "source": source,
        "source_counts": dict(source_counts or {}),
        "last_refresh": None,
        **_series_window(close),
    }


def _series_window(close) -> dict:
    clean = close.dropna()
    dates = clean.index
    return {
        "history_days": len(clean),
        "row_count": len(clean),
        "first_date": str(dates.min().date()) if len(dates) else None,
        "last_date": str(dates.max().date()) if len(dates) else None,
    }


def _model_source(ps: portfolio.PortfolioSeries) -> str:
    sources = [source for source, count in sorted((ps.sources or {}).items()) if count]
    return sources[0] if len(sources) == 1 else "mixed" if sources else "unknown"


def _last_refresh(symbol: str) -> dict | None:
    try:
        for row in persistence.get_store().refresh_log(limit=250):
            if row.get("symbol") == symbol:
                return row
    except Exception:
        return None
    return None


def _title(kind: str, identity: dict, data_quality: dict) -> str:
    name = identity.get("name") or identity.get("symbol") or identity.get("id") or kind
    if data_quality.get("data_mode") == "demo":
        return f"Demo Report — Not Real Market Evidence: {name}"
    if data_quality.get("data_mode") == "invalid_for_research":
        return f"Data Quality Blocked — Upload Real History: {name}"
    if data_quality.get("data_mode") == "mixed":
        return f"Mixed Data Warning — Verify Before Use: {name}"
    return f"Advisor Report: {name}"


def _strategy_unavailable(reason: str) -> dict:
    return {
        "available": False,
        "reason": reason or "Need at least 60 rows of history for Strategy Lab analysis.",
        "required_rows": 60,
        "required_action": "Provide at least 60 sessions of price history to unlock strategy evidence.",
    }


def _strategy_summary(st: dict) -> dict:
    return {
        "available": True,
        "beat_benchmark": st["beat_benchmark"],
        "strategy_return_pct": st["strategy"]["total_return_pct"],
        "benchmark_return_pct": st["benchmark"]["total_return_pct"],
        "strategy_sharpe": st["strategy"]["sharpe"],
        "max_drawdown_pct": st["strategy"]["max_drawdown_pct"],
        "trade_stats": st["trade_stats"],
        "methodology": st["methodology"],
        "assumptions": st["assumptions"],
    }


def _assumptions() -> dict:
    return {
        "analysis_only": True,
        "no_execution": True,
        "no_return_guarantee": True,
        "forecast_horizon_days": 21,
        "verify_checklist": [
            "Confirm data source and date range.",
            "Review Strategy Lab after costs and slippage.",
            "Document risks that would invalidate the recommendation.",
            "For models, inspect Portfolio Clinic suggestions and simulated-data caveats.",
        ],
    }

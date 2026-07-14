"""Walk-forward evidence lab for deterministic Helios signals.

The Evidence Lab repeatedly freezes history at prior dates, computes the same
causal trend/momentum signal used by Strategy Lab, then measures subsequent
forward returns against a benchmark. It is analysis-only and never changes the
live signal, forecast, scoring, or trade methodology.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import analytics_cache, costs, data, portfolio, provenance, signal_journal, signals
from ._common import (
    avg as _avg,
    clean_close as _clean_close,
    dedupe as _dedupe,
    finite as _finite,
    hold_preserved as _hold_preserved,
    journal_hold_preserved as _journal_hold_preserved,
    journal_paper_hit as _journal_hit,
    paper_hit as _paper_hit,
    pct as _pct,
)

DEFAULT_HORIZONS = (5, 21, 63)


def analyze_model(
    model: portfolio.Model,
    *,
    horizon_days: int = 21,
    train_window: int = 252,
    step: int = 21,
) -> dict[str, Any]:
    try:
        ps = portfolio.build_series(model, allow_sample=False, allow_simulated=False)
    except ValueError as exc:
        return _blocked_payload(
            kind="model",
            target_id=model.id,
            name=model.name,
            reason=str(exc),
            missing_tickers=[holding.ticker for holding in model.holdings],
            benchmark_symbol=signal_journal.benchmark_for_model(model),
        )
    p = provenance.portfolio(ps.provenance)
    if not p["eligible_for_real_research"]:
        return _blocked_payload(
            kind="model",
            target_id=model.id,
            name=model.name,
            reason=p["reason"],
            missing_tickers=p["missing_tickers"],
            benchmark_symbol=signal_journal.benchmark_for_model(model),
            data_provenance=p,
            warnings=p["warnings"],
        )
    benchmark_symbol = signal_journal.benchmark_for_model(model)
    benchmark = _benchmark_close(benchmark_symbol)
    return analyze_series(
        ps.close,
        target={"kind": "model", "id": model.id, "name": model.name, "mandate": model.mandate_key},
        data_provenance=p,
        benchmark_symbol=benchmark_symbol,
        benchmark_close=benchmark,
        horizon_days=horizon_days,
        train_window=train_window,
        step=step,
        warnings=list(ps.warnings),
    )


def analyze_instrument(
    instrument: data.Instrument,
    *,
    horizon_days: int = 21,
    train_window: int = 252,
    step: int = 21,
    benchmark_symbol: str = "SPY",
) -> dict[str, Any]:
    close = instrument.df["close"].dropna() if "close" in instrument.df else pd.Series(dtype=float)
    p = provenance.instrument(
        instrument.source,
        len(close),
        min_history=max(60, min(train_window, 252)),
        price_provider=getattr(instrument, "price_provider", ""),
    )
    if not p["eligible_for_real_research"]:
        return _blocked_payload(
            kind="instrument",
            target_id=instrument.symbol,
            name=instrument.name,
            reason=p["reason"],
            missing_tickers=[],
            benchmark_symbol=benchmark_symbol,
            data_provenance=p,
            warnings=p["warnings"],
        )
    benchmark = _benchmark_close(benchmark_symbol)
    return analyze_series(
        close,
        target={"kind": "instrument", "id": instrument.symbol, "name": instrument.name, "source": instrument.source},
        data_provenance=p,
        benchmark_symbol=benchmark_symbol,
        benchmark_close=benchmark,
        horizon_days=horizon_days,
        train_window=train_window,
        step=step,
        warnings=list(getattr(instrument, "warnings", [])),
    )


def analyze_series(
    close: pd.Series,
    *,
    target: dict[str, Any],
    data_provenance: dict[str, Any],
    benchmark_symbol: str,
    benchmark_close: pd.Series | None,
    horizon_days: int = 21,
    train_window: int = 252,
    step: int = 21,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    horizon_days = int(np.clip(int(horizon_days or 21), 5, 252))
    train_window = int(np.clip(int(train_window or 252), 90, 756))
    step = int(np.clip(int(step or 21), 5, 63))
    decay_horizons = tuple(sorted({5, horizon_days, 63}))
    max_horizon = max(decay_horizons)
    px = _clean_close(close)
    if len(px) < train_window + max_horizon + 5:
        return _blocked_payload(
            kind=str(target.get("kind") or "unknown"),
            target_id=str(target.get("id") or ""),
            name=str(target.get("name") or ""),
            reason=f"Need at least {train_window + max_horizon + 5} sessions for walk-forward evidence.",
            missing_tickers=[],
            benchmark_symbol=benchmark_symbol,
            data_provenance=data_provenance,
            warnings=list(warnings or []),
        )

    bench = _clean_close(benchmark_close) if benchmark_close is not None else pd.Series(dtype=float)
    benchmark_status = "available" if len(bench) >= train_window else "unavailable"
    cache_key = _walk_cache_key(target, px, benchmark_symbol, bench, horizon_days, train_window, step)
    windows_by_method = analytics_cache.get("evidence_walk", cache_key)
    if not isinstance(windows_by_method, dict) or "rolling" not in windows_by_method:
        # Every signal/regime input is strictly backward-looking, so both are
        # computed once on the full series and sliced per window (see tests).
        score_series = signals.historical_signals(px)
        regime_labels = _regime_labels(bench if len(bench) else px)
        windows_by_method = {
            mode: {
                horizon: _walk_windows(
                    px, bench, horizon, train_window, step, benchmark_status,
                    score_series, regime_labels, window_mode=mode,
                )
                for horizon in decay_horizons
            }
            for mode in ("rolling", "anchored")
        }
        analytics_cache.put("evidence_walk", cache_key, windows_by_method)
    rolling_by_horizon = windows_by_method["rolling"]
    anchored_by_horizon = windows_by_method["anchored"]
    base_windows = rolling_by_horizon[horizon_days]
    anchored_windows = anchored_by_horizon[horizon_days]
    decay = [_decay_row(horizon, rolling_by_horizon[horizon], step) for horizon in decay_horizons]
    out_warnings = list(warnings or [])
    if benchmark_status != "available":
        out_warnings.append(f"Benchmark {benchmark_symbol} needs live or uploaded history for alpha evidence.")

    summary = _summary(base_windows)
    return {
        "target": target,
        "data_mode": data_provenance.get("data_mode"),
        "display_label": data_provenance.get("display_label"),
        "eligible_for_real_research": bool(data_provenance.get("eligible_for_real_research")),
        "reason": data_provenance.get("reason", ""),
        "required_action": data_provenance.get("required_action", ""),
        "data_provenance": data_provenance,
        "benchmark": {"symbol": benchmark_symbol, "status": benchmark_status},
        "parameters": {
            "horizon_days": horizon_days,
            "train_window": train_window,
            "step": step,
            "decay_horizons": list(decay_horizons),
            "validation_methods": ["rolling", "anchored"],
        },
        "summary": summary,
        "validation_methods": {
            "rolling": _validation_method_summary(base_windows, "rolling", train_window),
            "anchored": _validation_method_summary(anchored_windows, "anchored", train_window),
        },
        "false_positives": _false_positive_summary(base_windows),
        "confidence_bands": {
            "forward_result_pct": _bands((row.get("forward_result_pct") for row in base_windows),
                                         overlap_factor=horizon_days / step),
            "alpha_pct": _bands((row.get("alpha_pct") for row in base_windows),
                                overlap_factor=horizon_days / step),
            "hit_rate_pct": _hit_rate_band(base_windows, overlap_factor=horizon_days / step),
        },
        "regime_sensitivity": _regime_sensitivity(base_windows),
        "regime_robustness": _regime_robustness(base_windows),
        "decay": decay,
        "multiplicity": {
            "evaluated_horizons": len(decay_horizons),
            "familywise_alpha": 0.05,
            "bonferroni_alpha": round(0.05 / len(decay_horizons), 6),
            "selection_warning": "Horizon comparisons are a declared family; do not select the best row without multiplicity adjustment.",
        },
        "prospective_validation": _prospective_validation(target),
        "windows": base_windows,
        "warnings": _dedupe(out_warnings),
        "methodology": {
            "analysis_only": True,
            "paper_tracking_only": True,
            "no_lookahead": True,
            "rolling_and_anchored": True,
            "evidence_layers": "Historical walk-forward windows plus prospective Signal Journal measurements when available.",
            "signal_basis": "Existing causal Helios historical signal: trend and momentum only.",
            # Short machine label the UI puts NEXT TO the headline stats, not
            # buried in this footer (review finding: the walk-forward proxy is
            # 2 of the live rating's 5 components; sentiment and fundamentals
            # cannot be backtested without look-ahead fabrication).
            "signal_basis_short": "trend+momentum proxy — not the live composite rating",
            "forward_measurement": "Signal is frozen at the close; forward return is measured over later sessions.",
            "alpha_basis": (f"Forward return minus {benchmark_symbol} return when benchmark "
                            f"history is available — {costs.GROSS_LABEL}."),
            "confidence_bands": ("Percentiles over all measured windows; the 90% mean CI uses "
                                 "effective N = windows / (horizon/step) because windows overlap "
                                 "when the horizon exceeds the step."),
        },
        "disclaimer": "Analysis only. Evidence Lab combines historical walk-forward paper evidence with prospective Signal Journal tracking when available; it does not execute trades or guarantee outcomes.",
    }


def _blocked_payload(
    *,
    kind: str,
    target_id: str,
    name: str,
    reason: str,
    missing_tickers: list[str],
    benchmark_symbol: str,
    data_provenance: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    p = data_provenance or {
        "data_mode": "invalid_for_research",
        "display_label": "Data Quality Blocked",
        "eligible_for_real_research": False,
        "reason": reason,
        "required_action": "Upload real price history before running walk-forward evidence.",
        "missing_tickers": missing_tickers,
        "warnings": [],
    }
    return {
        "target": {"kind": kind, "id": target_id, "name": name},
        "data_mode": p.get("data_mode"),
        "display_label": p.get("display_label"),
        "eligible_for_real_research": False,
        "reason": reason,
        "required_action": "Upload real price history before running walk-forward evidence.",
        "missing_tickers": missing_tickers,
        "evidence_unavailable": True,
        "data_provenance": p,
        "benchmark": {"symbol": benchmark_symbol, "status": "blocked"},
        "parameters": {},
        "summary": _empty_summary(),
        "validation_methods": {
            "rolling": _validation_method_summary([], "rolling", 0),
            "anchored": _validation_method_summary([], "anchored", 0),
        },
        "false_positives": {"count": 0, "rate_pct": None, "basis": "No measured walk-forward windows."},
        "confidence_bands": {"forward_result_pct": {}, "alpha_pct": {}, "hit_rate_pct": {}},
        "regime_sensitivity": [],
        "regime_robustness": {"status": "unavailable", "passed": False, "covered_regimes": 0, "required_regimes": 3},
        "decay": [],
        "multiplicity": {"evaluated_horizons": 0, "familywise_alpha": 0.05, "bonferroni_alpha": None},
        "prospective_validation": _empty_prospective_validation("blocked", "Prospective tracking waits for eligible real-data evidence."),
        "windows": [],
        "warnings": _dedupe(list(warnings or []) + [reason]),
        "methodology": {"analysis_only": True, "paper_tracking_only": True, "no_lookahead": True},
        "disclaimer": "Analysis only. Walk-forward evidence requires eligible real price history.",
    }


def _prospective_validation(target: dict[str, Any]) -> dict[str, Any]:
    target_kind = str(target.get("kind") or "")
    target_id = str(target.get("id") or "")
    if not target_kind or not target_id:
        return _empty_prospective_validation("not_started", "No target is selected for prospective tracking.")
    try:
        entries = [
            entry for entry in signal_journal.list_entries(limit=500)
            if str(entry.get("target_kind") or "") == target_kind and str(entry.get("target_id") or "") == target_id
        ]
    except Exception as exc:
        return _empty_prospective_validation("unavailable", f"Signal Journal unavailable: {exc}")
    if not entries:
        return _empty_prospective_validation(
            "not_started",
            "Run deterministic analysis over time to create prospective Signal Journal evidence for this target.",
        )

    measured = [entry for entry in entries if entry.get("forward_status") == "measured"]
    pending = [entry for entry in entries if entry.get("forward_status") != "measured"]
    hit_flags = [_journal_hit(entry) for entry in measured]
    hit_flags = [flag for flag in hit_flags if flag is not None]
    hold_flags = [_journal_hold_preserved(entry) for entry in measured]
    hold_flags = [flag for flag in hold_flags if flag is not None]
    directional_alphas = [
        costs.directional_alpha(entry.get("action_label"), entry.get("alpha_pct"))
        for entry in measured
    ]
    status = "active"
    if measured and not pending:
        status = "measured"
    elif pending and not measured:
        status = "measuring"
    ordered = sorted(entries, key=lambda entry: (str(entry.get("input_end_date") or ""), str(entry.get("created_at") or "")), reverse=True)
    return {
        "status": status,
        "basis": "Same-target prospective Signal Journal entries. Measured rows resolve only after later local/live history covers the original horizon.",
        "total_count": len(entries),
        "outcome_count": len(measured),
        "measured_count": len(hit_flags),
        "pending_count": len(pending),
        "hit_count": sum(1 for flag in hit_flags if flag),
        "hit_rate_pct": _pct(sum(1 for flag in hit_flags if flag), len(hit_flags)),
        "hold_measured_count": len(hold_flags),
        "hold_preserved_count": sum(1 for flag in hold_flags if flag),
        "hold_preservation_rate_pct": _pct(sum(1 for flag in hold_flags if flag), len(hold_flags)),
        "avg_score": _avg(entry.get("score") for entry in entries),
        "avg_forward_result_pct": _avg(entry.get("forward_result_pct") for entry in measured),
        "avg_benchmark_result_pct": _avg(entry.get("benchmark_result_pct") for entry in measured),
        "avg_alpha_pct": _avg(directional_alphas),
        "avg_alpha_after_default_costs_pct": costs.net_of_default_costs(_avg(directional_alphas)),
        "alpha_cost_basis": costs.NET_ASSUMPTION_LABEL,
        "benchmark_comparison": signal_journal.benchmark_comparison(entries),
        "latest_entries": [_compact_journal_entry(entry) for entry in ordered[:10]],
        "caveat": "Prospective paper tracking only; no orders, brokerage execution, return guarantee, or investment advice.",
    }


def _empty_prospective_validation(status: str, basis: str) -> dict[str, Any]:
    return {
        "status": status,
        "basis": basis,
        "total_count": 0,
        "measured_count": 0,
        "outcome_count": 0,
        "pending_count": 0,
        "hit_count": 0,
        "hit_rate_pct": None,
        "hold_measured_count": 0,
        "hold_preserved_count": 0,
        "hold_preservation_rate_pct": None,
        "avg_score": None,
        "avg_forward_result_pct": None,
        "avg_benchmark_result_pct": None,
        "avg_alpha_pct": None,
        "avg_alpha_after_default_costs_pct": None,
        "benchmark_comparison": [],
        "latest_entries": [],
        "caveat": "Prospective paper tracking only; no orders, brokerage execution, return guarantee, or investment advice.",
    }


def _compact_journal_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry.get("id"),
        "created_at": entry.get("created_at"),
        "target_kind": entry.get("target_kind"),
        "target_id": entry.get("target_id"),
        "target_name": entry.get("target_name"),
        "benchmark": entry.get("benchmark"),
        "input_start_date": entry.get("input_start_date"),
        "input_end_date": entry.get("input_end_date"),
        "input_rows": entry.get("input_rows"),
        "horizon_days": entry.get("horizon_days"),
        "score": entry.get("score"),
        "action_label": entry.get("action_label"),
        "forward_status": entry.get("forward_status"),
        "forward_end_date": entry.get("forward_end_date"),
        "forward_result_pct": entry.get("forward_result_pct"),
        "benchmark_result_pct": entry.get("benchmark_result_pct"),
        "alpha_pct": entry.get("alpha_pct"),
        "paper_hit": _journal_hit(entry),
        "hold_preserved": _journal_hold_preserved(entry),
    }


def _walk_cache_key(
    target: dict[str, Any],
    close: pd.Series,
    benchmark_symbol: str,
    benchmark: pd.Series,
    horizon_days: int,
    train_window: int,
    step: int,
) -> tuple | None:
    base = analytics_cache.series_key(f"{target.get('kind')}:{target.get('id')}", close)
    if base is None:
        return None
    bench_key = analytics_cache.series_key(str(benchmark_symbol), benchmark) or (str(benchmark_symbol), "", 0)
    return base + bench_key + ("dual-oos-v1", int(horizon_days), int(train_window), int(step))


def _walk_windows(
    close: pd.Series,
    benchmark: pd.Series,
    horizon: int,
    train_window: int,
    step: int,
    benchmark_status: str,
    score_series: pd.Series,
    regime_labels: pd.Series,
    window_mode: str = "rolling",
) -> list[dict[str, Any]]:
    """Walk-forward windows sliced from precomputed causal series.

    `score_series` and `regime_labels` are computed once on the full history;
    because every underlying input (rolling means, EMAs, RSI, drawdown) is
    strictly backward-looking, slicing them at the signal date is exactly
    equivalent to recomputing on each frozen history prefix (asserted in tests).
    """
    rows = []
    if len(close) < train_window + horizon + 1:
        return rows
    max_signal_index = len(close) - horizon - 1
    for signal_index in range(train_window - 1, max_signal_index + 1, step):
        input_start_index = 0 if window_mode == "anchored" else max(0, signal_index - train_window + 1)
        input_start_date = close.index[input_start_index].strftime("%Y-%m-%d")
        signal_date = close.index[signal_index]
        forward_date = close.index[signal_index + horizon]
        signal_score = float(score_series.iloc[signal_index])
        action = _action(signal_score)
        start_price = float(close.iloc[signal_index])
        end_price = float(close.iloc[signal_index + horizon])
        forward = (end_price / start_price - 1.0) * 100 if start_price else None
        benchmark_result = _benchmark_return(benchmark, signal_date, forward_date) if benchmark_status == "available" else None
        alpha = round(float(forward - benchmark_result), 4) if forward is not None and benchmark_result is not None else None
        hit = _paper_hit(action, forward, alpha)
        preserved = _hold_preserved(action, forward)
        row = {
            "signal_date": signal_date.strftime("%Y-%m-%d"),
            "forward_end_date": forward_date.strftime("%Y-%m-%d"),
            "input_start_date": input_start_date,
            "input_rows": int(signal_index - input_start_index + 1),
            "validation_method": window_mode,
            "horizon_days": int(horizon),
            "signal_score": round(signal_score, 4),
            "action_label": action,
            "forward_result_pct": round(float(forward), 4) if forward is not None else None,
            "benchmark_result_pct": round(float(benchmark_result), 4) if benchmark_result is not None else None,
            "alpha_pct": alpha,
            "paper_hit": hit,
            "hold_preserved": preserved,
            "false_positive": bool(action in {"BUY", "SELL"} and hit is False),
            "regime": _label_on_or_before(regime_labels, signal_date),
        }
        rows.append(row)
    return rows


def _validation_method_summary(rows: list[dict[str, Any]], mode: str, train_window: int) -> dict[str, Any]:
    summary = _summary(rows)
    return {
        "mode": mode,
        "training_policy": (
            f"Fixed trailing {train_window}-session information set before every signal."
            if mode == "rolling" and train_window
            else "Expanding information set anchored at the first available observation."
        ),
        "summary": summary,
        "confidence_bands": {
            "alpha_pct": _bands((row.get("alpha_pct") for row in rows)),
            "hit_rate_pct": _hit_rate_band(rows),
        },
        "regime_sensitivity": _regime_sensitivity(rows),
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    hit_flags = [row["paper_hit"] for row in rows if row.get("paper_hit") is not None]
    hold_flags = [row["hold_preserved"] for row in rows if row.get("hold_preserved") is not None]
    directional = [row for row in rows if row.get("action_label") in {"BUY", "SELL"}]
    directional_alphas = [
        costs.directional_alpha(row.get("action_label"), row.get("alpha_pct"))
        for row in directional
    ]
    alpha_count = sum(1 for row in directional if _finite(row.get("alpha_pct")) is not None)
    return {
        **_empty_summary(),
        "window_count": len(rows),
        "outcome_count": len(rows),
        "directional_signal_count": len(directional),
        "measured_count": len(hit_flags),
        "benchmark_coverage_pct": _pct(alpha_count, len(directional)),
        "hit_count": sum(1 for flag in hit_flags if flag),
        "hit_rate_pct": _pct(sum(1 for flag in hit_flags if flag), len(hit_flags)),
        "hold_count": sum(1 for row in rows if row.get("action_label") == "HOLD"),
        "hold_measured_count": len(hold_flags),
        "hold_preserved_count": sum(1 for flag in hold_flags if flag),
        "hold_preservation_rate_pct": _pct(sum(1 for flag in hold_flags if flag), len(hold_flags)),
        "avg_score": _avg(row.get("signal_score") for row in rows),
        "avg_forward_result_pct": _avg(row.get("forward_result_pct") for row in rows),
        "avg_benchmark_result_pct": _avg(row.get("benchmark_result_pct") for row in rows),
        "avg_alpha_pct": _avg(directional_alphas),
        # Presentation-level companion: stored windows stay GROSS (a paper
        # signal incurs no costs); the unlabeled word "alpha" was the
        # dishonesty (review finding).
        "avg_alpha_after_default_costs_pct": costs.net_of_default_costs(
            _avg(directional_alphas)),
        "alpha_cost_basis": costs.NET_ASSUMPTION_LABEL,
        "positive_alpha_rate_pct": _pct(
            sum(1 for value in directional_alphas if value is not None and value > 0),
            sum(1 for value in directional_alphas if value is not None),
        ),
        "first_signal_date": rows[0]["signal_date"] if rows else None,
        "last_signal_date": rows[-1]["signal_date"] if rows else None,
    }


def _empty_summary() -> dict[str, Any]:
    return {
        "window_count": 0,
        "outcome_count": 0,
        "directional_signal_count": 0,
        "measured_count": 0,
        "benchmark_coverage_pct": None,
        "hit_count": 0,
        "hit_rate_pct": None,
        "hold_count": 0,
        "hold_measured_count": 0,
        "hold_preserved_count": 0,
        "hold_preservation_rate_pct": None,
        "avg_score": None,
        "avg_forward_result_pct": None,
        "avg_benchmark_result_pct": None,
        "avg_alpha_pct": None,
        "avg_alpha_after_default_costs_pct": None,
        "alpha_cost_basis": costs.NET_ASSUMPTION_LABEL,
        "positive_alpha_rate_pct": None,
        "first_signal_date": None,
        "last_signal_date": None,
    }


def _false_positive_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    directional = [row for row in rows if row.get("action_label") in {"BUY", "SELL"}]
    measured = [row for row in directional if row.get("paper_hit") is not None]
    count = sum(1 for row in measured if row.get("false_positive"))
    return {
        "count": count,
        "rate_pct": _pct(count, len(measured)),
        "directional_signal_count": len(directional),
        "measured_directional_count": len(measured),
        "basis": "BUY/SELL windows with benchmark alpha that failed the paper-hit rule.",
    }


def _regime_sensitivity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for label in ("risk-on", "neutral", "risk-off"):
        group = [row for row in rows if row.get("regime") == label]
        if not group:
            continue
        fp = _false_positive_summary(group)
        hits = [row["paper_hit"] for row in group if row.get("paper_hit") is not None]
        holds = [row["hold_preserved"] for row in group if row.get("hold_preserved") is not None]
        directional_alphas = [
            costs.directional_alpha(row.get("action_label"), row.get("alpha_pct"))
            for row in group
        ]
        out.append({
            "regime": label,
            "count": len(group),
            "hit_rate_pct": _pct(sum(1 for flag in hits if flag), len(hits)),
            "hold_preservation_rate_pct": _pct(sum(1 for flag in holds if flag), len(holds)),
            "avg_alpha_pct": _avg(directional_alphas),
            "avg_alpha_after_default_costs_pct": costs.net_of_default_costs(
                _avg(directional_alphas)),
            "avg_forward_result_pct": _avg(row.get("forward_result_pct") for row in group),
            "false_positive_rate_pct": fp["rate_pct"],
        })
    return out


def _regime_robustness(rows: list[dict[str, Any]], min_windows_per_regime: int = 3) -> dict[str, Any]:
    sensitivity = _regime_sensitivity(rows)
    adequate = [row for row in sensitivity if int(row.get("count") or 0) >= min_windows_per_regime]
    required = ("risk-on", "neutral", "risk-off")
    covered = {str(row.get("regime")) for row in adequate}
    net_alpha = [
        float(row["avg_alpha_after_default_costs_pct"])
        for row in adequate if _finite(row.get("avg_alpha_after_default_costs_pct")) is not None
    ]
    false_positive_rates = [
        float(row["false_positive_rate_pct"])
        for row in adequate if _finite(row.get("false_positive_rate_pct")) is not None
    ]
    coverage_passed = set(required) <= covered
    performance_consistent = bool(net_alpha) and min(net_alpha) >= 0.0
    false_positive_control = bool(false_positive_rates) and max(false_positive_rates) <= 50.0
    passed = coverage_passed and performance_consistent and false_positive_control
    return {
        "status": "passed" if passed else "failed" if coverage_passed else "insufficient_coverage",
        "passed": passed,
        "coverage_passed": coverage_passed,
        "performance_consistent": performance_consistent,
        "false_positive_control": false_positive_control,
        "covered_regimes": len(covered),
        "required_regimes": len(required),
        "min_windows_per_regime": min_windows_per_regime,
        "worst_net_alpha_pct": round(min(net_alpha), 4) if net_alpha else None,
        "worst_false_positive_rate_pct": round(max(false_positive_rates), 4) if false_positive_rates else None,
        "basis": "Every declared price regime needs enough windows, non-negative alpha after default implementation costs, and no more than 50% directional false positives.",
    }


def _decay_row(horizon: int, rows: list[dict[str, Any]], step: int = 21) -> dict[str, Any]:
    summary = _summary(rows)
    values_x = [_finite(row.get("signal_score")) for row in rows]
    values_y = [_finite(row.get("alpha_pct")) for row in rows]
    paired = [(x, y) for x, y in zip(values_x, values_y) if x is not None and y is not None]
    ic = None
    if len(paired) >= 3:
        xs = np.array([x for x, _ in paired], dtype=float)
        ys = np.array([y for _, y in paired], dtype=float)
        if float(xs.std()) > 1e-12 and float(ys.std()) > 1e-12:
            ic = round(float(np.corrcoef(xs, ys)[0, 1]), 4)
    return {
        "horizon_days": horizon,
        "measured_count": summary["measured_count"],
        "hit_rate_pct": summary["hit_rate_pct"],
        "avg_forward_result_pct": summary["avg_forward_result_pct"],
        "avg_alpha_pct": summary["avg_alpha_pct"],
        "avg_alpha_after_default_costs_pct": summary.get("avg_alpha_after_default_costs_pct"),
        "false_positive_rate_pct": _false_positive_summary(rows)["rate_pct"],
        "information_coefficient": ic,
        "confidence_bands": _bands((
            costs.directional_alpha(row.get("action_label"), row.get("alpha_pct"))
            for row in rows
        ),
                                   overlap_factor=horizon / step),
    }


def _bands(values, overlap_factor: float = 1.0) -> dict[str, Any]:
    """Percentiles on the FULL sample; the CI uses effective N = windows /
    overlap_factor. Windows overlap whenever horizon > step (e.g. the 63-day
    decay row at step 21 shares 2/3 of each window with its neighbor), and
    treating them as independent draws narrowed the bands by ~sqrt(overlap)
    (review finding — first-order Hansen–Hodrick scaling restores coverage)."""
    clean = [float(value) for value in values if _finite(value) is not None]
    if not clean:
        return {"count": 0, "mean": None, "p05": None, "p25": None, "p50": None, "p75": None, "p95": None, "ci90_low": None, "ci90_high": None}
    arr = np.array(clean, dtype=float)
    mean = float(arr.mean())
    m = max(1.0, float(overlap_factor))
    n_eff = max(1.0, len(arr) / m)
    se = float(arr.std(ddof=1) / np.sqrt(n_eff)) if len(arr) > 1 else 0.0
    return {
        "count": int(len(arr)),
        "n_eff": round(n_eff, 1),
        "overlap_factor": round(m, 2),
        "mean": round(mean, 4),
        "p05": round(float(np.percentile(arr, 5)), 4),
        "p25": round(float(np.percentile(arr, 25)), 4),
        "p50": round(float(np.percentile(arr, 50)), 4),
        "p75": round(float(np.percentile(arr, 75)), 4),
        "p95": round(float(np.percentile(arr, 95)), 4),
        "ci90_low": round(mean - 1.645 * se, 4),
        "ci90_high": round(mean + 1.645 * se, 4),
    }


def _hit_rate_band(rows: list[dict[str, Any]], overlap_factor: float = 1.0) -> dict[str, Any]:
    flags = [row.get("paper_hit") for row in rows if row.get("paper_hit") is not None]
    if not flags:
        return {"count": 0, "mean": None, "ci90_low": None, "ci90_high": None}
    p = sum(1 for flag in flags if flag) / len(flags)
    m = max(1.0, float(overlap_factor))
    n_eff = max(1.0, len(flags) / m)
    se = np.sqrt(max(p * (1 - p), 0.0) / n_eff)
    return {
        "count": len(flags),
        "n_eff": round(n_eff, 1),
        "overlap_factor": round(m, 2),
        "mean": round(p * 100, 2),
        "ci90_low": round(max(0.0, (p - 1.645 * se) * 100), 2),
        "ci90_high": round(min(100.0, (p + 1.645 * se) * 100), 2),
    }


def _action(score: float) -> str:
    if score > 0.15:
        return "BUY"
    if score < -0.15:
        return "SELL"
    return "HOLD"


def _regime_labels(close: pd.Series) -> pd.Series:
    """Causal per-session regime labels for a clean price series.

    Vectorizes the label decision of `regime.classify_regime`: every driver
    (SMA50/200, 21/63-day returns, 63-day realized vol, drawdown from the
    running high) is strictly backward-looking, so the label at session i
    matches `classify_regime(close.iloc[:i + 1])["label"]` (asserted in tests).
    """
    px = pd.Series(close).dropna().astype(float)
    n = len(px)
    if n == 0:
        return pd.Series(dtype=object)
    last = px.to_numpy(dtype=float)
    sma50 = px.rolling(50).mean().to_numpy(dtype=float)
    sma200 = px.rolling(200).mean().to_numpy(dtype=float)
    ret21 = _trailing_return_pct(px, 21)
    ret63 = _trailing_return_pct(px, 63)
    returns = px.to_numpy(dtype=float)[1:] / px.to_numpy(dtype=float)[:-1] - 1.0
    vol = np.full(n, np.nan)
    if n > 1:
        vol[1:] = pd.Series(returns).rolling(63, min_periods=1).std().to_numpy() * np.sqrt(252) * 100
    drawdown = (last / px.cummax().to_numpy(dtype=float) - 1.0) * 100

    score = np.full(n, 50.0)
    fin200 = np.isfinite(sma200)
    score += np.where(fin200, np.where(last >= sma200, 16.0, -18.0), -4.0)
    cross = np.isfinite(sma50) & fin200
    score += np.where(cross, np.where(sma50 >= sma200, 14.0, -16.0), 0.0)
    score += np.where(ret63 >= 0, 11.0, -13.0)
    score += np.where(ret21 >= 0, 7.0, -8.0)
    score += np.where(vol <= 18, 6.0, 0.0) + np.where(vol >= 30, -9.0, 0.0)
    score += np.where(drawdown >= -5, 6.0, np.where(drawdown <= -15, -11.0, -2.0))
    final = np.clip(score, 0.0, 100.0)
    labels = np.where(final >= 65, "risk-on", np.where(final <= 35, "risk-off", "neutral"))
    labels[: min(59, n)] = "neutral"  # classify_regime needs >= 60 sessions
    return pd.Series(labels, index=px.index)


def _trailing_return_pct(px: pd.Series, periods: int) -> np.ndarray:
    """Vectorized equivalent of regime._return_pct at every session."""
    start = px.shift(periods)
    ret = (px / start - 1.0) * 100.0
    return ret.where(start.notna() & (start != 0), 0.0).to_numpy(dtype=float)


def _label_on_or_before(labels: pd.Series, date: pd.Timestamp) -> str:
    upto = labels.loc[:date]
    return str(upto.iloc[-1]) if len(upto) else "neutral"


def _benchmark_return(benchmark: pd.Series, start_date: pd.Timestamp, end_date: pd.Timestamp) -> float | None:
    """Benchmark return over the TARGET's exact calendar window.

    Both endpoints are the last bar at/before the target's window dates — the
    old first-bar-at/AFTER end lookup measured the benchmark over a strictly
    wider span whenever the target trades a calendar the benchmark doesn't
    (crypto weeks, holidays, sparse uploads), corrupting alpha (review
    finding). Same semantics as decision_journal._return_over_span. A window
    the benchmark doesn't cover yet stays unresolved (None), never truncated."""
    if benchmark.empty or benchmark.index.max() < end_date:
        return None
    start = _value_on_or_before(benchmark, start_date)
    end = _value_on_or_before(benchmark, end_date)
    if start is None or end is None or start <= 0:
        return None
    return (end / start - 1.0) * 100


def _value_on_or_before(series: pd.Series, date: pd.Timestamp) -> float | None:
    values = series.loc[:date]
    return float(values.iloc[-1]) if len(values) else None


def _benchmark_close(symbol: str) -> pd.Series | None:
    inst = data.get(symbol)
    if inst is None or not provenance.is_real_source(inst.source) or "close" not in inst.df:
        return None
    return inst.df["close"]

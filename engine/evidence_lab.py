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

from . import data, portfolio, provenance, regime, signal_journal, signals

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
    p = provenance.instrument(instrument.source, len(close), min_history=max(60, min(train_window, 252)))
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
    base_windows = _walk_windows(px, bench, horizon_days, train_window, step, benchmark_status)
    decay = [
        _decay_row(horizon, _walk_windows(px, bench, horizon, train_window, step, benchmark_status))
        for horizon in decay_horizons
    ]
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
        },
        "summary": summary,
        "false_positives": _false_positive_summary(base_windows),
        "confidence_bands": {
            "forward_result_pct": _bands(row.get("forward_result_pct") for row in base_windows),
            "alpha_pct": _bands(row.get("alpha_pct") for row in base_windows),
            "hit_rate_pct": _hit_rate_band(base_windows),
        },
        "regime_sensitivity": _regime_sensitivity(base_windows),
        "decay": decay,
        "prospective_validation": _prospective_validation(target),
        "windows": base_windows,
        "warnings": _dedupe(out_warnings),
        "methodology": {
            "analysis_only": True,
            "paper_tracking_only": True,
            "no_lookahead": True,
            "evidence_layers": "Historical walk-forward windows plus prospective Signal Journal measurements when available.",
            "signal_basis": "Existing causal Helios historical signal: trend and momentum only.",
            "forward_measurement": "Signal is frozen at the close; forward return is measured over later sessions.",
            "alpha_basis": f"Forward return minus {benchmark_symbol} return when benchmark history is available.",
            "confidence_bands": "Percentiles and normal-approximation 90% mean confidence interval over measured walk-forward windows.",
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
        "false_positives": {"count": 0, "rate_pct": None, "basis": "No measured walk-forward windows."},
        "confidence_bands": {"forward_result_pct": {}, "alpha_pct": {}, "hit_rate_pct": {}},
        "regime_sensitivity": [],
        "decay": [],
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
        "measured_count": len(measured),
        "pending_count": len(pending),
        "hit_count": sum(1 for flag in hit_flags if flag),
        "hit_rate_pct": _pct(sum(1 for flag in hit_flags if flag), len(hit_flags)),
        "avg_score": _avg(entry.get("score") for entry in entries),
        "avg_forward_result_pct": _avg(entry.get("forward_result_pct") for entry in measured),
        "avg_benchmark_result_pct": _avg(entry.get("benchmark_result_pct") for entry in measured),
        "avg_alpha_pct": _avg(entry.get("alpha_pct") for entry in measured),
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
        "pending_count": 0,
        "hit_count": 0,
        "hit_rate_pct": None,
        "avg_score": None,
        "avg_forward_result_pct": None,
        "avg_benchmark_result_pct": None,
        "avg_alpha_pct": None,
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
    }


def _journal_hit(entry: dict[str, Any]) -> bool | None:
    return _paper_hit(
        str(entry.get("action_label") or "HOLD").upper(),
        _finite(entry.get("forward_result_pct")),
        _finite(entry.get("alpha_pct")),
    )


def _walk_windows(
    close: pd.Series,
    benchmark: pd.Series,
    horizon: int,
    train_window: int,
    step: int,
    benchmark_status: str,
) -> list[dict[str, Any]]:
    rows = []
    if len(close) < train_window + horizon + 1:
        return rows
    max_signal_index = len(close) - horizon - 1
    for signal_index in range(train_window - 1, max_signal_index + 1, step):
        history = close.iloc[: signal_index + 1]
        signal_date = close.index[signal_index]
        forward_date = close.index[signal_index + horizon]
        signal_score = float(signals.historical_signals(history).iloc[-1])
        action = _action(signal_score)
        start_price = float(close.iloc[signal_index])
        end_price = float(close.iloc[signal_index + horizon])
        forward = (end_price / start_price - 1.0) * 100 if start_price else None
        benchmark_result = _benchmark_return(benchmark, signal_date, forward_date) if benchmark_status == "available" else None
        alpha = round(float(forward - benchmark_result), 4) if forward is not None and benchmark_result is not None else None
        hit = _paper_hit(action, forward, alpha)
        row = {
            "signal_date": signal_date.strftime("%Y-%m-%d"),
            "forward_end_date": forward_date.strftime("%Y-%m-%d"),
            "input_start_date": history.index[0].strftime("%Y-%m-%d"),
            "input_rows": int(len(history)),
            "horizon_days": int(horizon),
            "signal_score": round(signal_score, 4),
            "action_label": action,
            "forward_result_pct": round(float(forward), 4) if forward is not None else None,
            "benchmark_result_pct": round(float(benchmark_result), 4) if benchmark_result is not None else None,
            "alpha_pct": alpha,
            "paper_hit": hit,
            "false_positive": bool(action in {"BUY", "SELL"} and hit is False),
            "regime": _regime_at(benchmark if len(benchmark) else close, signal_date),
        }
        rows.append(row)
    return rows


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    hit_flags = [row["paper_hit"] for row in rows if row.get("paper_hit") is not None]
    return {
        **_empty_summary(),
        "window_count": len(rows),
        "measured_count": len(rows),
        "hit_count": sum(1 for flag in hit_flags if flag),
        "hit_rate_pct": _pct(sum(1 for flag in hit_flags if flag), len(hit_flags)),
        "avg_score": _avg(row.get("signal_score") for row in rows),
        "avg_forward_result_pct": _avg(row.get("forward_result_pct") for row in rows),
        "avg_benchmark_result_pct": _avg(row.get("benchmark_result_pct") for row in rows),
        "avg_alpha_pct": _avg(row.get("alpha_pct") for row in rows),
        "positive_alpha_rate_pct": _pct(sum(1 for row in rows if _finite(row.get("alpha_pct")) is not None and row["alpha_pct"] > 0), sum(1 for row in rows if _finite(row.get("alpha_pct")) is not None)),
        "first_signal_date": rows[0]["signal_date"] if rows else None,
        "last_signal_date": rows[-1]["signal_date"] if rows else None,
    }


def _empty_summary() -> dict[str, Any]:
    return {
        "window_count": 0,
        "measured_count": 0,
        "hit_count": 0,
        "hit_rate_pct": None,
        "avg_score": None,
        "avg_forward_result_pct": None,
        "avg_benchmark_result_pct": None,
        "avg_alpha_pct": None,
        "positive_alpha_rate_pct": None,
        "first_signal_date": None,
        "last_signal_date": None,
    }


def _false_positive_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    directional = [row for row in rows if row.get("action_label") in {"BUY", "SELL"}]
    count = sum(1 for row in directional if row.get("false_positive"))
    return {
        "count": count,
        "rate_pct": _pct(count, len(directional)),
        "directional_signal_count": len(directional),
        "basis": "BUY/SELL windows that failed the paper-hit rule.",
    }


def _regime_sensitivity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for label in ("risk-on", "neutral", "risk-off"):
        group = [row for row in rows if row.get("regime") == label]
        if not group:
            continue
        fp = _false_positive_summary(group)
        hits = [row["paper_hit"] for row in group if row.get("paper_hit") is not None]
        out.append({
            "regime": label,
            "count": len(group),
            "hit_rate_pct": _pct(sum(1 for flag in hits if flag), len(hits)),
            "avg_alpha_pct": _avg(row.get("alpha_pct") for row in group),
            "avg_forward_result_pct": _avg(row.get("forward_result_pct") for row in group),
            "false_positive_rate_pct": fp["rate_pct"],
        })
    return out


def _decay_row(horizon: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
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
        "false_positive_rate_pct": _false_positive_summary(rows)["rate_pct"],
        "information_coefficient": ic,
        "confidence_bands": _bands(row.get("alpha_pct") for row in rows),
    }


def _bands(values) -> dict[str, Any]:
    clean = [float(value) for value in values if _finite(value) is not None]
    if not clean:
        return {"count": 0, "mean": None, "p05": None, "p25": None, "p50": None, "p75": None, "p95": None, "ci90_low": None, "ci90_high": None}
    arr = np.array(clean, dtype=float)
    mean = float(arr.mean())
    se = float(arr.std(ddof=1) / np.sqrt(len(arr))) if len(arr) > 1 else 0.0
    return {
        "count": int(len(arr)),
        "mean": round(mean, 4),
        "p05": round(float(np.percentile(arr, 5)), 4),
        "p25": round(float(np.percentile(arr, 25)), 4),
        "p50": round(float(np.percentile(arr, 50)), 4),
        "p75": round(float(np.percentile(arr, 75)), 4),
        "p95": round(float(np.percentile(arr, 95)), 4),
        "ci90_low": round(mean - 1.645 * se, 4),
        "ci90_high": round(mean + 1.645 * se, 4),
    }


def _hit_rate_band(rows: list[dict[str, Any]]) -> dict[str, Any]:
    flags = [row.get("paper_hit") for row in rows if row.get("paper_hit") is not None]
    if not flags:
        return {"count": 0, "mean": None, "ci90_low": None, "ci90_high": None}
    p = sum(1 for flag in flags if flag) / len(flags)
    se = np.sqrt(max(p * (1 - p), 0.0) / len(flags))
    return {
        "count": len(flags),
        "mean": round(p * 100, 2),
        "ci90_low": round(max(0.0, (p - 1.645 * se) * 100), 2),
        "ci90_high": round(min(100.0, (p + 1.645 * se) * 100), 2),
    }


def _paper_hit(action: str, forward: float | None, alpha: float | None) -> bool | None:
    if forward is None:
        return None
    evidence = alpha if alpha is not None else forward
    if action == "BUY":
        return evidence > 0
    if action == "SELL":
        return evidence < 0
    return evidence >= -1.0


def _action(score: float) -> str:
    if score > 0.15:
        return "BUY"
    if score < -0.15:
        return "SELL"
    return "HOLD"


def _regime_at(close: pd.Series, signal_date: pd.Timestamp) -> str:
    hist = close.loc[:signal_date]
    return str(regime.classify_regime(hist).get("label") or "neutral")


def _benchmark_return(benchmark: pd.Series, start_date: pd.Timestamp, end_date: pd.Timestamp) -> float | None:
    start = _value_on_or_before(benchmark, start_date)
    end = _value_on_or_after(benchmark, end_date)
    if start is None or end is None or start == 0:
        return None
    return (end / start - 1.0) * 100


def _value_on_or_before(series: pd.Series, date: pd.Timestamp) -> float | None:
    values = series.loc[:date]
    return float(values.iloc[-1]) if len(values) else None


def _value_on_or_after(series: pd.Series, date: pd.Timestamp) -> float | None:
    values = series.loc[date:]
    return float(values.iloc[0]) if len(values) else None


def _benchmark_close(symbol: str) -> pd.Series | None:
    inst = data.get(symbol)
    if inst is None or not provenance.is_real_source(inst.source) or "close" not in inst.df:
        return None
    return inst.df["close"]


def _clean_close(close: pd.Series | None) -> pd.Series:
    if close is None:
        return pd.Series(dtype=float)
    out = pd.Series(close).dropna().copy()
    out.index = pd.to_datetime(out.index, errors="coerce")
    out = out[~out.index.isna()]
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    out.index = out.index.normalize()
    return pd.to_numeric(out.sort_index(), errors="coerce").dropna()


def _avg(values) -> float | None:
    clean = [float(value) for value in values if _finite(value) is not None]
    return round(float(np.mean(clean)), 4) if clean else None


def _pct(num: int, den: int) -> float | None:
    return round(float(num) / den * 100, 2) if den else None


def _finite(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and np.isfinite(value) else None


def _dedupe(items: list[str]) -> list[str]:
    out = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out

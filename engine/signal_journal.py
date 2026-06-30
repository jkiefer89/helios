"""Signal journal and paper-performance tracking.

The journal records deterministic Helios signal outputs with the exact input
window used at the time. It never stores raw price histories; it stores compact
audit facts plus a pending/measured forward result when later data exists.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from . import data, persistence, portfolio

MODEL_BENCHMARKS = {
    "pure_growth": "QQQ",
    "balanced": "SPY",
    "income": "BND",
    "capital_preservation": "BND",
    "cd_alternative": "SGOV",
}


def benchmark_for_model(model: portfolio.Model) -> str:
    return MODEL_BENCHMARKS.get(model.mandate_key, "SPY")


def record_signal(
    *,
    target_kind: str,
    target_id: str,
    target_name: str,
    close: pd.Series,
    input_close: pd.Series,
    signal: dict[str, Any],
    horizon_days: int,
    benchmark: str,
    source_counts: dict[str, int],
    eligible_for_real_research: bool,
    data_mode: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_input = _clean_close(input_close)
    full_close = _clean_close(close)
    if clean_input.empty:
        return {"recorded": False, "warning": "No input history available for signal journal."}
    input_start = str(clean_input.index.min().date())
    input_end = str(clean_input.index.max().date())
    score = float(signal.get("score") or 0.0)
    action = str(signal.get("action") or "HOLD").upper()
    event = {
        "dedupe_key": _dedupe_key(target_kind, target_id, input_end, horizon_days, action, score),
        "target_kind": target_kind,
        "target_id": target_id,
        "target_name": target_name,
        "benchmark": benchmark,
        "input_start_date": input_start,
        "input_end_date": input_end,
        "input_rows": int(clean_input.shape[0]),
        "horizon_days": int(horizon_days),
        "score": round(score, 6),
        "action_label": action,
        "data_mode": data_mode,
        "eligible_for_real_research": bool(eligible_for_real_research),
        "source_counts": source_counts,
        "metadata": metadata or {},
        **_forward_result(full_close, input_end, horizon_days),
    }
    benchmark_result = _benchmark_result(benchmark, input_end, horizon_days)
    if benchmark_result.get("benchmark_result_pct") is not None:
        event.update(benchmark_result)
        if event.get("forward_result_pct") is not None:
            event["alpha_pct"] = round(event["forward_result_pct"] - benchmark_result["benchmark_result_pct"], 4)
    result = persistence.get_store().record_signal_event(event)
    return result.get("entry") or result


def list_entries(limit: int = 100) -> list[dict[str, Any]]:
    refresh_forward_results(limit=limit)
    return persistence.get_store().signal_journal(limit=limit)


def refresh_forward_results(limit: int = 250) -> None:
    store = persistence.get_store()
    for entry in store.signal_journal(limit=limit):
        if entry["forward_status"] == "measured":
            continue
        close = _series_for_entry(entry)
        result = _forward_result(close, entry["input_end_date"], entry["horizon_days"])
        benchmark_result = _benchmark_result(entry["benchmark"], entry["input_end_date"], entry["horizon_days"])
        if benchmark_result.get("benchmark_result_pct") is not None:
            result.update(benchmark_result)
        if result.get("forward_result_pct") is not None and result.get("benchmark_result_pct") is not None:
            result["alpha_pct"] = round(result["forward_result_pct"] - result["benchmark_result_pct"], 4)
        if result["forward_status"] == "measured":
            store.update_signal_forward_result(entry["dedupe_key"], result)


def _series_for_entry(entry: dict[str, Any]) -> pd.Series:
    try:
        if entry["target_kind"] == "instrument":
            inst = data.get(entry["target_id"])
            return inst.df["close"] if inst is not None else pd.Series(dtype=float)
        if entry["target_kind"] == "model":
            model = portfolio.get(entry["target_id"])
            return portfolio.build_series(model).close if model is not None else pd.Series(dtype=float)
    except Exception:
        return pd.Series(dtype=float)
    return pd.Series(dtype=float)


def _benchmark_result(benchmark: str, input_end_date: str, horizon_days: int) -> dict[str, Any]:
    try:
        inst = data.get(benchmark)
        if inst is None:
            return {}
        result = _forward_result(inst.df["close"], input_end_date, horizon_days)
        if result.get("forward_result_pct") is None:
            return {}
        return {"benchmark_result_pct": result["forward_result_pct"]}
    except Exception:
        return {}


def _forward_result(close: pd.Series, input_end_date: str, horizon_days: int) -> dict[str, Any]:
    clean = _clean_close(close)
    if clean.empty:
        return {"forward_status": "pending", "forward_start_date": None, "forward_end_date": None, "forward_result_pct": None, "evaluated_at": None}
    input_end = pd.Timestamp(input_end_date)
    history = clean[clean.index <= input_end]
    future = clean[clean.index > input_end]
    if history.empty or len(future) < int(horizon_days):
        return {"forward_status": "pending", "forward_start_date": None, "forward_end_date": None, "forward_result_pct": None, "evaluated_at": None}
    start_price = float(history.iloc[-1])
    end_date = future.index[int(horizon_days) - 1]
    end_price = float(future.iloc[int(horizon_days) - 1])
    result_pct = (end_price / start_price - 1.0) * 100.0 if start_price else None
    return {
        "forward_status": "measured",
        "forward_start_date": str(input_end.date()),
        "forward_end_date": str(end_date.date()),
        "forward_result_pct": round(float(result_pct), 4) if result_pct is not None else None,
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _clean_close(close: pd.Series) -> pd.Series:
    if close is None:
        return pd.Series(dtype=float)
    out = pd.Series(close).dropna().copy()
    out.index = pd.to_datetime(out.index, errors="coerce")
    out = out[~out.index.isna()]
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    out.index = out.index.normalize()
    return pd.to_numeric(out.sort_index(), errors="coerce").dropna()


def _dedupe_key(target_kind: str, target_id: str, input_end: str, horizon_days: int, action: str, score: float) -> str:
    raw = f"{target_kind}|{target_id}|{input_end}|{horizon_days}|{action}|{score:.4f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]

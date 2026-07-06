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

from . import data, persistence, portfolio, provenance
from ._common import (
    avg as _avg,
    clean_close as _clean_close,
    finite as _finite,
    journal_paper_hit as _paper_hit,
    pct as _pct,
)

MODEL_BENCHMARKS = {
    "pure_growth": "QQQ",
    "balanced": "SPY",
    "income": "BND",
    "capital_preservation": "BND",
    "cd_alternative": "SGOV",
}

# Signals recorded from demo/sample data are never measured against later
# prices (real or synthetic); the status stays within persistence's 16-char cap.
NOT_MEASURABLE_STATUS = "not_measurable"


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
    real_eligible = bool(eligible_for_real_research)
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
        "eligible_for_real_research": real_eligible,
        "source_counts": source_counts,
        "metadata": dict(metadata or {}),
        **(_forward_result(full_close, input_end, horizon_days) if real_eligible
           else _not_measurable_result()),
    }
    if real_eligible:
        benchmark_result = _benchmark_result(benchmark, input_end, horizon_days)
        if benchmark_result.get("benchmark_result_pct") is not None:
            event.update(benchmark_result)
            if event.get("forward_result_pct") is not None:
                event["alpha_pct"] = round(event["forward_result_pct"] - benchmark_result["benchmark_result_pct"], 4)
        elif benchmark_result.get("benchmark_note"):
            event["metadata"]["benchmark_note"] = benchmark_result["benchmark_note"]
    result = persistence.get_store().record_signal_event(event)
    return result.get("entry") or result


def list_entries(limit: int = 100) -> list[dict[str, Any]]:
    """Read-only journal view. Pending forward results are refreshed after
    data refreshes (see data.ensure_live_symbols), never on the read path."""
    return persistence.get_store().signal_journal(limit=limit)


def dashboard_payload(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "summary": summarize_entries(entries),
        "benchmark_comparison": benchmark_comparison(entries),
        "model_evidence": model_evidence(entries),
        "drift": drift_series(entries),
    }


def summarize_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    # Headline evidence (hit rate, alpha) aggregates ONLY signals that were
    # real-eligible at record time; demo/sample signals are counted separately.
    real = [entry for entry in entries if _real_eligible(entry)]
    demo = [entry for entry in entries if not _real_eligible(entry)]
    measured = [entry for entry in real if entry.get("forward_status") == "measured"]
    pending = [entry for entry in real if entry.get("forward_status") != "measured"]
    demo_measured = [entry for entry in demo if entry.get("forward_status") == "measured"]
    hit_flags = [_paper_hit(entry) for entry in measured]
    hit_flags = [flag for flag in hit_flags if flag is not None]
    return {
        "total_count": len(entries),
        "measured_count": len(measured),
        "pending_count": len(pending),
        "hit_count": sum(1 for flag in hit_flags if flag),
        "hit_rate_pct": _pct(sum(1 for flag in hit_flags if flag), len(hit_flags)),
        "avg_score": _avg(entry.get("score") for entry in entries),
        "avg_forward_result_pct": _avg(entry.get("forward_result_pct") for entry in measured),
        "avg_benchmark_result_pct": _avg(entry.get("benchmark_result_pct") for entry in measured),
        "avg_alpha_pct": _avg(entry.get("alpha_pct") for entry in measured),
        "model_count": len({entry.get("target_id") for entry in entries if entry.get("target_kind") == "model"}),
        "instrument_count": len({entry.get("target_id") for entry in entries if entry.get("target_kind") == "instrument"}),
        "research_ready_count": len(real),
        "demo_count": len(demo),
        "demo_measured_count": len(demo_measured),
        "demo_pending_count": len(demo) - len(demo_measured),
        "measurement_basis": "Hit rate and alpha aggregate only signals recorded with real-eligible (live/uploaded) data.",
    }


def benchmark_comparison(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    demo_measured: dict[str, int] = {}
    for entry in entries:
        benchmark = str(entry.get("benchmark") or "").strip()
        if not benchmark or entry.get("forward_status") != "measured":
            continue
        if _real_eligible(entry):
            groups.setdefault(benchmark, []).append(entry)
        else:
            # Demo-time measurements (legacy rows) never feed benchmark stats.
            demo_measured[benchmark] = demo_measured.get(benchmark, 0) + 1
    rows = []
    for benchmark, rows_for_benchmark in groups.items():
        hit_flags = [_paper_hit(entry) for entry in rows_for_benchmark]
        hit_flags = [flag for flag in hit_flags if flag is not None]
        rows.append({
            "benchmark": benchmark,
            "measured_count": len(rows_for_benchmark),
            "avg_forward_result_pct": _avg(entry.get("forward_result_pct") for entry in rows_for_benchmark),
            "avg_benchmark_result_pct": _avg(entry.get("benchmark_result_pct") for entry in rows_for_benchmark),
            "avg_alpha_pct": _avg(entry.get("alpha_pct") for entry in rows_for_benchmark),
            "hit_rate_pct": _pct(sum(1 for flag in hit_flags if flag), len(hit_flags)),
            "demo_measured_count": demo_measured.pop(benchmark, 0),
        })
    for benchmark, count in demo_measured.items():
        rows.append({
            "benchmark": benchmark,
            "measured_count": 0,
            "avg_forward_result_pct": None,
            "avg_benchmark_result_pct": None,
            "avg_alpha_pct": None,
            "hit_rate_pct": None,
            "demo_measured_count": count,
        })
    return sorted(rows, key=lambda row: (row["measured_count"], row["benchmark"]), reverse=True)


def model_evidence(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        if entry.get("target_kind") == "model":
            groups.setdefault(str(entry.get("target_id") or ""), []).append(entry)
    rows = []
    for target_id, model_entries in groups.items():
        ordered = sorted(model_entries, key=lambda entry: str(entry.get("created_at") or ""), reverse=True)
        real_entries = [entry for entry in model_entries if _real_eligible(entry)]
        measured = [entry for entry in real_entries if entry.get("forward_status") == "measured"]
        hit_flags = [_paper_hit(entry) for entry in measured]
        hit_flags = [flag for flag in hit_flags if flag is not None]
        latest = ordered[0] if ordered else {}
        source_counts = _merge_source_counts(model_entries)
        rows.append({
            "target_id": target_id,
            "target_name": latest.get("target_name") or target_id,
            "signal_count": len(model_entries),
            "measured_count": len(measured),
            "pending_count": len(real_entries) - len(measured),
            "hit_rate_pct": _pct(sum(1 for flag in hit_flags if flag), len(hit_flags)),
            "avg_score": _avg(entry.get("score") for entry in model_entries),
            "avg_alpha_pct": _avg(entry.get("alpha_pct") for entry in measured),
            "latest_action_label": latest.get("action_label") or "",
            "latest_score": latest.get("score"),
            "latest_input_end_date": latest.get("input_end_date") or "",
            "benchmark": latest.get("benchmark") or "",
            "data_modes": sorted({str(entry.get("data_mode") or "unknown") for entry in model_entries}),
            "source_counts": source_counts,
            "research_ready_count": len(real_entries),
            "demo_count": len(model_entries) - len(real_entries),
        })
    return sorted(rows, key=lambda row: (row["pending_count"], row["signal_count"], row["target_name"]), reverse=True)


def drift_series(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(entries, key=lambda entry: (str(entry.get("input_end_date") or ""), str(entry.get("created_at") or ""), str(entry.get("dedupe_key") or "")))
    out = []
    measured_count = 0
    hit_count = 0
    alpha_values: list[float] = []
    for index, entry in enumerate(ordered, start=1):
        hit = _paper_hit(entry)
        # Cumulative headline evidence only accrues from real-eligible signals.
        if entry.get("forward_status") == "measured" and _real_eligible(entry):
            measured_count += 1
            if hit:
                hit_count += 1
            if _finite(entry.get("alpha_pct")) is not None:
                alpha_values.append(float(entry["alpha_pct"]))
        out.append({
            "index": index,
            "created_at": entry.get("created_at"),
            "input_end_date": entry.get("input_end_date"),
            "target_kind": entry.get("target_kind"),
            "target_id": entry.get("target_id"),
            "target_name": entry.get("target_name"),
            "action_label": entry.get("action_label"),
            "score": entry.get("score"),
            "data_mode": entry.get("data_mode"),
            "eligible_for_real_research": _real_eligible(entry),
            "forward_status": entry.get("forward_status"),
            "forward_result_pct": entry.get("forward_result_pct"),
            "benchmark_result_pct": entry.get("benchmark_result_pct"),
            "alpha_pct": entry.get("alpha_pct"),
            "paper_hit": hit,
            "cumulative_measured_count": measured_count,
            "cumulative_hit_rate_pct": _pct(hit_count, measured_count),
            "cumulative_avg_alpha_pct": _avg(alpha_values),
        })
    return out


def refresh_forward_results(limit: int = 250) -> None:
    store = persistence.get_store()
    for entry in store.signal_journal(limit=limit):
        if entry["forward_status"] == "measured":
            continue
        if not _real_eligible(entry):
            # Demo-time signals must never be scored against later live prices.
            if entry["forward_status"] != NOT_MEASURABLE_STATUS:
                store.update_signal_forward_result(entry["dedupe_key"], _not_measurable_result())
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


def _real_eligible(entry: dict[str, Any]) -> bool:
    """Was this entry recorded from real-eligible (live/uploaded) data?"""
    return bool(entry.get("eligible_for_real_research"))


def _not_measurable_result() -> dict[str, Any]:
    return {
        "forward_status": NOT_MEASURABLE_STATUS,
        "forward_start_date": None,
        "forward_end_date": None,
        "forward_result_pct": None,
        "evaluated_at": None,
    }


def _series_for_entry(entry: dict[str, Any]) -> pd.Series:
    try:
        if entry["target_kind"] == "instrument":
            inst = data.get(entry["target_id"])
            return inst.df["close"] if inst is not None else pd.Series(dtype=float)
        if entry["target_kind"] == "model":
            model = portfolio.get(entry["target_id"])
            if model is None:
                return pd.Series(dtype=float)
            # Forward results must come from real prices only: a permissive
            # rebuild would silently measure the signal on sample/simulated
            # series, so failures leave the entry pending instead.
            return portfolio.build_series(model, allow_sample=False, allow_simulated=False).close
    except Exception:
        return pd.Series(dtype=float)
    return pd.Series(dtype=float)


def _benchmark_result(benchmark: str, input_end_date: str, horizon_days: int) -> dict[str, Any]:
    try:
        inst = data.get(benchmark)
        if inst is None:
            return {}
        if not provenance.is_real_source(inst.source):
            # Mirror evidence_lab._benchmark_close: a synthetic sample benchmark
            # must not supply alpha for real signals.
            return {"benchmark_note": (
                f"Benchmark {benchmark} has no live/uploaded price history; "
                "benchmark result and alpha are withheld."
            )}
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


def _dedupe_key(target_kind: str, target_id: str, input_end: str, horizon_days: int, action: str, score: float) -> str:
    raw = f"{target_kind}|{target_id}|{input_end}|{horizon_days}|{action}|{score:.4f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _merge_source_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for entry in entries:
        for source, count in dict(entry.get("source_counts") or {}).items():
            merged[str(source)] = merged.get(str(source), 0) + int(count or 0)
    return merged

"""Immutable calculation envelopes and deterministic replay verification."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from . import persistence

CALCULATION_VERSION = "helios-deterministic-2026.07.13-v2"
_SENSITIVE_KEYS = ("api_key", "apikey", "token", "secret", "password", "passwd", "authorization", "cookie")


def new_id(kind: str) -> str:
    prefix = "".join(ch for ch in str(kind).lower() if ch.isalnum())[:12] or "evidence"
    return f"ev-{prefix}-{uuid.uuid4().hex[:24]}"


def reference(evidence_id: str) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "calculation_version": CALCULATION_VERSION,
        "replay_url": f"/api/evidence/{evidence_id}",
    }


def series_payload(close: pd.Series) -> dict[str, Any]:
    clean = close.dropna().astype(float).sort_index()
    rows = [
        [pd.Timestamp(index).isoformat(), format(float(value), ".17g")]
        for index, value in clean.items()
        if np.isfinite(value)
    ]
    return {
        "rows": rows,
        "row_count": len(rows),
        "first_date": rows[0][0] if rows else None,
        "last_date": rows[-1][0] if rows else None,
    }


def manifest(
    *,
    series: pd.Series | None = None,
    source: str = "",
    provider: str = "",
    retrieved_at: str = "",
    transformations: list[str] | tuple[str, ...] = (),
    model_version: int | str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    series_data = series_payload(series) if series is not None else {}
    return {
        "calculation_version": CALCULATION_VERSION,
        "series_hash": sha256(series_data) if series_data else "",
        "source": source,
        "provider": provider,
        "retrieved_at": retrieved_at,
        "transformations": list(transformations),
        "model_version": model_version,
        "series_window": {
            key: series_data.get(key) for key in ("row_count", "first_date", "last_date")
        } if series_data else {},
        **dict(extra or {}),
    }


def capture(
    *,
    evidence_id: str,
    artifact_kind: str,
    target_kind: str,
    target_id: str,
    input_payload: dict[str, Any],
    output_payload: dict[str, Any],
    evidence_manifest: dict[str, Any],
) -> dict[str, Any]:
    canonical_input = normalize(input_payload)
    canonical_output = normalize(output_payload)
    canonical_manifest = normalize(evidence_manifest)
    snapshot = {
        "evidence_id": evidence_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifact_kind": artifact_kind,
        "target_kind": target_kind,
        "target_id": target_id,
        "calculation_version": CALCULATION_VERSION,
        "input_hash": sha256(canonical_input),
        "output_hash": sha256(canonical_output),
        "manifest": canonical_manifest,
        "input": canonical_input,
        "output": canonical_output,
    }
    snapshot["envelope_hash"] = sha256(_envelope_payload(snapshot))
    result = persistence.get_store().record_evidence_snapshot(snapshot)
    return {**reference(evidence_id), "recorded": bool(result.get("recorded") or result.get("duplicate")),
            "input_hash": snapshot["input_hash"], "output_hash": snapshot["output_hash"],
            "envelope_hash": snapshot["envelope_hash"]}


def verify(evidence_id: str) -> dict[str, Any]:
    snapshot = persistence.get_store().get_evidence_snapshot(evidence_id)
    if snapshot is None:
        return {"status": "not_found", "evidence_id": evidence_id}
    input_hash = sha256(snapshot.get("input") or {})
    output_hash = sha256(snapshot.get("output") or {})
    envelope_hash = sha256(_envelope_payload(snapshot))
    valid = (
        input_hash == snapshot["input_hash"]
        and output_hash == snapshot["output_hash"]
        and envelope_hash == snapshot.get("envelope_hash")
    )
    return {
        **snapshot,
        "status": "verified" if valid else "hash_mismatch",
        "verified": valid,
        "input_canonical": canonical_text(snapshot.get("input") or {}),
        "output_canonical": canonical_text(snapshot.get("output") or {}),
    }


def replay(evidence_id: str) -> dict[str, Any]:
    """Verify the immutable envelope and recalculate capital-relevant facts.

    Generated identifiers and timestamps are retained and hash-verified, while
    deterministic fields are independently rebuilt from the captured input.
    This makes replay more than retrieval without pretending that an operator
    identity or wall-clock timestamp is a calculation.
    """
    snapshot = verify(evidence_id)
    if not snapshot.get("verified"):
        return snapshot
    replay_result = _recalculate(snapshot)
    return {
        **snapshot,
        "calculation_replay": replay_result,
        "replay_verified": bool(replay_result.get("supported") and replay_result.get("matched")),
    }


def _recalculate(snapshot: dict[str, Any]) -> dict[str, Any]:
    kind = str(snapshot.get("artifact_kind") or "")
    if kind == "signal":
        return _replay_signal(snapshot)
    if kind == "decision":
        return _replay_decision(snapshot)
    if kind == "governance":
        return _replay_governance(snapshot)
    if kind == "report":
        return _replay_report(snapshot)
    if kind == "trial_assessment":
        return _replay_trial_assessment(snapshot)
    if kind in {"signal_forward_result", "decision_forward_outcome"}:
        return _replay_forward_result(snapshot)
    return {
        "supported": False,
        "matched": False,
        "status": "integrity_verified_recalculation_not_supported",
        "checks": [],
    }


def _replay_signal(snapshot: dict[str, Any]) -> dict[str, Any]:
    inputs = snapshot.get("input") or {}
    output = snapshot.get("output") or {}
    series = _series_from_payload(inputs.get("input_series") or {})
    available = _series_from_payload(inputs.get("available_series") or {})
    signal = inputs.get("signal") or {}
    horizon = int(inputs.get("horizon_days") or 0)
    checks = [
        _replay_check("target_kind", snapshot.get("target_kind"), output.get("target_kind")),
        _replay_check("target_id", snapshot.get("target_id"), output.get("target_id")),
        _replay_check("input_start_date", _date_at(series, "first"), output.get("input_start_date")),
        _replay_check("input_end_date", _date_at(series, "last"), output.get("input_end_date")),
        _replay_check("input_rows", int(len(series)), int(output.get("input_rows") or 0)),
        _replay_check("horizon_days", horizon, int(output.get("horizon_days") or 0)),
        _replay_check("score", round(float(signal.get("score") or 0.0), 6), float(output.get("score") or 0.0)),
        _replay_check("action_label", str(signal.get("action") or "HOLD").upper(), output.get("action_label")),
        _replay_check("data_mode", inputs.get("data_mode"), output.get("data_mode")),
        _replay_check("source_counts", normalize(inputs.get("source_counts") or {}), normalize(output.get("source_counts") or {})),
        _replay_check("trial_id", inputs.get("trial_id") or "", output.get("trial_id") or ""),
        _replay_check("recording_source", inputs.get("recording_source"), output.get("recording_source")),
    ]
    if output.get("forward_status") == "measured" and not available.empty:
        forward = _forward_result(available, str(output.get("input_end_date") or ""), horizon)
        checks.extend([
            _replay_check("forward_end_date", forward.get("forward_end_date"), output.get("forward_end_date")),
            _replay_check("forward_result_pct", forward.get("forward_result_pct"), output.get("forward_result_pct")),
        ])
        benchmark = _series_from_payload(inputs.get("benchmark_series") or {})
        if not benchmark.empty and output.get("benchmark_result_pct") is not None:
            value = _span_return(
                benchmark,
                str(output.get("input_end_date") or ""),
                str(output.get("forward_end_date") or ""),
            )
            checks.append(_replay_check("benchmark_result_pct", value, output.get("benchmark_result_pct")))
    return _replay_summary(checks)


def _replay_decision(snapshot: dict[str, Any]) -> dict[str, Any]:
    inputs = snapshot.get("input") or {}
    output = snapshot.get("output") or {}
    series = _series_from_payload(inputs.get("series") or {})
    signal = inputs.get("engine_signal") or {}
    operator_action = str(inputs.get("operator_action") or "").upper()
    engine_action = str(signal.get("action") or "").upper()
    agreement = ""
    if engine_action:
        try:
            from .decision_journal import classify_agreement

            agreement = classify_agreement(engine_action, operator_action)
        except Exception:
            agreement = ""
    checks = [
        _replay_check("target_kind", snapshot.get("target_kind"), output.get("target_kind")),
        _replay_check("target_id", snapshot.get("target_id"), output.get("target_id")),
        _replay_check("decision_date", _date_at(series, "last"), output.get("decision_date")),
        _replay_check("decision_price", float(series.iloc[-1]) if len(series) else None, output.get("decision_price")),
        _replay_check("engine_action", engine_action, output.get("engine_action")),
        _replay_check("engine_score", signal.get("score"), output.get("engine_score")),
        _replay_check("my_action", operator_action, output.get("my_action")),
        _replay_check("agreement", agreement, output.get("agreement")),
        _replay_check("rationale", inputs.get("rationale"), output.get("rationale")),
        _replay_check("mandate", inputs.get("mandate"), output.get("mandate")),
        _replay_check("benchmark", inputs.get("benchmark"), output.get("benchmark")),
        _replay_check("data_mode", inputs.get("data_mode"), output.get("data_mode")),
    ]
    return _replay_summary(checks)


def _replay_governance(snapshot: dict[str, Any]) -> dict[str, Any]:
    inputs = snapshot.get("input") or {}
    output = snapshot.get("output") or {}
    frozen = output.get("snapshot") or {}
    checks = [
        _replay_check("model", normalize(inputs.get("model") or {}), normalize(frozen.get("model") or {})),
        _replay_check("version_diff", normalize(inputs.get("version_diff") or {}), normalize(frozen.get("version_diff") or {})),
        _replay_check("risk_gate", normalize(inputs.get("risk_gate") or {}), normalize(frozen.get("risk_gate") or {})),
        _replay_check("approval_status", inputs.get("requested_status") or "", output.get("approval_status") or ""),
    ]
    return _replay_summary(checks)


def _replay_report(snapshot: dict[str, Any]) -> dict[str, Any]:
    inputs = snapshot.get("input") or {}
    output = snapshot.get("output") or {}
    checks = [
        _replay_check("report_payload", normalize(inputs.get("report") or {}), normalize(output.get("report") or {})),
        _replay_check("signal_journal", normalize(inputs.get("signal_journal") or {}), normalize(output.get("signal_journal") or {})),
    ]
    try:
        from . import report_exports

        rerendered = report_exports.render_html(output)
        checks.append(_replay_check("html_render", rerendered, output.get("html") or ""))
    except Exception as exc:
        checks.append({"field": "html_render", "matched": False, "error": str(exc)[:200]})
    return _replay_summary(checks)


def _replay_trial_assessment(snapshot: dict[str, Any]) -> dict[str, Any]:
    from . import trials

    inputs = snapshot.get("input") or {}
    output = snapshot.get("output") or {}
    rebuilt = trials.replay_assessment_basis(inputs.get("assessment_basis") or {})
    checks = [
        _replay_check(field, normalize(rebuilt.get(field)), normalize(output.get(field)))
        for field in ("state", "passed", "checks", "observations", "metrics")
    ]
    return _replay_summary(checks)


def _replay_forward_result(snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot.get("artifact_kind") == "decision_forward_outcome":
        return _replay_decision_outcome(snapshot)
    return _replay_signal_forward_result(snapshot)


def _replay_signal_forward_result(snapshot: dict[str, Any]) -> dict[str, Any]:
    inputs = snapshot.get("input") or {}
    output = snapshot.get("output") or {}
    series = _series_from_payload(inputs.get("available_series") or {})
    benchmark = _series_from_payload(inputs.get("benchmark_series") or {})
    input_end = str(inputs.get("input_end_date") or inputs.get("decision_date") or "")
    horizon = int(inputs.get("horizon_days") or 0)
    checks: list[dict[str, Any]] = []
    if not series.empty and input_end and horizon > 0 and output.get("forward_result_pct") is not None:
        forward = _forward_result(series, input_end, horizon)
        checks.extend([
            _replay_check("forward_end_date", forward.get("forward_end_date"), output.get("forward_end_date")),
            _replay_check("forward_result_pct", forward.get("forward_result_pct"), output.get("forward_result_pct")),
        ])
        if not benchmark.empty and forward.get("forward_end_date") and output.get("benchmark_result_pct") is not None:
            benchmark_result = _span_return(benchmark, input_end, str(forward["forward_end_date"]))
            checks.append(_replay_check(
                "benchmark_result_pct", benchmark_result, output.get("benchmark_result_pct"),
            ))
            if benchmark_result is not None:
                alpha = round(float(forward["forward_result_pct"]) - benchmark_result, 4)
                checks.append(_replay_check("alpha_pct", alpha, output.get("alpha_pct")))
    return _replay_summary(checks) if checks else {
        "supported": True,
        "matched": True,
        "status": "replayed_no_measured_return",
        "checks": [],
    }


def _replay_decision_outcome(snapshot: dict[str, Any]) -> dict[str, Any]:
    from ._common import paper_hit

    inputs = snapshot.get("input") or {}
    output = snapshot.get("output") or {}
    series = _series_from_payload(inputs.get("available_series") or {})
    benchmark = _series_from_payload(inputs.get("benchmark_series") or {})
    anchor = str(inputs.get("score_anchor") or inputs.get("decision_date") or "")
    outcomes = output.get("outcomes") if isinstance(output.get("outcomes"), dict) else {}
    my_action = _decision_action_bucket(inputs.get("my_action"))
    engine_action = _decision_action_bucket(inputs.get("engine_action"))
    checks: list[dict[str, Any]] = []
    for horizon_key, row in sorted(outcomes.items(), key=lambda item: int(item[0])):
        if not isinstance(row, dict):
            checks.append({"field": f"outcomes.{horizon_key}", "matched": False})
            continue
        forward = _forward_result(series, anchor, int(horizon_key))
        checks.extend([
            _replay_check(f"outcomes.{horizon_key}.end_date", forward.get("forward_end_date"), row.get("end_date")),
            _replay_check(f"outcomes.{horizon_key}.target_return_pct", forward.get("forward_result_pct"), row.get("target_return_pct")),
        ])
        benchmark_result = None
        if not benchmark.empty and forward.get("forward_end_date"):
            benchmark_result = _span_return(benchmark, anchor, str(forward["forward_end_date"]))
        if row.get("benchmark_return_pct") is not None:
            checks.append(_replay_check(
                f"outcomes.{horizon_key}.benchmark_return_pct",
                benchmark_result,
                row.get("benchmark_return_pct"),
            ))
        alpha = None
        if benchmark_result is not None and forward.get("forward_result_pct") is not None:
            alpha = round(float(forward["forward_result_pct"]) - benchmark_result, 4)
        if row.get("alpha_pct") is not None:
            checks.append(_replay_check(f"outcomes.{horizon_key}.alpha_pct", alpha, row.get("alpha_pct")))
        checks.append(_replay_check(
            f"outcomes.{horizon_key}.hit",
            paper_hit(my_action, forward.get("forward_result_pct"), alpha),
            row.get("hit"),
        ))
        if inputs.get("engine_action"):
            checks.append(_replay_check(
                f"outcomes.{horizon_key}.engine_hit",
                paper_hit(engine_action, forward.get("forward_result_pct"), alpha),
                row.get("engine_hit"),
            ))
    return _replay_summary(checks) if checks else {
        "supported": True,
        "matched": True,
        "status": "replayed_no_measured_return",
        "checks": [],
    }


def _decision_action_bucket(action: Any) -> str:
    return {
        "BUY": "BUY",
        "ADD": "BUY",
        "HOLD": "HOLD",
        "TRIM": "SELL",
        "SELL": "SELL",
    }.get(str(action or "").upper(), "HOLD")


def _series_from_payload(payload: dict[str, Any]) -> pd.Series:
    rows = payload.get("rows") if isinstance(payload, dict) else []
    parsed: list[tuple[pd.Timestamp, float]] = []
    for row in rows or []:
        try:
            parsed.append((pd.Timestamp(row[0]), float(row[1])))
        except (TypeError, ValueError, IndexError):
            continue
    return pd.Series([value for _, value in parsed], index=[index for index, _ in parsed], dtype=float).sort_index()


def _forward_result(series: pd.Series, input_end: str, horizon: int) -> dict[str, Any]:
    anchor = pd.Timestamp(input_end)
    history = series[series.index <= anchor]
    future = series[series.index > anchor]
    if history.empty or len(future) < horizon:
        return {"forward_end_date": None, "forward_result_pct": None}
    end_date = future.index[horizon - 1]
    value = (float(future.iloc[horizon - 1]) / float(history.iloc[-1]) - 1.0) * 100.0
    return {"forward_end_date": str(end_date.date()), "forward_result_pct": round(value, 4)}


def _span_return(series: pd.Series, start: str, end: str) -> float | None:
    start_rows = series[series.index <= pd.Timestamp(start)]
    end_rows = series[series.index <= pd.Timestamp(end)]
    if start_rows.empty or end_rows.empty or float(start_rows.iloc[-1]) == 0:
        return None
    return round((float(end_rows.iloc[-1]) / float(start_rows.iloc[-1]) - 1.0) * 100.0, 4)


def _date_at(series: pd.Series, position: str) -> str | None:
    if series.empty:
        return None
    value = series.index[0] if position == "first" else series.index[-1]
    return str(pd.Timestamp(value).date())


def _replay_check(field: str, expected: Any, actual: Any) -> dict[str, Any]:
    expected_value = normalize(expected)
    actual_value = normalize(actual)
    return {
        "field": field,
        "matched": expected_value == actual_value,
        "expected_hash": sha256(expected_value),
        "actual_hash": sha256(actual_value),
    }


def _replay_summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
    matched = bool(checks) and all(bool(check.get("matched")) for check in checks)
    return {
        "supported": True,
        "matched": matched,
        "status": "replayed" if matched else "recalculation_mismatch",
        "checks": checks,
    }


def sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_text(payload).encode("utf-8")).hexdigest()


def canonical_text(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def normalize(payload: Any) -> Any:
    """Convert to secret-safe JSON-native data before hashing and storage."""
    return json.loads(canonical_text(_sanitize(payload)))


def _sanitize(payload: Any) -> Any:
    if isinstance(payload, dict):
        out = {}
        for key, value in payload.items():
            name = str(key)
            if any(marker in name.lower() for marker in _SENSITIVE_KEYS):
                out[name] = "[redacted]"
            else:
                out[name] = _sanitize(value)
        return out
    if isinstance(payload, (list, tuple)):
        return [_sanitize(item) for item in payload]
    if isinstance(payload, str):
        return persistence.redact_long_text(payload, limit=1_000_000)
    return payload


def _envelope_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": snapshot.get("evidence_id"),
        "created_at": snapshot.get("created_at"),
        "artifact_kind": snapshot.get("artifact_kind"),
        "target_kind": snapshot.get("target_kind"),
        "target_id": snapshot.get("target_id"),
        "calculation_version": snapshot.get("calculation_version"),
        "input_hash": snapshot.get("input_hash"),
        "output_hash": snapshot.get("output_hash"),
        "manifest": snapshot.get("manifest") or {},
    }

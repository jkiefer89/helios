"""Model validation dashboard.

This module combines existing deterministic governance, walk-forward evidence,
and Signal Journal facts into one review payload. It does not change Helios
analytics, rebalance models, execute trades, or bypass provenance gates.
"""
from __future__ import annotations

from typing import Any

from . import evidence_lab, model_governance, portfolio
from ._common import avg as _avg


def dashboard(
    *,
    models: list[portfolio.Model] | None = None,
    horizon_days: int = 21,
    train_window: int = 252,
    step: int = 21,
) -> dict[str, Any]:
    current_models = models if models is not None else portfolio.all_models()
    governance = model_governance.payload(current_models)
    governance_by_id = {row["id"]: row for row in governance.get("models", [])}
    rows = [
        _validation_row(
            model,
            governance_by_id.get(model.id, {}),
            horizon_days=horizon_days,
            train_window=train_window,
            step=step,
        )
        for model in current_models
    ]
    eligible = [row for row in rows if row["validation_state"] == "eligible"]
    eligible.sort(key=lambda row: (row["validation_score"], row["walk_forward"]["window_count"], row["model_name"]), reverse=True)
    blocked = [row for row in rows if row["validation_state"] != "eligible"]
    ordered = []
    for index, row in enumerate(eligible):
        role = "champion" if index == 0 else "challenger"
        ordered.append({**row, "role": role})
    ordered.extend({**row, "role": "blocked"} for row in blocked)
    champion = ordered[0] if ordered and ordered[0]["role"] == "champion" else None
    challengers = [row for row in ordered if row["role"] == "challenger"]
    alerts = _dashboard_alerts(ordered)
    return {
        "models": ordered,
        "champion": champion,
        "challengers": challengers,
        "alerts": alerts,
        "summary": {
            "model_count": len(rows),
            "eligible_count": len(eligible),
            "blocked_count": len(blocked),
            "champion_model_id": champion["model_id"] if champion else None,
            "challenger_count": len(challengers),
            "alert_count": len(alerts),
            "governance_available": bool((governance.get("summary") or {}).get("available")),
        },
        "parameters": {"horizon_days": int(horizon_days), "train_window": int(train_window), "step": int(step)},
        "methodology": {
            "analysis_only": True,
            "paper_tracking_only": True,
            "no_execution": True,
            "does_not_change_analytics": True,
            "ranking_basis": "Review ranking combines existing walk-forward hit rate, alpha, false-positive rate, signal decay, and governance state.",
        },
        "disclaimer": "Model validation is analysis-only. It does not execute trades, guarantee outcomes, or override Helios provenance gates.",
    }


def _validation_row(
    model: portfolio.Model,
    governance: dict[str, Any],
    *,
    horizon_days: int,
    train_window: int,
    step: int,
) -> dict[str, Any]:
    evidence = evidence_lab.analyze_model(
        model,
        horizon_days=horizon_days,
        train_window=train_window,
        step=step,
    )
    if evidence.get("evidence_unavailable") or not evidence.get("eligible_for_real_research"):
        return {
            "model_id": model.id,
            "model_name": model.name,
            "mandate": model.mandate_key,
            "role": "blocked",
            "validation_state": "blocked",
            "validation_score": 0.0,
            "validation_grade": "Blocked",
            "evidence_unavailable": True,
            "reason": evidence.get("reason") or "",
            "required_action": evidence.get("required_action") or "Provide eligible real price history.",
            "walk_forward": evidence.get("summary") or {},
            "false_positives": evidence.get("false_positives") or {},
            "regime_sensitivity": evidence.get("regime_sensitivity") or [],
            "decay": evidence.get("decay") or [],
            "confidence_bands": evidence.get("confidence_bands") or {},
            "prospective_validation": evidence.get("prospective_validation") or {},
            "governance": _governance_summary(governance),
            "drift_alerts": [{
                "severity": "blocked",
                "title": "Validation blocked",
                "detail": evidence.get("required_action") or evidence.get("reason") or "Eligible real history is required.",
            }],
            "methodology": _row_methodology(),
            "disclaimer": evidence.get("disclaimer") or "",
        }
    walk = evidence.get("summary") or {}
    false_positives = evidence.get("false_positives") or {}
    decay = evidence.get("decay") or []
    score = _validation_score(walk, false_positives, decay, governance)
    alerts = _drift_alerts(walk, false_positives, decay, evidence.get("prospective_validation") or {}, governance)
    return {
        "model_id": model.id,
        "model_name": model.name,
        "mandate": model.mandate_key,
        "role": "candidate",
        "validation_state": "eligible",
        "validation_score": score,
        "validation_grade": _grade(score),
        "evidence_unavailable": False,
        "reason": evidence.get("reason") or "",
        "required_action": evidence.get("required_action") or "",
        "walk_forward": walk,
        "false_positives": false_positives,
        "regime_sensitivity": evidence.get("regime_sensitivity") or [],
        "decay": decay,
        "confidence_bands": evidence.get("confidence_bands") or {},
        "prospective_validation": evidence.get("prospective_validation") or {},
        "governance": _governance_summary(governance),
        "drift_alerts": alerts,
        "methodology": _row_methodology(),
        "disclaimer": evidence.get("disclaimer") or "",
    }


def _validation_score(
    walk: dict[str, Any],
    false_positives: dict[str, Any],
    decay: list[dict[str, Any]],
    governance: dict[str, Any],
) -> float:
    hit_rate = _number(walk.get("hit_rate_pct"), default=0.0)
    alpha = _number(walk.get("avg_alpha_pct"), default=0.0)
    alpha_component = _clamp(50.0 + alpha * 10.0, 0.0, 100.0)
    fp_rate = _number(false_positives.get("rate_pct"), default=50.0)
    false_positive_component = _clamp(100.0 - fp_rate, 0.0, 100.0)
    decay_component = _decay_component(decay)
    governance_component = 75.0
    if governance.get("risk_limit_state") == "breach":
        governance_component -= 25.0
    if governance.get("approval_status") == "approved":
        governance_component += 10.0
    score = (
        hit_rate * 0.35
        + alpha_component * 0.25
        + false_positive_component * 0.20
        + decay_component * 0.15
        + _clamp(governance_component, 0.0, 100.0) * 0.05
    )
    return round(_clamp(score, 0.0, 100.0), 2)


def _decay_component(decay: list[dict[str, Any]]) -> float:
    if not decay:
        return 50.0
    alphas = [_number(row.get("avg_alpha_pct"), default=None) for row in decay]
    alphas = [value for value in alphas if value is not None]
    hit_rates = [_number(row.get("hit_rate_pct"), default=None) for row in decay]
    hit_rates = [value for value in hit_rates if value is not None]
    if not alphas and not hit_rates:
        return 50.0
    alpha_component = _clamp(50.0 + (_avg(alphas) or 0.0) * 10.0, 0.0, 100.0)
    hit_component = _avg(hit_rates) if hit_rates else 50.0
    return round((alpha_component + hit_component) / 2.0, 2)


def _drift_alerts(
    walk: dict[str, Any],
    false_positives: dict[str, Any],
    decay: list[dict[str, Any]],
    prospective: dict[str, Any],
    governance: dict[str, Any],
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    if governance.get("risk_limit_state") == "breach":
        alerts.append({"severity": "high", "title": "Governance breach", "detail": "Risk-limit state is breached before validation approval."})
    avg_alpha = _number(walk.get("avg_alpha_pct"), default=None)
    if avg_alpha is not None and avg_alpha < 0:
        alerts.append({"severity": "high", "title": "Negative walk-forward alpha", "detail": f"Average alpha vs benchmark is {avg_alpha:.2f}%."})
    fp_rate = _number(false_positives.get("rate_pct"), default=None)
    if fp_rate is not None and fp_rate >= 35:
        alerts.append({"severity": "medium", "title": "False positives elevated", "detail": f"False-positive rate is {fp_rate:.1f}%."})
    long_decay = decay[-1] if decay else {}
    long_alpha = _number(long_decay.get("avg_alpha_pct"), default=None)
    if long_alpha is not None and long_alpha < 0:
        alerts.append({"severity": "medium", "title": "Signal decay warning", "detail": f"Longer-horizon decay alpha is {long_alpha:.2f}%."})
    prospective_alpha = _number(prospective.get("avg_alpha_pct"), default=None)
    if prospective_alpha is not None and prospective_alpha < 0:
        alerts.append({"severity": "medium", "title": "Prospective drift", "detail": f"Signal Journal alpha is {prospective_alpha:.2f}%."})
    if not alerts:
        alerts.append({"severity": "info", "title": "No validation drift alert", "detail": "Current walk-forward and governance checks did not trigger a material alert."})
    return alerts


def _dashboard_alerts(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    for row in rows:
        for alert in row.get("drift_alerts") or []:
            severity = alert.get("severity") or "info"
            if severity in {"blocked", "high", "medium"}:
                out.append({
                    "severity": severity,
                    "model_id": row["model_id"],
                    "model_name": row["model_name"],
                    "title": alert.get("title") or "Validation alert",
                    "detail": alert.get("detail") or "",
                })
    return out


def _governance_summary(governance: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": int(governance.get("version") or 1),
        "approval_status": governance.get("approval_status") or "draft",
        "risk_limit_state": governance.get("risk_limit_state") or "unknown",
        "risk_limit_violations": governance.get("risk_limit_violations") or [],
        "rebalance_status": governance.get("rebalance_status") or "not_recorded",
        "snapshot_count": int(governance.get("snapshot_count") or 0),
        "latest_change_note": governance.get("latest_change_note") or "",
        "updated_by": governance.get("updated_by") or "",
    }


def _row_methodology() -> dict[str, bool]:
    return {
        "analysis_only": True,
        "paper_tracking_only": True,
        "no_execution": True,
        "does_not_change_analytics": True,
    }


def _grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def _number(value: Any, *, default: float | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))

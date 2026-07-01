"""Model governance audit trail and mandate/risk-limit checks.

The governance layer records advisor workflow facts around a model. It does not
alter Helios analytics, trade labels, forecasts, scores, or provenance gates.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from . import data, mandate, model_library, persistence, portfolio

APPROVAL_STATUSES = {"draft", "pending_review", "approved", "rejected", "archived"}
REBALANCE_ACTIONS = {"rebalance_recorded", "rebalance_reviewed"}
DEFAULT_ACTOR = "Advisor Console"


def payload(models: list[portfolio.Model] | None = None) -> dict[str, Any]:
    """Return the current governance workspace for loaded models."""
    store = persistence.get_store()
    if not store.available:
        warning = store.warning or "SQLite persistence is required for model governance."
        return {
            "models": [],
            "snapshots": [],
            "change_log": [],
            "rebalance_history": [],
            "summary": {
                "available": False,
                "model_count": 0,
                "approved_count": 0,
                "pending_count": 0,
                "draft_count": 0,
                "archived_count": 0,
                "breach_count": 0,
                "snapshot_count": 0,
                "change_count": 0,
            },
            "warning": warning,
            "disclaimer": _disclaimer(),
        }

    current_models = models if models is not None else portfolio.all_models()
    events = store.model_governance_events(limit=1000)
    events_by_model: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        events_by_model.setdefault(event["model_id"], []).append(event)

    rows = [_model_row(model, events_by_model.get(model.id, [])) for model in current_models]
    rows.sort(key=lambda item: item["name"])
    change_log = [event for event in events if event["model_id"] in {row["id"] for row in rows}]
    snapshots = [
        _snapshot_summary(event)
        for event in change_log
        if isinstance(event.get("snapshot"), dict) and event["snapshot"]
    ]
    rebalance_history = [
        _event_summary(event)
        for event in change_log
        if event.get("action") in REBALANCE_ACTIONS
    ]
    counts = Counter(row["approval_status"] for row in rows)
    summary = {
        "available": True,
        "model_count": len(rows),
        "approved_count": counts.get("approved", 0),
        "pending_count": counts.get("pending_review", 0),
        "draft_count": counts.get("draft", 0),
        "archived_count": counts.get("archived", 0),
        "breach_count": sum(1 for row in rows if row["risk_limit_state"] == "breach"),
        "snapshot_count": len(snapshots),
        "change_count": len(change_log),
    }
    return {
        "models": rows,
        "snapshots": snapshots,
        "change_log": [_event_summary(event) for event in change_log],
        "rebalance_history": rebalance_history,
        "summary": summary,
        "warning": "",
        "disclaimer": _disclaimer(),
    }


def record_event(
    model: portfolio.Model,
    *,
    actor: str = DEFAULT_ACTOR,
    action: str = "",
    note: str = "",
    approval_status: str = "",
) -> dict[str, Any]:
    """Archive a governance event for the current model state."""
    store = persistence.get_store()
    if not store.available:
        return {"recorded": False, "warning": store.warning or "SQLite persistence is required for model governance."}

    current_events = store.model_governance_events(model.id, limit=1000)
    current = _model_row(model, current_events)
    status = _normalise_status(approval_status) or current["approval_status"]
    safe_action = _normalise_action(action, bool(approval_status))
    version = store.next_model_governance_version(model.id)
    snapshot = _snapshot(model, version=version, approval_status=status)
    event = store.record_model_governance_event(
        model_id=model.id,
        version=version,
        actor=actor,
        action=safe_action,
        note=note,
        approval_status=status,
        snapshot=snapshot,
    )
    return {"recorded": event is not None, "event": event, "warning": "" if event else store.warning}


def preview_edit(
    model: portfolio.Model,
    *,
    holdings: list[dict[str, Any]],
    rebalance_to_target: bool = False,
) -> dict[str, Any]:
    """Return a save preview for proposed holdings without mutating the model."""
    draft = _draft_model(model, holdings, rebalance_to_target=rebalance_to_target)
    return _editor_payload(model, draft, rebalance_to_target=rebalance_to_target)


def save_edit(
    model: portfolio.Model,
    *,
    holdings: list[dict[str, Any]],
    change_note: str,
    actor: str = DEFAULT_ACTOR,
    rebalance_to_target: bool = False,
) -> dict[str, Any]:
    """Persist edited holdings and record a governance snapshot."""
    note = str(change_note or "").strip()
    if len(note) < 5:
        return {"saved": False, "warning": "A change note is required before saving model edits."}
    draft = _draft_model(model, holdings, rebalance_to_target=rebalance_to_target)
    source_by_ticker = {holding.ticker: holding.source for holding in model.holdings}
    edited = portfolio.Model(
        id=model.id,
        name=model.name,
        mandate_key=model.mandate_key,
        mandate_context=model.mandate_context,
        holdings=[
            portfolio.Holding(holding.ticker, holding.weight, source_by_ticker.get(holding.ticker, "pending"))
            for holding in draft.holdings
        ],
    )
    portfolio.register(edited)
    persisted = portfolio._persist_model(edited, source_filename="model-editor")
    if not persisted.get("persisted"):
        return {"saved": False, "warning": persisted.get("warning") or "Could not persist model edits."}
    event_result = record_event(
        edited,
        actor=actor or DEFAULT_ACTOR,
        action="model_edit",
        note=note,
    )
    if not event_result.get("recorded"):
        return {"saved": False, "warning": event_result.get("warning") or "Could not record model edit governance event."}
    return {
        **_editor_payload(model, edited, rebalance_to_target=rebalance_to_target),
        "saved": True,
        "event": event_result["event"],
    }


def _model_row(model: portfolio.Model, events: list[dict[str, Any]]) -> dict[str, Any]:
    latest_event = events[0] if events else None
    latest_status_event = next((event for event in events if event.get("approval_status")), None)
    status = _normalise_status(latest_status_event.get("approval_status") if latest_status_event else "") or "draft"
    version = int(latest_event.get("version") or 1) if latest_event else 1
    limits = _risk_limits(model)
    rebalance = _rebalance_rules(model)
    violations = _risk_limit_violations(model, limits)
    latest_rebalance = next((event for event in events if event.get("action") in REBALANCE_ACTIONS), None)
    latest_note_event = next((event for event in events if event.get("note")), None)
    approved_by = ""
    approval_updated_at = None
    if latest_status_event:
        approved_by = latest_status_event.get("actor") or ""
        approval_updated_at = latest_status_event.get("created_at")
    return {
        "id": model.id,
        "name": model.name,
        "mandate": model.mandate_key,
        "mandate_label": mandate.get(model.mandate_key)["label"],
        "mandate_context": model.mandate_context,
        "version": version,
        "approval_status": status,
        "approved_by": approved_by if status == "approved" else "",
        "approval_updated_at": approval_updated_at,
        "risk_limits": limits,
        "risk_limit_state": "breach" if violations else "within_limits",
        "risk_limit_violations": violations,
        "rebalance_rules": rebalance,
        "rebalance_status": "recorded" if latest_rebalance else "not_recorded",
        "last_rebalance_at": latest_rebalance.get("created_at") if latest_rebalance else None,
        "version_count": max(version, 1),
        "snapshot_count": sum(1 for event in events if event.get("snapshot")),
        "change_note_count": sum(1 for event in events if event.get("note")),
        "latest_change_note": latest_note_event.get("note") if latest_note_event else "",
        "updated_by": latest_event.get("actor") if latest_event else "",
        "updated_at": latest_event.get("created_at") if latest_event else None,
        "holdings_count": len(model.holdings),
        "top_holding": model.holdings[0].ticker if model.holdings else "",
        "top_weight_pct": round(float(model.holdings[0].weight) * 100, 2) if model.holdings else 0.0,
        "holdings": _holdings_payload(model.holdings),
        "source": _template_for_model(model).get("provenance", {}).get("source_type", "client_model"),
        "provenance": _template_for_model(model).get("provenance", {
            "source_type": "client_model",
            "version": "",
            "basis": "uploaded or locally created client model",
            "caveat": "Governance metadata is local workflow evidence, not investment advice.",
        }),
    }


def _editor_payload(
    current_model: portfolio.Model,
    draft_model: portfolio.Model,
    *,
    rebalance_to_target: bool,
) -> dict[str, Any]:
    limits = _risk_limits(current_model)
    violations = _risk_limit_violations(draft_model, limits)
    return {
        "model": {
            "id": draft_model.id,
            "name": draft_model.name,
            "mandate": draft_model.mandate_key,
            "mandate_label": mandate.get(draft_model.mandate_key)["label"],
            "holdings": _holdings_payload(draft_model.holdings),
        },
        "current_holdings": _holdings_payload(current_model.holdings),
        "proposed_holdings": _holdings_payload(draft_model.holdings),
        "rebalance_to_target": bool(rebalance_to_target),
        "risk_limits": limits,
        "risk_limit_state": "breach" if violations else "within_limits",
        "risk_limit_violations": violations,
        "can_save": True,
        "requires_change_note": True,
        "disclaimer": _disclaimer(),
    }


def _draft_model(
    model: portfolio.Model,
    holdings: list[dict[str, Any]],
    *,
    rebalance_to_target: bool,
) -> portfolio.Model:
    proposed = _parse_editor_holdings(holdings, rebalance_to_target=rebalance_to_target)
    return portfolio.Model(
        id=model.id,
        name=model.name,
        mandate_key=model.mandate_key,
        mandate_context=model.mandate_context,
        holdings=proposed,
    )


def _parse_editor_holdings(raw_holdings: list[dict[str, Any]], *, rebalance_to_target: bool) -> list[portfolio.Holding]:
    if not isinstance(raw_holdings, list) or not raw_holdings:
        raise ValueError("Add at least one holding before previewing or saving model edits.")
    merged: dict[str, float] = {}
    order: list[str] = []
    for raw in raw_holdings:
        if not isinstance(raw, dict):
            continue
        ticker = data.clean_symbol(str(raw.get("ticker") or raw.get("symbol") or ""), fallback="")
        if not ticker:
            continue
        weight_pct = _editor_weight_pct(raw)
        if weight_pct <= 0:
            continue
        if ticker not in merged:
            order.append(ticker)
        merged[ticker] = merged.get(ticker, 0.0) + weight_pct
    if not merged:
        raise ValueError("Add at least one valid ticker with a positive target weight.")
    if len(merged) > portfolio.MAX_HOLDINGS:
        raise ValueError(f"Model editor supports up to {portfolio.MAX_HOLDINGS} holdings.")
    total_pct = sum(merged.values())
    if total_pct <= 0:
        raise ValueError("Target weights must be positive.")
    divisor = total_pct if rebalance_to_target else 100.0
    holdings = [portfolio.Holding(ticker, round(merged[ticker] / divisor, 6)) for ticker in order]
    return holdings


def _editor_weight_pct(raw: dict[str, Any]) -> float:
    value = raw.get("weight_pct")
    if value is None:
        value = raw.get("target_weight_pct")
    if value is None:
        value = raw.get("weight")
        if isinstance(value, (int, float)) and 0 < float(value) <= 1:
            return float(value) * 100
    try:
        return max(0.0, float(str(value).replace("%", "").strip()))
    except (TypeError, ValueError):
        return 0.0


def _holdings_payload(holdings: list[portfolio.Holding]) -> list[dict[str, Any]]:
    return [
        {
            "ticker": holding.ticker,
            "weight": round(float(holding.weight), 6),
            "weight_pct": round(float(holding.weight) * 100, 2),
            "source": holding.source or "pending",
        }
        for holding in holdings
    ]


def _snapshot(model: portfolio.Model, *, version: int, approval_status: str) -> dict[str, Any]:
    return {
        "model": {
            "id": model.id,
            "name": model.name,
            "mandate": model.mandate_key,
            "mandate_label": mandate.get(model.mandate_key)["label"],
            "mandate_context": model.mandate_context,
        },
        "version": version,
        "approval_status": approval_status,
        "holdings": [
            {"ticker": holding.ticker, "weight": round(float(holding.weight), 6), "weight_pct": round(float(holding.weight) * 100, 2)}
            for holding in model.holdings
        ],
        "risk_limits": _risk_limits(model),
        "risk_limit_violations": _risk_limit_violations(model, _risk_limits(model)),
        "rebalance_rules": _rebalance_rules(model),
        "provenance": _template_for_model(model).get("provenance", {
            "source_type": "client_model",
            "version": "",
            "basis": "uploaded or locally created client model",
            "caveat": "Governance snapshot is local workflow evidence, not investment advice.",
        }),
        "disclaimer": _disclaimer(),
    }


def _snapshot_summary(event: dict[str, Any]) -> dict[str, Any]:
    snapshot = event.get("snapshot") or {}
    model = snapshot.get("model") or {}
    return {
        "event_id": event["id"],
        "model_id": event["model_id"],
        "model_name": model.get("name") or event["model_id"],
        "version": event["version"],
        "created_at": event["created_at"],
        "actor": event["actor"],
        "action": event["action"],
        "approval_status": event["approval_status"],
        "holding_count": len(snapshot.get("holdings") or []),
        "risk_limit_state": "breach" if snapshot.get("risk_limit_violations") else "within_limits",
    }


def _event_summary(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event["id"],
        "model_id": event["model_id"],
        "created_at": event["created_at"],
        "version": event["version"],
        "actor": event["actor"],
        "action": event["action"],
        "note": event["note"],
        "approval_status": event["approval_status"],
    }


def _risk_limits(model: portfolio.Model) -> dict[str, float | int]:
    template = _template_for_model(model)
    if template:
        return dict(template["risk_limits"])
    m = mandate.get(model.mandate_key)
    return {
        "max_single_position_pct": round(float(m.get("single_name_cap", 0.35)) * 100, 2),
        "max_theme_position_pct": 100.0,
        "max_etf_position_pct": round(float(m.get("single_name_cap", 0.35)) * 100, 2),
        "min_holdings": 5,
    }


def _rebalance_rules(model: portfolio.Model) -> dict[str, Any]:
    template = _template_for_model(model)
    if template:
        return dict(template["rebalance_rules"])
    return {
        "frequency": "quarterly",
        "drift_band_pct": 5.0,
        "review_trigger": "review after material drift, mandate change, or risk-limit breach",
    }


def _risk_limit_violations(model: portfolio.Model, limits: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    max_single = float(limits.get("max_single_position_pct") or 0.0)
    for holding in model.holdings:
        weight_pct = round(float(holding.weight) * 100, 2)
        if max_single and weight_pct > max_single:
            out.append({
                "field": "max_single_position_pct",
                "ticker": holding.ticker,
                "limit": max_single,
                "actual": weight_pct,
                "message": f"{holding.ticker} is {weight_pct:.1f}% versus {max_single:.1f}% max single-position limit.",
            })
    min_holdings = int(limits.get("min_holdings") or 0)
    if min_holdings and len(model.holdings) < min_holdings:
        out.append({
            "field": "min_holdings",
            "limit": min_holdings,
            "actual": len(model.holdings),
            "message": f"Model has {len(model.holdings)} holdings versus {min_holdings} required by governance rules.",
        })
    total_pct = round(sum(float(holding.weight) for holding in model.holdings) * 100, 2)
    if model.holdings and abs(total_pct - 100.0) > 0.5:
        out.append({
            "field": "total_weight_pct",
            "limit": 100.0,
            "actual": total_pct,
            "message": f"Model weights sum to {total_pct:.1f}%; governance expects normalized 100% allocation.",
        })
    return out


def _template_for_model(model: portfolio.Model) -> dict[str, Any]:
    for template in model_library.public_list():
        if template["model_id"] == model.id:
            return template
    return {}


def _normalise_status(value: Any) -> str:
    status = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return status if status in APPROVAL_STATUSES else ""


def _normalise_action(value: Any, has_status: bool) -> str:
    action = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    allowed = {"approval_update", "archive_snapshot", "change_note", "model_edit", *REBALANCE_ACTIONS}
    if action in allowed:
        return action
    return "approval_update" if has_status else "change_note"


def _disclaimer() -> str:
    return (
        "Model governance records local approval, mandate, risk-limit, rebalance, "
        "and snapshot evidence only. It is analysis-only and does not execute "
        "trades, guarantee outcomes, or override Helios data provenance gates."
    )

"""Institutional provider, operations, and independent-validation APIs."""
from __future__ import annotations

from flask import Blueprint, request

from engine import (
    data, data_quality, independent_validation, operations, portfolio, provider_registry,
)

from .core import current_actor, err, ok

bp = Blueprint("institutional", __name__)


@bp.route("/api/providers")
def providers_status():
    return ok({
        **provider_registry.registry(),
        "reconciliations": provider_registry.reconciliations(limit=50),
    })


@bp.route("/api/providers/reconciliations", methods=["POST"])
def provider_reconciliation_record():
    payload = request.get_json(silent=True) or {}
    try:
        raw_symbols = payload.get("symbols") if isinstance(payload.get("symbols"), list) else []
        symbols = []
        for raw_symbol in raw_symbols[:100]:
            symbol = data.clean_symbol(str(raw_symbol or ""), fallback="")
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        if not symbols:
            raise ValueError("Provide at least one valid reconciliation symbol.")
        primary = str(payload.get("primary_provider") or "")
        backup = str(payload.get("backup_provider") or "")
        period = str(payload.get("period") or "3mo")[:12]
        observations = [
            data.reconcile_price_sources(
                symbol,
                period=period,
                primary_provider=primary,
                backup_provider=backup,
            )
            for symbol in symbols
        ]
        result = provider_registry.record_reconciliation(
            data_domain=str(payload.get("data_domain") or "prices"),
            primary_provider=primary,
            backup_provider=backup,
            observations=observations,
            actor=current_actor(),
            note=str(payload.get("note") or ""),
            observed_by_server=True,
        )
    except (ValueError, RuntimeError) as exc:
        return err(str(exc), 400 if isinstance(exc, ValueError) else 503)
    return ok({"reconciliation": result})


@bp.route("/api/providers/cutovers", methods=["POST"])
def provider_cutover_approve():
    payload = request.get_json(silent=True) or {}
    try:
        result = provider_registry.approve_cutover(
            data_domain=str(payload.get("data_domain") or "prices"),
            primary_provider=str(payload.get("primary_provider") or ""),
            backup_provider=str(payload.get("backup_provider") or ""),
            reconciliation_id=str(payload.get("reconciliation_id") or ""),
            actor=current_actor(),
            note=str(payload.get("note") or ""),
        )
    except (ValueError, RuntimeError) as exc:
        return err(str(exc), 400 if isinstance(exc, ValueError) else 503)
    return ok({"cutover": result, "registry": provider_registry.registry()})


@bp.route("/api/operations/status")
def operations_status():
    return ok(operations.operational_status())


@bp.route("/api/operations/incidents/sync", methods=["POST"])
def incidents_sync():
    quality = data_quality.dashboard_payload(sync_alerts=True)
    return ok({
        "data_quality": quality,
        "incident_sync": operations.sync_incidents(quality.get("issues") or [], actor=current_actor()),
    })


@bp.route("/api/operations/incidents/<incident_id>", methods=["POST"])
def incident_update(incident_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        incident = operations.update_incident(
            incident_id,
            status=str(payload.get("status") or ""),
            actor=current_actor(),
            owner=str(payload.get("owner") or ""),
            note=str(payload.get("note") or ""),
        )
    except (ValueError, RuntimeError) as exc:
        return err(str(exc), 400 if isinstance(exc, ValueError) else 503)
    return ok({"incident": incident, "events": operations.incident_events(incident_id)})


@bp.route("/api/operations/backup/verify", methods=["POST"])
def backup_verify():
    try:
        return ok({"verification": operations.verify_latest_backup(actor=current_actor())})
    except RuntimeError as exc:
        return err(str(exc), 503)


@bp.route("/api/models/<model_id>/independent-validation")
def independent_validation_status(model_id: str):
    model = portfolio.get(model_id)
    if model is None:
        return err("Model not found.", 404)
    return ok({
        "status": independent_validation.status(model_id),
        "reviews": independent_validation.reviews(model_id),
        "exceptions": independent_validation.exceptions(model_id),
    })


@bp.route("/api/models/<model_id>/independent-validation", methods=["POST"])
def independent_validation_review(model_id: str):
    model = portfolio.get(model_id)
    if model is None:
        return err("Model not found.", 404)
    payload = request.get_json(silent=True) or {}
    try:
        review = independent_validation.record_review(
            model_id=model_id,
            model_version=int(payload.get("model_version") or independent_validation.current_model_version(model_id)),
            sponsor=str(payload.get("sponsor") or ""),
            validator=current_actor(),
            outcome=str(payload.get("outcome") or ""),
            controls=payload.get("controls") if isinstance(payload.get("controls"), dict) else {},
            findings=payload.get("findings") if isinstance(payload.get("findings"), list) else [],
            reviewed_at=str(payload.get("reviewed_at") or "") or None,
            next_review_due=str(payload.get("next_review_due") or "") or None,
            note=str(payload.get("note") or ""),
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        return err(str(exc), 400 if isinstance(exc, (TypeError, ValueError)) else 503)
    return ok({"review": review, "status": independent_validation.status(model_id)})


@bp.route("/api/models/<model_id>/validation-exceptions", methods=["POST"])
def validation_exception_create(model_id: str):
    model = portfolio.get(model_id)
    if model is None:
        return err("Model not found.", 404)
    payload = request.get_json(silent=True) or {}
    try:
        exception = independent_validation.record_exception(
            model_id=model_id,
            model_version=int(payload.get("model_version") or independent_validation.current_model_version(model_id)),
            control_key=str(payload.get("control_key") or ""),
            owner=str(payload.get("owner") or ""),
            approver=current_actor(),
            reason=str(payload.get("reason") or ""),
            compensating_controls=(
                payload.get("compensating_controls")
                if isinstance(payload.get("compensating_controls"), list) else []
            ),
            expires_at=str(payload.get("expires_at") or ""),
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        return err(str(exc), 400 if isinstance(exc, (TypeError, ValueError)) else 503)
    return ok({"exception": exception, "status": independent_validation.status(model_id)})


@bp.route("/api/models/<model_id>/validation-exceptions/<exception_id>", methods=["POST"])
def validation_exception_resolve(model_id: str, exception_id: str):
    if portfolio.get(model_id) is None:
        return err("Model not found.", 404)
    payload = request.get_json(silent=True) or {}
    try:
        exception = independent_validation.resolve_exception(
            exception_id,
            actor=current_actor(),
            note=str(payload.get("note") or ""),
            status=str(payload.get("status") or "resolved"),
            expected_model_id=model_id,
        )
    except (ValueError, RuntimeError) as exc:
        return err(str(exc), 400 if isinstance(exc, ValueError) else 503)
    return ok({"exception": exception, "status": independent_validation.status(model_id)})

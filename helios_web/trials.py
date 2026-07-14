"""Prospective trial registry routes."""
from __future__ import annotations

from flask import Blueprint, request

from engine import persistence, trials

from .core import ANALYSIS_ONLY_DISCLAIMER, _safe_int_arg, current_actor, err, ok

bp = Blueprint("trials", __name__)


@bp.route("/api/trials")
def list_trials():
    limit, limit_error = _safe_int_arg("limit", 200, 1, 1000)
    if limit_error:
        return err(limit_error, 400)
    return ok({
        "trials": trials.list_trials(
            limit=limit,
            target_kind=str(request.args.get("target_kind") or ""),
            target_id=str(request.args.get("target_id") or ""),
        ),
        "storage_available": persistence.get_store().available,
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })


@bp.route("/api/trials", methods=["POST"])
def register_trial():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return err("Request body must be a JSON object.", 400)
    try:
        result = trials.register(
            target_kind=str(body.get("target_kind") or ""),
            target_id=str(body.get("target_id") or ""),
            protocol=body.get("protocol") if isinstance(body.get("protocol"), dict) else {},
            actor=current_actor(),
            supersedes_trial_id=str(body.get("supersedes_trial_id") or ""),
        )
    except ValueError as exc:
        return err(str(exc), 400)
    if not result.get("created"):
        return err(result.get("warning") or "Trial could not be registered.", int(result.get("status_code") or 503))
    return ok({"trial": result["trial"], "disclaimer": ANALYSIS_ONLY_DISCLAIMER})


@bp.route("/api/trials/<trial_id>")
def trial_detail(trial_id: str):
    trial = trials.get(trial_id)
    if not trial:
        return err("Unknown trial.", 404)
    return ok({"trial": trial, "disclaimer": ANALYSIS_ONLY_DISCLAIMER})


@bp.route("/api/trials/<trial_id>/assess", methods=["POST"])
def assess_trial(trial_id: str):
    try:
        result = trials.record_assessment(trial_id, actor=current_actor())
    except ValueError as exc:
        return err(str(exc), 404)
    except RuntimeError as exc:
        return err(str(exc), 503)
    return ok({**result, "disclaimer": ANALYSIS_ONLY_DISCLAIMER})


@bp.route("/api/trials/<trial_id>/close", methods=["POST"])
def close_trial(trial_id: str):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return err("Request body must be a JSON object.", 400)
    try:
        result = trials.close(
            trial_id,
            status=str(body.get("status") or ""),
            note=str(body.get("note") or ""),
            actor=current_actor(),
        )
    except ValueError as exc:
        return err(str(exc), 400)
    if not result.get("closed"):
        return err(result.get("warning") or "Trial could not be closed.", int(result.get("status_code") or 503))
    return ok({"trial": result["trial"], "disclaimer": ANALYSIS_ONLY_DISCLAIMER})

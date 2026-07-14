"""Immutable evidence replay and integrity verification routes."""
from __future__ import annotations

from flask import Blueprint

from engine import evidence

from .core import err, ok

bp = Blueprint("evidence", __name__)


@bp.route("/api/evidence/<evidence_id>")
def evidence_replay(evidence_id: str):
    result = evidence.replay(evidence_id)
    if result.get("status") == "not_found":
        return err("Unknown evidence snapshot.", 404)
    if not result.get("verified"):
        return err("Evidence snapshot hash verification failed.", 409)
    if result.get("calculation_replay", {}).get("supported") and not result.get("replay_verified"):
        return err("Evidence snapshot deterministic replay failed.", 409)
    return ok(result)

"""Rebalance blueprint — constrained current-to-target trade proposals."""
from __future__ import annotations

from flask import Blueprint, request

from engine import rebalance

from .core import err, ok

bp = Blueprint("rebalance", __name__)


@bp.route("/api/rebalance/propose", methods=["POST"])
def propose():
    """POST because a proposal is expensive compute over live account state —
    and keeping it off GET preserves the pure-read contract for caches."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return err("Request body must be a JSON object.", 400)
    account_id = str(body.get("account_id") or "").strip()
    model_id = str(body.get("model_id") or "").strip()
    if not account_id or not model_id:
        return err("account_id and model_id are required.", 400)
    constraints = body.get("constraints") if isinstance(body.get("constraints"), dict) else None
    return ok(rebalance.propose_rebalance(account_id, model_id, constraints=constraints))

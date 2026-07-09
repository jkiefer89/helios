"""Decision-journal blueprint — the operator's calls vs the engine's, scored."""
from __future__ import annotations

from flask import Blueprint, request

from engine import decision_journal

from .core import ANALYSIS_ONLY_DISCLAIMER, _safe_int_arg, err, ok

bp = Blueprint("decisions", __name__)


@bp.route("/api/decisions")
def list_decisions():
    limit, limit_error = _safe_int_arg("limit", 200, 1, 1000)
    if limit_error:
        return err(limit_error, 400)
    entries = decision_journal.list_decisions(limit=limit)
    return ok({
        "decisions": entries,
        "scoreboard": decision_journal.scoreboard(entries),
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })


@bp.route("/api/decisions", methods=["POST"])
def record_decision():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return err("Request body must be a JSON object.", 400)
    try:
        entry = decision_journal.record_decision(
            target_kind=str(body.get("target_kind") or "instrument"),
            target_id=str(body.get("target_id") or "").strip(),
            my_action=str(body.get("my_action") or ""),
            rationale=str(body.get("rationale") or ""),
            signal=body.get("signal") if isinstance(body.get("signal"), dict) else None,
            mandate=str(body.get("mandate") or ""),
            context=body.get("context") if isinstance(body.get("context"), dict) else None,
        )
    except ValueError as exc:
        return err(str(exc), 400)
    if not entry.get("decision_id"):
        return err(entry.get("warning") or "Decision could not be recorded (persistence unavailable).", 503)
    return ok({"decision": entry, "disclaimer": ANALYSIS_ONLY_DISCLAIMER})

"""AI blueprint — optional AI Copilot proxy. Provider calls are server-side
and fail closed; payload introspection lives in engine.ai_copilot."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from engine import ai_copilot

from .core import ANALYSIS_ONLY_DISCLAIMER, clean, err, ok

bp = Blueprint("ai", __name__)


@bp.route("/api/ai/status")
def ai_status():
    provider = ai_copilot.get_provider()
    return ok(provider.status())


@bp.route("/api/ai/opportunity/explain", methods=["POST"])
def ai_opportunity_explain():
    return _ai_call("explain_opportunity")


@bp.route("/api/ai/opportunity/critique", methods=["POST"])
def ai_opportunity_critique():
    return _ai_call("critique_opportunity")


@bp.route("/api/ai/strategy/summary", methods=["POST"])
def ai_strategy_summary():
    return _ai_call("summarize_strategy")


@bp.route("/api/ai/clinic/summary", methods=["POST"])
def ai_clinic_summary():
    return _ai_call("summarize_portfolio_clinic")


@bp.route("/api/ai/report", methods=["POST"])
def ai_report():
    return _ai_call("write_advisor_report")


@bp.route("/api/ai/question", methods=["POST"])
def ai_question():
    return _ai_call("answer_question", require_question=True)


@bp.route("/api/ai/chat", methods=["POST"])
def ai_chat():
    """Multi-turn research dialogue: {messages: [{role, content}...], payload: {...}}."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return err("Request body must be a JSON object.", 400)
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return err("messages must be a non-empty array of {role, content}.", 400)
    payload = body.get("payload") or {}
    if not isinstance(payload, dict):
        return err("payload must be a JSON object.", 400)
    provider = ai_copilot.get_provider()
    status = provider.status()
    data_quality = ai_copilot.payload_data_quality(payload)
    if not status.get("available"):
        return jsonify(clean({
            "error": status.get("reason") or "AI provider unavailable.",
            "status": status,
            "data_quality": data_quality,
        })), 503
    try:
        result = provider.chat(messages, payload)
    except ValueError as exc:
        return err(str(exc), 400)
    except ai_copilot.AIError as exc:
        safe_status = exc.status or provider.status()
        return jsonify(clean({
            "error": str(exc),
            "status": safe_status,
            "data_quality": data_quality,
        })), getattr(exc, "status_code", 503)
    return ok({
        **result,
        "status": provider.status(),
        "data_quality": data_quality,
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })


def _ai_call(method_name: str, require_question: bool = False):
    parsed, parse_error = _ai_request_payload(require_question=require_question)
    if parse_error:
        return err(parse_error, 400)
    payload, question, regenerate = parsed
    provider = ai_copilot.get_provider()
    status = provider.status()
    data_quality = ai_copilot.payload_data_quality(payload)
    if not status.get("available"):
        return jsonify(clean({
            "error": status.get("reason") or "AI provider unavailable.",
            "status": status,
            "data_quality": data_quality,
        })), 503
    try:
        if method_name == "answer_question":
            result = provider.answer_question(payload, question, regenerate=regenerate)
        else:
            result = getattr(provider, method_name)(payload, regenerate=regenerate)
    except ai_copilot.AIError as exc:
        safe_status = exc.status or provider.status()
        return jsonify(clean({
            "error": str(exc),
            "status": safe_status,
            "data_quality": data_quality,
        })), getattr(exc, "status_code", 503)
    except (AttributeError, TypeError, ValueError):
        return err("Invalid AI request.", 400)
    return ok({
        "result": result,
        "status": provider.status(),
        "provider": result.get("provider"),
        "model": result.get("model"),
        "data_quality": data_quality,
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })


def _ai_request_payload(require_question: bool = False):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, "Request body must be a JSON object."
    payload = body.get("payload")
    if payload is None:
        payload = {k: v for k, v in body.items() if k not in {"question", "regenerate"}}
    if not isinstance(payload, dict):
        return None, "payload must be a JSON object."
    question = str(body.get("question") or "").strip()
    if require_question and not question:
        return None, "question is required."
    regenerate = bool(body.get("regenerate", False))
    return (payload, question, regenerate), None

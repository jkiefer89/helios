"""Independent model-validation reviews, exceptions, and review cadence.

This module records human control evidence around the deterministic Helios
validation engine. It never changes a model calculation or converts an
exception into model evidence.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import evidence, persistence, provider_registry

DEFAULT_REVIEW_CADENCE_DAYS = 365
VALID_OUTCOMES = {"approved", "rejected", "conditional"}
VALID_EXCEPTION_STATUSES = {"open", "resolved", "withdrawn"}


def current_model_version(model_id: str) -> int:
    events = persistence.get_store().model_governance_events(model_id, limit=1)
    return int(events[0].get("version") or 1) if events else 1


def record_review(
    *,
    model_id: str,
    model_version: int,
    sponsor: str,
    validator: str,
    outcome: str,
    controls: dict[str, Any],
    findings: list[dict[str, Any]] | list[str],
    reviewed_at: str | None = None,
    next_review_due: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    safe_model_id = str(model_id or "").strip()[:64]
    safe_sponsor = persistence.redact_secrets(sponsor)[:120]
    safe_validator = persistence.redact_secrets(validator)[:120]
    safe_outcome = str(outcome or "").strip().lower()
    if not safe_model_id:
        raise ValueError("model_id is required.")
    if int(model_version) < 1:
        raise ValueError("model_version must be at least 1.")
    if not safe_sponsor or not safe_validator:
        raise ValueError("Sponsor and independent validator identities are required.")
    if safe_sponsor.casefold() == safe_validator.casefold():
        raise ValueError("Independent validator must differ from the model sponsor.")
    if safe_outcome not in VALID_OUTCOMES:
        raise ValueError("outcome must be approved, rejected, or conditional.")
    if not isinstance(controls, dict) or not controls:
        raise ValueError("Documented validation controls are required.")
    reviewed = _date(reviewed_at or date.today().isoformat(), "reviewed_at")
    if reviewed > date.today():
        raise ValueError("reviewed_at cannot be in the future.")
    cadence = _cadence_days()
    due = _date(next_review_due or (reviewed + timedelta(days=cadence)).isoformat(), "next_review_due")
    if due <= reviewed:
        raise ValueError("next_review_due must be after reviewed_at.")
    store = persistence.get_store()
    if not store.available:
        raise RuntimeError(store.warning or "SQLite persistence is required for independent validation.")
    review_id = f"validation-{uuid.uuid4().hex[:24]}"
    with store.transaction(durable=True) as conn:
        conn.execute(
            """
            INSERT INTO independent_validation_reviews(
              review_id, created_at, model_id, model_version, sponsor, validator,
              outcome, reviewed_at, next_review_due, controls_json, findings_json, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id, persistence.utc_now(), safe_model_id, int(model_version),
                safe_sponsor, safe_validator, safe_outcome, reviewed.isoformat(),
                due.isoformat(), store._store_json(evidence.normalize(controls)),
                store._store_json(evidence.normalize({"items": findings if isinstance(findings, list) else []})),
                persistence.redact_secrets(note)[:1200],
            ),
        )
        store.audit_append(
            "independent_validation_review",
            {"review_id": review_id, "model_id": safe_model_id, "version": int(model_version), "outcome": safe_outcome},
            actor=safe_validator,
            required=True,
        )
    return next(row for row in reviews(safe_model_id, limit=100) if row["review_id"] == review_id)


def record_exception(
    *,
    model_id: str,
    model_version: int,
    control_key: str,
    owner: str,
    approver: str,
    reason: str,
    compensating_controls: list[str],
    expires_at: str,
) -> dict[str, Any]:
    safe_model_id = str(model_id or "").strip()[:64]
    safe_owner = persistence.redact_secrets(owner)[:120]
    safe_approver = persistence.redact_secrets(approver)[:120]
    safe_reason = persistence.redact_secrets(reason)[:1200]
    controls = [persistence.redact_secrets(item)[:400] for item in compensating_controls if str(item).strip()]
    expiry = _date(expires_at, "expires_at")
    if not safe_model_id or int(model_version) < 1:
        raise ValueError("A valid model_id and model_version are required.")
    if not str(control_key or "").strip():
        raise ValueError("control_key is required.")
    if not safe_owner or not safe_approver or safe_owner.casefold() == safe_approver.casefold():
        raise ValueError("Exception owner and independent approver must be different identified people.")
    if len(safe_reason) < 8 or not controls:
        raise ValueError("Exception reason and compensating controls are required.")
    if expiry <= date.today():
        raise ValueError("Exception expiry must be in the future.")
    store = persistence.get_store()
    if not store.available:
        raise RuntimeError(store.warning or "SQLite persistence is required for validation exceptions.")
    exception_id = f"exception-{uuid.uuid4().hex[:24]}"
    with store.transaction(durable=True) as conn:
        conn.execute(
            """
            INSERT INTO model_validation_exceptions(
              exception_id, created_at, model_id, model_version, control_key,
              owner, approver, reason, compensating_controls_json, expires_at,
              status, resolved_at, resolution_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', '', '')
            """,
            (
                exception_id, persistence.utc_now(), safe_model_id, int(model_version),
                str(control_key).strip()[:80], safe_owner, safe_approver, safe_reason,
                store._store_json({"items": controls}), expiry.isoformat(),
            ),
        )
        store.audit_append(
            "model_validation_exception_opened",
            {"exception_id": exception_id, "model_id": safe_model_id, "version": int(model_version)},
            actor=safe_approver,
            required=True,
        )
    return next(row for row in exceptions(safe_model_id, limit=100) if row["exception_id"] == exception_id)


def resolve_exception(
    exception_id: str,
    *,
    actor: str,
    note: str,
    status: str = "resolved",
    expected_model_id: str | None = None,
) -> dict[str, Any]:
    safe_status = str(status or "").strip().lower()
    if safe_status not in {"resolved", "withdrawn"}:
        raise ValueError("Exception status must be resolved or withdrawn.")
    if len(str(note or "").strip()) < 5:
        raise ValueError("A resolution note is required.")
    store = persistence.get_store()
    with store.transaction(durable=True) as conn:
        safe_exception_id = str(exception_id)[:64]
        safe_model_id = str(expected_model_id or "").strip()[:64]
        if safe_model_id:
            row = conn.execute(
                "SELECT * FROM model_validation_exceptions WHERE exception_id = ? AND model_id = ?",
                (safe_exception_id, safe_model_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM model_validation_exceptions WHERE exception_id = ?",
                (safe_exception_id,),
            ).fetchone()
        if not row:
            raise ValueError("Validation exception not found.")
        if row["status"] != "open":
            raise ValueError("Validation exception is already closed.")
        conn.execute(
            """
            UPDATE model_validation_exceptions
            SET status = ?, resolved_at = ?, resolution_note = ?
            WHERE exception_id = ? AND model_id = ? AND status = 'open'
            """,
            (
                safe_status, persistence.utc_now(), persistence.redact_secrets(note)[:1200],
                safe_exception_id, row["model_id"],
            ),
        )
        store.audit_append(
            "model_validation_exception_closed",
            {"exception_id": str(exception_id)[:64], "status": safe_status},
            actor=actor,
            required=True,
        )
    return next(row for row in exceptions(row["model_id"], limit=500) if row["exception_id"] == exception_id)


def status(model_id: str, model_version: int | None = None) -> dict[str, Any]:
    version = int(model_version or current_model_version(model_id))
    if not provider_registry.controls_required():
        return {
            "passed": True,
            "state": "not_required",
            "model_id": model_id,
            "model_version": version,
            "review": None,
            "exceptions": [],
            "detail": "Independent validation control is disabled for this local/test process.",
            "required_action": "",
        }
    review = next((row for row in reviews(model_id, limit=100) if row["model_version"] == version), None)
    open_exceptions = [row for row in exceptions(model_id, limit=500) if row["model_version"] == version and row["status"] == "open"]
    expired = [row for row in open_exceptions if _date(row["expires_at"], "expires_at") < date.today()]
    review_current = bool(
        review
        and review["outcome"] == "approved"
        and _date(review["reviewed_at"], "reviewed_at") <= date.today()
        and _date(review["next_review_due"], "next_review_due") >= date.today()
        and review["sponsor"].casefold() != review["validator"].casefold()
        and review["controls"]
    )
    passed = review_current and not expired
    if not review:
        detail = f"Model version {version} has no independent validation review."
    elif not review_current:
        detail = "The latest independent validation review is not approved/current for this model version."
    elif expired:
        detail = f"{len(expired)} validation exception(s) expired without resolution."
    else:
        detail = (
            f"Independent review approved by {review['validator']} through "
            f"{review['next_review_due']}; {len(open_exceptions)} controlled exception(s) open."
        )
    return {
        "passed": passed,
        "state": "pass" if passed else "blocked",
        "model_id": model_id,
        "model_version": version,
        "review": review,
        "exceptions": open_exceptions,
        "detail": detail,
        "required_action": "" if passed else "Complete independent model validation and resolve or renew expired exceptions.",
    }


def reviews(model_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    store = persistence.get_store()
    if not store.available:
        return []
    params: tuple[Any, ...]
    if model_id:
        sql = "SELECT * FROM independent_validation_reviews WHERE model_id = ? ORDER BY reviewed_at DESC, created_at DESC LIMIT ?"
        params = (str(model_id)[:64], max(1, min(int(limit), 1000)))
    else:
        sql = "SELECT * FROM independent_validation_reviews ORDER BY reviewed_at DESC, created_at DESC LIMIT ?"
        params = (max(1, min(int(limit), 1000)),)
    with store._connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            **dict(row),
            "model_version": int(row["model_version"]),
            "controls": store._load_json(row["controls_json"]),
            "findings": _items(store._load_json(row["findings_json"])),
        }
        for row in rows
    ]


def exceptions(model_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    store = persistence.get_store()
    if not store.available:
        return []
    params: tuple[Any, ...]
    if model_id:
        sql = "SELECT * FROM model_validation_exceptions WHERE model_id = ? ORDER BY created_at DESC LIMIT ?"
        params = (str(model_id)[:64], max(1, min(int(limit), 2000)))
    else:
        sql = "SELECT * FROM model_validation_exceptions ORDER BY created_at DESC LIMIT ?"
        params = (max(1, min(int(limit), 2000)),)
    with store._connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            **dict(row),
            "model_version": int(row["model_version"]),
            "compensating_controls": _items(store._load_json(row["compensating_controls_json"])),
        }
        for row in rows
    ]


def _cadence_days() -> int:
    try:
        value = int(__import__("os").environ.get("HELIOS_MODEL_VALIDATION_CADENCE_DAYS", DEFAULT_REVIEW_CADENCE_DAYS))
    except (TypeError, ValueError):
        value = DEFAULT_REVIEW_CADENCE_DAYS
    return max(30, min(value, 1095))


def _items(value: Any) -> list[Any]:
    if isinstance(value, dict):
        items = value.get("items")
        return items if isinstance(items, list) else []
    return value if isinstance(value, list) else []


def _date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD).") from exc

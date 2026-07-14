"""Operational controls: privileged audit, incidents, and backup verification."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from . import persistence

SEVERITIES = {"info", "warning", "high", "critical"}
INCIDENT_STATUSES = {"open", "acknowledged", "resolved"}
_PRIVILEGED_EVENT_LOCK = threading.Lock()


def record_privileged_event(
    *,
    actor: str,
    actor_roles: list[str] | tuple[str, ...],
    action: str,
    resource: str,
    outcome: str,
    source_ip: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    store = persistence.get_store()
    if not store.available:
        return None
    event_id = f"priv-{uuid.uuid4().hex[:24]}"
    created_at = persistence.utc_now()
    safe_actor = persistence.redact_secrets(actor)[:120] or "unknown"
    roles = sorted({persistence.redact_secrets(role)[:40] for role in actor_roles if str(role).strip()})
    safe_action = persistence.redact_secrets(action)[:80]
    safe_resource = persistence.redact_secrets(resource)[:160]
    safe_outcome = persistence.redact_secrets(outcome)[:32]
    ip_hash = hashlib.sha256(str(source_ip or "").encode("utf-8")).hexdigest() if source_ip else ""
    safe_details = _redact_object(details or {})
    # Acquire the store connection before the chain lock. Privileged request
    # transactions already hold the encrypted-store lock; reversing this order
    # lets a second request hold the chain lock while waiting on the store and
    # deadlocks the first request during its final audit append.
    with store._connect(durable=True) as conn:
        with _PRIVILEGED_EVENT_LOCK:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except Exception:
                pass
            head = conn.execute(
                "SELECT event_hash FROM privileged_events ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            previous_hash = head["event_hash"] if head else "genesis"
            envelope = {
                "event_id": event_id,
                "created_at": created_at,
                "actor": safe_actor,
                "actor_roles": roles,
                "action": safe_action,
                "resource": safe_resource,
                "outcome": safe_outcome,
                "source_ip_hash": ip_hash,
                "details": safe_details,
                "previous_hash": previous_hash,
            }
            event_hash = _hash(envelope)
            conn.execute(
                """
                INSERT INTO privileged_events(
                  event_id, created_at, actor, actor_roles, action, resource,
                  outcome, source_ip_hash, details_json, previous_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, created_at, safe_actor, ",".join(roles), safe_action,
                    safe_resource, safe_outcome, ip_hash, store._store_json(safe_details),
                    previous_hash, event_hash,
                ),
            )
    return {**envelope, "event_hash": event_hash}


def privileged_events(limit: int = 100) -> list[dict[str, Any]]:
    store = persistence.get_store()
    if not store.available:
        return []
    with store.transaction(durable=True) as conn:
        rows = conn.execute(
            "SELECT * FROM privileged_events ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (max(1, min(int(limit), 1000)),),
        ).fetchall()
    return [
        {
            **dict(row),
            "actor_roles": [part for part in str(row["actor_roles"]).split(",") if part],
            "details": store._load_json(row["details_json"]),
        }
        for row in rows
    ]


def verify_privileged_events(limit: int = 100000) -> dict[str, Any]:
    store = persistence.get_store()
    if not store.available:
        return {"status": "unavailable", "warning": store.warning}
    with store._connect() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM privileged_events").fetchone()[0])
        rows = conn.execute(
            "SELECT * FROM privileged_events ORDER BY created_at ASC, rowid ASC LIMIT ?",
            (max(1, min(int(limit), 1_000_000)),),
        ).fetchall()
    previous = "genesis"
    for row in rows:
        details = store._load_json(row["details_json"])
        envelope = {
            "event_id": row["event_id"],
            "created_at": row["created_at"],
            "actor": row["actor"],
            "actor_roles": [part for part in str(row["actor_roles"]).split(",") if part],
            "action": row["action"],
            "resource": row["resource"],
            "outcome": row["outcome"],
            "source_ip_hash": row["source_ip_hash"],
            "details": details,
            "previous_hash": previous,
        }
        if row["previous_hash"] != previous or row["event_hash"] != _hash(envelope):
            return {"status": "broken", "event_id": row["event_id"], "entries_checked": len(rows)}
        previous = row["event_hash"]
    return {
        "status": "partial" if len(rows) < total else "intact",
        "entries": len(rows),
        "total_entries": total,
        "head_hash": previous,
    }


def sync_incidents(issues: list[dict[str, Any]], *, actor: str = "system") -> dict[str, Any]:
    """Persist current blocker/warning conditions after an explicit refresh/sync."""
    store = persistence.get_store()
    if not store.available:
        return {"synced": False, "warning": store.warning, "incidents": []}
    incoming: dict[str, dict[str, Any]] = {}
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        category = persistence.redact_secrets(str(issue.get("category") or "operations"))[:64]
        target = persistence.redact_secrets(str(issue.get("target") or "workspace"))[:120]
        source_key = f"{category}:{target}".lower()
        severity = str(issue.get("severity") or "warning").lower()
        if severity == "blocker":
            severity = "high"
        if severity not in SEVERITIES:
            severity = "warning"
        incoming[source_key] = {
            "category": category,
            "target": target,
            "severity": severity,
            "title": f"{category.replace('_', ' ').title()}: {target}",
            "detail": persistence.redact_secrets(str(issue.get("detail") or ""))[:1600],
            "metadata": {
                "target": target,
                "next_step": persistence.redact_secrets(str(issue.get("next_step") or ""))[:800],
                "source": "data_quality",
            },
        }
    now = persistence.utc_now()
    owner = persistence.redact_secrets(__import__("os").environ.get("HELIOS_INCIDENT_OWNER", "Unassigned"))[:120]
    created = 0
    resolved = 0
    with store._connect() as conn:
        existing = {
            row["source_key"]: row
            for row in conn.execute("SELECT * FROM operational_incidents WHERE status != 'resolved'").fetchall()
        }
        for source_key, issue in incoming.items():
            prior = existing.get(source_key)
            if prior:
                conn.execute(
                    """
                    UPDATE operational_incidents
                    SET updated_at = ?, severity = ?, title = ?, detail = ?, metadata_json = ?
                    WHERE incident_id = ?
                    """,
                    (now, issue["severity"], issue["title"], issue["detail"], store._store_json(issue["metadata"]), prior["incident_id"]),
                )
                continue
            incident_id = f"incident-{uuid.uuid4().hex[:24]}"
            conn.execute(
                """
                INSERT INTO operational_incidents(
                  incident_id, created_at, updated_at, category, severity, status,
                  owner, title, source_key, detail, metadata_json
                ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)
                """,
                (
                    incident_id, now, now, issue["category"], issue["severity"], owner,
                    issue["title"], source_key, issue["detail"], store._store_json(issue["metadata"]),
                ),
            )
            _incident_event(conn, store, incident_id, actor, "opened", "Detected by data-quality sync.")
            created += 1
        for source_key, prior in existing.items():
            if source_key in incoming:
                continue
            conn.execute(
                """
                UPDATE operational_incidents
                SET status = 'resolved', updated_at = ?, resolved_at = ?
                WHERE incident_id = ? AND status != 'resolved'
                """,
                (now, now, prior["incident_id"]),
            )
            _incident_event(conn, store, prior["incident_id"], actor, "auto_resolved", "Condition cleared on explicit sync.")
            resolved += 1
        store.audit_append(
            "operational_incident_sync",
            {"incoming": len(incoming), "created": created, "resolved": resolved},
            actor=actor,
            required=True,
        )
    return {"synced": True, "created": created, "resolved": resolved, "incidents": incidents(limit=500)}


def update_incident(incident_id: str, *, status: str, actor: str, owner: str = "", note: str = "") -> dict[str, Any]:
    safe_status = str(status or "").lower()
    if safe_status not in {"acknowledged", "resolved"}:
        raise ValueError("Incident status must be acknowledged or resolved.")
    if len(str(note or "").strip()) < 5:
        raise ValueError("An incident note is required.")
    store = persistence.get_store()
    now = persistence.utc_now()
    with store.transaction(durable=True) as conn:
        row = conn.execute(
            "SELECT * FROM operational_incidents WHERE incident_id = ?",
            (str(incident_id)[:64],),
        ).fetchone()
        if not row:
            raise ValueError("Incident not found.")
        if row["status"] == "resolved" and safe_status != "resolved":
            raise ValueError("Resolved incidents cannot be reopened without a new monitored condition.")
        safe_owner = persistence.redact_secrets(owner)[:120] or row["owner"]
        acknowledged = row["acknowledged_at"] or (now if safe_status == "acknowledged" else "")
        resolved_at = now if safe_status == "resolved" else row["resolved_at"]
        conn.execute(
            """
            UPDATE operational_incidents
            SET status = ?, owner = ?, updated_at = ?, acknowledged_at = ?, resolved_at = ?
            WHERE incident_id = ?
            """,
            (safe_status, safe_owner, now, acknowledged, resolved_at, str(incident_id)[:64]),
        )
        _incident_event(conn, store, str(incident_id)[:64], actor, safe_status, note)
        store.audit_append(
            f"incident_{safe_status}",
            {"incident_id": str(incident_id)[:64], "owner": safe_owner},
            actor=actor,
            required=True,
        )
    return next(row for row in incidents(limit=1000) if row["incident_id"] == incident_id)


def incidents(limit: int = 200, status: str = "") -> list[dict[str, Any]]:
    store = persistence.get_store()
    if not store.available:
        return []
    if status:
        sql = "SELECT * FROM operational_incidents WHERE status = ? ORDER BY updated_at DESC LIMIT ?"
        params = (str(status)[:24], max(1, min(int(limit), 2000)))
    else:
        sql = "SELECT * FROM operational_incidents ORDER BY updated_at DESC LIMIT ?"
        params = (max(1, min(int(limit), 2000)),)
    with store._connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        payload = dict(row)
        metadata = store._load_json(row["metadata_json"])
        title_target = str(payload.get("title") or "").partition(": ")[2]
        source_target = str(payload.get("source_key") or "").partition(":")[2]
        target = persistence.redact_secrets(
            str(metadata.get("target") or title_target or source_target or "workspace")
        )[:120]
        result.append({**payload, "target": target, "metadata": metadata})
    return result


def incident_events(incident_id: str, limit: int = 200) -> list[dict[str, Any]]:
    store = persistence.get_store()
    if not store.available:
        return []
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM incident_events WHERE incident_id = ? ORDER BY created_at DESC LIMIT ?",
            (str(incident_id)[:64], max(1, min(int(limit), 1000))),
        ).fetchall()
    return [{**dict(row), "metadata": store._load_json(row["metadata_json"])} for row in rows]


def verify_latest_backup(*, actor: str) -> dict[str, Any]:
    """Dry-run the newest encrypted backup in memory; never mutates live data."""
    store = persistence.get_store()
    if not store.available or store.path is None:
        raise RuntimeError(store.warning or "SQLite persistence is unavailable.")
    store.flush(force=True)
    if store._cipher is None:
        raise RuntimeError("Backup verification requires encrypted SQLite snapshot backups.")
    backup = store._backup_existing_snapshot()
    if backup is None:
        raise RuntimeError("Could not create a current encrypted backup for verification.")
    raw = backup.read_bytes()
    if not raw.startswith(persistence.ENCRYPTED_DB_MAGIC):
        raise RuntimeError("Latest backup is not an encrypted Helios SQLite snapshot.")
    payload = store._cipher.decrypt_bytes(raw[len(persistence.ENCRYPTED_DB_MAGIC):])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.deserialize(payload)
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        version_row = conn.execute("SELECT MAX(version) AS version FROM schema_version").fetchone()
        schema_version = int(version_row["version"] or 0)
        audit = _verify_audit_connection(conn)
        counts = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("instruments", "models", "signal_journal", "report_snapshots", "audit_chain")
        }
    finally:
        conn.close()
    passed = integrity == "ok" and schema_version <= persistence.SCHEMA_VERSION and audit["status"] == "intact"
    result = {
        "passed": passed,
        "verified_at": persistence.utc_now(),
        "backup": backup.name,
        "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
        "integrity": integrity,
        "schema_version": schema_version,
        "supported_schema_version": persistence.SCHEMA_VERSION,
        "audit_chain": audit,
        "counts": counts,
        "restore_performed": False,
        "isolated_restore_tested": True,
        "live_data_mutated": False,
        "method": "in_memory_decrypt_integrity_schema_audit",
    }
    record_privileged_event(
        actor=actor,
        actor_roles=["admin"],
        action="backup_verify",
        resource=backup.name,
        outcome="passed" if passed else "failed",
        details=result,
    )
    return result


def operational_status() -> dict[str, Any]:
    store = persistence.get_store()
    rows = incidents(limit=1000)
    latest_backup_verification = next(
        (event for event in privileged_events(limit=200) if event.get("action") == "backup_verify"),
        None,
    )
    return {
        "incidents": rows,
        "summary": {
            "open": sum(1 for row in rows if row["status"] == "open"),
            "acknowledged": sum(1 for row in rows if row["status"] == "acknowledged"),
            "critical": sum(1 for row in rows if row["severity"] == "critical" and row["status"] != "resolved"),
        },
        "incident_owner": __import__("os").environ.get("HELIOS_INCIDENT_OWNER", "Unassigned"),
        "notification_adapter": {
            "configured": bool(__import__("os").environ.get("HELIOS_INCIDENT_WEBHOOK_URL", "").strip()),
            "mode": "explicit_external_adapter",
            "automatic_delivery": False,
        },
        "backup": store.backup_status(),
        "latest_backup_verification": latest_backup_verification,
        "audit_chain": store.audit_verify(limit=100000),
        "privileged_chain": verify_privileged_events(limit=100000),
        "persistence_encryption": store.encryption,
    }


def institutional_readiness() -> dict[str, Any]:
    """Fail-closed operational gate for approval and external-facing export.

    The application remains available for remediation while this gate is
    blocked. External contracts, IdP enrollment, off-host retention, and human
    model review still require explicit evidence outside this process.
    """
    if os.environ.get("HELIOS_INSTITUTIONAL_CONTROLS", "1").strip().lower() in {
        "0", "false", "no", "off",
    }:
        return {
            "passed": True,
            "state": "not_required",
            "detail": "Institutional operational controls are disabled for this local/test process.",
            "required_action": "",
            "checks": [],
            "blockers": [],
        }
    status = operational_status()
    verification = status.get("latest_backup_verification") or {}
    verification_details = verification.get("details") or {}
    max_backup_age = _env_int("HELIOS_BACKUP_VERIFICATION_MAX_AGE_DAYS", 30, 1, 365)
    verification_age = _age_days(verification.get("created_at"))
    current_audit_head = str((status.get("audit_chain") or {}).get("head_hash") or "")
    verified_audit_head = str((verification_details.get("audit_chain") or {}).get("head_hash") or "")
    auth_enabled = os.environ.get("HELIOS_AUTH", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }
    mfa_or_sso = _env_bool("HELIOS_REQUIRE_MFA", False) or _env_bool("HELIOS_SSO_ENABLED", False)
    trusted_transport = _env_bool("HELIOS_TRUSTED_TLS", False) or _env_bool("HELIOS_TRUSTED_PROXY_TLS", False)
    owner = str(status.get("incident_owner") or "").strip()
    checks = [
        _readiness_check(
            "persistence",
            bool(persistence.get_store().available),
            "Durable SQLite persistence is available.",
            "Enable durable persistence before institutional approval or export.",
        ),
        _readiness_check(
            "encryption",
            bool((status.get("persistence_encryption") or {}).get("enabled")),
            "Local research persistence is encrypted.",
            "Configure required persistence encryption and a protected key source.",
        ),
        _readiness_check(
            "backup_restore",
            bool(
                verification_details.get("passed")
                and verification_details.get("isolated_restore_tested")
                and verification_age is not None
                and verification_age <= max_backup_age
                and bool(current_audit_head)
                and current_audit_head == verified_audit_head
            ),
            (
                (
                    f"Latest isolated encrypted-backup verification is {verification_age} day(s) old "
                    "and matches the current application audit head."
                    if verification_age is not None and current_audit_head == verified_audit_head
                    else "The latest backup does not include the current audited application state."
                )
            ),
            f"Verify an encrypted backup restore within {max_backup_age} days.",
        ),
        _readiness_check(
            "audit_chain",
            (status.get("audit_chain") or {}).get("status") == "intact",
            "Application audit-chain integrity is intact.",
            "Repair and independently investigate the application audit-chain failure.",
        ),
        _readiness_check(
            "privileged_audit_chain",
            (status.get("privileged_chain") or {}).get("status") == "intact",
            "Privileged-action audit-chain integrity is intact.",
            "Restore durable privileged-action audit integrity before approval or export.",
        ),
        _readiness_check(
            "critical_incidents",
            int((status.get("summary") or {}).get("critical") or 0) == 0,
            "No unresolved critical operational incident is recorded.",
            "Resolve every critical incident before approval or external-facing export.",
        ),
        _readiness_check(
            "incident_owner",
            bool(owner and owner.lower() != "unassigned"),
            f"Operational owner: {owner or 'Unassigned'}.",
            "Assign an accountable incident and recovery owner.",
        ),
        _readiness_check(
            "authentication",
            auth_enabled,
            "Application authentication is enabled.",
            "Enable authentication for institutional operation.",
        ),
        _readiness_check(
            "mfa_or_sso",
            mfa_or_sso,
            "MFA enforcement or trusted-proxy SSO is configured.",
            "Require verified MFA or configure trusted-proxy SSO.",
        ),
        _readiness_check(
            "trusted_transport",
            trusted_transport,
            "Trusted TLS is explicitly asserted for the deployment boundary.",
            "Terminate trusted TLS at the approved proxy and assert the trusted transport boundary.",
        ),
    ]
    blockers = [row for row in checks if not row["passed"]]
    return {
        "passed": not blockers,
        "state": "pass" if not blockers else "blocked",
        "detail": "Institutional operational controls passed." if not blockers else "Institutional operational controls are incomplete.",
        "required_action": " ".join(row["required_action"] for row in blockers),
        "checks": checks,
        "blockers": blockers,
    }


def _readiness_check(key: str, passed: bool, detail: str, required_action: str) -> dict[str, Any]:
    return {
        "key": key,
        "passed": bool(passed),
        "state": "pass" if passed else "blocked",
        "detail": detail,
        "required_action": "" if passed else required_action,
    }


def _age_days(value: Any) -> int | None:
    if not value:
        return None
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - observed).days)
    except (TypeError, ValueError):
        return None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high))


def _incident_event(conn, store, incident_id: str, actor: str, action: str, note: str) -> None:
    conn.execute(
        """
        INSERT INTO incident_events(event_id, incident_id, created_at, actor, action, note, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, '{}')
        """,
        (
            f"incident-event-{uuid.uuid4().hex[:20]}", incident_id, persistence.utc_now(),
            persistence.redact_secrets(actor)[:120], action[:48], persistence.redact_secrets(note)[:1200],
        ),
    )


def _verify_audit_connection(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM audit_chain ORDER BY seq ASC").fetchall()
    previous = "genesis"
    for row in rows:
        if int(row["chain_version"] or 1) >= 2:
            source = f"{previous}|{row['payload_hash']}|{row['ts']}|{row['actor']}|{row['action']}"
        else:
            source = f"{previous}|{row['payload_hash']}|{row['ts']}|{row['action']}"
        expected = hashlib.sha256(source.encode()).hexdigest()
        if row["prev_hash"] != previous or row["chain_hash"] != expected:
            return {"status": "broken", "first_bad_seq": int(row["seq"]), "entries_checked": len(rows)}
        previous = row["chain_hash"]
    return {"status": "intact", "entries": len(rows), "head_hash": previous}


def _redact_object(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            safe_key = persistence.redact_secrets(str(key))[:120]
            compact = safe_key.lower().replace("-", "_")
            if any(marker in compact for marker in ("api_key", "apikey", "token", "secret", "password", "authorization")):
                out[safe_key] = "[redacted]"
            else:
                out[safe_key] = _redact_object(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact_object(item) for item in value[:200]]
    if isinstance(value, str):
        return persistence.redact_secrets(value)[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return persistence.redact_secrets(str(value))[:2000]


def _hash(value: dict[str, Any]) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

"""Operational controls: privileged audit, incidents, and backup verification."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
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
    export_result: dict[str, Any] = {"configured": False, "written": False}
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
            export_result = _append_privileged_export({**envelope, "event_hash": event_hash})
    return {**envelope, "event_hash": event_hash, "external_export": export_result}


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
    """Restore-test the newest off-host encrypted export in isolated memory."""
    started = time.monotonic()
    store = persistence.get_store()
    if not store.available or store.path is None:
        raise RuntimeError(store.warning or "SQLite persistence is unavailable.")
    store.flush(force=True)
    if store._cipher is None:
        raise RuntimeError("Backup verification requires encrypted SQLite snapshot backups.")
    destination, manifest_path, manifest, backup = _latest_export_bundle()
    raw = backup.read_bytes()
    if not raw.startswith(persistence.ENCRYPTED_DB_MAGIC):
        raise RuntimeError("Latest off-host export is not an encrypted Helios SQLite snapshot.")
    snapshot_hash = hashlib.sha256(raw).hexdigest()
    if snapshot_hash != str(manifest.get("snapshot_sha256") or ""):
        raise RuntimeError("Off-host snapshot checksum does not match its manifest.")
    if int(manifest.get("snapshot_bytes") or -1) != len(raw):
        raise RuntimeError("Off-host snapshot byte count does not match its manifest.")
    scope = persistence.tenancy.configured_scope()
    scope_hash = hashlib.sha256(f"{scope.tenant_id}|{scope.client_id}".encode("utf-8")).hexdigest()
    if str(manifest.get("workspace_scope_sha256") or "") != scope_hash:
        raise RuntimeError("Off-host snapshot belongs to a different workspace scope.")
    payload = store._cipher.decrypt_bytes(raw[len(persistence.ENCRYPTED_DB_MAGIC):])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.deserialize(payload)
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        version_row = conn.execute("SELECT MAX(version) AS version FROM schema_version").fetchone()
        schema_version = int(version_row["version"] or 0)
        audit = _verify_audit_connection(conn)
        privileged = _verify_privileged_connection(conn, store)
        counts = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("instruments", "models", "signal_journal", "report_snapshots", "audit_chain")
        }
    finally:
        conn.close()
    manifest_schema = int(manifest.get("schema_version") or 0)
    manifest_heads_match = (
        str(manifest.get("application_audit_head") or "") == str(audit.get("head_hash") or "")
        and str(manifest.get("privileged_audit_head") or "") == str(privileged.get("head_hash") or "")
    )
    integrity_passed = (
        integrity == "ok"
        and schema_version <= persistence.SCHEMA_VERSION
        and manifest_schema == schema_version
        and audit["status"] == "intact"
        and privileged["status"] == "intact"
        and manifest_heads_match
    )
    rto_seconds = round(max(0.0, time.monotonic() - started), 4)
    rpo_seconds = max(0.0, time.time() - backup.stat().st_mtime)
    rpo_target_seconds = _env_int("HELIOS_RESTORE_RPO_HOURS", 24, 1, 720) * 3600
    rto_target_seconds = _env_int("HELIOS_RESTORE_RTO_SECONDS", 300, 1, 86400)
    rpo_met = rpo_seconds <= rpo_target_seconds
    rto_met = rto_seconds <= rto_target_seconds
    passed = integrity_passed and rpo_met and rto_met
    result = {
        "passed": passed,
        "verified_at": persistence.utc_now(),
        "backup": backup.name,
        "manifest": manifest_path.name,
        "offhost_destination_sha256": hashlib.sha256(str(destination).encode("utf-8")).hexdigest(),
        "offhost_attested": _env_bool("HELIOS_OFFHOST_BACKUP_ATTESTED", False),
        "snapshot_sha256": snapshot_hash,
        "integrity": integrity,
        "schema_version": schema_version,
        "supported_schema_version": persistence.SCHEMA_VERSION,
        "audit_chain": audit,
        "privileged_audit_chain": privileged,
        "manifest_heads_match": manifest_heads_match,
        "counts": counts,
        "integrity_passed": integrity_passed,
        "rpo_seconds": round(rpo_seconds, 4),
        "rpo_target_seconds": rpo_target_seconds,
        "rpo_met": rpo_met,
        "rto_seconds": rto_seconds,
        "rto_target_seconds": rto_target_seconds,
        "rto_met": rto_met,
        "restore_drill_passed": passed,
        "restore_performed": False,
        "isolated_restore_tested": True,
        "live_data_mutated": False,
        "method": "offhost_manifest_checksum_scope_decrypt_integrity_schema_audit",
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


def export_encrypted_backup(*, actor: str) -> dict[str, Any]:
    """Export the encrypted snapshot and a checksum manifest to an operator path."""
    store = persistence.get_store()
    if not store.available or store.path is None:
        raise RuntimeError(store.warning or "SQLite persistence is unavailable.")
    if store._cipher is None:
        raise RuntimeError("Off-host backup export requires encrypted SQLite persistence.")
    raw_destination = (os.environ.get("HELIOS_OFFHOST_BACKUP_EXPORT_DIR") or "").strip()
    if not raw_destination:
        raise RuntimeError("Set HELIOS_OFFHOST_BACKUP_EXPORT_DIR to an operator-managed backup destination.")
    store.flush(force=True)
    raw = store.path.read_bytes()
    if not raw.startswith(persistence.ENCRYPTED_DB_MAGIC):
        raise RuntimeError("The current persistence snapshot is not encrypted.")
    destination = Path(raw_destination).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    snapshot_name = f"helios-{stamp}.hdb"
    snapshot_path = destination / snapshot_name
    manifest_path = destination / f"{snapshot_name}.manifest.json"
    scope = persistence.tenancy.configured_scope()
    scope_hash = hashlib.sha256(f"{scope.tenant_id}|{scope.client_id}".encode("utf-8")).hexdigest()
    application_audit = store.audit_verify(limit=100000)
    privileged_audit = verify_privileged_events(limit=100000)
    manifest = {
        "format": "helios-encrypted-backup-v1",
        "created_at": persistence.utc_now(),
        "snapshot": snapshot_name,
        "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
        "snapshot_bytes": len(raw),
        "schema_version": persistence.SCHEMA_VERSION,
        "encryption_key_id": str(store.encryption.get("key_id") or ""),
        "encryption_key_version": str(store.encryption.get("key_version") or ""),
        "workspace_scope_sha256": scope_hash,
        "application_audit_head": str(application_audit.get("head_hash") or ""),
        "privileged_audit_head": str(privileged_audit.get("head_hash") or ""),
        "plaintext_exported": False,
    }
    try:
        snapshot_path.write_bytes(raw)
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8",
        )
        for path in (snapshot_path, manifest_path):
            try:
                path.chmod(0o600)
            except OSError:
                pass
        event = record_privileged_event(
            actor=actor,
            actor_roles=["admin"],
            action="backup_export",
            resource=snapshot_name,
            outcome="passed",
            details={
                "snapshot_sha256": manifest["snapshot_sha256"],
                "snapshot_bytes": len(raw),
                "manifest": manifest_path.name,
                "destination_sha256": hashlib.sha256(str(destination).encode("utf-8")).hexdigest(),
                "offhost_attested": _env_bool("HELIOS_OFFHOST_BACKUP_ATTESTED", False),
                "plaintext_exported": False,
            },
        )
    except Exception:
        for path in (snapshot_path, manifest_path):
            try:
                path.unlink()
            except OSError:
                pass
        raise
    return {
        **manifest,
        "manifest": manifest_path.name,
        "destination_configured": True,
        "offhost_attested": _env_bool("HELIOS_OFFHOST_BACKUP_ATTESTED", False),
        "audit_event_id": (event or {}).get("event_id"),
    }


def audit_export_checkpoint(*, actor: str) -> dict[str, Any]:
    """Append a current chain checkpoint to the configured external audit spool."""
    if not (os.environ.get("HELIOS_AUDIT_EXPORT_PATH") or "").strip():
        raise RuntimeError("Set HELIOS_AUDIT_EXPORT_PATH to an operator-managed append destination.")
    event = record_privileged_event(
        actor=actor,
        actor_roles=["admin"],
        action="audit_export_checkpoint",
        resource="privileged_event_chain",
        outcome="passed",
        details={
            "application_audit": persistence.get_store().audit_verify(limit=100000),
            "privileged_audit": verify_privileged_events(limit=100000),
            "worm_siem_attested": _env_bool("HELIOS_WORM_SIEM_ATTESTED", False),
        },
    )
    export = (event or {}).get("external_export") or {}
    if not export.get("written"):
        raise RuntimeError(str(export.get("error") or "Audit checkpoint was not exported."))
    return {"event_id": event.get("event_id"), "event_hash": event.get("event_hash"), **export}


def audit_export_status() -> dict[str, Any]:
    raw_path = (os.environ.get("HELIOS_AUDIT_EXPORT_PATH") or "").strip()
    configured = bool(raw_path)
    path = Path(raw_path).expanduser() if configured else None
    exists = bool(path and path.is_file())
    latest = _latest_jsonl_record(path) if exists and path is not None else {}
    application_head = str(persistence.get_store().audit_verify(limit=100000).get("head_hash") or "")
    privileged_head = str(verify_privileged_events(limit=100000).get("head_hash") or "")
    latest_details = latest.get("details") if isinstance(latest.get("details"), dict) else {}
    checkpoint_application = latest_details.get("application_audit")
    checkpoint_application = checkpoint_application if isinstance(checkpoint_application, dict) else {}
    current_checkpoint = bool(
        latest.get("action") == "audit_export_checkpoint"
        and str(latest.get("event_hash") or "") == privileged_head
        and str(checkpoint_application.get("head_hash") or "") == application_head
    )
    return {
        "configured": configured,
        "required": _env_bool("HELIOS_AUDIT_EXPORT_REQUIRED", False),
        "worm_siem_attested": _env_bool("HELIOS_WORM_SIEM_ATTESTED", False),
        "written": exists,
        "latest_event_hash": str(latest.get("event_hash") or ""),
        "current_privileged_head": privileged_head,
        "checkpoint_application_head": str(checkpoint_application.get("head_hash") or ""),
        "current_application_head": application_head,
        "current_checkpoint": current_checkpoint,
        "destination_sha256": hashlib.sha256(str(path).encode("utf-8")).hexdigest() if path else "",
        "updated_at": (
            datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
            if exists else ""
        ),
        "local_append_only_evidence": True,
        "external_immutability_verified_by_helios": False,
    }


def backup_export_status() -> dict[str, Any]:
    configured = bool((os.environ.get("HELIOS_OFFHOST_BACKUP_EXPORT_DIR") or "").strip())
    latest = next(
        (event for event in privileged_events(limit=500) if event.get("action") == "backup_export"),
        None,
    )
    return {
        "configured": configured,
        "offhost_attested": _env_bool("HELIOS_OFFHOST_BACKUP_ATTESTED", False),
        "latest_export": latest,
        "plaintext_exported": False,
        "external_custody_verified_by_helios": False,
    }


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
        "backup_export": backup_export_status(),
        "audit_export": audit_export_status(),
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
    from . import identity

    sso_status = identity.verifier_status()
    mfa_or_sso = _env_bool("HELIOS_REQUIRE_MFA", False) or bool(sso_status.get("institutional_ready"))
    enterprise_identity_ready = not bool(sso_status.get("enabled")) or bool(
        sso_status.get("institutional_ready")
    )
    trusted_transport = _env_bool("HELIOS_TRUSTED_TLS", False) or _env_bool("HELIOS_TRUSTED_PROXY_TLS", False)
    owner = str(status.get("incident_owner") or "").strip()
    encryption = status.get("persistence_encryption") or {}
    backup_export = status.get("backup_export") or {}
    audit_export = status.get("audit_export") or {}
    latest_backup_export = backup_export.get("latest_export") or {}
    checks = [
        _readiness_check(
            "persistence",
            bool(persistence.get_store().available),
            "Durable SQLite persistence is available.",
            "Enable durable persistence before institutional approval or export.",
        ),
        _readiness_check(
            "encryption",
            bool(encryption.get("enabled")),
            "Local research persistence is encrypted.",
            "Configure required persistence encryption and a protected key source.",
        ),
        _readiness_check(
            "key_custody_rotation",
            bool(
                encryption.get("enabled")
                and encryption.get("external_custody")
                and encryption.get("custody_attested")
                and encryption.get("key_id")
                and encryption.get("key_version")
                and encryption.get("rotation_configured")
            ),
            (
                f"Encryption key {encryption.get('key_id') or 'unidentified'} version "
                f"{encryption.get('key_version') or 'unknown'} has external custody and a recovery key configured."
            ),
            "Inject the active key from KMS/HSM or an external vault, attest custody, identify its version, and configure at least one previous recovery key for rotation.",
        ),
        _readiness_check(
            "offhost_backup",
            bool(
                backup_export.get("configured")
                and backup_export.get("offhost_attested")
                and latest_backup_export.get("outcome") == "passed"
            ),
            "An encrypted backup export is recorded for an operator-attested off-host destination.",
            "Configure and attest an off-host backup destination, then export a current encrypted snapshot and manifest.",
        ),
        _readiness_check(
            "worm_siem_export",
            bool(
                audit_export.get("required")
                and audit_export.get("configured")
                and audit_export.get("worm_siem_attested")
                and audit_export.get("written")
                and audit_export.get("current_checkpoint")
            ),
            "Privileged events are appended to an operator-attested WORM/SIEM destination.",
            "Configure an append destination, attest external WORM/SIEM retention, and record an audit checkpoint.",
        ),
        _readiness_check(
            "backup_restore",
            bool(
                verification_details.get("passed")
                and verification_details.get("isolated_restore_tested")
                and verification_details.get("rpo_met")
                and verification_details.get("rto_met")
                and verification_details.get("manifest_heads_match")
                and verification_details.get("offhost_attested")
                and str(verification_details.get("method") or "").startswith("offhost_")
                and verification_age is not None
                and verification_age <= max_backup_age
                and bool(current_audit_head)
                and current_audit_head == verified_audit_head
            ),
            (
                (
                    f"Latest isolated encrypted-backup drill is {verification_age} day(s) old, "
                    f"met RPO {verification_details.get('rpo_seconds', 'n/a')}s / "
                    f"RTO {verification_details.get('rto_seconds', 'n/a')}s, and matches the current application audit head."
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
            "MFA enforcement or cryptographically verified proxy SSO is configured.",
            "Require verified MFA or configure signed proxy SSO with issuer, audience, and a strong assertion secret.",
        ),
        _readiness_check(
            "enterprise_identity",
            enterprise_identity_ready,
            (
                "Enabled enterprise identity is deployable."
                if sso_status.get("enabled") else
                "Enterprise identity is disabled; local authentication policy applies."
            ),
            "Complete signed SSO configuration, set exact trusted proxy IPs, and disable Basic fallback.",
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


def _verify_privileged_connection(
    conn: sqlite3.Connection,
    store: persistence.SQLiteStore,
) -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM privileged_events ORDER BY created_at ASC, rowid ASC").fetchall()
    previous = "genesis"
    for row in rows:
        envelope = {
            "event_id": row["event_id"],
            "created_at": row["created_at"],
            "actor": row["actor"],
            "actor_roles": [part for part in str(row["actor_roles"]).split(",") if part],
            "action": row["action"],
            "resource": row["resource"],
            "outcome": row["outcome"],
            "source_ip_hash": row["source_ip_hash"],
            "details": store._load_json(row["details_json"]),
            "previous_hash": previous,
        }
        if row["previous_hash"] != previous or row["event_hash"] != _hash(envelope):
            return {
                "status": "broken",
                "event_id": row["event_id"],
                "entries_checked": len(rows),
            }
        previous = row["event_hash"]
    return {"status": "intact", "entries": len(rows), "head_hash": previous}


def _latest_export_bundle() -> tuple[Path, Path, dict[str, Any], Path]:
    raw_destination = (os.environ.get("HELIOS_OFFHOST_BACKUP_EXPORT_DIR") or "").strip()
    if not raw_destination:
        raise RuntimeError("Set HELIOS_OFFHOST_BACKUP_EXPORT_DIR before running an off-host restore drill.")
    if not _env_bool("HELIOS_OFFHOST_BACKUP_ATTESTED", False):
        raise RuntimeError("Attest the operator-managed off-host backup destination before verification.")
    destination = Path(raw_destination).expanduser().resolve()
    try:
        manifests = sorted(
            destination.glob("helios-*.hdb.manifest.json"),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
        )
    except OSError as exc:
        raise RuntimeError("The configured off-host backup destination is unavailable.") from exc
    if not manifests:
        raise RuntimeError("No exported Helios backup manifest is available for restore verification.")
    manifest_path = manifests[-1].resolve()
    if manifest_path.parent != destination:
        raise RuntimeError("Off-host backup manifest resolved outside the configured destination.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Latest off-host backup manifest is unreadable or invalid.") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != "helios-encrypted-backup-v1":
        raise RuntimeError("Latest off-host backup manifest has an unsupported format.")
    snapshot_name = str(manifest.get("snapshot") or "")
    if not snapshot_name or Path(snapshot_name).name != snapshot_name:
        raise RuntimeError("Off-host backup manifest contains an unsafe snapshot path.")
    snapshot = (destination / snapshot_name).resolve()
    if snapshot.parent != destination or not snapshot.is_file():
        raise RuntimeError("Off-host backup snapshot referenced by the manifest is missing.")
    return destination, manifest_path, manifest, snapshot


def _latest_jsonl_record(path: Path, max_bytes: int = 1_048_576) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - max_bytes))
            lines = handle.read().splitlines()
    except OSError:
        return {}
    for line in reversed(lines):
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return {}


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


def _append_privileged_export(event: dict[str, Any]) -> dict[str, Any]:
    raw_path = (os.environ.get("HELIOS_AUDIT_EXPORT_PATH") or "").strip()
    required = _env_bool("HELIOS_AUDIT_EXPORT_REQUIRED", False)
    if not raw_path:
        if required:
            raise RuntimeError("Privileged audit export is required but HELIOS_AUDIT_EXPORT_PATH is unset.")
        return {"configured": False, "written": False, "required": False}
    path = Path(raw_path).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "format": "helios-privileged-event-v1",
                **_redact_object(event),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        ).encode("utf-8") + b"\n"
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return {
            "configured": True,
            "written": True,
            "required": required,
            "destination_sha256": hashlib.sha256(str(path).encode("utf-8")).hexdigest(),
            "event_hash": str(event.get("event_hash") or ""),
        }
    except Exception as exc:
        if required:
            raise RuntimeError(f"Required privileged audit export failed: {exc}") from exc
        return {
            "configured": True,
            "written": False,
            "required": False,
            "error": persistence.redact_secrets(str(exc)),
        }


def _hash(value: dict[str, Any]) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

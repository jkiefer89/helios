"""Institutional provider registry, reconciliation evidence, and cutover gates.

Provider credentials prove only that a connector is configured. Licensing,
entitlement, SLA ownership, and production approval are separate explicit facts;
Helios never infers them from an API key.
"""
from __future__ import annotations

import math
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from . import evidence, persistence

DEFAULT_RECONCILIATION_MAX_AGE_DAYS = 30
DEFAULT_MAX_ABS_DIFFERENCE_PCT = 0.25
DEFAULT_RECONCILIATION_MIN_SYMBOLS = 2
PROVIDER_DEFINITIONS = {
    "fmp": {
        "name": "Financial Modeling Prep",
        "domains": ["prices", "fundamentals"],
        "credential_env": "HELIOS_FMP_KEY",
        "license_env": "HELIOS_PROVIDER_FMP_LICENSED",
        "entitlement_env": "HELIOS_PROVIDER_FMP_ENTITLED",
        "sla_env": "HELIOS_PROVIDER_FMP_SLA_OWNER",
    },
    "intrinio": {
        "name": "Intrinio",
        "domains": ["fundamentals"],
        "credential_env": "HELIOS_INTRINIO_KEY",
        "license_env": "HELIOS_PROVIDER_INTRINIO_LICENSED",
        "entitlement_env": "HELIOS_PROVIDER_INTRINIO_ENTITLED",
        "sla_env": "HELIOS_PROVIDER_INTRINIO_SLA_OWNER",
    },
    "tiingo": {
        "name": "Tiingo",
        "domains": ["prices"],
        "credential_env": "HELIOS_TIINGO_KEY",
        "license_env": "HELIOS_PROVIDER_TIINGO_LICENSED",
        "entitlement_env": "HELIOS_PROVIDER_TIINGO_ENTITLED",
        "sla_env": "HELIOS_PROVIDER_TIINGO_SLA_OWNER",
    },
    "yfinance": {
        "name": "yfinance / Yahoo public interface",
        "domains": ["prices", "fundamentals"],
        "credential_env": "",
        "license_env": "",
        "entitlement_env": "",
        "sla_env": "",
        "research_only": True,
    },
}


def controls_required() -> bool:
    return _env_bool("HELIOS_INSTITUTIONAL_CONTROLS", True)


def registry() -> dict[str, Any]:
    active: dict[str, dict[str, Any]] = {}
    for row in cutovers(limit=100):
        if row["status"] == "approved":
            active.setdefault(row["data_domain"], row)
    providers = []
    for key, definition in PROVIDER_DEFINITIONS.items():
        credential_env = str(definition.get("credential_env") or "")
        license_env = str(definition.get("license_env") or "")
        entitlement_env = str(definition.get("entitlement_env") or "")
        sla_env = str(definition.get("sla_env") or "")
        configured = True if not credential_env else bool(os.environ.get(credential_env, "").strip())
        licensed = bool(license_env and _env_bool(license_env, False))
        entitled = bool(entitlement_env and _env_bool(entitlement_env, False))
        sla_owner = os.environ.get(sla_env, "").strip()[:120] if sla_env else ""
        roles = []
        for domain, row in active.items():
            if row["primary_provider"] == key:
                roles.append(f"{domain}:primary")
            if row["backup_provider"] == key:
                roles.append(f"{domain}:backup")
        providers.append({
            "key": key,
            "name": definition["name"],
            "domains": list(definition["domains"]),
            "configured": configured,
            "licensed": licensed,
            "entitled": entitled,
            "sla_owner": sla_owner,
            "research_only": bool(definition.get("research_only")),
            "institutional_ready": bool(configured and licensed and entitled and sla_owner),
            "roles": roles,
        })
    return {
        "controls_required": controls_required(),
        "providers": providers,
        "cutovers": list(active.values()),
        "reconciliation_policy": {
            "minimum_symbol_count": _env_int(
                "HELIOS_PROVIDER_RECONCILIATION_MIN_SYMBOLS",
                DEFAULT_RECONCILIATION_MIN_SYMBOLS,
                1,
                100,
            ),
            "maximum_age_days": _env_int(
                "HELIOS_PROVIDER_RECONCILIATION_MAX_AGE_DAYS",
                DEFAULT_RECONCILIATION_MAX_AGE_DAYS,
                1,
                365,
            ),
            "maximum_absolute_difference_pct": _env_float(
                "HELIOS_PROVIDER_RECONCILIATION_MAX_DIFF_PCT",
                DEFAULT_MAX_ABS_DIFFERENCE_PCT,
                0.0,
                100.0,
            ),
            "evidence_origin": "server_fetch",
        },
        "disclaimer": (
            "Provider configuration is not proof of licensing or entitlement. "
            "Institutional readiness requires explicit contractual confirmation, "
            "an SLA owner, reconciliation evidence, and an approved cutover."
        ),
    }


def record_reconciliation(
    *,
    data_domain: str,
    primary_provider: str,
    backup_provider: str,
    observations: list[dict[str, Any]],
    actor: str,
    note: str = "",
    observed_by_server: bool = False,
) -> dict[str, Any]:
    domain = _domain(data_domain)
    primary = _provider(primary_provider, domain)
    backup = _provider(backup_provider, domain)
    if primary == backup:
        raise ValueError("Primary and backup providers must be different.")
    rows = [_redact_object(row) for row in observations if isinstance(row, dict)]
    compared: list[dict[str, Any]] = []
    maximums: list[float] = []
    invalid_evidence = 0
    for row in rows:
        if str(row.get("status") or "").lower() != "ok":
            continue
        value = row.get("max_abs_diff_pct")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            invalid_evidence += 1
            continue
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            invalid_evidence += 1
            continue
        compared.append(row)
        maximums.append(numeric)
    limit = _env_float(
        "HELIOS_PROVIDER_RECONCILIATION_MAX_DIFF_PCT",
        DEFAULT_MAX_ABS_DIFFERENCE_PCT,
        0.0,
        100.0,
    )
    mismatches = sum(1 for value in maximums if value > limit)
    unavailable = len(rows) - len(compared)
    minimum_symbols = _env_int(
        "HELIOS_PROVIDER_RECONCILIATION_MIN_SYMBOLS",
        DEFAULT_RECONCILIATION_MIN_SYMBOLS,
        1,
        100,
    )
    status = (
        "passed"
        if (
            observed_by_server
            and len(rows) >= minimum_symbols
            and len(compared) == len(rows)
            and not mismatches
            and not invalid_evidence
        )
        else "failed"
    )
    reconciliation_id = f"recon-{uuid.uuid4().hex[:24]}"
    now = persistence.utc_now()
    store = persistence.get_store()
    if not store.available:
        raise RuntimeError(store.warning or "SQLite persistence is required for provider reconciliation.")
    evidence_payload = {
        "observations": rows,
        "max_allowed_difference_pct": limit,
        "minimum_symbol_count": minimum_symbols,
        "unavailable_count": unavailable,
        "invalid_evidence_count": invalid_evidence,
        "method": "side_by_side_adjusted_history_comparison",
        "observation_origin": "server_fetch" if observed_by_server else "untrusted_manual_input",
        "observed_at": now,
        "primary_provider": primary,
        "backup_provider": backup,
    }
    reconciliation_hash = evidence.sha256(evidence_payload)
    evidence_payload["evidence_hash"] = reconciliation_hash
    with store.transaction(durable=True) as conn:
        conn.execute(
            """
            INSERT INTO provider_reconciliations(
              reconciliation_id, created_at, actor, data_domain,
              primary_provider, backup_provider, symbol_count, compared_count,
              mismatch_count, max_abs_difference_pct, status, evidence_json, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reconciliation_id, now, persistence.redact_secrets(actor)[:120], domain,
                primary, backup, len(rows), len(compared), mismatches,
                max(maximums) if maximums else None, status,
                store._store_json(evidence_payload), persistence.redact_secrets(note)[:800],
            ),
        )
        store.audit_append(
            "provider_reconciliation",
            {
                "reconciliation_id": reconciliation_id,
                "domain": domain,
                "status": status,
                "evidence_hash": reconciliation_hash,
            },
            actor=actor,
            required=True,
        )
    return get_reconciliation(reconciliation_id) or {}


def approve_cutover(
    *,
    data_domain: str,
    primary_provider: str,
    backup_provider: str,
    reconciliation_id: str,
    actor: str,
    note: str,
) -> dict[str, Any]:
    domain = _domain(data_domain)
    primary = _provider(primary_provider, domain)
    backup = _provider(backup_provider, domain)
    if primary == backup:
        raise ValueError("Primary and backup providers must be different.")
    if len(str(note or "").strip()) < 8:
        raise ValueError("A cutover approval note is required.")
    rows = {row["key"]: row for row in registry()["providers"]}
    for provider in (primary, backup):
        status = rows[provider]
        if not status["institutional_ready"]:
            raise ValueError(
                f"{status['name']} is not institutionally ready; confirm configuration, "
                "licensing, entitlement, and SLA ownership explicitly."
            )
    reconciliation = get_reconciliation(reconciliation_id)
    if not reconciliation or reconciliation["data_domain"] != domain:
        raise ValueError("The reconciliation does not exist for this data domain.")
    if reconciliation["primary_provider"] != primary or reconciliation["backup_provider"] != backup:
        raise ValueError("The reconciliation provider pair does not match this cutover.")
    if reconciliation["status"] != "passed":
        raise ValueError("Provider cutover requires a passed reconciliation.")
    reconciliation_evidence = reconciliation.get("evidence") or {}
    supplied_hash = str(reconciliation_evidence.get("evidence_hash") or "")
    hashed_payload = {
        key: value for key, value in reconciliation_evidence.items() if key != "evidence_hash"
    }
    if (
        reconciliation_evidence.get("observation_origin") != "server_fetch"
        or not supplied_hash
        or evidence.sha256(hashed_payload) != supplied_hash
    ):
        raise ValueError("Provider cutover requires intact server-generated reconciliation evidence.")
    age_days = (datetime.now(timezone.utc) - _parse_datetime(reconciliation["created_at"])).days
    max_age = _env_int(
        "HELIOS_PROVIDER_RECONCILIATION_MAX_AGE_DAYS",
        DEFAULT_RECONCILIATION_MAX_AGE_DAYS,
        1,
        365,
    )
    if age_days > max_age:
        raise ValueError(f"Provider reconciliation is stale ({age_days} days; maximum {max_age}).")
    cutover_id = f"cutover-{uuid.uuid4().hex[:24]}"
    store = persistence.get_store()
    with store.transaction(durable=True) as conn:
        conn.execute(
            """
            INSERT INTO provider_cutovers(
              cutover_id, created_at, actor, data_domain, primary_provider,
              backup_provider, reconciliation_id, status, note, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?)
            """,
            (
                cutover_id, persistence.utc_now(), persistence.redact_secrets(actor)[:120],
                domain, primary, backup, reconciliation_id,
                persistence.redact_secrets(note)[:800],
                store._store_json({"reconciliation_age_days": age_days, "maximum_age_days": max_age}),
            ),
        )
        store.audit_append(
            "provider_cutover_approved",
            {"cutover_id": cutover_id, "domain": domain, "primary": primary, "backup": backup},
            actor=actor,
            required=True,
        )
    return next(row for row in cutovers(limit=50) if row["cutover_id"] == cutover_id)


def domain_readiness(data_domain: str) -> dict[str, Any]:
    domain = _domain(data_domain)
    if not controls_required():
        return {
            "passed": True,
            "state": "not_required",
            "detail": "Institutional provider controls are disabled for this local/test process.",
            "required_action": "",
            "cutover": None,
        }
    active = next((row for row in cutovers(limit=50) if row["data_domain"] == domain and row["status"] == "approved"), None)
    if not active:
        return {
            "passed": False,
            "state": "blocked",
            "detail": f"No approved licensed primary/backup cutover exists for {domain}.",
            "required_action": "Confirm provider contracts and entitlements, reconcile the feeds, and approve a cutover.",
            "cutover": None,
        }
    provider_rows = {row["key"]: row for row in registry()["providers"]}
    invalid = [
        provider for provider in (active["primary_provider"], active["backup_provider"])
        if not provider_rows.get(provider, {}).get("institutional_ready")
    ]
    if invalid:
        return {
            "passed": False,
            "state": "blocked",
            "detail": "Approved cutover references provider controls that are no longer complete: " + ", ".join(invalid),
            "required_action": "Restore licensing, entitlement, configuration, and SLA-owner evidence or approve a new cutover.",
            "cutover": active,
        }
    reconciliation = get_reconciliation(active["reconciliation_id"])
    max_age = _env_int(
        "HELIOS_PROVIDER_RECONCILIATION_MAX_AGE_DAYS",
        DEFAULT_RECONCILIATION_MAX_AGE_DAYS,
        1,
        365,
    )
    age = (datetime.now(timezone.utc) - _parse_datetime(reconciliation["created_at"])).days if reconciliation else max_age + 1
    reconciliation_evidence = reconciliation.get("evidence") if reconciliation else {}
    supplied_hash = str((reconciliation_evidence or {}).get("evidence_hash") or "")
    hashed_payload = {
        key: value for key, value in (reconciliation_evidence or {}).items()
        if key != "evidence_hash"
    }
    evidence_intact = bool(
        reconciliation_evidence
        and reconciliation_evidence.get("observation_origin") == "server_fetch"
        and supplied_hash
        and evidence.sha256(hashed_payload) == supplied_hash
    )
    passed = bool(
        reconciliation
        and reconciliation["status"] == "passed"
        and age <= max_age
        and evidence_intact
    )
    return {
        "passed": passed,
        "state": "pass" if passed else "blocked",
        "detail": (
            f"Approved {domain} cutover: {active['primary_provider']} primary, "
            f"{active['backup_provider']} backup; reconciliation age {age} day(s)."
            if passed else "The active provider reconciliation is missing, failed, stale, or has invalid evidence integrity."
        ),
        "required_action": "" if passed else "Run and approve a current provider reconciliation before institutional use.",
        "cutover": active,
        "reconciliation": reconciliation,
    }


def provider_key(label: str) -> str:
    """Normalize persisted adapter labels to their governed provider key."""
    value = str(label or "").strip().lower()
    aliases = {
        "fmp_eod_adjusted": "fmp",
        "financial_modeling_prep": "fmp",
        "tiingo_eod_adjusted": "tiingo",
        "yahoo": "yfinance",
        "yahoo_finance": "yfinance",
    }
    return aliases.get(value, value)


def provider_allowed(label: str, data_domain: str) -> bool:
    if not controls_required():
        return True
    status = domain_readiness(data_domain)
    cutover = status.get("cutover") if isinstance(status, dict) else None
    if not status.get("passed") or not isinstance(cutover, dict):
        return False
    key = provider_key(label)
    return key in {cutover.get("primary_provider"), cutover.get("backup_provider")}


def provider_contract_readiness(label: str, data_domain: str) -> dict[str, Any]:
    """Validate provider controls before a bootstrap reconciliation call.

    A reconciliation must happen before a cutover can exist, so this check does
    not require an active cutover. It does require the provider to be configured,
    licensed, entitled, assigned to an SLA owner, and not research-only whenever
    institutional controls are enabled.
    """
    domain = _domain(data_domain)
    key = _provider(label, domain)
    if not controls_required():
        return {"passed": True, "provider": key, "state": "not_required", "detail": ""}
    row = next((item for item in registry()["providers"] if item["key"] == key), None) or {}
    passed = bool(row.get("institutional_ready") and not row.get("research_only"))
    return {
        "passed": passed,
        "provider": key,
        "state": "pass" if passed else "blocked",
        "detail": (
            f"{row.get('name') or key} has complete contractual provider controls."
            if passed
            else (
                f"{row.get('name') or key} cannot be queried for institutional reconciliation; "
                "confirm configuration, licensing, entitlement, and SLA ownership."
            )
        ),
        "required_action": "" if passed else "Complete provider controls before any symbol is sent.",
    }


def provider_usage_readiness(label: str, data_domain: str) -> dict[str, Any]:
    status = domain_readiness(data_domain)
    key = provider_key(label)
    if not controls_required():
        return {**status, "provider": key or "unknown"}
    if not status.get("passed"):
        return {**status, "provider": key or "unknown"}
    cutover = status.get("cutover") or {}
    allowed = {str(cutover.get("primary_provider") or ""), str(cutover.get("backup_provider") or "")}
    passed = bool(key and key in allowed)
    return {
        **status,
        "passed": passed,
        "state": "pass" if passed else "blocked",
        "provider": key or "unknown",
        "detail": (
            f"Price history was produced by approved provider {key}."
            if passed
            else f"Price history provider {key or 'unknown'} is outside the approved primary/backup cutover."
        ),
        "required_action": (
            "" if passed else "Refresh the history through the approved primary or backup provider before institutional use."
        ),
    }


def cutovers(limit: int = 100) -> list[dict[str, Any]]:
    store = persistence.get_store()
    if not store.available:
        return []
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM provider_cutovers ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return [
        {
            **dict(row),
            "metadata": store._load_json(row["metadata_json"]),
        }
        for row in rows
    ]


def reconciliations(limit: int = 100) -> list[dict[str, Any]]:
    store = persistence.get_store()
    if not store.available:
        return []
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM provider_reconciliations ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return [_reconciliation_row(row, store) for row in rows]


def get_reconciliation(reconciliation_id: str) -> dict[str, Any] | None:
    store = persistence.get_store()
    if not store.available:
        return None
    with store._connect() as conn:
        row = conn.execute(
            "SELECT * FROM provider_reconciliations WHERE reconciliation_id = ?",
            (str(reconciliation_id)[:64],),
        ).fetchone()
    return _reconciliation_row(row, store) if row else None


def _reconciliation_row(row, store: persistence.SQLiteStore) -> dict[str, Any]:
    return {**dict(row), "evidence": store._load_json(row["evidence_json"])}


def _redact_object(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = persistence.redact_secrets(str(raw_key))[:120]
            compact = key.lower().replace("-", "_")
            if any(marker in compact for marker in (
                "api_key", "apikey", "token", "secret", "password", "authorization",
            )):
                result[key] = "[redacted]"
            else:
                result[key] = _redact_object(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact_object(item) for item in value[:500]]
    if isinstance(value, str):
        return persistence.redact_secrets(value)[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return persistence.redact_secrets(str(value))[:2000]


def _provider(value: str, domain: str) -> str:
    key = str(value or "").strip().lower()
    definition = PROVIDER_DEFINITIONS.get(key)
    if not definition or domain not in definition["domains"]:
        raise ValueError(f"Unknown {domain} provider '{value}'.")
    return key


def _domain(value: str) -> str:
    domain = str(value or "").strip().lower()
    if domain not in {"prices", "fundamentals"}:
        raise ValueError("data_domain must be prices or fundamentals.")
    return domain


def _parse_datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high))

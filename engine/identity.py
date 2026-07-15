"""Cryptographically verified enterprise identity assertions.

The trusted reverse proxy signs a compact JSON assertion with HMAC-SHA256.
Helios verifies signature, time, issuer, audience, roles, MFA, and workspace
scope. The shared secret remains server-side and is never returned by status
APIs or included in audit payloads.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import threading
import time
from typing import Any

from . import tenancy

ASSERTION_HEADER = "X-Helios-Assertion"
BARE_IDENTITY_HEADERS = (
    "X-Helios-User",
    "X-Helios-Roles",
    "X-Helios-MFA",
    "Remote-User",
    "X-Forwarded-User",
    "X-Auth-Request-User",
)
ALLOWED_ROLES = {
    "viewer", "analyst", "data_steward", "model_sponsor",
    "independent_validator", "approver", "admin",
}
_SUBJECT_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,120}$")
_ASSERTION_REPLAY_CACHE: dict[str, float] = {}
_ASSERTION_REPLAY_LOCK = threading.Lock()
_MAX_REPLAY_CACHE = 10_000


class IdentityAssertionError(ValueError):
    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.status_code = status_code


def _env_bool(name: str, default: bool = False) -> bool:
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


def sso_enabled() -> bool:
    return _env_bool("HELIOS_SSO_ENABLED", False)


def basic_fallback_allowed() -> bool:
    return _env_bool("HELIOS_SSO_ALLOW_BASIC_FALLBACK", False)


def verifier_status() -> dict[str, Any]:
    enabled = sso_enabled()
    secret = str(os.environ.get("HELIOS_SSO_ASSERTION_SECRET") or "")
    issuer = str(os.environ.get("HELIOS_SSO_ISSUER") or "").strip()
    audience = str(os.environ.get("HELIOS_SSO_AUDIENCE") or "helios").strip()
    trusted_proxy_count = len([
        part for part in os.environ.get("HELIOS_TRUSTED_PROXY_IPS", "").split(",") if part.strip()
    ])
    errors = []
    if enabled and len(secret.encode("utf-8")) < 32:
        errors.append("HELIOS_SSO_ASSERTION_SECRET must contain at least 32 bytes")
    if enabled and not issuer:
        errors.append("HELIOS_SSO_ISSUER is required")
    if enabled and not audience:
        errors.append("HELIOS_SSO_AUDIENCE is required")
    configured = enabled and not errors
    basic_fallback = basic_fallback_allowed()
    institutional_blockers = list(errors)
    if enabled and configured and trusted_proxy_count == 0:
        institutional_blockers.append("At least one HELIOS_TRUSTED_PROXY_IPS entry is required")
    if enabled and basic_fallback:
        institutional_blockers.append("HELIOS_SSO_ALLOW_BASIC_FALLBACK must be disabled")
    return {
        "enabled": enabled,
        "configured": configured,
        "institutional_ready": configured and trusted_proxy_count > 0 and not basic_fallback,
        "mode": "signed_hmac_assertion" if enabled else "disabled",
        "issuer_configured": bool(issuer),
        "audience": audience if enabled else "",
        "secret_configured": len(secret.encode("utf-8")) >= 32,
        "max_ttl_seconds": _env_int("HELIOS_SSO_MAX_TTL_SECONDS", 300, 30, 3600),
        "clock_skew_seconds": _env_int("HELIOS_SSO_CLOCK_SKEW_SECONDS", 30, 0, 300),
        "replay_protection": "process_local_one_time_jti",
        "trusted_proxy_count": trusted_proxy_count,
        "basic_fallback_allowed": basic_fallback,
        "errors": errors,
        "institutional_blockers": institutional_blockers,
    }


def _b64decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise IdentityAssertionError("Enterprise identity assertion encoding is invalid.") from exc


def _number(claims: dict[str, Any], name: str) -> float:
    value = claims.get(name)
    if isinstance(value, bool):
        raise IdentityAssertionError(f"Enterprise identity claim '{name}' is invalid.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise IdentityAssertionError(f"Enterprise identity claim '{name}' is required.") from exc
    if not math.isfinite(number):
        raise IdentityAssertionError(f"Enterprise identity claim '{name}' is invalid.")
    return number


def verify_assertion(token: str, *, now: float | None = None) -> dict[str, Any]:
    status = verifier_status()
    if not status["configured"]:
        raise IdentityAssertionError(
            "Enterprise identity verification is enabled but not fully configured.", 503,
        )
    pieces = str(token or "").strip().split(".")
    if len(pieces) != 2 or not all(pieces):
        raise IdentityAssertionError("Enterprise identity assertion format is invalid.")
    payload_segment, signature_segment = pieces
    secret = str(os.environ.get("HELIOS_SSO_ASSERTION_SECRET") or "").encode("utf-8")
    try:
        signed_payload = payload_segment.encode("ascii")
    except UnicodeEncodeError as exc:
        raise IdentityAssertionError("Enterprise identity assertion format is invalid.") from exc
    expected = hmac.new(secret, signed_payload, hashlib.sha256).digest()
    supplied = _b64decode(signature_segment)
    if not hmac.compare_digest(expected, supplied):
        raise IdentityAssertionError("Enterprise identity assertion signature is invalid.")
    try:
        claims = json.loads(_b64decode(payload_segment).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IdentityAssertionError("Enterprise identity assertion payload is invalid.") from exc
    if not isinstance(claims, dict):
        raise IdentityAssertionError("Enterprise identity assertion payload must be an object.")

    issuer = str(os.environ.get("HELIOS_SSO_ISSUER") or "").strip()
    audience = str(os.environ.get("HELIOS_SSO_AUDIENCE") or "helios").strip()
    claim_issuer = str(claims.get("iss") or "").encode("utf-8")
    if not hmac.compare_digest(claim_issuer, issuer.encode("utf-8")):
        raise IdentityAssertionError("Enterprise identity assertion issuer is invalid.")
    raw_audience = claims.get("aud")
    audiences = raw_audience if isinstance(raw_audience, list) else [raw_audience]
    if audience not in {str(item) for item in audiences if item is not None}:
        raise IdentityAssertionError("Enterprise identity assertion audience is invalid.")

    current = float(time.time() if now is None else now)
    skew = int(status["clock_skew_seconds"])
    issued_at = _number(claims, "iat")
    not_before = _number(claims, "nbf")
    expires_at = _number(claims, "exp")
    if issued_at > current + skew or not_before > current + skew:
        raise IdentityAssertionError("Enterprise identity assertion is not yet valid.")
    if expires_at <= current - skew:
        raise IdentityAssertionError("Enterprise identity assertion has expired.")
    if expires_at <= issued_at or expires_at - issued_at > int(status["max_ttl_seconds"]):
        raise IdentityAssertionError("Enterprise identity assertion lifetime exceeds policy.")

    subject = str(claims.get("sub") or "").strip()
    if not _SUBJECT_RE.fullmatch(subject):
        raise IdentityAssertionError("Enterprise identity subject is invalid.")
    jti = str(claims.get("jti") or "").strip()
    if not _SUBJECT_RE.fullmatch(jti):
        raise IdentityAssertionError("Enterprise identity assertion ID is invalid.")
    roles_value = claims.get("roles")
    if not isinstance(roles_value, list) or not roles_value:
        raise IdentityAssertionError("Enterprise identity roles must be a non-empty list.")
    roles = tuple(sorted({str(role).strip().lower() for role in roles_value if str(role).strip()}))
    if not roles or any(role not in ALLOWED_ROLES for role in roles):
        raise IdentityAssertionError("Enterprise identity assertion contains an unsupported role.")
    if not isinstance(claims.get("mfa_verified"), bool):
        raise IdentityAssertionError("Enterprise identity mfa_verified claim must be boolean.")

    scope = tenancy.configured_scope()
    if (
        str(claims.get("tenant_id") or "") != scope.tenant_id
        or str(claims.get("client_id") or "") != scope.client_id
    ):
        raise IdentityAssertionError("Enterprise identity assertion is outside this tenant/client scope.", 403)
    assertion_id_hash = hashlib.sha256(jti.encode("utf-8")).hexdigest()
    _consume_assertion_id(assertion_id_hash, expires_at, current, skew)
    return {
        "username": subject,
        "roles": roles,
        "mfa_verified": bool(claims["mfa_verified"]),
        "tenant_id": scope.tenant_id,
        "client_id": scope.client_id,
        "assertion_id_hash": assertion_id_hash[:16],
        "expires_at": int(expires_at),
    }


def _consume_assertion_id(assertion_id_hash: str, expires_at: float, now: float, skew: int) -> None:
    with _ASSERTION_REPLAY_LOCK:
        expired = [key for key, expiry in _ASSERTION_REPLAY_CACHE.items() if expiry < now - skew]
        for key in expired:
            _ASSERTION_REPLAY_CACHE.pop(key, None)
        if assertion_id_hash in _ASSERTION_REPLAY_CACHE:
            raise IdentityAssertionError("Enterprise identity assertion has already been used.")
        if len(_ASSERTION_REPLAY_CACHE) >= _MAX_REPLAY_CACHE:
            raise IdentityAssertionError(
                "Enterprise identity replay protection is at capacity; retry after assertions expire.",
                503,
            )
        _ASSERTION_REPLAY_CACHE[assertion_id_hash] = expires_at


def reset_replay_cache_for_tests() -> None:
    """Clear process-local replay state between isolated offline tests."""
    with _ASSERTION_REPLAY_LOCK:
        _ASSERTION_REPLAY_CACHE.clear()

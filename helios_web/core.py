"""Shared Flask app plus cross-cutting web concerns.

Owns the singleton ``app`` object, the HTTP Basic-Auth gate and brute-force
throttle, the CSRF heuristic, security headers, JSON error handlers, the
_ok/_err response helpers, request-arg parsing, the shared upload/live-fetch
semaphores, and the startup banner. Section routes live in sibling blueprint
modules that import from here.
"""
from __future__ import annotations

import math
import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from hmac import compare_digest
from collections.abc import Callable
from urllib.parse import urlsplit

import numpy as np
import pandas as pd
from flask import Flask, Response, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from engine import identity, tenancy

from .localenv import APP_ROOT

# No Flask template/static folders: the React dist (plus the inline build-
# instructions fallback) is served by the spa blueprint from frontend/dist.
app = Flask(__name__, root_path=str(APP_ROOT), static_folder=None, template_folder=None)
# Cap request bodies so a huge upload can't exhaust memory (16 MB is ample for CSVs).
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
FRONTEND_DIST = APP_ROOT / "frontend" / "dist"


# --------------------------------------------------------------------------- #
# Access control — HTTP Basic Auth guards the WHOLE app (pages, API, static).
#   HELIOS_USER / HELIOS_PASSWORD   configure the credentials
#   (password is auto-generated and printed at startup if unset)
#   HELIOS_AUTH=0                   disable the gate (trusted localhost only)
# --------------------------------------------------------------------------- #
AUTH_ENABLED = os.environ.get("HELIOS_AUTH", "1") != "0"
AUTH_USER = os.environ.get("HELIOS_USER", "advisor")


def _resolve_auth_password(raw: str | None) -> tuple[str, bool]:
    """Returns (password, generated). Empty/whitespace HELIOS_PASSWORD (e.g. an
    untouched .env.example copy) counts as unset: auto-generate instead of
    silently locking every login out with an unusable blank password."""
    if raw is None or not raw.strip():
        return secrets.token_urlsafe(9), True
    return raw, False


AUTH_PASSWORD, PASSWORD_GENERATED = _resolve_auth_password(os.environ.get("HELIOS_PASSWORD"))


@dataclass(frozen=True)
class Principal:
    username: str
    roles: tuple[str, ...]
    auth_method: str
    mfa_verified: bool
    tenant_id: str = tenancy.DEFAULT_TENANT_ID
    client_id: str = tenancy.DEFAULT_CLIENT_ID
    assertion_expires_at: float = 0.0


@dataclass
class SessionRecord:
    principal: Principal
    created_at: float
    last_seen: float
    absolute_expires_at: float


ROLE_PERMISSIONS = {
    "viewer": {"read"},
    "analyst": {"read", "research_write"},
    "data_steward": {"read", "data_write", "operations"},
    "model_sponsor": {"read", "research_write", "governance_submit"},
    "independent_validator": {"read", "validation_review"},
    "approver": {"read", "governance_approve"},
    "admin": {
        "read", "research_write", "data_write", "operations",
        "provider_admin", "governance_submit", "governance_approve",
        "validation_review", "security_admin",
    },
}
PRIVILEGED_PERMISSIONS = {
    "data_write", "operations", "provider_admin", "governance_approve",
    "governance_submit", "validation_review", "security_admin",
}
_SESSIONS: dict[str, SessionRecord] = {}
_SESSIONS_LOCK = threading.Lock()
SESSION_COOKIE = "helios_session"
REQUEST_TOKEN_HEADER = "X-Helios-Request-Token"
_REQUEST_TOKEN_SECRET = secrets.token_bytes(32)


def _auth_ok(username: str, password: str) -> bool:
    """Constant-time credential check (always compares both fields)."""
    if not username or not password:
        return False
    u_ok = compare_digest(username.encode("utf-8"), AUTH_USER.encode("utf-8"))
    p_ok = compare_digest(password.encode("utf-8"), AUTH_PASSWORD.encode("utf-8"))
    return u_ok and p_ok


# Brute-force throttle: per-remote-IP failure counter with a temporary lockout,
# plus a constant delay on every failed credential check.
_AUTH_FAILURE_DELAY_SECONDS = 0.3
_AUTH_LOCKOUT_THRESHOLD = 10
_AUTH_LOCKOUT_SECONDS = 60.0
_AUTH_FAILURES: dict[str, tuple[int, float]] = {}
_AUTH_FAILURES_LOCK = threading.Lock()

# Browser requests that mutate state must originate from our own origin.
# curl / same-origin app fetches send no Sec-Fetch-Site header and pass.
#
# CONTRACT: methods listed here bypass the CSRF check entirely, so every GET
# handler must be a pure read of caches/stores — no journal writes, no alert
# counter bumps, no forced feed fetches. Anything that mutates state must be
# a POST/PUT/DELETE route (see /api/macro/refresh for the pattern). Caching
# reads that populate an in-process cache are fine; durable writes are not.
_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _is_cross_site_request() -> bool:
    """CSRF heuristic using browser-set fetch metadata headers."""
    sec_fetch_site = (request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if sec_fetch_site and sec_fetch_site not in {"same-origin", "none"}:
        return True
    origin = (request.headers.get("Origin") or "").strip()
    if origin:
        origin_host = urlsplit(origin).netloc.lower()
        if origin_host != (request.host or "").lower():
            return True
    return False


def _auth_locked_out(remote: str) -> bool:
    with _AUTH_FAILURES_LOCK:
        count, last_failure = _AUTH_FAILURES.get(remote, (0, 0.0))
        if count < _AUTH_LOCKOUT_THRESHOLD:
            return False
        if time.monotonic() - last_failure < _AUTH_LOCKOUT_SECONDS:
            return True
        del _AUTH_FAILURES[remote]
        return False


def _register_auth_failure(remote: str) -> None:
    with _AUTH_FAILURES_LOCK:
        count, _ = _AUTH_FAILURES.get(remote, (0, 0.0))
        _AUTH_FAILURES[remote] = (count + 1, time.monotonic())
    app.logger.warning("Failed Basic-auth attempt from %s", remote)
    time.sleep(_AUTH_FAILURE_DELAY_SECONDS)


def _clear_auth_failures(remote: str) -> None:
    with _AUTH_FAILURES_LOCK:
        _AUTH_FAILURES.pop(remote, None)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_seconds(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high))


def _roles(raw: str, default: str = "viewer") -> tuple[str, ...]:
    roles = tuple(sorted({part.strip().lower() for part in raw.split(",") if part.strip()}))
    return roles or (default,)


def _permissions(principal: Principal) -> set[str]:
    permissions: set[str] = set()
    for role in principal.roles:
        permissions.update(ROLE_PERMISSIONS.get(role, set()))
    return permissions


def _session_token_from_request() -> str:
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.cookies.get(SESSION_COOKIE, "").strip()


def issue_request_token(principal: Principal) -> dict[str, object]:
    """Issue a short-lived synchronizer token for unsafe browser requests."""
    institutional = _institutional_controls_required()
    active_session = _active_session_matches(principal)
    bootstrap = institutional and not active_session
    ttl = (
        min(_env_seconds("HELIOS_REQUEST_TOKEN_SECONDS", 1800, 300, 7200), 300)
        if bootstrap
        else _env_seconds("HELIOS_REQUEST_TOKEN_SECONDS", 1800, 300, 7200)
    )
    expires_at = int(time.time()) + ttl
    payload = {
        "v": 1,
        "exp": expires_at,
        "nonce": secrets.token_urlsafe(12),
        "principal": principal.username,
        "tenant_id": principal.tenant_id,
        "client_id": principal.client_id,
        "binding": _request_token_binding(principal),
        "scope": "session_bootstrap" if bootstrap else "unsafe",
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        _REQUEST_TOKEN_SECRET, encoded.encode("ascii"), hashlib.sha256,
    ).hexdigest()
    return {
        "header": REQUEST_TOKEN_HEADER,
        "token": f"{encoded}.{signature}",
        "expires_at": expires_at,
        "expires_in_seconds": ttl,
        "available": True,
        "binding": "session_bootstrap" if bootstrap else (
            "session" if _session_token_from_request() else "principal"
        ),
        "reason": (
            "Use this short-lived token only to start a secured Helios session."
            if bootstrap else ""
        ),
    }


def _request_token_binding(principal: Principal) -> str:
    session_token = _session_token_from_request()
    if session_token:
        return "session:" + hashlib.sha256(session_token.encode("utf-8")).hexdigest()
    material = "|".join((
        principal.username,
        principal.auth_method,
        principal.tenant_id,
        principal.client_id,
    ))
    return "principal:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _active_session_matches(principal: Principal) -> bool:
    session_principal = _session_principal(_session_token_from_request())
    return bool(
        session_principal is not None
        and session_principal.username == principal.username
        and session_principal.tenant_id == principal.tenant_id
        and session_principal.client_id == principal.client_id
    )


def _valid_request_token(token: str, principal: Principal) -> bool:
    try:
        encoded, signature = token.rsplit(".", 1)
        expected = hmac.new(
            _REQUEST_TOKEN_SECRET, encoded.encode("ascii"), hashlib.sha256,
        ).hexdigest()
        if not compare_digest(signature, expected):
            return False
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        binding = str(payload.get("binding") or "")
        token_scope = str(payload.get("scope") or "unsafe")
        session_bootstrap = request.method == "POST" and request.path == "/api/auth/session"
        scope_valid = (
            token_scope == "session_bootstrap"
            if session_bootstrap and _institutional_controls_required()
            else token_scope == "unsafe"
        )
        binding_valid = (
            binding.startswith("principal:")
            if session_bootstrap and _institutional_controls_required()
            else (not _institutional_controls_required() or binding.startswith("session:"))
        )
        return bool(
            int(payload.get("v") or 0) == 1
            and int(payload.get("exp") or 0) >= int(time.time())
            and compare_digest(str(payload.get("principal") or ""), principal.username)
            and compare_digest(str(payload.get("tenant_id") or ""), principal.tenant_id)
            and compare_digest(str(payload.get("client_id") or ""), principal.client_id)
            and scope_valid
            and binding_valid
            and compare_digest(binding, _request_token_binding(principal))
        )
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error):
        return False


def _request_token_required() -> bool:
    if request.method in _CSRF_SAFE_METHODS:
        return False
    if app.testing and not _env_bool("HELIOS_TEST_ENFORCE_REQUEST_TOKEN", False):
        return False
    return True


def _session_principal(token: str) -> Principal | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = time.time()
    idle_seconds = _env_seconds("HELIOS_SESSION_IDLE_SECONDS", 900, 60, 86_400)
    with _SESSIONS_LOCK:
        record = _SESSIONS.get(token_hash)
        if record is None:
            return None
        if now >= record.absolute_expires_at or now - record.last_seen >= idle_seconds:
            _SESSIONS.pop(token_hash, None)
            return None
        record.last_seen = now
        return record.principal


def _trusted_proxy() -> bool:
    configured = {
        part.strip() for part in os.environ.get("HELIOS_TRUSTED_PROXY_IPS", "").split(",")
        if part.strip()
    }
    return bool(configured) and (request.remote_addr or "") in configured


def _sso_principal() -> Principal | None:
    assertion = request.headers.get(identity.ASSERTION_HEADER, "").strip()
    if not identity.sso_enabled():
        if assertion:
            raise identity.IdentityAssertionError(
                "Enterprise identity assertions are not accepted while SSO is disabled."
            )
        return None
    if not _trusted_proxy():
        if identity.basic_fallback_allowed():
            return None
        raise identity.IdentityAssertionError(
            "Enterprise identity requests must originate from a configured trusted proxy."
        )
    if not assertion:
        if identity.basic_fallback_allowed():
            return None
        raise identity.IdentityAssertionError("A signed enterprise identity assertion is required.")
    claims = identity.verify_assertion(assertion)
    return Principal(
        claims["username"],
        tuple(claims["roles"]),
        "signed_proxy_assertion",
        bool(claims["mfa_verified"]),
        claims["tenant_id"],
        claims["client_id"],
        float(claims["expires_at"]),
    )


def _scope_principal(username: str, roles: tuple[str, ...], method: str, mfa: bool) -> Principal:
    scope = tenancy.configured_scope()
    return Principal(username, roles, method, mfa, scope.tenant_id, scope.client_id)


def _bare_identity_headers() -> list[str]:
    return [name for name in identity.BARE_IDENTITY_HEADERS if request.headers.get(name) is not None]


def _principal_scope_matches(principal: Principal) -> bool:
    scope = tenancy.configured_scope()
    return principal.tenant_id == scope.tenant_id and principal.client_id == scope.client_id


def _storage_scope_guard():
    try:
        tenancy.configured_scope()
        from engine import persistence

        store = persistence.get_store()
    except ValueError as exc:
        return _guard_response(str(exc), 503)
    except Exception:
        app.logger.exception("Could not validate the configured workspace scope")
        return _guard_response("Workspace scope validation failed.", 503)
    if getattr(store, "scope_mismatch", False):
        return _guard_response(
            "The configured database belongs to a different tenant/client scope.", 503,
        )
    return None


def _basic_principal() -> Principal | None:
    auth = request.authorization
    if not auth or auth.type != "basic" or not _auth_ok(auth.username or "", auth.password or ""):
        return None
    return _scope_principal(
        (auth.username or AUTH_USER)[:120],
        _roles(os.environ.get("HELIOS_BASIC_ROLES", "admin"), default="admin"),
        "basic",
        _env_bool("HELIOS_BASIC_MFA_VERIFIED", False),
    )


def _authenticate_request() -> Principal | None:
    # A caller that explicitly supplies an enterprise assertion must have that
    # assertion verified even when a browser session cookie is also present.
    # Otherwise a replayed/forged assertion could be silently masked by the
    # existing session and never reach the verifier.
    if request.headers.get(identity.ASSERTION_HEADER, "").strip():
        return _sso_principal()
    principal = _session_principal(_session_token_from_request())
    if principal is not None:
        return principal
    principal = _sso_principal()
    if principal is not None:
        return principal
    return _basic_principal()


def _required_permission() -> str:
    if request.method in _CSRF_SAFE_METHODS:
        return "read"
    path = request.path
    if path.startswith("/api/auth/"):
        return "read"
    if path.startswith("/api/providers"):
        return "provider_admin"
    if path.startswith("/api/model/forward"):
        return "data_write"
    if path.startswith("/api/operations"):
        return "operations"
    if path.startswith("/api/data-quality/sync"):
        return "operations"
    if path.startswith("/api/ledger/account/") and request.method == "DELETE":
        return "operations"
    if path.startswith("/api/ledger"):
        return "data_write"
    if "/independent-validation" in path or "/validation-exceptions" in path:
        return "validation_review"
    if path.startswith("/api/model-governance"):
        payload = request.get_json(silent=True) if request.is_json else {}
        status = str((payload or {}).get("approval_status") or "").lower()
        return "governance_approve" if status in {"approved", "rejected"} else "governance_submit"
    if path.startswith(("/api/model/upload", "/api/model-library/import", "/api/model/thesis", "/api/research-context")):
        return "governance_submit"
    if path.startswith("/api/trials"):
        if path.endswith("/close"):
            payload = request.get_json(silent=True) if request.is_json else {}
            status = str((payload or {}).get("status") or "").lower()
            return "governance_approve" if status == "completed" else "governance_submit"
        return "governance_submit"
    if path.startswith("/api/models/") and path.endswith("/editor"):
        return "governance_submit"
    if path.startswith(("/api/live", "/api/upload", "/api/data/", "/api/model/upload")):
        return "data_write"
    return "research_write"


def _host_allowed() -> bool:
    configured = {
        part.strip().lower() for part in os.environ.get("HELIOS_TRUSTED_HOSTS", "").split(",")
        if part.strip()
    }
    if not configured:
        return True
    host = (request.host or "").split(":", 1)[0].strip("[]").lower()
    return host in configured


def _security_event(action: str, outcome: str, principal: Principal | None = None, details: dict | None = None) -> bool:
    try:
        from engine import operations

        recorded = operations.record_privileged_event(
            actor=principal.username if principal else "unknown",
            actor_roles=list(principal.roles) if principal else [],
            action=action,
            resource=request.path,
            outcome=outcome,
            source_ip=request.remote_addr or "",
            details={
                **(details or {}),
                **({
                    "tenant_id": principal.tenant_id,
                    "client_id": principal.client_id,
                } if principal else {}),
            },
        )
        return recorded is not None
    except Exception:
        app.logger.exception("Could not record privileged security event")
        return False


def _institutional_controls_required() -> bool:
    return _env_bool("HELIOS_INSTITUTIONAL_CONTROLS", True)


def _privileged_preflight(principal: Principal, permission: str):
    if permission not in PRIVILEGED_PERMISSIONS or not _institutional_controls_required():
        return None
    if not AUTH_ENABLED and not app.testing:
        return _guard_response("Institutional controls require authenticated access.", 503)
    try:
        from engine import persistence

        store = persistence.get_store()
    except Exception:
        store = None
    if (
        store is None
        or not store.available
        or not bool((getattr(store, "encryption", None) or {}).get("enabled"))
    ):
        return _guard_response(
            "Institutional privileged actions require durable encrypted audit persistence.",
            503,
        )
    if not _security_event(
        f"http_{request.method.lower()}_attempt",
        "attempted",
        principal,
        {"permission": permission},
    ):
        return _guard_response("Privileged-action audit preflight failed.", 503)
    g.helios_privileged_attempt_recorded = True
    g.helios_privileged_commit_callbacks = []
    g.helios_privileged_rollback_callbacks = []
    try:
        transaction = store.transaction(durable=True)
        transaction.__enter__()
        g.helios_privileged_transaction = transaction
    except Exception:
        app.logger.exception("Could not begin privileged-action transaction")
        return _guard_response("Privileged-action durable transaction could not start.", 503)
    return None


def _finish_privileged_transaction(*, commit: bool) -> None:
    transaction = getattr(g, "helios_privileged_transaction", None)
    if transaction is None:
        return
    g.helios_privileged_transaction = None
    if commit:
        transaction.__exit__(None, None, None)
        return
    rollback = RuntimeError("Privileged request did not complete successfully.")
    transaction.__exit__(RuntimeError, rollback, None)


def privileged_transaction_active() -> bool:
    return getattr(g, "helios_privileged_transaction", None) is not None


def defer_privileged_state(
    *,
    commit: Callable[[], None] | None = None,
    rollback: Callable[[], None] | None = None,
) -> bool:
    """Tie process-local state changes to the active privileged DB transaction.

    A commit callback runs only after the durable DB commit. A rollback callback
    runs after any non-success response, audit failure, or commit failure. When
    institutional controls are not active, commit callbacks run immediately.
    """
    if not privileged_transaction_active():
        if commit is not None:
            commit()
        return False
    if commit is not None:
        g.helios_privileged_commit_callbacks.append(commit)
    if rollback is not None:
        g.helios_privileged_rollback_callbacks.append(rollback)
    return True


def _run_privileged_callbacks(kind: str) -> None:
    name = f"helios_privileged_{kind}_callbacks"
    callbacks = list(getattr(g, name, []) or [])
    setattr(g, name, [])
    if kind == "rollback":
        callbacks.reverse()
    for callback in callbacks:
        callback()


def _rollback_privileged_state() -> None:
    try:
        _run_privileged_callbacks("rollback")
    except Exception:
        app.logger.exception("Could not restore privileged process-local state")
    g.helios_privileged_commit_callbacks = []


def current_principal() -> Principal:
    principal = getattr(g, "helios_principal", None)
    return principal or _scope_principal("local-operator", ("admin",), "local", True)


def current_actor() -> str:
    return current_principal().username


def create_session(principal: Principal) -> tuple[str, dict]:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = time.time()
    absolute_seconds = _env_seconds("HELIOS_SESSION_ABSOLUTE_SECONDS", 28_800, 300, 604_800)
    if principal.auth_method == "signed_proxy_assertion" and principal.assertion_expires_at:
        remaining = max(1, int(principal.assertion_expires_at - now))
        absolute_seconds = min(absolute_seconds, remaining)
    with _SESSIONS_LOCK:
        _prune_sessions(now)
        if len(_SESSIONS) >= 200:
            oldest = min(_SESSIONS, key=lambda key: _SESSIONS[key].last_seen)
            _SESSIONS.pop(oldest, None)
        _SESSIONS[token_hash] = SessionRecord(principal, now, now, now + absolute_seconds)
    return token, {
        "user": principal.username,
        "roles": list(principal.roles),
        "auth_method": principal.auth_method,
        "mfa_verified": principal.mfa_verified,
        "tenant_id": principal.tenant_id,
        "client_id": principal.client_id,
        "absolute_expires_in_seconds": absolute_seconds,
        "idle_timeout_seconds": _env_seconds("HELIOS_SESSION_IDLE_SECONDS", 900, 60, 86_400),
    }


def revoke_session(token: str) -> bool:
    if not token:
        return False
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _SESSIONS_LOCK:
        return _SESSIONS.pop(token_hash, None) is not None


def _prune_sessions(now: float) -> None:
    idle_seconds = _env_seconds("HELIOS_SESSION_IDLE_SECONDS", 900, 60, 86_400)
    expired = [
        key for key, record in _SESSIONS.items()
        if now >= record.absolute_expires_at or now - record.last_seen >= idle_seconds
    ]
    for key in expired:
        _SESSIONS.pop(key, None)


def _guard_response(
    message: str,
    code: int,
    headers: dict | None = None,
    details: dict | None = None,
) -> Response:
    """Auth/CSRF/lockout refusal: JSON error envelope for API paths, plain text
    elsewhere so the browser Basic-auth prompt keeps working."""
    if request.path.startswith("/api/"):
        resp = jsonify({"error": message, **(details or {})})
        resp.status_code = code
    else:
        resp = Response(message, code)
    for key, value in (headers or {}).items():
        resp.headers[key] = value
    return resp


@app.before_request
def _require_auth():
    if not _host_allowed():
        _security_event("host_rejected", "denied", details={"host": request.host})
        return _guard_response("Untrusted host.", 400)
    if request.method not in _CSRF_SAFE_METHODS and _is_cross_site_request():
        _security_event("csrf_rejected", "denied")
        return _guard_response("Forbidden.", 403)
    bare_headers = _bare_identity_headers()
    if bare_headers:
        _security_event(
            "unsigned_identity_headers_rejected", "denied",
            details={"headers": sorted(bare_headers)},
        )
        return _guard_response("Unsigned identity headers are not accepted.", 401)
    scope_error = _storage_scope_guard()
    if scope_error is not None:
        return scope_error
    if not AUTH_ENABLED:
        if identity.sso_enabled():
            return _guard_response(
                "Enterprise SSO requires HELIOS_AUTH=1; refusing an authentication bypass configuration.",
                503,
            )
        principal = _scope_principal("local-operator", ("admin",), "auth_disabled", True)
        g.helios_principal = principal
        if _request_token_required() and not _valid_request_token(
            request.headers.get(REQUEST_TOKEN_HEADER, ""), principal,
        ):
            _security_event("request_token_rejected", "denied", principal)
            return _guard_response(
                "A valid unsafe-request token is required.", 403,
                details={"code": "request_token_invalid", "retryable": True},
            )
        permission = _required_permission()
        g.helios_permission = permission
        return _privileged_preflight(principal, permission)
    remote = request.remote_addr or "unknown"
    if _auth_locked_out(remote):
        _security_event("authentication_lockout", "denied")
        return _guard_response("Too many failed login attempts — try again shortly.", 429)
    try:
        principal = _authenticate_request()
    except identity.IdentityAssertionError as exc:
        _security_event("enterprise_identity_rejected", "denied", details={"reason": str(exc)})
        return _guard_response(str(exc), exc.status_code)
    if principal is not None:
        if not _principal_scope_matches(principal):
            _security_event("principal_scope_rejected", "denied", principal)
            return _guard_response("Authenticated principal is outside this tenant/client scope.", 403)
        _clear_auth_failures(remote)
        g.helios_principal = principal
        if _request_token_required() and not _valid_request_token(
            request.headers.get(REQUEST_TOKEN_HEADER, ""), principal,
        ):
            _security_event("request_token_rejected", "denied", principal)
            return _guard_response(
                "A valid unsafe-request token is required.", 403,
                details={"code": "request_token_invalid", "retryable": True},
            )
        permission = _required_permission()
        g.helios_permission = permission
        if permission not in _permissions(principal):
            _security_event(
                "authorization_denied", "denied", principal,
                {"required_permission": permission},
            )
            return _guard_response("Insufficient role permission.", 403)
        if (
            permission in PRIVILEGED_PERMISSIONS
            and (_env_bool("HELIOS_REQUIRE_MFA", False) or _institutional_controls_required())
            and not principal.mfa_verified
        ):
            _security_event(
                "mfa_required", "denied", principal,
                {"required_permission": permission},
            )
            return _guard_response("Verified MFA is required for this operation.", 403)
        return _privileged_preflight(principal, permission)
    auth = request.authorization
    if auth is not None:
        _register_auth_failure(remote)
        _security_event("authentication_failed", "denied")
    return _guard_response(
        "Helios — authentication required.",
        401,
        {"WWW-Authenticate": 'Basic realm="Helios analytics", charset="UTF-8"'},
    )


def _react_frontend_ready() -> bool:
    return (FRONTEND_DIST / "index.html").is_file()


def _serve_react_index():
    return send_from_directory(FRONTEND_DIST, "index.html")


@app.after_request
def _security_headers(resp: Response) -> Response:
    """Defense-in-depth headers. The CSP restricts scripts to our own origin
    on every response — there are no per-route exceptions."""
    permission = getattr(g, "helios_permission", "")
    if request.method not in _CSRF_SAFE_METHODS and permission in PRIVILEGED_PERMISSIONS:
        transaction = getattr(g, "helios_privileged_transaction", None)
        if transaction is not None and resp.status_code < 400:
            audited = _security_event(
                f"http_{request.method.lower()}",
                "succeeded",
                current_principal(),
                {"permission": permission, "status_code": resp.status_code},
            )
            if audited:
                try:
                    _finish_privileged_transaction(commit=True)
                    _run_privileged_callbacks("commit")
                except Exception:
                    app.logger.exception("Could not durably commit privileged action")
                    _rollback_privileged_state()
                    resp = _guard_response("Privileged action could not be durably committed.", 503)
            else:
                _finish_privileged_transaction(commit=False)
                _rollback_privileged_state()
                resp = _guard_response("Privileged action completed without durable audit confirmation.", 503)
        elif transaction is not None:
            _finish_privileged_transaction(commit=False)
            _rollback_privileged_state()
            _security_event(
                f"http_{request.method.lower()}",
                "failed",
                current_principal(),
                {"permission": permission, "status_code": resp.status_code},
            )
        else:
            audited = _security_event(
                f"http_{request.method.lower()}",
                "succeeded" if resp.status_code < 400 else "failed",
                current_principal(),
                {"permission": permission, "status_code": resp.status_code},
            )
            if _institutional_controls_required() and resp.status_code < 400 and not audited:
                resp = _guard_response("Privileged action completed without durable audit confirmation.", 503)
    # Apply headers after any fail-closed response replacement so the error
    # response receives the same browser defenses as the original response.
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "base-uri 'none'; frame-ancestors 'none'; object-src 'none'"
    )
    if _env_bool("HELIOS_TRUSTED_TLS", False):
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


@app.teardown_request
def _rollback_unfinished_privileged_transaction(_error=None) -> None:
    if getattr(g, "helios_privileged_transaction", None) is not None:
        _finish_privileged_transaction(commit=False)
        _rollback_privileged_state()


# Bound concurrent outbound live fetches so they can't consume every worker
# thread (each /api/live makes several blocking network calls).
_LIVE_SEMAPHORE = threading.BoundedSemaphore(2)
# Bound concurrent spreadsheet parses so a few large uploads can't pin every
# worker thread on CPU-heavy XML parsing.
_UPLOAD_SEMAPHORE = threading.BoundedSemaphore(2)

ANALYSIS_ONLY_DISCLAIMER = (
    "Analysis only — Helios provides research evidence and does not provide "
    "investment advice, brokerage services, order execution, or return guarantees."
)


# --------------------------------------------------------------------------- #
# Startup banner (LAN URLs + login), shared by the dev and waitress entrypoints
# --------------------------------------------------------------------------- #
def lan_ips() -> list[str]:
    ips: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # no packets sent; just resolves the local iface
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


def print_banner(host: str, port: int, tls: bool = False) -> None:
    scheme = "https" if tls else "http"
    public = host in ("0.0.0.0", "::")
    line = "  " + "─" * 52
    print("\n  HELIOS — investment-model analytics")
    print(line)
    if public:
        print("  Reachable on your local network at:")
        print(f"    • this machine : {scheme}://127.0.0.1:{port}")
        for ip in lan_ips():
            print(f"    • on your LAN  : {scheme}://{ip}:{port}")
    else:
        print(f"    • {scheme}://{host}:{port}")
    print(line)
    if AUTH_ENABLED:
        print("  Login (HTTP Basic Auth):")
        print(f"    user     : {AUTH_USER}")
        if PASSWORD_GENERATED:
            print(f"    password : {AUTH_PASSWORD}")
            print("    (auto-generated — set HELIOS_PASSWORD to choose your own)")
        else:
            print("    password : set via HELIOS_PASSWORD")
    else:
        print("  ⚠  AUTH DISABLED (HELIOS_AUTH=0)")
        if public:
            print("     Anyone on this network can view all data — do not use this")
            print("     on a shared or untrusted network.")
    print(line)
    if public and not tls:
        print("  ⚠  Plain HTTP — the password travels unencrypted over the network.")
        print("     On untrusted Wi-Fi, encrypt it:   HELIOS_TLS=1 ./run.sh")
        print("     (or bind localhost: HELIOS_HOST=127.0.0.1 and use an SSH tunnel)")
        print(line)
    print("  macOS may prompt to allow incoming connections — click Allow.\n", flush=True)


# --------------------------------------------------------------------------- #
# JSON sanitation: numpy types + NaN/inf -> valid JSON
# --------------------------------------------------------------------------- #
def clean(obj):
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())
    if isinstance(obj, (pd.Timestamp,)):
        return obj.strftime("%Y-%m-%d")
    return obj


def ok(payload):
    return jsonify(clean(payload))


def err(message, status_code=400, **details):
    return jsonify(clean({"error": message, **details})), status_code


def _safe_int_arg(
    name: str,
    default: int,
    min_value: int,
    max_value: int,
    *,
    clamp: bool = True,
) -> tuple[int | None, str | None]:
    """Parse an int query arg: unparseable values yield a 400 message,
    out-of-range values are clamped into [min_value, max_value]."""
    raw = request.args.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, f"{name} must be an integer between {min_value} and {max_value}."
    if not clamp and not min_value <= value <= max_value:
        return None, f"{name} must be an integer between {min_value} and {max_value}."
    return max(min_value, min(value, max_value)), None


def _safe_float_arg(name: str, default: float, min_value: float, max_value: float) -> tuple[float | None, str | None]:
    """Parse a float query arg: unparseable values yield a 400 message,
    out-of-range values are clamped into [min_value, max_value]."""
    raw = request.args.get(name, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, f"{name} must be a number between {min_value:g} and {max_value:g}."
    return max(min_value, min(value, max_value)), None


def _json_http_error(e: HTTPException):
    code = e.code or 500
    message = {
        400: "Bad request.",
        404: "Not found.",
        413: "Uploaded file is too large.",
        415: "Unsupported media type.",
        500: "Internal server error.",
    }.get(code, e.description or "Request failed.")
    return err(message, code)


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(413)
@app.errorhandler(415)
def handle_http_error(e: HTTPException):
    return _json_http_error(e)


@app.errorhandler(500)
def handle_internal_error(e: HTTPException):
    return err("Internal server error.", 500)


@app.errorhandler(Exception)
def handle_unexpected_error(e: Exception):
    if isinstance(e, HTTPException):
        return _json_http_error(e)
    app.logger.exception("Unhandled server error")
    return err("Internal server error.", 500)

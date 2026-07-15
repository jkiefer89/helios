"""Authentication session and security-posture API."""
from __future__ import annotations

import os

from flask import Blueprint, make_response, request

from engine import identity, operations, persistence, tenancy

from .core import (
    AUTH_ENABLED, SESSION_COOKIE, _env_bool, _env_seconds,
    _institutional_controls_required, _session_token_from_request, _trusted_proxy,
    create_session, current_principal, issue_request_token, ok, revoke_session,
)

bp = Blueprint("security", __name__)


@bp.route("/api/security/status")
def security_status():
    principal = current_principal()
    operational = operations.operational_status()
    return ok({
        "principal": {
            "user": principal.username,
            "roles": list(principal.roles),
            "auth_method": principal.auth_method,
            "mfa_verified": principal.mfa_verified,
            "tenant_id": principal.tenant_id,
            "client_id": principal.client_id,
        },
        "authentication": {
            "enabled": AUTH_ENABLED,
            "sso_enabled": identity.sso_enabled(),
            "enterprise_identity": identity.verifier_status(),
            "mfa_required_for_privileged_actions": (
                _env_bool("HELIOS_REQUIRE_MFA", False) or _institutional_controls_required()
            ),
            "session_idle_seconds": _env_seconds("HELIOS_SESSION_IDLE_SECONDS", 900, 60, 86_400),
            "session_absolute_seconds": _env_seconds("HELIOS_SESSION_ABSOLUTE_SECONDS", 28_800, 300, 604_800),
        },
        "transport": {
            "trusted_tls_asserted": _env_bool("HELIOS_TRUSTED_TLS", False),
            "trusted_hosts_configured": bool(os.environ.get("HELIOS_TRUSTED_HOSTS", "").strip()),
            "trusted_proxy_count": len([part for part in os.environ.get("HELIOS_TRUSTED_PROXY_IPS", "").split(",") if part.strip()]),
        },
        "audit": {
            "privileged_chain": operational["privileged_chain"],
            "application_chain": operational["audit_chain"],
        },
        "workspace": {
            **tenancy.configured_scope().public(),
            "database_bound": bool(persistence.get_store().available),
            "scope_mismatch": bool(getattr(persistence.get_store(), "scope_mismatch", False)),
        },
        "request_protection": issue_request_token(principal),
        "external_requirements": [
            "Institutional SSO/MFA requires a user-managed identity provider and a trusted proxy that signs short-lived Helios assertions.",
            "True WORM retention requires forwarding privileged events to a separately administered SIEM or archive.",
        ],
    })


@bp.route("/api/auth/session", methods=["POST"])
def session_create():
    principal = current_principal()
    token, metadata = create_session(principal)
    operations.record_privileged_event(
        actor=principal.username,
        actor_roles=list(principal.roles),
        action="session_created",
        resource="session",
        outcome="succeeded",
        details=metadata,
    )
    response = make_response(ok({"session": metadata}))
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=(
            request.is_secure
            or _env_bool("HELIOS_TRUSTED_TLS", False)
            or (_env_bool("HELIOS_TRUSTED_PROXY_TLS", False) and _trusted_proxy())
        ),
        samesite="Strict",
        max_age=metadata["absolute_expires_in_seconds"],
        path="/",
    )
    return response


@bp.route("/api/auth/session", methods=["DELETE"])
def session_delete():
    principal = current_principal()
    revoked = revoke_session(_session_token_from_request())
    operations.record_privileged_event(
        actor=principal.username,
        actor_roles=list(principal.roles),
        action="session_revoked",
        resource="session",
        outcome="succeeded" if revoked else "not_found",
    )
    response = make_response(ok({"revoked": revoked}))
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="Strict")
    return response

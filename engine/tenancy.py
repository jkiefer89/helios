"""Single-workspace tenant/client boundary for local Helios deployments."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass

DEFAULT_TENANT_ID = "local-workspace"
DEFAULT_CLIENT_ID = "local-client"
_SCOPE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ScopeMismatchError(RuntimeError):
    """Raised when a database is opened under a different configured scope."""


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class WorkspaceScope:
    tenant_id: str
    client_id: str

    @property
    def scope_hash(self) -> str:
        payload = f"helios-scope-v1\0{self.tenant_id}\0{self.client_id}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def public(self) -> dict[str, str]:
        return {
            "tenant_id": self.tenant_id,
            "client_id": self.client_id,
            "scope_hash": self.scope_hash,
            "boundary": "single_tenant_client_database",
        }


def _scope_id(name: str, default: str) -> str:
    value = str(os.environ.get(name) or default).strip()
    if not _SCOPE_ID_RE.fullmatch(value):
        raise ValueError(
            f"{name} must be 1-64 characters using letters, numbers, dot, underscore, or hyphen."
        )
    return value


def configured_scope() -> WorkspaceScope:
    return WorkspaceScope(
        tenant_id=_scope_id("HELIOS_TENANT_ID", DEFAULT_TENANT_ID),
        client_id=_scope_id("HELIOS_CLIENT_ID", DEFAULT_CLIENT_ID),
    )


def institutional_controls_enabled() -> bool:
    return _env_bool("HELIOS_INSTITUTIONAL_CONTROLS", True)


def scope_is_explicit() -> bool:
    return bool(
        str(os.environ.get("HELIOS_TENANT_ID") or "").strip()
        and str(os.environ.get("HELIOS_CLIENT_ID") or "").strip()
    )


def assert_new_database_scope_configured() -> WorkspaceScope:
    """Refuse an implicit/default identity for a new institutional database."""
    scope = configured_scope()
    if not institutional_controls_enabled():
        return scope
    if (
        not scope_is_explicit()
        or scope.tenant_id == DEFAULT_TENANT_ID
        or scope.client_id == DEFAULT_CLIENT_ID
    ):
        raise ScopeMismatchError(
            "a new institutional Helios database requires explicit non-default "
            "HELIOS_TENANT_ID and HELIOS_CLIENT_ID values"
        )
    return scope


def assert_legacy_scope_adoption_allowed() -> WorkspaceScope:
    """Require an explicit, one-time operator decision before binding old data.

    Non-institutional local/test processes retain automatic migration behavior.
    Institutional processes must name both scope IDs and opt in once; after the
    identity row is written this switch is no longer consulted.
    """
    scope = configured_scope()
    if not institutional_controls_enabled():
        return scope
    if scope_is_explicit() and _env_bool("HELIOS_ALLOW_LEGACY_SCOPE_ADOPTION", False):
        return scope
    raise ScopeMismatchError(
        "legacy Helios database has no tenant/client identity; set explicit "
        "HELIOS_TENANT_ID and HELIOS_CLIENT_ID, then set "
        "HELIOS_ALLOW_LEGACY_SCOPE_ADOPTION=1 for one verified startup"
    )


def assert_database_scope(tenant_id: str, client_id: str, scope_hash: str) -> WorkspaceScope:
    expected = configured_scope()
    if (
        str(tenant_id) != expected.tenant_id
        or str(client_id) != expected.client_id
        or str(scope_hash) != expected.scope_hash
    ):
        raise ScopeMismatchError(
            "database belongs to a different Helios tenant/client scope; "
            "configure a separate HELIOS_DB_PATH for each tenant and client"
        )
    return expected

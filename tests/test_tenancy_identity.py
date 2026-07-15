"""Tenant/client database binding and signed enterprise identity controls."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import time
from contextlib import closing

import app as helios
import pytest

from engine import identity, persistence, tenancy
from helios_web import core as web_core


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _assertion(secret: str, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://idp.example.test",
        "aud": "helios-test",
        "sub": "enterprise-advisor",
        "jti": f"assertion-{time.time_ns()}",
        "roles": ["viewer", "model_sponsor"],
        "mfa_verified": True,
        "tenant_id": "tenant-one",
        "client_id": "client-alpha",
        "iat": now - 5,
        "nbf": now - 5,
        "exp": now + 120,
    }
    claims.update(overrides)
    payload = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    return f"{payload}.{_b64(signature)}"


def _configure_sso(monkeypatch, *, secret: str = "s" * 40) -> str:
    monkeypatch.setattr(web_core, "AUTH_ENABLED", True)
    monkeypatch.setenv("HELIOS_SSO_ENABLED", "1")
    monkeypatch.setenv("HELIOS_SSO_ASSERTION_SECRET", secret)
    monkeypatch.setenv("HELIOS_SSO_ISSUER", "https://idp.example.test")
    monkeypatch.setenv("HELIOS_SSO_AUDIENCE", "helios-test")
    monkeypatch.setenv("HELIOS_TRUSTED_PROXY_IPS", "10.0.0.10")
    monkeypatch.setenv("HELIOS_TENANT_ID", "tenant-one")
    monkeypatch.setenv("HELIOS_CLIENT_ID", "client-alpha")
    monkeypatch.setenv("HELIOS_DB_PATH", "off")
    persistence.reset_store_for_tests()
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return secret


def _signed_get(client, token: str, path: str = "/api/health", remote: str = "10.0.0.10"):
    return client.get(
        path,
        headers={identity.ASSERTION_HEADER: token},
        environ_base={"REMOTE_ADDR": remote},
    )


def test_database_is_pinned_to_one_tenant_client_scope(monkeypatch, tmp_path):
    db = tmp_path / "scoped.db"
    monkeypatch.setenv("HELIOS_DB_PATH", str(db))
    monkeypatch.setenv("HELIOS_TENANT_ID", "tenant-one")
    monkeypatch.setenv("HELIOS_CLIENT_ID", "client-alpha")
    persistence.reset_store_for_tests()
    first = persistence.get_store()
    assert first.available
    assert first.status()["scope"]["tenant_id"] == "tenant-one"
    with first._connect() as conn:
        row = conn.execute("SELECT * FROM workspace_identity WHERE singleton = 1").fetchone()
    assert row["tenant_id"] == "tenant-one"
    assert row["client_id"] == "client-alpha"

    persistence.reset_store_for_tests()
    monkeypatch.setenv("HELIOS_TENANT_ID", "tenant-two")
    second = persistence.get_store()

    assert second.available is False
    assert second.scope_mismatch is True
    assert "different Helios tenant/client scope" in second.warning


def test_new_institutional_database_requires_explicit_nondefault_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "institutional.db"))
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    monkeypatch.delenv("HELIOS_TENANT_ID", raising=False)
    monkeypatch.delenv("HELIOS_CLIENT_ID", raising=False)
    persistence.reset_store_for_tests()

    implicit = persistence.get_store()

    assert implicit.available is False
    assert implicit.scope_mismatch is True
    assert "requires explicit non-default" in implicit.warning

    monkeypatch.setenv("HELIOS_TENANT_ID", tenancy.DEFAULT_TENANT_ID)
    monkeypatch.setenv("HELIOS_CLIENT_ID", tenancy.DEFAULT_CLIENT_ID)
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "institutional-defaults.db"))
    persistence.reset_store_for_tests()
    defaults = persistence.get_store()

    assert defaults.available is False
    assert defaults.scope_mismatch is True
    assert "requires explicit non-default" in defaults.warning


def test_scope_mismatch_is_rejected_before_schema_migration(monkeypatch, tmp_path):
    db = tmp_path / "preflight-mismatch.db"
    scope = tenancy.WorkspaceScope("tenant-one", "client-alpha")
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (13, 'before-preflight')")
        conn.execute(
            "CREATE TABLE workspace_identity (singleton INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, "
            "client_id TEXT NOT NULL, scope_hash TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO workspace_identity VALUES (1, ?, ?, ?, 'before-preflight')",
            (scope.tenant_id, scope.client_id, scope.scope_hash),
        )
        conn.execute(
            "CREATE TABLE ledger_accounts (account_id TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '', "
            "model_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}')"
        )
        conn.commit()

    monkeypatch.setenv("HELIOS_DB_PATH", str(db))
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    monkeypatch.setenv("HELIOS_TENANT_ID", "tenant-two")
    monkeypatch.setenv("HELIOS_CLIENT_ID", "client-alpha")
    persistence.reset_store_for_tests()

    store = persistence.get_store()

    assert store.available is False
    assert store.scope_mismatch is True
    with closing(sqlite3.connect(db)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(ledger_accounts)")}
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        new_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='instruments'"
        ).fetchone()
    assert "status" not in columns
    assert version == 13
    assert new_table is None


def test_institutional_legacy_database_requires_explicit_one_time_adoption(monkeypatch, tmp_path):
    db = tmp_path / "legacy-unscoped.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (13, 'legacy')")
        conn.execute(
            "CREATE TABLE ledger_accounts (account_id TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '', "
            "model_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}')"
        )
        conn.execute("INSERT INTO ledger_accounts VALUES ('LEGACY', 'Legacy', '', 'legacy', '{}')")
        conn.commit()

    monkeypatch.setenv("HELIOS_DB_PATH", str(db))
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    monkeypatch.delenv("HELIOS_TENANT_ID", raising=False)
    monkeypatch.delenv("HELIOS_CLIENT_ID", raising=False)
    monkeypatch.delenv("HELIOS_ALLOW_LEGACY_SCOPE_ADOPTION", raising=False)
    persistence.reset_store_for_tests()

    refused = persistence.get_store()
    assert refused.available is False
    assert refused.scope_mismatch is True
    assert "legacy Helios database" in refused.warning
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_identity'"
        ).fetchone() is None

    monkeypatch.setenv("HELIOS_TENANT_ID", "tenant-one")
    monkeypatch.setenv("HELIOS_CLIENT_ID", "client-alpha")
    monkeypatch.setenv("HELIOS_ALLOW_LEGACY_SCOPE_ADOPTION", "1")
    persistence.reset_store_for_tests()
    adopted = persistence.get_store()

    assert adopted.available is True
    with adopted._connect() as conn:
        identity_row = conn.execute(
            "SELECT tenant_id, client_id FROM workspace_identity WHERE singleton = 1"
        ).fetchone()
    assert (identity_row["tenant_id"], identity_row["client_id"]) == ("tenant-one", "client-alpha")


def test_encrypted_scope_mismatch_creates_no_backup_artifact(monkeypatch, tmp_path):
    db = tmp_path / "encrypted-scoped.db"
    key = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
    monkeypatch.setenv("HELIOS_DB_PATH", str(db))
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "required")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY", key)
    monkeypatch.setenv("HELIOS_TENANT_ID", "tenant-one")
    monkeypatch.setenv("HELIOS_CLIENT_ID", "client-alpha")
    persistence.reset_store_for_tests()
    first = persistence.get_store()
    assert first.available is True
    first.flush()
    persistence.reset_store_for_tests()

    monkeypatch.setenv("HELIOS_TENANT_ID", "tenant-two")
    second = persistence.get_store()

    assert second.available is False
    assert second.scope_mismatch is True
    backup_dir = tmp_path / persistence.BACKUP_DIR_NAME
    assert not backup_dir.exists() or list(backup_dir.iterdir()) == []


def test_replay_cache_capacity_fails_closed_without_evicting_used_assertion(monkeypatch):
    secret = _configure_sso(monkeypatch)
    monkeypatch.setattr(identity, "_MAX_REPLAY_CACHE", 1)
    first = _assertion(secret)
    second = _assertion(secret)

    assert identity.verify_assertion(first)["username"] == "enterprise-advisor"
    with pytest.raises(identity.IdentityAssertionError) as capacity:
        identity.verify_assertion(second)
    assert capacity.value.status_code == 503
    assert "at capacity" in str(capacity.value)
    with pytest.raises(identity.IdentityAssertionError, match="already been used"):
        identity.verify_assertion(first)


def test_cross_scope_database_reuse_blocks_web_access(monkeypatch, tmp_path):
    db = tmp_path / "scoped-web.db"
    monkeypatch.setenv("HELIOS_DB_PATH", str(db))
    monkeypatch.setenv("HELIOS_TENANT_ID", "tenant-one")
    monkeypatch.setenv("HELIOS_CLIENT_ID", "client-alpha")
    persistence.reset_store_for_tests()
    assert persistence.get_store().available
    persistence.reset_store_for_tests()
    monkeypatch.setenv("HELIOS_TENANT_ID", "tenant-two")
    monkeypatch.setattr(web_core, "AUTH_ENABLED", False)
    helios.app.config.update(TESTING=True)

    response = helios.app.test_client().get("/api/health")

    assert response.status_code == 503
    assert "different tenant/client scope" in response.get_json()["error"]


def test_signed_enterprise_assertion_authenticates_and_exposes_no_secret(monkeypatch):
    secret = _configure_sso(monkeypatch)
    client = helios.app.test_client()
    token = _assertion(secret)

    response = _signed_get(client, token, "/api/security/status")

    assert response.status_code == 200
    body = response.get_json()
    assert body["principal"] == {
        "user": "enterprise-advisor",
        "roles": ["model_sponsor", "viewer"],
        "auth_method": "signed_proxy_assertion",
        "mfa_verified": True,
        "tenant_id": "tenant-one",
        "client_id": "client-alpha",
    }
    assert body["authentication"]["enterprise_identity"]["configured"] is True
    assert body["workspace"]["boundary"] == "single_tenant_client_database"
    assert secret not in response.get_data(as_text=True)


def test_signed_assertion_is_one_time_and_replay_is_rejected(monkeypatch):
    secret = _configure_sso(monkeypatch)
    token = _assertion(secret)
    client = helios.app.test_client()

    first = _signed_get(client, token, "/api/security/status")
    replay = _signed_get(client, token, "/api/security/status")

    assert first.status_code == 200
    assert replay.status_code == 401
    assert "already been used" in replay.get_json()["error"]


def test_assertion_backed_session_cannot_outlive_assertion(monkeypatch):
    secret = _configure_sso(monkeypatch)
    expiry = int(time.time()) + 90
    token = _assertion(secret, exp=expiry)
    client = helios.app.test_client()

    created = client.post(
        "/api/auth/session",
        headers={identity.ASSERTION_HEADER: token},
        environ_base={"REMOTE_ADDR": "10.0.0.10"},
    )
    replay = client.post(
        "/api/auth/session",
        headers={identity.ASSERTION_HEADER: token},
        environ_base={"REMOTE_ADDR": "10.0.0.10"},
    )

    assert created.status_code == 200
    lifetime = created.get_json()["session"]["absolute_expires_in_seconds"]
    assert 1 <= lifetime <= 90
    assert replay.status_code == 401


def test_enterprise_identity_institutional_readiness_requires_proxy_and_no_fallback(monkeypatch):
    _configure_sso(monkeypatch)
    status = identity.verifier_status()
    assert status["configured"] is True
    assert status["institutional_ready"] is True

    monkeypatch.delenv("HELIOS_TRUSTED_PROXY_IPS", raising=False)
    assert identity.verifier_status()["institutional_ready"] is False

    monkeypatch.setenv("HELIOS_TRUSTED_PROXY_IPS", "10.0.0.10")
    monkeypatch.setenv("HELIOS_SSO_ALLOW_BASIC_FALLBACK", "1")
    assert identity.verifier_status()["institutional_ready"] is False


@pytest.mark.parametrize(
    "overrides,error_text,status",
    [
        ({"exp": 1}, "expired", 401),
        ({"aud": "other-app"}, "audience", 401),
        ({"iss": "https://attacker.invalid"}, "issuer", 401),
        ({"iss": "https://idp.example.test/\u2603"}, "issuer", 401),
        ({"tenant_id": "tenant-two"}, "outside this tenant/client scope", 403),
        ({"client_id": "client-other"}, "outside this tenant/client scope", 403),
        ({"roles": ["admin", "unknown-role"]}, "unsupported role", 401),
        ({"mfa_verified": "yes"}, "must be boolean", 401),
    ],
)
def test_signed_assertion_claim_failures_are_closed(monkeypatch, overrides, error_text, status):
    secret = _configure_sso(monkeypatch)
    response = _signed_get(helios.app.test_client(), _assertion(secret, **overrides))

    assert response.status_code == status
    assert error_text in response.get_json()["error"]


def test_forged_signature_untrusted_proxy_and_bare_headers_are_rejected(monkeypatch):
    secret = _configure_sso(monkeypatch)
    client = helios.app.test_client()
    forged = _assertion("x" * 40)

    assert _signed_get(client, forged).status_code == 401
    assert _signed_get(client, _assertion(secret), remote="10.0.0.11").status_code == 401
    bare = client.get(
        "/api/health",
        headers={"X-Helios-User": "spoofed", "X-Helios-Roles": "admin"},
        environ_base={"REMOTE_ADDR": "10.0.0.10"},
    )
    assert bare.status_code == 401
    assert "Unsigned identity headers" in bare.get_json()["error"]


def test_sso_enabled_without_verifier_configuration_fails_closed(monkeypatch):
    _configure_sso(monkeypatch, secret="short")

    response = _signed_get(helios.app.test_client(), "payload.signature")

    assert response.status_code == 503
    assert "not fully configured" in response.get_json()["error"]


def test_sso_cannot_be_combined_with_disabled_authentication(monkeypatch):
    _configure_sso(monkeypatch)
    monkeypatch.setattr(web_core, "AUTH_ENABLED", False)

    response = helios.app.test_client().get("/api/health")

    assert response.status_code == 503
    assert "requires HELIOS_AUTH=1" in response.get_json()["error"]


def test_basic_auth_remains_available_when_sso_is_disabled(monkeypatch):
    import base64 as basic_base64

    monkeypatch.setattr(web_core, "AUTH_ENABLED", True)
    monkeypatch.setattr(web_core, "AUTH_USER", "advisor")
    monkeypatch.setattr(web_core, "AUTH_PASSWORD", "correct-horse-battery")
    monkeypatch.setenv("HELIOS_SSO_ENABLED", "0")
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "0")
    monkeypatch.delenv("HELIOS_TENANT_ID", raising=False)
    monkeypatch.delenv("HELIOS_CLIENT_ID", raising=False)
    monkeypatch.setenv("HELIOS_DB_PATH", "off")
    persistence.reset_store_for_tests()
    token = basic_base64.b64encode(b"advisor:correct-horse-battery").decode("ascii")

    response = helios.app.test_client().get(
        "/api/security/status", headers={"Authorization": f"Basic {token}"},
    )

    assert response.status_code == 200
    assert response.get_json()["principal"]["auth_method"] == "basic"
    assert response.get_json()["principal"]["tenant_id"] == tenancy.DEFAULT_TENANT_ID


def test_session_retains_and_enforces_enterprise_scope(monkeypatch):
    secret = _configure_sso(monkeypatch)
    client = helios.app.test_client()
    created = client.post(
        "/api/auth/session",
        headers={identity.ASSERTION_HEADER: _assertion(secret)},
        environ_base={"REMOTE_ADDR": "10.0.0.10"},
    )
    assert created.status_code == 200
    assert created.get_json()["session"]["tenant_id"] == "tenant-one"

    monkeypatch.setenv("HELIOS_TENANT_ID", "tenant-two")
    rejected = client.get("/api/health")

    assert rejected.status_code == 403
    assert "outside this tenant/client scope" in rejected.get_json()["error"]

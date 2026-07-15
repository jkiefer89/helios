import base64
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

import pandas as pd
import pytest

import app as helios
import serve
from engine import (
    data, data_quality, fundamentals, holdings, identity, independent_validation, operations,
    persistence, portfolio, provider_registry, research_gate,
)
from helios_web import core as web_core


def _store(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    store = persistence.get_store()
    assert store.available
    return store


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _approve_price_pair(monkeypatch, *, actor: str = "data committee") -> dict:
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    for provider in ("FMP", "TIINGO"):
        monkeypatch.setenv(f"HELIOS_PROVIDER_{provider}_LICENSED", "1")
        monkeypatch.setenv(f"HELIOS_PROVIDER_{provider}_ENTITLED", "1")
        monkeypatch.setenv(f"HELIOS_PROVIDER_{provider}_SLA_OWNER", "Data Operations")
    monkeypatch.setenv("HELIOS_FMP_KEY", "configured")
    monkeypatch.setenv("HELIOS_TIINGO_KEY", "configured")
    reconciliation = provider_registry.record_reconciliation(
        data_domain="prices",
        primary_provider="fmp",
        backup_provider="tiingo",
        observations=[
            {"symbol": "AAPL", "status": "ok", "max_abs_diff_pct": 0.01},
            {"symbol": "MSFT", "status": "ok", "max_abs_diff_pct": 0.01},
        ],
        actor=actor,
        observed_by_server=True,
    )
    return provider_registry.approve_cutover(
        data_domain="prices",
        primary_provider="fmp",
        backup_provider="tiingo",
        reconciliation_id=reconciliation["reconciliation_id"],
        actor=actor,
        note="Approved provider pair after server reconciliation.",
    )


def test_provider_key_never_implies_license_or_entitlement(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    monkeypatch.setenv("HELIOS_FMP_KEY", "configured-not-a-license")

    fmp = next(row for row in provider_registry.registry()["providers"] if row["key"] == "fmp")

    assert fmp["configured"] is True
    assert fmp["licensed"] is False
    assert fmp["entitled"] is False
    assert fmp["institutional_ready"] is False
    assert provider_registry.domain_readiness("prices")["passed"] is False


def test_provider_reconciliation_rolls_back_when_required_audit_fails(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("simulated required audit failure")

    monkeypatch.setattr(store, "audit_append", fail_audit)
    with pytest.raises(RuntimeError, match="simulated required audit failure"):
        provider_registry.record_reconciliation(
            data_domain="prices",
            primary_provider="fmp",
            backup_provider="tiingo",
            observations=[
                {"symbol": "AAPL", "status": "ok", "max_abs_diff_pct": 0.01},
                {"symbol": "MSFT", "status": "ok", "max_abs_diff_pct": 0.01},
            ],
            actor="data-operator",
            observed_by_server=True,
        )

    with store._connect() as conn:
        count = int(conn.execute("SELECT COUNT(*) FROM provider_reconciliations").fetchone()[0])
    assert count == 0


def test_provider_contract_gate_blocks_before_reconciliation_network_call(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    monkeypatch.setenv("HELIOS_FMP_KEY", "configured-without-contract-controls")
    calls = []
    monkeypatch.setattr(data, "_provider_price_history", lambda *args: calls.append(args))

    with pytest.raises(ValueError, match="cannot be queried"):
        data.reconcile_price_sources("AAPL", primary_provider="fmp", backup_provider="yfinance")

    assert calls == []


def test_fundamentals_have_no_unapproved_institutional_fallback(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    calls = []
    monkeypatch.setattr(fundamentals, "_fmp_provider", lambda ticker: calls.append(("fmp", ticker)))
    monkeypatch.setattr(fundamentals, "_intrinio_provider", lambda ticker: calls.append(("intrinio", ticker)))
    monkeypatch.setattr(fundamentals, "_yfinance_provider", lambda ticker: calls.append(("yfinance", ticker)))

    assert fundamentals._auto_provider("AAPL") == {}
    assert calls == []


def test_forward_model_provider_gate_precedes_lookthrough_network_work(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    portfolio.register(portfolio.Model(
        id="FORWARD-GATED", name="Forward Gated", mandate_key="balanced",
        mandate_context="", holdings=[portfolio.Holding("AAPL", 1.0)],
    ))
    monkeypatch.setattr(web_core, "AUTH_ENABLED", False)
    monkeypatch.setattr(provider_registry, "domain_readiness", lambda _domain: {
        "passed": False,
        "detail": "No approved fundamentals cutover.",
        "required_action": "Approve providers.",
    })
    monkeypatch.setattr(holdings, "model_lookthrough", lambda *_args, **_kwargs: pytest.fail("look-through ran before provider gate"))
    helios.app.config.update(TESTING=True)

    response = helios.app.test_client().post("/api/model/forward?id=FORWARD-GATED")

    assert response.status_code == 503
    assert "provider gate blocked" in response.get_json()["error"].lower()


def test_provider_cutover_requires_explicit_controls_and_current_reconciliation(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    for provider in ("FMP", "TIINGO"):
        monkeypatch.setenv(f"HELIOS_PROVIDER_{provider}_LICENSED", "1")
        monkeypatch.setenv(f"HELIOS_PROVIDER_{provider}_ENTITLED", "1")
        monkeypatch.setenv(f"HELIOS_PROVIDER_{provider}_SLA_OWNER", f"{provider} owner")
    monkeypatch.setenv("HELIOS_FMP_KEY", "fmp-configured")
    monkeypatch.setenv("HELIOS_TIINGO_KEY", "tiingo-configured")

    reconciliation = provider_registry.record_reconciliation(
        data_domain="prices",
        primary_provider="fmp",
        backup_provider="tiingo",
        observations=[
            {"symbol": "AAPL", "status": "ok", "max_abs_diff_pct": 0.04},
            {"symbol": "MSFT", "status": "ok", "max_abs_diff_pct": 0.05},
        ],
        actor="data committee",
        note="Side-by-side adjusted close review.",
        observed_by_server=True,
    )
    cutover = provider_registry.approve_cutover(
        data_domain="prices",
        primary_provider="fmp",
        backup_provider="tiingo",
        reconciliation_id=reconciliation["reconciliation_id"],
        actor="data committee",
        note="Approved after reconciliation and entitlement review.",
    )

    assert reconciliation["status"] == "passed"
    assert cutover["status"] == "approved"
    readiness = provider_registry.domain_readiness("prices")
    assert readiness["passed"] is True
    assert readiness["cutover"]["backup_provider"] == "tiingo"


def test_provider_and_incident_control_routes_complete_operational_workflow(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setattr(web_core, "AUTH_ENABLED", False)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    for provider in ("FMP", "TIINGO"):
        monkeypatch.setenv(f"HELIOS_PROVIDER_{provider}_LICENSED", "1")
        monkeypatch.setenv(f"HELIOS_PROVIDER_{provider}_ENTITLED", "1")
        monkeypatch.setenv(f"HELIOS_PROVIDER_{provider}_SLA_OWNER", "Data Operations")
    monkeypatch.setenv("HELIOS_FMP_KEY", "configured-not-returned")
    monkeypatch.setenv("HELIOS_TIINGO_KEY", "configured-not-returned")
    helios.app.config.update(TESTING=True)
    client = helios.app.test_client()
    monkeypatch.setattr(
        data,
        "reconcile_price_sources",
        lambda symbol, **_kwargs: {
            "symbol": symbol,
            "status": "ok",
            "max_abs_diff_pct": 0.02,
            "overlap_days": 60,
        },
    )

    reconciliation = client.post("/api/providers/reconciliations", json={
        "data_domain": "prices",
        "primary_provider": "fmp",
        "backup_provider": "tiingo",
        "symbols": ["SPY", "AAPL"],
        "note": "Adjusted close overlap reviewed by data operations.",
    })
    assert reconciliation.status_code == 200
    reconciliation_id = reconciliation.get_json()["reconciliation"]["reconciliation_id"]
    cutover = client.post("/api/providers/cutovers", json={
        "data_domain": "prices",
        "primary_provider": "fmp",
        "backup_provider": "tiingo",
        "reconciliation_id": reconciliation_id,
        "note": "Approved current primary and backup provider pair.",
    })
    assert cutover.status_code == 200
    assert cutover.get_json()["cutover"]["status"] == "approved"
    assert "configured-not-returned" not in f"{reconciliation.get_data(as_text=True)}{cutover.get_data(as_text=True)}"

    created = operations.sync_incidents([{
        "category": "refresh_failures",
        "severity": "blocker",
        "target": "SPY",
        "detail": "Both licensed feeds failed refresh.",
        "next_step": "Escalate to the data operations owner.",
    }], actor="monitor")["incidents"][0]
    acknowledged = client.post(f"/api/operations/incidents/{created['incident_id']}", json={
        "status": "acknowledged",
        "owner": "Data Operations",
        "note": "Primary and backup provider incident under review.",
    })
    assert acknowledged.status_code == 200
    assert acknowledged.get_json()["incident"]["status"] == "acknowledged"


def test_provider_reconciliation_rejects_missing_numeric_evidence_and_redacts_secrets(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    result = provider_registry.record_reconciliation(
        data_domain="prices",
        primary_provider="fmp",
        backup_provider="tiingo",
        observations=[
            {"symbol": "AAPL", "status": "ok", "max_abs_diff_pct": "0.04", "api_key": "never-store"},
        ],
        actor="data operator",
    )

    assert result["status"] == "failed"
    assert result["compared_count"] == 0
    assert result["evidence"]["invalid_evidence_count"] == 1
    assert "never-store" not in str(result)
    assert result["evidence"]["observations"][0]["api_key"] == "[redacted]"


def test_provider_cutover_rejects_manual_or_tampered_reconciliation_evidence(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    for provider in ("FMP", "TIINGO"):
        monkeypatch.setenv(f"HELIOS_PROVIDER_{provider}_LICENSED", "1")
        monkeypatch.setenv(f"HELIOS_PROVIDER_{provider}_ENTITLED", "1")
        monkeypatch.setenv(f"HELIOS_PROVIDER_{provider}_SLA_OWNER", "Data Operations")
    monkeypatch.setenv("HELIOS_FMP_KEY", "configured")
    monkeypatch.setenv("HELIOS_TIINGO_KEY", "configured")
    observations = [
        {"symbol": "AAPL", "status": "ok", "max_abs_diff_pct": 0.01},
        {"symbol": "MSFT", "status": "ok", "max_abs_diff_pct": 0.01},
    ]
    manual = provider_registry.record_reconciliation(
        data_domain="prices", primary_provider="fmp", backup_provider="tiingo",
        observations=observations, actor="operator", observed_by_server=False,
    )
    assert manual["status"] == "failed"
    assert manual["evidence"]["observation_origin"] == "untrusted_manual_input"

    server = provider_registry.record_reconciliation(
        data_domain="prices", primary_provider="fmp", backup_provider="tiingo",
        observations=observations, actor="operator", observed_by_server=True,
    )
    forged = dict(server["evidence"])
    forged["observations"] = [*forged["observations"], {"symbol": "FAKE", "status": "ok", "max_abs_diff_pct": 0.0}]
    with store._connect() as conn:
        conn.execute(
            "UPDATE provider_reconciliations SET evidence_json = ? WHERE reconciliation_id = ?",
            (store._store_json(forged), server["reconciliation_id"]),
        )
    with pytest.raises(ValueError, match="intact server-generated"):
        provider_registry.approve_cutover(
            data_domain="prices", primary_provider="fmp", backup_provider="tiingo",
            reconciliation_id=server["reconciliation_id"], actor="operator",
            note="Attempted cutover using modified evidence.",
        )


def test_latest_approved_cutover_wins_registry_role(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    with store._connect() as conn:
        for stamp, primary, backup in (
            ("2026-01-01T00:00:00+00:00", "tiingo", "fmp"),
            ("2026-02-01T00:00:00+00:00", "fmp", "tiingo"),
        ):
            conn.execute(
                """
                INSERT INTO provider_cutovers(
                  cutover_id, created_at, actor, data_domain, primary_provider,
                  backup_provider, reconciliation_id, status, note, metadata_json
                ) VALUES (?, ?, 'committee', 'prices', ?, ?, 'recon', 'approved', 'approved', '{}')
                """,
                (f"cutover-{stamp[:10]}", stamp, primary, backup),
            )

    roles = {row["key"]: row["roles"] for row in provider_registry.registry()["providers"]}
    assert "prices:primary" in roles["fmp"]
    assert "prices:backup" in roles["tiingo"]


def test_live_history_provider_must_match_approved_cutover(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    for provider in ("FMP", "TIINGO"):
        monkeypatch.setenv(f"HELIOS_PROVIDER_{provider}_LICENSED", "1")
        monkeypatch.setenv(f"HELIOS_PROVIDER_{provider}_ENTITLED", "1")
        monkeypatch.setenv(f"HELIOS_PROVIDER_{provider}_SLA_OWNER", "Data Operations")
    monkeypatch.setenv("HELIOS_FMP_KEY", "configured")
    monkeypatch.setenv("HELIOS_TIINGO_KEY", "configured")
    reconciliation = provider_registry.record_reconciliation(
        data_domain="prices", primary_provider="fmp", backup_provider="tiingo",
        observations=[
            {"symbol": "AAPL", "status": "ok", "max_abs_diff_pct": 0.01},
            {"symbol": "MSFT", "status": "ok", "max_abs_diff_pct": 0.01},
        ],
        actor="committee",
        observed_by_server=True,
    )
    provider_registry.approve_cutover(
        data_domain="prices", primary_provider="fmp", backup_provider="tiingo",
        reconciliation_id=reconciliation["reconciliation_id"], actor="committee",
        note="Approved provider pair for institutional price histories.",
    )
    frame = pd.DataFrame(
        {"close": range(100, 400)},
        index=pd.bdate_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=300).tz_localize(None),
    )
    instrument = data.Instrument("LIVEYF", "Unexpected Feed", frame, "live", price_provider="yfinance")
    data.register(instrument)

    assert research_gate.instrument_readiness(instrument)["provider_control"]["passed"] is False
    model = portfolio.Model("PROVIDER-TEST", "Provider test", "growth", "", [portfolio.Holding("LIVEYF", 1.0)])
    assert research_gate._model_provider_control(model)["passed"] is False


def test_provider_gate_blocks_analysis_strategy_and_data_quality(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    _approve_price_pair(monkeypatch)
    frame = pd.DataFrame(
        {"close": range(100, 400)},
        index=pd.bdate_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=300).tz_localize(None),
    )
    data.register(data.Instrument("OUTSIDE", "Outside Feed", frame, "live", price_provider="yfinance"))
    helios.app.config.update(TESTING=True)
    client = helios.app.test_client()

    analysis = client.get("/api/analyze?ticker=OUTSIDE&horizon=21")
    strategy = client.get("/api/strategy/analyze?ticker=OUTSIDE")
    quality = client.get("/api/data-quality").get_json()

    assert analysis.status_code == 409
    assert "metrics" not in analysis.get_json()
    assert strategy.status_code == 200
    assert strategy.get_json()["eligible_for_real_research"] is False
    assert "strategy" not in strategy.get_json()
    provider_issues = [row for row in quality["issues"] if row["category"] == "provider_controls"]
    assert provider_issues and provider_issues[0]["severity"] == "blocker"
    assert quality["research_ready"] is False


def test_institutional_provider_order_never_falls_back_to_public_feed(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    assert data._price_provider_order() == []

    _approve_price_pair(monkeypatch)
    assert data._price_provider_order() == ["fmp", "tiingo"]


def test_institutional_provider_registry_failure_returns_no_provider(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    monkeypatch.setattr(
        provider_registry,
        "domain_readiness",
        lambda _domain: (_ for _ in ()).throw(RuntimeError("registry unavailable")),
    )

    assert data._price_provider_order() == []
    assert data.live_provider_available() is False


def test_institutional_price_fetch_never_uses_yfinance_side_channels(monkeypatch):
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    index = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=260)
    frame = pd.DataFrame({"close": range(100, 360)}, index=index)

    class ForbiddenYFinance:
        @staticmethod
        def Ticker(_symbol):
            raise AssertionError("institutional fetch touched yfinance metadata")

    monkeypatch.setattr(data, "HAS_YF", True)
    monkeypatch.setattr(data, "_yf", ForbiddenYFinance())
    monkeypatch.setattr(data, "_price_provider_order", lambda: ["fmp"])
    monkeypatch.setattr(data, "_provider_price_history", lambda provider, symbol, period: frame)

    instrument = data.fetch_live("SAFE", persist=False)

    assert instrument.price_provider == "fmp_eod_adjusted"
    assert instrument.headlines == []
    assert getattr(instrument, "_provider_ticker") is None


def test_active_provider_readiness_fails_after_reconciliation_tampering(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    cutover = _approve_price_pair(monkeypatch)
    assert provider_registry.domain_readiness("prices")["passed"] is True
    reconciliation = provider_registry.get_reconciliation(cutover["reconciliation_id"])
    forged = dict(reconciliation["evidence"])
    forged["max_allowed_difference_pct"] = 99.0
    with store._connect() as conn:
        conn.execute(
            "UPDATE provider_reconciliations SET evidence_json = ? WHERE reconciliation_id = ?",
            (store._store_json(forged), cutover["reconciliation_id"]),
        )
    status = provider_registry.domain_readiness("prices")
    assert status["passed"] is False
    assert "integrity" in status["detail"].lower()


def test_independent_validation_separates_sponsor_and_validator(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")

    with pytest.raises(ValueError, match="differ"):
        independent_validation.record_review(
            model_id="model-a", model_version=1, sponsor="Same Person", validator="same person",
            outcome="approved", controls={"oos": "passed"}, findings=[],
        )

    review = independent_validation.record_review(
        model_id="model-a",
        model_version=1,
        sponsor="Model Sponsor",
        validator="Independent Validator",
        outcome="approved",
        controls={"oos": "passed", "provenance": "passed", "implementation": "reviewed"},
        findings=[],
        next_review_due=(date.today() + timedelta(days=180)).isoformat(),
        note="Independent controls completed.",
    )

    status = independent_validation.status("model-a", 1)
    assert review["validator"] == "Independent Validator"
    assert status["passed"] is True


def test_independent_validation_rejects_future_review_date(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="cannot be in the future"):
        independent_validation.record_review(
            model_id="model-future", model_version=1, sponsor="Sponsor",
            validator="Validator", outcome="approved", controls={"oos": "passed"},
            findings=[], reviewed_at=(date.today() + timedelta(days=1)).isoformat(),
        )


def test_independent_validation_redacts_nested_secrets(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    review = independent_validation.record_review(
        model_id="model-secret", model_version=1, sponsor="Sponsor", validator="Validator",
        outcome="approved",
        controls={"provenance": {"status": "passed", "api_key": "never-store"}},
        findings=[{"token": "also-never-store", "detail": "No issue."}],
        next_review_due=(date.today() + timedelta(days=180)).isoformat(),
    )

    assert "never-store" not in str(review)
    assert review["controls"]["provenance"]["api_key"] == "[redacted]"
    assert review["findings"][0]["token"] == "[redacted]"


def test_expired_validation_exception_blocks_current_review(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    independent_validation.record_review(
        model_id="model-b", model_version=1, sponsor="Sponsor", validator="Validator",
        outcome="approved", controls={"oos": "passed"}, findings=[],
        next_review_due=(date.today() + timedelta(days=180)).isoformat(),
    )
    exception = independent_validation.record_exception(
        model_id="model-b", model_version=1, control_key="liquidity",
        owner="Owner", approver="Approver", reason="Temporary vendor coverage gap.",
        compensating_controls=["Block unsized holdings", "Weekly manual review"],
        expires_at=(date.today() + timedelta(days=2)).isoformat(),
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE model_validation_exceptions SET expires_at = ? WHERE exception_id = ?",
            ((date.today() - timedelta(days=1)).isoformat(), exception["exception_id"]),
        )

    status = independent_validation.status("model-b", 1)
    assert status["passed"] is False
    assert "expired" in status["detail"].lower()


def test_validation_exception_cannot_be_closed_through_another_model(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    exception = independent_validation.record_exception(
        model_id="model-a", model_version=1, control_key="coverage", owner="Owner",
        approver="Approver", reason="Temporary coverage exception for review.",
        compensating_controls=["Block client export"],
        expires_at=(date.today() + timedelta(days=10)).isoformat(),
    )

    with pytest.raises(ValueError, match="not found"):
        independent_validation.resolve_exception(
            exception["exception_id"], actor="Approver", note="Wrong model route.",
            expected_model_id="model-b",
        )
    assert independent_validation.exceptions("model-a")[0]["status"] == "open"


def test_privileged_event_chain_detects_tampering(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    first = operations.record_privileged_event(
        actor="admin", actor_roles=["admin"], action="provider_cutover",
        resource="prices", outcome="succeeded", details={"api_key": "do-not-store"},
    )
    operations.record_privileged_event(
        actor="validator", actor_roles=["independent_validator"], action="model_review",
        resource="model-a", outcome="succeeded",
    )
    assert first is not None
    assert operations.verify_privileged_events()["status"] == "intact"
    assert "do-not-store" not in str(operations.privileged_events())

    with store._connect() as conn:
        conn.execute(
            "UPDATE privileged_events SET outcome = 'tampered' WHERE event_id = ?",
            (first["event_id"],),
        )
    assert operations.verify_privileged_events()["status"] == "broken"


def test_partial_privileged_chain_is_never_reported_intact(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    for number in range(3):
        operations.record_privileged_event(
            actor="admin", actor_roles=["admin"], action=f"event-{number}",
            resource="control", outcome="succeeded",
        )
    result = operations.verify_privileged_events(limit=1)
    assert result["status"] == "partial"
    assert result["entries"] == 1
    assert result["total_entries"] == 3


def test_application_audit_chain_detects_actor_tampering(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    store.audit_append("approval", {"model": "model-a"}, actor="validator-a")
    assert store.audit_verify()["status"] == "intact"
    with store._connect() as conn:
        conn.execute("UPDATE audit_chain SET actor = 'validator-b' WHERE seq = 1")
    assert store.audit_verify()["status"] == "broken"


def test_required_audit_append_fails_loudly_when_persistence_is_unavailable(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    store.available = False
    store.warning = "audit store unavailable"

    with pytest.raises(RuntimeError, match="audit store unavailable"):
        store.audit_append("critical.operation", {"status": "attempted"}, required=True)


def test_plaintext_persistence_blocks_privileged_mutation_preflight(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "plaintext.db"))
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    persistence.reset_store_for_tests()
    store = persistence.get_store()
    assert store.available is True
    assert store.encryption["enabled"] is False
    monkeypatch.setattr(web_core, "AUTH_ENABLED", False)
    helios.app.config.update(TESTING=True)

    response = helios.app.test_client().post("/api/data-quality/sync")

    assert response.status_code == 503
    assert "encrypted audit persistence" in response.get_json()["error"].lower()


def test_incident_sync_tracks_acknowledgment_and_resolution(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    sync = operations.sync_incidents([
        {
            "category": "refresh_failures", "severity": "blocker", "target": "AAPL",
            "detail": "Provider timeout", "next_step": "Retry primary and backup.",
        }
    ], actor="monitor")
    incident = sync["incidents"][0]
    assert incident["severity"] == "high"
    assert incident["target"] == "AAPL"
    acknowledged = operations.update_incident(
        incident["incident_id"], status="acknowledged", actor="operator",
        owner="Data Operations", note="Investigating both providers.",
    )
    assert acknowledged["status"] == "acknowledged"
    cleared = operations.sync_incidents([], actor="monitor")
    assert cleared["resolved"] == 1
    assert operations.incidents()[0]["status"] == "resolved"
    with pytest.raises(ValueError, match="cannot be reopened"):
        operations.update_incident(
            incident["incident_id"], status="acknowledged", actor="operator",
            note="Attempt to reopen a resolved incident.",
        )


def test_latest_offhost_encrypted_backup_is_verified_without_live_restore(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "required")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
    monkeypatch.setenv("HELIOS_OFFHOST_BACKUP_EXPORT_DIR", str(tmp_path / "offhost"))
    monkeypatch.setenv("HELIOS_OFFHOST_BACKUP_ATTESTED", "1")
    store = _store(monkeypatch, tmp_path)
    store.audit_append("test", {"status": "ok"})
    exported = operations.export_encrypted_backup(actor="backup operator")

    result = operations.verify_latest_backup(actor="backup operator")

    assert result["passed"] is True
    assert result["integrity"] == "ok"
    assert result["restore_performed"] is False
    assert result["isolated_restore_tested"] is True
    assert result["live_data_mutated"] is False
    assert result["audit_chain"]["status"] == "intact"
    assert result["rpo_met"] is True
    assert result["rto_met"] is True
    assert result["restore_drill_passed"] is True
    assert result["manifest"] == exported["manifest"]
    assert result["manifest_heads_match"] is True
    assert result["method"].startswith("offhost_")
    operational = operations.operational_status()
    assert operational["latest_backup_verification"]["details"]["isolated_restore_tested"] is True


@pytest.mark.parametrize("tamper", ["snapshot", "scope"])
def test_offhost_restore_rejects_tampered_snapshot_or_scope(monkeypatch, tmp_path, tamper):
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "required")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
    offhost = tmp_path / "offhost"
    monkeypatch.setenv("HELIOS_OFFHOST_BACKUP_EXPORT_DIR", str(offhost))
    monkeypatch.setenv("HELIOS_OFFHOST_BACKUP_ATTESTED", "1")
    _store(monkeypatch, tmp_path / "database")
    exported = operations.export_encrypted_backup(actor="backup operator")
    snapshot = offhost / exported["snapshot"]
    manifest = offhost / exported["manifest"]
    if tamper == "snapshot":
        raw = snapshot.read_bytes()
        snapshot.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        expected = "checksum"
    else:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["workspace_scope_sha256"] = "0" * 64
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        expected = "different workspace scope"

    with pytest.raises(RuntimeError, match=expected):
        operations.verify_latest_backup(actor="backup operator")


def test_offhost_restore_fails_closed_on_manifest_head_or_stale_rpo(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "required")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
    offhost = tmp_path / "offhost"
    monkeypatch.setenv("HELIOS_OFFHOST_BACKUP_EXPORT_DIR", str(offhost))
    monkeypatch.setenv("HELIOS_OFFHOST_BACKUP_ATTESTED", "1")
    monkeypatch.setenv("HELIOS_RESTORE_RPO_HOURS", "1")
    _store(monkeypatch, tmp_path / "database")
    exported = operations.export_encrypted_backup(actor="backup operator")
    snapshot = offhost / exported["snapshot"]
    manifest = offhost / exported["manifest"]
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["application_audit_head"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    head_mismatch = operations.verify_latest_backup(actor="backup operator")

    assert head_mismatch["passed"] is False
    assert head_mismatch["manifest_heads_match"] is False
    assert head_mismatch["integrity_passed"] is False

    payload["application_audit_head"] = exported["application_audit_head"]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    stale_time = time.time() - 7200
    os.utime(snapshot, (stale_time, stale_time))

    stale = operations.verify_latest_backup(actor="backup operator")

    assert stale["passed"] is False
    assert stale["integrity_passed"] is True
    assert stale["rpo_met"] is False


def test_encrypted_backup_and_privileged_audit_exports_are_manifested_and_redacted(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "required")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
    store = _store(monkeypatch, tmp_path / "database")
    audit_path = tmp_path / "siem" / "privileged.jsonl"
    offhost = tmp_path / "offhost"
    monkeypatch.setenv("HELIOS_AUDIT_EXPORT_PATH", str(audit_path))
    monkeypatch.setenv("HELIOS_AUDIT_EXPORT_REQUIRED", "1")
    monkeypatch.setenv("HELIOS_WORM_SIEM_ATTESTED", "1")
    monkeypatch.setenv("HELIOS_OFFHOST_BACKUP_EXPORT_DIR", str(offhost))
    monkeypatch.setenv("HELIOS_OFFHOST_BACKUP_ATTESTED", "1")

    operations.record_privileged_event(
        actor="custody operator", actor_roles=["admin"], action="custody_test",
        resource="workspace", outcome="passed",
        details={"api_key": "NEVER_EXPORT_THIS", "note": "safe checkpoint"},
    )
    exported = operations.export_encrypted_backup(actor="custody operator")
    checkpoint = operations.audit_export_checkpoint(actor="custody operator")

    snapshot = offhost / exported["snapshot"]
    manifest = offhost / exported["manifest"]
    assert snapshot.read_bytes().startswith(persistence.ENCRYPTED_DB_MAGIC)
    manifest_payload = __import__("json").loads(manifest.read_text(encoding="utf-8"))
    assert manifest_payload["snapshot_sha256"] == exported["snapshot_sha256"]
    assert manifest_payload["plaintext_exported"] is False
    assert manifest_payload["application_audit_head"]
    assert manifest_payload["privileged_audit_head"]
    audit_text = audit_path.read_text(encoding="utf-8")
    assert "NEVER_EXPORT_THIS" not in audit_text
    assert "[redacted]" in audit_text
    assert checkpoint["written"] is True
    audit_status = operations.audit_export_status()
    assert audit_status["worm_siem_attested"] is True
    assert audit_status["required"] is True
    assert audit_status["current_checkpoint"] is True
    assert operations.backup_export_status()["latest_export"]["outcome"] == "passed"
    assert store.available is True

    operations.record_privileged_event(
        actor="custody operator", actor_roles=["admin"], action="post_checkpoint_change",
        resource="workspace", outcome="passed",
    )
    assert operations.audit_export_status()["current_checkpoint"] is False


def test_required_audit_export_failure_rolls_back_privileged_event(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    monkeypatch.setenv("HELIOS_AUDIT_EXPORT_REQUIRED", "1")
    monkeypatch.delenv("HELIOS_AUDIT_EXPORT_PATH", raising=False)

    with pytest.raises(RuntimeError, match="required"):
        operations.record_privileged_event(
            actor="operator", actor_roles=["admin"], action="must_rollback",
            resource="workspace", outcome="passed",
        )

    assert operations.privileged_events() == []
    assert store.available is True


def test_rbac_and_mfa_block_privileged_provider_operations(monkeypatch):
    monkeypatch.setattr(web_core, "AUTH_ENABLED", True)
    monkeypatch.setattr(web_core, "AUTH_USER", "advisor")
    monkeypatch.setattr(web_core, "AUTH_PASSWORD", "strong-local-password")
    monkeypatch.setattr(web_core, "_AUTH_FAILURE_DELAY_SECONDS", 0.0)
    monkeypatch.setenv("HELIOS_BASIC_ROLES", "viewer")
    helios.app.config.update(TESTING=True)
    client = helios.app.test_client()

    denied = client.post(
        "/api/providers/reconciliations", json={},
        headers=_basic("advisor", "strong-local-password"),
    )
    assert denied.status_code == 403
    assert "permission" in denied.get_json()["error"].lower()

    monkeypatch.setenv("HELIOS_BASIC_ROLES", "admin")
    monkeypatch.setenv("HELIOS_REQUIRE_MFA", "1")
    denied_mfa = client.post(
        "/api/providers/reconciliations", json={},
        headers=_basic("advisor", "strong-local-password"),
    )
    assert denied_mfa.status_code == 403
    assert "mfa" in denied_mfa.get_json()["error"].lower()


def test_trusted_tls_public_bind_fails_closed_without_cert(monkeypatch):
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    monkeypatch.setattr(serve.web_core, "AUTH_ENABLED", True)
    monkeypatch.delenv("HELIOS_TRUSTED_PROXY_TLS", raising=False)
    monkeypatch.delenv("HELIOS_TLS_CERT_PATH", raising=False)
    monkeypatch.delenv("HELIOS_TLS_KEY_PATH", raising=False)

    with pytest.raises(SystemExit, match="direct public/LAN bind"):
        serve.validate_deployment_security("0.0.0.0", use_tls=False)

    serve.validate_deployment_security("127.0.0.1", use_tls=False)

    with pytest.raises(SystemExit, match="direct public/LAN bind"):
        serve.validate_deployment_security("192.168.1.50", use_tls=False)


def test_public_bind_cannot_delegate_tls_to_plaintext_waitress(monkeypatch):
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    monkeypatch.setenv("HELIOS_TRUSTED_PROXY_TLS", "1")
    monkeypatch.setenv("HELIOS_TRUSTED_PROXY_IPS", "10.0.0.10")
    monkeypatch.setattr(serve.web_core, "AUTH_ENABLED", True)

    with pytest.raises(SystemExit, match="direct public/LAN bind"):
        serve.validate_deployment_security("0.0.0.0", use_tls=False)


def test_public_bind_rejects_even_operator_supplied_development_tls(monkeypatch, tmp_path):
    cert = tmp_path / "trusted-cert.pem"
    key = tmp_path / "trusted-key.pem"
    cert.write_text("test certificate path", encoding="utf-8")
    key.write_text("test key path", encoding="utf-8")
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    monkeypatch.setenv("HELIOS_TLS_CERT_PATH", str(cert))
    monkeypatch.setenv("HELIOS_TLS_KEY_PATH", str(key))
    monkeypatch.setenv("HELIOS_TLS_TRUSTED", "1")
    monkeypatch.delenv("HELIOS_TRUSTED_PROXY_TLS", raising=False)
    monkeypatch.setattr(serve.web_core, "AUTH_ENABLED", True)

    with pytest.raises(SystemExit, match="direct public/LAN bind"):
        serve.validate_deployment_security("0.0.0.0", use_tls=True)


def test_sso_headers_require_explicit_trusted_proxy(monkeypatch):
    monkeypatch.setattr(web_core, "AUTH_ENABLED", True)
    monkeypatch.setenv("HELIOS_SSO_ENABLED", "1")
    monkeypatch.delenv("HELIOS_TRUSTED_PROXY_IPS", raising=False)
    helios.app.config.update(TESTING=True)
    client = helios.app.test_client()

    response = client.get("/api/health", headers={"X-Helios-User": "spoofed", "X-Helios-Roles": "admin"})

    assert response.status_code == 401


def test_security_status_and_session_routes_expose_no_secrets(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setattr(web_core, "AUTH_ENABLED", False)
    monkeypatch.setenv("HELIOS_TRUSTED_TLS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-never-appear")
    helios.app.config.update(TESTING=True)
    client = helios.app.test_client()

    status = client.get("/api/security/status")
    created = client.post("/api/auth/session")
    deleted = client.delete("/api/auth/session")

    assert status.status_code == 200
    assert status.get_json()["principal"]["auth_method"] == "auth_disabled"
    assert status.get_json()["transport"]["trusted_tls_asserted"] is True
    assert created.status_code == 200
    assert "Secure" in created.headers["Set-Cookie"]
    assert deleted.status_code == 200
    combined = f"{status.get_data(as_text=True)}{created.get_data(as_text=True)}{deleted.get_data(as_text=True)}"
    assert "must-never-appear" not in combined


def test_institutional_request_tokens_require_an_active_secured_session(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "required")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
    _store(monkeypatch, tmp_path)
    monkeypatch.setattr(web_core, "AUTH_ENABLED", True)
    monkeypatch.setattr(web_core, "AUTH_USER", "advisor")
    monkeypatch.setattr(web_core, "AUTH_PASSWORD", "strong-local-password")
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    monkeypatch.setenv("HELIOS_BASIC_ROLES", "admin")
    monkeypatch.setenv("HELIOS_BASIC_MFA_VERIFIED", "1")
    monkeypatch.setenv("HELIOS_TEST_ENFORCE_REQUEST_TOKEN", "1")
    monkeypatch.setattr(data, "HAS_YF", False)
    helios.app.config.update(TESTING=True)
    client = helios.app.test_client()
    headers = _basic("advisor", "strong-local-password")

    before = client.get("/api/security/status", headers=headers).get_json()["request_protection"]
    assert before["available"] is True
    assert before["token"]
    assert before["binding"] == "session_bootstrap"

    created = client.post(
        "/api/auth/session",
        headers={**headers, before["header"]: before["token"]},
    )
    assert created.status_code == 200
    after = client.get("/api/security/status", headers=headers).get_json()["request_protection"]
    assert after["available"] is True
    assert after["binding"] == "session"
    assert after["token"]

    reached_handler = client.post(
        "/api/live",
        json={"symbol": "AAPL"},
        headers={**headers, after["header"]: after["token"]},
    )
    assert reached_handler.status_code == 503
    assert reached_handler.get_json().get("code") != "request_token_invalid"


def test_proxy_tls_cookie_flag_requires_the_request_to_come_from_trusted_proxy(monkeypatch):
    monkeypatch.setattr(web_core, "AUTH_ENABLED", False)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "0")
    monkeypatch.setenv("HELIOS_TRUSTED_PROXY_TLS", "1")
    monkeypatch.setenv("HELIOS_TRUSTED_PROXY_IPS", "10.0.0.10")
    monkeypatch.delenv("HELIOS_TRUSTED_TLS", raising=False)
    helios.app.config.update(TESTING=True)
    client = helios.app.test_client()

    trusted = client.post("/api/auth/session", environ_base={"REMOTE_ADDR": "10.0.0.10"})
    spoofed = client.post("/api/auth/session", environ_base={"REMOTE_ADDR": "10.0.0.11"})

    assert "Secure" in trusted.headers["Set-Cookie"]
    assert "Secure" not in spoofed.headers["Set-Cookie"]


def test_institutional_operational_readiness_requires_every_control(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    monkeypatch.setenv("HELIOS_AUTH", "1")
    monkeypatch.setenv("HELIOS_REQUIRE_MFA", "1")
    monkeypatch.setenv("HELIOS_TRUSTED_TLS", "1")
    monkeypatch.setenv("HELIOS_INCIDENT_OWNER", "Operations Owner")
    observed_at = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(operations, "operational_status", lambda: {
        "summary": {"critical": 0},
        "incident_owner": "Operations Owner",
        "latest_backup_verification": {
            "created_at": observed_at,
            "details": {
                "passed": True,
                "isolated_restore_tested": True,
                "rpo_met": True,
                "rto_met": True,
                "manifest_heads_match": True,
                "offhost_attested": True,
                "method": "offhost_manifest_checksum_scope_decrypt_integrity_schema_audit",
                "audit_chain": {"head_hash": "current-head"},
            },
        },
        "audit_chain": {"status": "intact", "head_hash": "current-head"},
        "privileged_chain": {"status": "intact"},
        "persistence_encryption": {
            "enabled": True, "external_custody": True, "custody_attested": True,
            "key_id": "test-key", "key_version": "2", "rotation_configured": True,
        },
        "backup_export": {
            "configured": True, "offhost_attested": True,
            "latest_export": {"outcome": "passed"},
        },
        "audit_export": {
            "configured": True, "required": True, "worm_siem_attested": True,
            "written": True, "current_checkpoint": True,
        },
    })

    ready = operations.institutional_readiness()
    assert ready["passed"] is True
    assert all(row["passed"] for row in ready["checks"])

    monkeypatch.setattr(operations, "operational_status", lambda: {
        "summary": {"critical": 1},
        "incident_owner": "Unassigned",
        "latest_backup_verification": None,
        "audit_chain": {"status": "broken"},
        "privileged_chain": {"status": "broken"},
        "persistence_encryption": {"enabled": False},
    })
    blocked = operations.institutional_readiness()
    assert blocked["passed"] is False
    assert {row["key"] for row in blocked["blockers"]} >= {
        "encryption", "backup_restore", "audit_chain", "privileged_audit_chain",
        "critical_incidents", "incident_owner",
    }


def test_institutional_readiness_accepts_only_deployable_signed_sso(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    monkeypatch.setenv("HELIOS_AUTH", "1")
    monkeypatch.setenv("HELIOS_REQUIRE_MFA", "0")
    monkeypatch.setenv("HELIOS_TRUSTED_TLS", "1")
    monkeypatch.setenv("HELIOS_SSO_ENABLED", "1")
    monkeypatch.setenv("HELIOS_SSO_ASSERTION_SECRET", "s" * 40)
    monkeypatch.setenv("HELIOS_SSO_ISSUER", "https://idp.example.test")
    monkeypatch.setenv("HELIOS_SSO_AUDIENCE", "helios-test")
    monkeypatch.setenv("HELIOS_SSO_ALLOW_BASIC_FALLBACK", "0")
    observed_at = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(operations, "operational_status", lambda: {
        "summary": {"critical": 0},
        "incident_owner": "Operations Owner",
        "latest_backup_verification": {
            "created_at": observed_at,
            "details": {
                "passed": True,
                "isolated_restore_tested": True,
                "rpo_met": True,
                "rto_met": True,
                "manifest_heads_match": True,
                "offhost_attested": True,
                "method": "offhost_manifest_checksum_scope_decrypt_integrity_schema_audit",
                "audit_chain": {"head_hash": "current-head"},
            },
        },
        "audit_chain": {"status": "intact", "head_hash": "current-head"},
        "privileged_chain": {"status": "intact"},
        "persistence_encryption": {
            "enabled": True, "external_custody": True, "custody_attested": True,
            "key_id": "test-key", "key_version": "2", "rotation_configured": True,
        },
        "backup_export": {
            "configured": True, "offhost_attested": True,
            "latest_export": {"outcome": "passed"},
        },
        "audit_export": {
            "configured": True, "required": True, "worm_siem_attested": True,
            "written": True, "current_checkpoint": True,
        },
    })

    monkeypatch.delenv("HELIOS_TRUSTED_PROXY_IPS", raising=False)
    no_proxy = operations.institutional_readiness()
    assert next(row for row in no_proxy["checks"] if row["key"] == "mfa_or_sso")["passed"] is False

    monkeypatch.setenv("HELIOS_TRUSTED_PROXY_IPS", "10.0.0.10")
    deployable = operations.institutional_readiness()
    assert identity.verifier_status()["institutional_ready"] is True
    assert next(row for row in deployable["checks"] if row["key"] == "mfa_or_sso")["passed"] is True

    monkeypatch.setenv("HELIOS_SSO_ALLOW_BASIC_FALLBACK", "1")
    fallback = operations.institutional_readiness()
    assert next(row for row in fallback["checks"] if row["key"] == "mfa_or_sso")["passed"] is False

    monkeypatch.setenv("HELIOS_REQUIRE_MFA", "1")
    masked = operations.institutional_readiness()
    assert next(row for row in masked["checks"] if row["key"] == "mfa_or_sso")["passed"] is True
    assert next(row for row in masked["checks"] if row["key"] == "enterprise_identity")["passed"] is False


def test_model_readiness_requires_completed_replay_verified_prospective_trial(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    model = portfolio.Model(
        id="TRIAL-GATED-MODEL", name="Trial Gated Model", mandate_key="balanced",
        mandate_context="", holdings=[],
    )
    portfolio.register(model)
    monkeypatch.setattr(research_gate, "_model_freshness", lambda _model: {"passed": True, "detail": "Current."})
    monkeypatch.setattr(research_gate, "_model_provenance", lambda _model: (True, "Eligible."))
    monkeypatch.setattr(research_gate.data_quality, "model_coverage", lambda _model: {
        "coverage_state": "real", "real_coverage_count": 0,
    })
    monkeypatch.setattr(research_gate, "_model_validation", lambda _model: {
        "passed": True, "measured_count": 20, "benchmark_coverage_pct": 100.0, "reason": "Passed.",
    })
    monkeypatch.setattr(research_gate, "_model_provider_control", lambda _model: {
        "passed": True, "detail": "Passed.", "required_action": "",
    })
    monkeypatch.setattr(research_gate.independent_validation, "status", lambda _id: {
        "passed": True, "detail": "Passed.", "required_action": "",
    })
    monkeypatch.setattr(research_gate.operations, "institutional_readiness", lambda: {
        "passed": True, "detail": "Passed.", "required_action": "",
    })

    result = research_gate.model_readiness(model)

    prospective = next(row for row in result["checks"] if row["key"] == "prospective_trial")
    assert prospective["passed"] is False
    assert result["passed"] is False


def test_failed_final_privileged_audit_returns_hardened_503(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setattr(web_core, "AUTH_ENABLED", False)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    events = iter([True, False])
    monkeypatch.setattr(web_core, "_security_event", lambda *_args, **_kwargs: next(events))
    monkeypatch.setattr(data_quality, "dashboard_payload", lambda sync_alerts=False: {"issues": []})
    monkeypatch.setattr(operations, "sync_incidents", lambda *_args, **_kwargs: {"synced": True})
    helios.app.config.update(TESTING=True)

    response = helios.app.test_client().post("/api/data-quality/sync")

    assert response.status_code == 503
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_failed_final_privileged_audit_rolls_back_route_mutation(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    monkeypatch.setattr(web_core, "AUTH_ENABLED", False)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    events = iter([True, False])
    monkeypatch.setattr(web_core, "_security_event", lambda *_args, **_kwargs: next(events))
    monkeypatch.setattr(data_quality, "dashboard_payload", lambda sync_alerts=False: {
        "issues": [{
            "category": "provider_failure",
            "target": "ROLLBACK",
            "severity": "blocker",
            "detail": "This route mutation must roll back when final audit fails.",
        }],
    })
    helios.app.config.update(TESTING=True)

    response = helios.app.test_client().post("/api/operations/incidents/sync")

    assert response.status_code == 503
    with store._connect() as conn:
        assert int(conn.execute("SELECT COUNT(*) FROM operational_incidents").fetchone()[0]) == 0


def test_failed_final_privileged_audit_restores_in_memory_instrument(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    monkeypatch.setattr(web_core, "AUTH_ENABLED", False)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    events = iter([True, False])
    monkeypatch.setattr(web_core, "_security_event", lambda *_args, **_kwargs: next(events))
    idx = pd.bdate_range("2025-01-02", periods=40)
    original = data.Instrument("ROLLBACK", "Original", pd.DataFrame({"close": range(100, 140)}, index=idx), "upload")
    data.register(original)
    helios.app.config.update(TESTING=True)

    replacement = "date,close\n" + "\n".join(
        f"{day.date()},{200 + offset}" for offset, day in enumerate(idx)
    )
    response = helios.app.test_client().post(
        "/api/upload",
        data={
            "symbol": "ROLLBACK",
            "name": "Replacement",
            "file": (BytesIO(replacement.encode()), "rollback.csv"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert data.get("ROLLBACK") is original
    assert data.get("ROLLBACK").name == "Original"
    with store._connect() as conn:
        assert int(conn.execute("SELECT COUNT(*) FROM instruments WHERE symbol = ?", ("ROLLBACK",)).fetchone()[0]) == 0


def test_privileged_batch_refresh_stays_on_request_transaction_thread(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setattr(web_core, "AUTH_ENABLED", False)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    monkeypatch.setattr(web_core, "_security_event", lambda *_args, **_kwargs: True)
    idx = pd.bdate_range("2025-01-02", periods=40)
    for symbol in ("SERIAL-A", "SERIAL-B"):
        data.register(data.Instrument(symbol, symbol, pd.DataFrame({"close": range(100, 140)}, index=idx), "live"))
    monkeypatch.setattr(data, "_live_refresh_worker_count", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr(
        "helios_web.data.ThreadPoolExecutor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("worker pool must not start")),
    )
    monkeypatch.setattr(
        data,
        "refresh_live_symbol",
        lambda symbol, refresh_journal=False: {
            "symbol": symbol, "status": "ok", "rows_added": 0, "message": "unchanged",
        },
    )
    monkeypatch.setattr(data, "_refresh_signal_journal_forward_results", lambda: None)
    monkeypatch.setattr(data_quality, "dashboard_payload", lambda sync_alerts=False: {"issues": []})
    monkeypatch.setattr(operations, "sync_incidents", lambda *_args, **_kwargs: {"synced": True})
    helios.app.config.update(TESTING=True)

    response = helios.app.test_client().post("/api/data/refresh", json={"all": True})

    assert response.status_code == 200
    assert response.get_json()["refreshed"] == 2


def test_model_editor_requires_model_sponsor_permission(monkeypatch):
    monkeypatch.setattr(web_core, "AUTH_ENABLED", True)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "0")
    monkeypatch.setenv("HELIOS_BASIC_ROLES", "analyst")
    helios.app.config.update(TESTING=True)
    client = helios.app.test_client()

    denied = client.post(
        "/api/models/UNKNOWN/editor",
        headers=_basic(web_core.AUTH_USER, web_core.AUTH_PASSWORD),
        json={"change_note": "Analyst must not mutate governed holdings.", "holdings": []},
    )
    assert denied.status_code == 403

    monkeypatch.setenv("HELIOS_BASIC_ROLES", "model_sponsor")
    allowed_through_rbac = client.post(
        "/api/models/UNKNOWN/editor",
        headers=_basic(web_core.AUTH_USER, web_core.AUTH_PASSWORD),
        json={"change_note": "Sponsor request reaches model validation.", "holdings": []},
    )
    assert allowed_through_rbac.status_code == 404


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/model-library/import", {"slug": "unknown"}),
        ("/api/model/thesis", {"id": "unknown", "change_note": "Governed change."}),
        ("/api/research-context", {"target_id": "UNKNOWN"}),
        ("/api/trials", {"target_kind": "instrument", "target_id": "UNKNOWN", "protocol": {}}),
    ],
)
def test_all_model_and_trial_mutation_routes_require_governance_submit(monkeypatch, path, body):
    monkeypatch.setattr(web_core, "AUTH_ENABLED", True)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "0")
    monkeypatch.setenv("HELIOS_BASIC_ROLES", "analyst")
    helios.app.config.update(TESTING=True)
    client = helios.app.test_client()
    headers = _basic(web_core.AUTH_USER, web_core.AUTH_PASSWORD)

    assert client.post(path, headers=headers, json=body).status_code == 403

    monkeypatch.setenv("HELIOS_BASIC_ROLES", "model_sponsor")
    assert client.post(path, headers=headers, json=body).status_code in {400, 404}


def test_initial_model_upload_requires_governance_submit(monkeypatch):
    monkeypatch.setattr(web_core, "AUTH_ENABLED", True)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "0")
    monkeypatch.setenv("HELIOS_BASIC_ROLES", "analyst")
    helios.app.config.update(TESTING=True)
    client = helios.app.test_client()
    headers = _basic(web_core.AUTH_USER, web_core.AUTH_PASSWORD)

    denied = client.post(
        "/api/model/upload",
        headers=headers,
        data={"file": (BytesIO(b"Ticker,Weight\nAAPL,100\n"), "model.csv"), "name": "Denied"},
        content_type="multipart/form-data",
    )
    assert denied.status_code == 403

    monkeypatch.setenv("HELIOS_BASIC_ROLES", "model_sponsor")
    allowed = client.post("/api/model/upload", headers=headers)
    assert allowed.status_code == 400


def test_completing_trial_requires_governance_approver(monkeypatch):
    monkeypatch.setattr(web_core, "AUTH_ENABLED", True)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "0")
    monkeypatch.setenv("HELIOS_BASIC_ROLES", "model_sponsor")
    helios.app.config.update(TESTING=True)
    client = helios.app.test_client()
    headers = _basic(web_core.AUTH_USER, web_core.AUTH_PASSWORD)

    denied = client.post(
        "/api/trials/unknown/close", headers=headers,
        json={"status": "completed", "note": "Committee completion review."},
    )
    assert denied.status_code == 403

    monkeypatch.setenv("HELIOS_BASIC_ROLES", "approver")
    allowed = client.post(
        "/api/trials/unknown/close", headers=headers,
        json={"status": "completed", "note": "Committee completion review."},
    )
    assert allowed.status_code == 400


def test_backup_verify_route_uses_authenticated_actor(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setattr(web_core, "AUTH_ENABLED", False)
    captured = {}
    monkeypatch.setattr(
        operations,
        "verify_latest_backup",
        lambda *, actor: captured.update(actor=actor) or {"passed": True, "restore_performed": False},
    )
    helios.app.config.update(TESTING=True)

    response = helios.app.test_client().post("/api/operations/backup/verify")

    assert response.status_code == 200
    assert response.get_json()["verification"]["passed"] is True
    assert captured["actor"] == "local-operator"


def test_backup_and_audit_export_routes_use_actor_and_structured_failures(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setattr(web_core, "AUTH_ENABLED", False)
    captured = []
    monkeypatch.setattr(
        operations,
        "export_encrypted_backup",
        lambda *, actor: captured.append(("backup", actor)) or {"snapshot": "backup.hdb"},
    )
    monkeypatch.setattr(
        operations,
        "audit_export_checkpoint",
        lambda *, actor: captured.append(("audit", actor)) or {"written": True},
    )
    helios.app.config.update(TESTING=True)
    client = helios.app.test_client()

    backup = client.post("/api/operations/backup/export")
    audit = client.post("/api/operations/audit-export/checkpoint")

    assert backup.status_code == 200
    assert backup.get_json()["export"]["snapshot"] == "backup.hdb"
    assert audit.status_code == 200
    assert audit.get_json()["checkpoint"]["written"] is True
    assert captured == [("backup", "local-operator"), ("audit", "local-operator")]

    monkeypatch.setattr(
        operations,
        "export_encrypted_backup",
        lambda *, actor: (_ for _ in ()).throw(RuntimeError("destination unavailable")),
    )
    failed = client.post("/api/operations/backup/export")
    body = failed.get_json()
    assert failed.status_code == 503
    assert body["code"] == "backup_export_failed"
    assert body["retryable"] is True
    assert "destination" in body["next_step"]


def test_validation_exception_routes_create_and_resolve(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setattr(web_core, "AUTH_ENABLED", False)
    portfolio.register(portfolio.Model("VAL-EX-API", "Validation Exception API", "balanced", "", []))
    helios.app.config.update(TESTING=True)
    client = helios.app.test_client()

    created = client.post("/api/models/VAL-EX-API/validation-exceptions", json={
        "model_version": 1,
        "control_key": "liquidity_data",
        "owner": "Model Sponsor",
        "reason": "Temporary licensed venue data gap under committee review.",
        "compensating_controls": ["Block approval", "Daily manual coverage review"],
        "expires_at": (date.today() + timedelta(days=10)).isoformat(),
    })
    assert created.status_code == 200
    exception = created.get_json()["exception"]
    assert exception["approver"] == "local-operator"

    resolved = client.post(
        f"/api/models/VAL-EX-API/validation-exceptions/{exception['exception_id']}",
        json={"status": "resolved", "note": "Licensed coverage restored and independently checked."},
    )
    assert resolved.status_code == 200
    assert resolved.get_json()["exception"]["status"] == "resolved"


def test_independent_validation_api_contract_and_role_mfa_guards(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    portfolio.register(portfolio.Model("VAL-API", "Validation API", "balanced", "", []))
    monkeypatch.setattr(web_core, "AUTH_ENABLED", False)
    helios.app.config.update(TESTING=True)
    client = helios.app.test_client()
    unknown = client.get("/api/models/UNKNOWN/independent-validation")
    assert unknown.status_code == 404
    future = client.post("/api/models/VAL-API/independent-validation", json={
        "sponsor": "Model Sponsor", "outcome": "approved", "controls": {"oos": "passed"},
        "reviewed_at": (date.today() + timedelta(days=1)).isoformat(),
    })
    assert future.status_code == 400

    monkeypatch.setattr(web_core, "AUTH_ENABLED", True)
    monkeypatch.setattr(web_core, "AUTH_USER", "validator")
    monkeypatch.setattr(web_core, "AUTH_PASSWORD", "strong-local-password")
    monkeypatch.setattr(web_core, "_AUTH_FAILURE_DELAY_SECONDS", 0.0)
    monkeypatch.setenv("HELIOS_BASIC_ROLES", "viewer")
    denied = client.post(
        "/api/models/VAL-API/independent-validation",
        json={"sponsor": "Sponsor", "outcome": "approved", "controls": {"oos": "passed"}},
        headers=_basic("validator", "strong-local-password"),
    )
    assert denied.status_code == 403

    monkeypatch.setenv("HELIOS_BASIC_ROLES", "independent_validator")
    monkeypatch.setenv("HELIOS_REQUIRE_MFA", "1")
    monkeypatch.setenv("HELIOS_BASIC_MFA_VERIFIED", "0")
    denied_mfa = client.post(
        "/api/models/VAL-API/independent-validation",
        json={"sponsor": "Sponsor", "outcome": "approved", "controls": {"oos": "passed"}},
        headers=_basic("validator", "strong-local-password"),
    )
    assert denied_mfa.status_code == 403
    monkeypatch.setenv("HELIOS_BASIC_MFA_VERIFIED", "1")
    accepted = client.post(
        "/api/models/VAL-API/independent-validation",
        json={
            "sponsor": "Model Sponsor", "outcome": "approved",
            "controls": {"oos": "passed"},
            "next_review_due": (date.today() + timedelta(days=180)).isoformat(),
        },
        headers=_basic("validator", "strong-local-password"),
    )
    assert accepted.status_code == 200
    assert accepted.get_json()["review"]["validator"] == "validator"


def test_data_and_ledger_writes_require_role_and_mfa(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setattr(web_core, "AUTH_ENABLED", True)
    monkeypatch.setattr(web_core, "AUTH_USER", "operator")
    monkeypatch.setattr(web_core, "AUTH_PASSWORD", "strong-local-password")
    monkeypatch.setattr(web_core, "_AUTH_FAILURE_DELAY_SECONDS", 0.0)
    helios.app.config.update(TESTING=True)
    client = helios.app.test_client()
    headers = _basic("operator", "strong-local-password")

    monkeypatch.setenv("HELIOS_BASIC_ROLES", "analyst")
    assert client.post("/api/data-quality/sync", headers=headers).status_code == 403
    assert client.post("/api/ledger/account/map", json={"account_id": "A"}, headers=headers).status_code == 403

    monkeypatch.setenv("HELIOS_BASIC_ROLES", "data_steward")
    monkeypatch.setenv("HELIOS_REQUIRE_MFA", "1")
    monkeypatch.setenv("HELIOS_BASIC_MFA_VERIFIED", "0")
    assert client.post("/api/data-quality/sync", headers=headers).status_code == 403
    assert client.post("/api/ledger/account/map", json={"account_id": "A"}, headers=headers).status_code == 403

    monkeypatch.setenv("HELIOS_BASIC_MFA_VERIFIED", "1")
    assert client.post("/api/data-quality/sync", headers=headers).status_code == 200
    mapped = client.post(
        "/api/ledger/account/map",
        json={"account_id": "ACCOUNT-1", "display_name": "Institutional account"},
        headers=headers,
    )
    assert mapped.status_code == 200
    assert mapped.get_json()["accounts"][0]["account_id"] == "ACCOUNT-1"


def test_data_quality_sync_attributes_incidents_to_authenticated_actor(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setattr(web_core, "AUTH_ENABLED", False)
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        data_quality,
        "dashboard_payload",
        lambda sync_alerts=False: {"issues": [], "synced": sync_alerts},
    )
    monkeypatch.setattr(
        operations,
        "sync_incidents",
        lambda _issues, *, actor: captured.update(actor=actor) or {"synced": True},
    )
    helios.app.config.update(TESTING=True)
    response = helios.app.test_client().post("/api/data-quality/sync")
    assert response.status_code == 200
    assert captured["actor"] == "local-operator"


def test_disabled_provider_controls_do_not_block_live_history(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "0")
    frame = pd.DataFrame(
        {"close": range(100, 400)},
        index=pd.bdate_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=300).tz_localize(None),
    )
    instrument = data.Instrument("LOCAL", "Local Research", frame, "live", price_provider="yfinance")

    assert research_gate.instrument_readiness(instrument)["provider_control"]["passed"] is True

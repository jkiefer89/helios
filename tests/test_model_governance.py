import numpy as np
import pandas as pd
import pytest
import base64
from io import BytesIO

import app as helios
from engine import data, model_governance, persistence, portfolio, research_gate
from helios_web import core as web_core


def _use_db(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    monkeypatch.delenv("HELIOS_GOVERNANCE_APPROVER_PIN", raising=False)
    monkeypatch.delenv("HELIOS_GOVERNANCE_APPROVER_PIN_HASH", raising=False)
    persistence.reset_store_for_tests()
    store = persistence.get_store()
    assert store.available is True
    return store


def _client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def _make_research_ready(monkeypatch, model, *extra_symbols):
    symbols = [holding.ticker for holding in model.holdings]
    symbols.extend(extra_symbols)
    symbols.extend(["SPY", "QQQ"])
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=760)
    for offset, symbol in enumerate(dict.fromkeys(symbols)):
        if symbol in {"SPY", "QQQ"}:
            pattern = np.array([0.006, -0.003, 0.005, -0.002, 0.004, -0.001])
        else:
            scale = 1.0 + offset * 0.03
            pattern = np.array([0.012, -0.004, 0.009, -0.003, 0.007, -0.002]) * scale
        returns = np.resize(pattern, len(idx))
        close = pd.Series(100.0 * np.cumprod(1.0 + returns), index=idx, name="close")
        data.register(data.Instrument(symbol, symbol, close.to_frame(), "upload", []))
    monkeypatch.setattr(
        research_gate,
        "_model_validation",
        lambda _model: {
            "passed": True,
            "measured_count": 24,
            "benchmark_coverage_pct": 100.0,
            "benchmark": {"symbol": "SPY", "status": "available"},
            "parameters": {"horizon_days": 21, "train_window": 252, "step": 21},
            "reason": "Governance fixture supplies separately tested historical validation.",
            "verdicts": {},
        },
    )


def test_model_governance_tracks_versions_approval_risk_limits_and_history(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nAAPL,20\nMSFT,20\nGOOGL,20\nAMZN,20\nNVDA,20\n",
        "committee-ready.csv",
        "Committee Ready Growth",
        "balanced",
        "Advisor-created model for governance review.",
    )
    _make_research_ready(monkeypatch, model)
    client = _client()

    initial = client.get("/api/model-governance").get_json()

    row = next(item for item in initial["models"] if item["id"] == model.id)
    assert row["version"] == 1
    assert row["approval_status"] == "draft"
    assert row["risk_limit_state"] == "within_limits"
    assert row["risk_limit_violations"] == []
    assert row["can_approve"] is True
    assert row["risk_limits"]["max_single_position_pct"] == 35.0
    assert row["rebalance_status"] == "not_recorded"
    assert initial["summary"]["breach_count"] == 0

    resp = client.post(
        f"/api/model-governance/{model.id}/events",
        json={
            "actor": "Advisor Console",
            "approval_status": "approved",
            "action": "rebalance_recorded",
            "note": "Initial investment committee approval after reducing concentration.",
        },
    )

    assert resp.status_code == 200
    event = resp.get_json()["event"]
    assert event["version"] == 2
    assert event["approval_status"] == "approved"
    assert event["actor"] == "local-operator"
    assert event["snapshot"]["model"]["id"] == model.id
    assert event["snapshot"]["holdings"][0]["ticker"] == "AAPL"

    updated = client.get("/api/model-governance").get_json()
    governed = next(item for item in updated["models"] if item["id"] == model.id)
    assert governed["version"] == 2
    assert governed["approval_status"] == "approved"
    assert governed["approved_by"] == "local-operator"
    assert governed["latest_change_note"] == "Initial investment committee approval after reducing concentration."
    assert governed["rebalance_status"] == "recorded"
    assert governed["snapshot_count"] == 1
    assert governed["change_note_count"] == 1
    assert updated["snapshots"][0]["version"] == 2
    assert updated["rebalance_history"][0]["model_id"] == model.id
    assert updated["change_log"][0]["actor"] == "local-operator"
    assert updated["summary"]["approved_count"] == 1


def test_model_governance_blocks_approval_when_risk_limits_breach_and_allows_rejection(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nAAPL,70\nMSFT,30\n",
        "breached.csv",
        "Breached Governance Model",
        "balanced",
        "Committee review model with excessive concentration.",
    )
    _make_research_ready(monkeypatch, model)
    client = _client()

    blocked = client.post(
        f"/api/model-governance/{model.id}/events",
        json={
            "actor": "Investment Committee",
            "approval_status": "approved",
            "action": "approval_update",
            "note": "Approve for implementation despite concentration.",
        },
    )

    assert blocked.status_code == 400
    assert "risk-limit" in blocked.get_json()["error"].lower()
    body = client.get("/api/model-governance").get_json()
    row = next(item for item in body["models"] if item["id"] == model.id)
    assert row["approval_status"] == "draft"
    assert row["can_approve"] is False
    assert "risk-limit" in row["approval_blocked_reason"].lower()

    rejected = client.post(
        f"/api/model-governance/{model.id}/events",
        json={
            "actor": "Investment Committee",
            "approval_status": "rejected",
            "action": "approval_update",
            "note": "Rejected until concentration is below the mandate limit.",
        },
    )

    assert rejected.status_code == 200
    event = rejected.get_json()["event"]
    assert event["approval_status"] == "rejected"
    assert event["note"] == "Rejected until concentration is below the mandate limit."
    updated = client.get("/api/model-governance").get_json()
    row = next(item for item in updated["models"] if item["id"] == model.id)
    assert row["approval_status"] == "rejected"
    assert row["approved_by"] == ""
    assert row["latest_change_note"] == "Rejected until concentration is below the mandate limit."


def test_model_editor_archives_before_after_diff_and_resets_approval(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nAAPL,20\nMSFT,20\nGOOGL,20\nAMZN,20\nNVDA,20\n",
        "approved-edit.csv",
        "Approved Edit Model",
        "balanced",
        "Approved model before a committee edit.",
    )
    _make_research_ready(monkeypatch, model, "AVGO")
    client = _client()
    approved = client.post(
        f"/api/model-governance/{model.id}/events",
        json={
            "actor": "Investment Committee",
            "approval_status": "approved",
            "action": "approval_update",
            "note": "Approved original five-name balanced sleeve.",
        },
    )
    assert approved.status_code == 200

    edited = client.post(
        f"/api/models/{model.id}/editor",
        json={
            "actor": "Portfolio Manager",
            "change_note": "Added AVGO and trimmed AAPL after committee review.",
            "holdings": [
                {"ticker": "AAPL", "weight_pct": 15},
                {"ticker": "MSFT", "weight_pct": 20},
                {"ticker": "GOOGL", "weight_pct": 20},
                {"ticker": "AMZN", "weight_pct": 20},
                {"ticker": "NVDA", "weight_pct": 15},
                {"ticker": "AVGO", "weight_pct": 10},
            ],
        },
    )

    assert edited.status_code == 200
    event = edited.get_json()["event"]
    snapshot = event["snapshot"]
    assert event["approval_status"] == "pending_review"
    assert snapshot["before_snapshot"]["holdings"][0]["ticker"] == "AAPL"
    assert snapshot["after_snapshot"]["holdings"][-1]["ticker"] == "AVGO"
    assert snapshot["version_diff"]["added"] == [{"ticker": "AVGO", "weight_pct": 10.0}]
    assert snapshot["version_diff"]["removed"] == []
    assert any(row["ticker"] == "AAPL" and row["from_weight_pct"] == 20.0 and row["to_weight_pct"] == 15.0 for row in snapshot["version_diff"]["changed_weights"])
    assert "Added AVGO" in snapshot["committee_note"]

    governance = client.get("/api/model-governance").get_json()
    row = next(item for item in governance["models"] if item["id"] == model.id)
    assert row["approval_status"] == "pending_review"
    assert row["can_approve"] is True
    assert governance["change_log"][0]["version_diff"]["added"][0]["ticker"] == "AVGO"
    assert governance["change_log"][0]["committee_note"] == "Added AVGO and trimmed AAPL after committee review."


def test_model_governance_approval_packet_exports_committee_evidence(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nAAPL,20\nMSFT,20\nGOOGL,20\nAMZN,20\nNVDA,20\n",
        "packet.csv",
        "Packet Model",
        "balanced",
        "Approval packet model.",
    )
    _make_research_ready(monkeypatch, model)
    client = _client()
    edit = client.post(
        f"/api/models/{model.id}/editor",
        json={
            "actor": "Portfolio Manager",
            "change_note": "Changed weights for approval packet evidence.",
            "holdings": [
                {"ticker": "AAPL", "weight_pct": 18},
                {"ticker": "MSFT", "weight_pct": 22},
                {"ticker": "GOOGL", "weight_pct": 20},
                {"ticker": "AMZN", "weight_pct": 20},
                {"ticker": "NVDA", "weight_pct": 20},
            ],
        },
    )
    assert edit.status_code == 200

    packet_resp = client.get(f"/api/model-governance/{model.id}/approval-packet")

    assert packet_resp.status_code == 200
    packet = packet_resp.get_json()["packet"]
    assert packet["model"]["id"] == model.id
    assert packet["approval"]["status"] == "pending_review"
    assert packet["risk_gate"]["can_approve"] is True
    assert packet["version_diff"]["changed_weights"]
    assert packet["before_snapshot"]["holdings"]
    assert packet["after_snapshot"]["holdings"]
    assert packet["committee_notes"][0]["note"] == "Changed weights for approval packet evidence."
    assert "pdf" in packet["export"]["formats"]
    assert packet["export"]["html_url"].endswith("/approval-packet.html")
    assert packet["export"]["pdf_url"].endswith("/approval-packet.pdf")
    assert "analysis-only" in packet["disclaimer"].lower()

    html = client.get(packet["export"]["html_url"])

    assert html.status_code == 200
    assert "text/html" in html.headers["Content-Type"]
    assert b"Model Approval Packet" in html.data
    assert b"Changed weights for approval packet evidence" in html.data

    pdf = client.get(packet["export"]["pdf_url"])

    assert pdf.status_code == 200
    assert pdf.content_type == "application/pdf"
    assert pdf.data.startswith(b"%PDF-")
    assert b"MODEL APPROVAL PACKET" in pdf.data
    assert b"LOCAL COMMITTEE IDENTITY" in pdf.data


def test_model_governance_requires_verified_committee_identity_when_pin_configured(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    monkeypatch.setenv("HELIOS_GOVERNANCE_APPROVER_PIN", "123456")
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nAAPL,20\nMSFT,20\nGOOGL,20\nAMZN,20\nNVDA,20\n",
        "verified-approval.csv",
        "Verified Approval Model",
        "balanced",
        "Committee approval model with local identity verification.",
    )
    _make_research_ready(monkeypatch, model)
    client = _client()

    missing_identity = client.post(
        f"/api/model-governance/{model.id}/events",
        json={
            "actor": "Advisor Console",
            "approval_status": "approved",
            "action": "approval_update",
            "note": "Approved after local committee review.",
        },
    )

    assert missing_identity.status_code == 400
    assert "committee identity" in missing_identity.get_json()["error"].lower()

    wrong_pin = client.post(
        f"/api/model-governance/{model.id}/events",
        json={
            "actor": "Advisor Console",
            "approval_status": "approved",
            "action": "approval_update",
            "note": "Approved after local committee review.",
            "committee_identity": {
                "signer_name": "Jordan Reviewer",
                "signer_role": "Voting member",
                "committee": "Investment Committee",
                "secret": "000000",
            },
        },
    )

    assert wrong_pin.status_code == 400
    assert "verification" in wrong_pin.get_json()["error"].lower()

    approved = client.post(
        f"/api/model-governance/{model.id}/events",
        json={
            "actor": "Advisor Console",
            "approval_status": "approved",
            "action": "approval_update",
            "note": "Approved after local committee review.",
            "committee_identity": {
                "signer_name": "Jordan Reviewer",
                "signer_role": "Voting member",
                "committee": "Investment Committee",
                "secret": "123456",
            },
        },
    )

    assert approved.status_code == 200
    event = approved.get_json()["event"]
    identity = event["metadata"]["committee_identity"]
    assert identity == event["snapshot"]["committee_identity"]
    assert identity["signer_name"] == "Jordan Reviewer"
    assert identity["signer_role"] == "Voting member"
    assert identity["committee"] == "Investment Committee"
    assert identity["verification_method"] == "local_pin"
    assert identity["verified"] is True
    assert identity["scope"] == "local_workspace"
    assert "secret" not in identity

    packet = client.get(f"/api/model-governance/{model.id}/approval-packet").get_json()["packet"]

    assert packet["committee_identity"]["signer_name"] == "Jordan Reviewer"
    assert packet["committee_identity"]["verified"] is True


def test_model_approval_requires_real_fresh_coverage_and_validation(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nNOEVIDENCE1,50\nNOEVIDENCE2,50\n",
        "not-ready.csv",
        "Not Ready Model",
        "balanced",
        "Must not be approved without research evidence.",
    )

    response = _client().post(
        f"/api/model-governance/{model.id}/events",
        json={
            "actor": "Investment Committee",
            "approval_status": "approved",
            "action": "approval_update",
            "note": "Attempted approval without sufficient evidence.",
        },
    )

    assert response.status_code == 400
    error = response.get_json()["error"].lower()
    assert "eligible live or uploaded" in error
    assert "benchmark-measured" in error


def test_same_name_model_reupload_invalidates_prior_approval(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nAAPL,20\nMSFT,20\nGOOGL,20\nAMZN,20\nNVDA,20\n",
        "reupload.csv",
        "Reupload Governance Model",
        "balanced",
        "Original approved version.",
    )
    _make_research_ready(monkeypatch, model, "AVGO")
    client = _client()
    approval = client.post(
        f"/api/model-governance/{model.id}/events",
        json={
            "actor": "Investment Committee",
            "approval_status": "approved",
            "action": "approval_update",
            "note": "Approved original holdings after evidence review.",
        },
    )
    assert approval.status_code == 200

    upload = client.post(
        "/api/model/upload",
        data={
            "file": (BytesIO(b"Ticker,Weight\nAAPL,15\nMSFT,20\nGOOGL,20\nAMZN,20\nNVDA,15\nAVGO,10\n"), "reupload-v2.csv"),
            "name": "Reupload Governance Model",
            "mandate": "balanced",
        },
        content_type="multipart/form-data",
    )

    assert upload.status_code == 200
    row = next(item for item in client.get("/api/model-governance").get_json()["models"] if item["id"] == model.id)
    assert row["approval_status"] == "pending_review"
    assert row["approved_by"] == ""


def test_initial_model_upload_creates_pending_governance_version(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)

    upload = _client().post(
        "/api/model/upload",
        data={
            "file": (BytesIO(b"Ticker,Weight\nAAPL,60\nMSFT,40\n"), "initial.csv"),
            "name": "Initially Governed",
            "mandate": "balanced",
        },
        content_type="multipart/form-data",
    )

    assert upload.status_code == 200
    events = store.model_governance_events(upload.get_json()["id"], limit=10)
    assert len(events) == 1
    assert events[0]["version"] == 1
    assert events[0]["action"] == "model_uploaded"
    assert events[0]["approval_status"] == "pending_review"


def _institutional_model_upload_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(web_core, "AUTH_ENABLED", True)
    monkeypatch.setenv("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    monkeypatch.setenv("HELIOS_BASIC_ROLES", "model_sponsor")
    monkeypatch.setenv("HELIOS_BASIC_MFA_VERIFIED", "1")
    monkeypatch.setenv("HELIOS_SSO_ENABLED", "0")
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "institutional-model.db"))
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "auto")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY_PATH", str(tmp_path / "institutional-model.key"))
    persistence.reset_store_for_tests()
    store = persistence.get_store()
    assert store.available is True
    assert store.encryption["enabled"] is True
    credentials = base64.b64encode(
        f"{web_core.AUTH_USER}:{web_core.AUTH_PASSWORD}".encode()
    ).decode()
    return store, {"Authorization": f"Basic {credentials}"}


def test_institutional_model_upload_commits_model_and_governance_atomically(monkeypatch, tmp_path):
    store, headers = _institutional_model_upload_setup(monkeypatch, tmp_path)

    response = _client().post(
        "/api/model/upload",
        headers=headers,
        data={
            "file": (BytesIO(b"Ticker,Weight\nAAPL,60\nMSFT,40\n"), "institutional.csv"),
            "name": "Institutional Atomic Model",
            "mandate": "balanced",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    model_id = response.get_json()["id"]
    assert portfolio.get(model_id) is not None
    assert any(row.id == model_id for row in store.load_models())
    event = store.model_governance_events(model_id, limit=1)[0]
    assert event["action"] == "model_uploaded"
    assert event["approval_status"] == "pending_review"


def test_institutional_model_upload_rolls_back_when_final_http_audit_fails(monkeypatch, tmp_path):
    store, headers = _institutional_model_upload_setup(monkeypatch, tmp_path)
    before_ids = set(portfolio._MODELS)
    real_security_event = web_core._security_event

    def fail_final_http_audit(action, outcome, principal=None, details=None):
        if action == "http_post" and outcome == "succeeded":
            return False
        return real_security_event(action, outcome, principal, details)

    monkeypatch.setattr(web_core, "_security_event", fail_final_http_audit)
    response = _client().post(
        "/api/model/upload",
        headers=headers,
        data={
            "file": (BytesIO(b"Ticker,Weight\nNVDA,55\nAVGO,45\n"), "rollback.csv"),
            "name": "Must Roll Back",
            "mandate": "balanced",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert set(portfolio._MODELS) == before_ids
    assert all(row.name != "Must Roll Back" for row in store.load_models())
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM model_governance_events").fetchone()[0] == 0


def test_model_thesis_requires_note_and_records_version_diff(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nAAPL,50\nMSFT,50\n",
        "thesis.csv", "Governed Thesis", "balanced", "Initial context.",
    )
    client = _client()

    missing = client.post("/api/model/thesis", json={
        "id": model.id, "thesis": "Revised thesis without note.",
    })
    assert missing.status_code == 400
    assert "change note" in missing.get_json()["error"].lower()

    saved = client.post("/api/model/thesis", json={
        "id": model.id,
        "thesis": "Focus on durable quality and measured downside control.",
        "thesis_params": {"income_monthly_draw_usd": 5000},
        "change_note": "Documented mandate interpretation for committee review.",
    })

    assert saved.status_code == 200
    event = saved.get_json()["governance_event"]
    assert event["action"] == "thesis_updated"
    assert event["approval_status"] == "pending_review"
    stored = store.model_governance_events(model.id, limit=1)[0]
    diff = stored["snapshot"]["version_diff"]
    assert diff["thesis_changed"] is True
    assert diff["thesis_params_changed"] is True
    assert stored["snapshot"]["after_snapshot"]["model"]["thesis"].startswith("Focus on durable")


def test_model_thesis_rolls_back_persistence_and_memory_when_governance_fails(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nAAPL,50\nMSFT,50\n",
        "thesis-atomic.csv", "Atomic Thesis", "balanced", "Initial context.",
    )
    original_thesis = model.thesis
    monkeypatch.setattr(
        model_governance,
        "record_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("simulated thesis audit failure")),
    )

    response = _client().post("/api/model/thesis", json={
        "id": model.id,
        "thesis": "This must not survive.",
        "change_note": "Attempt a revision that must roll back.",
    })

    assert response.status_code == 503
    assert portfolio.get(model.id).thesis == original_thesis
    persisted = next(item for item in store.load_models() if item.id == model.id)
    assert (persisted.metadata or {}).get("thesis", "") == original_thesis
    assert store.model_governance_events(model.id, limit=10) == []


def test_direct_thesis_helper_cannot_change_durable_model_without_governance(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nAAPL,50\nMSFT,50\n",
        "direct-thesis.csv", "Direct Thesis", "balanced", "Initial context.",
    )

    portfolio.set_thesis(model.id, "Process-local research note.")

    persisted = next(item for item in store.load_models() if item.id == model.id)
    assert persisted.metadata.get("thesis", "") == ""
    assert store.model_governance_events(model.id, limit=10) == []


def test_model_governance_is_unavailable_without_sqlite_persistence(monkeypatch):
    monkeypatch.setenv("HELIOS_DB_PATH", "off")
    persistence.reset_store_for_tests()
    model = portfolio.Model(
        id="NO-STORE",
        name="No Store Model",
        mandate_key="balanced",
        mandate_context="",
        holdings=[portfolio.Holding("AAPL", 1.0)],
    )
    portfolio.register(model)

    resp = _client().get("/api/model-governance")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["models"] == []
    assert body["summary"]["available"] is False
    assert "SQLite persistence" in body["warning"]


def test_model_editor_previews_breaches_and_requires_change_note(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nAAPL,40\nMSFT,30\nGOOGL,30\n",
        "editable.csv",
        "Editable Model",
        "balanced",
        "Initial editable model.",
    )
    client = _client()

    preview = client.post(
        f"/api/models/{model.id}/editor/preview",
        json={
            "holdings": [
                {"ticker": "AAPL", "weight_pct": 55},
                {"ticker": "MSFT", "weight_pct": 25},
                {"ticker": "GOOGL", "weight_pct": 20},
            ]
        },
    )

    assert preview.status_code == 200
    body = preview.get_json()
    assert body["model"]["id"] == model.id
    assert body["risk_limit_state"] == "breach"
    assert body["risk_limit_violations"][0]["field"] == "max_single_position_pct"
    assert body["proposed_holdings"][0]["ticker"] == "AAPL"
    assert body["proposed_holdings"][0]["weight_pct"] == 55.0
    assert body["can_save"] is True

    missing_note = client.post(
        f"/api/models/{model.id}/editor",
        json={
            "holdings": [
                {"ticker": "AAPL", "weight_pct": 34},
                {"ticker": "MSFT", "weight_pct": 33},
                {"ticker": "GOOGL", "weight_pct": 33},
            ],
            "change_note": "",
        },
    )

    assert missing_note.status_code == 400
    assert "change note" in missing_note.get_json()["error"].lower()


@pytest.mark.parametrize("weight", [0, -5, "not-a-number"])
def test_model_editor_rejects_nonpositive_and_unreadable_weights(monkeypatch, tmp_path, weight):
    _use_db(monkeypatch, tmp_path)
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nAAPL,50\nMSFT,50\n",
        "strict-weights.csv",
        "Strict Weights",
        "balanced",
        "Reject malformed allocations.",
    )

    response = _client().post(
        f"/api/models/{model.id}/editor/preview",
        json={"holdings": [
            {"ticker": "AAPL", "weight_pct": weight},
            {"ticker": "MSFT", "weight_pct": 100},
        ]},
    )

    assert response.status_code == 400
    assert "weight" in response.get_json()["error"].lower()


def test_model_editor_saves_normalized_holdings_and_governance_snapshot(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nAAPL,40\nMSFT,30\nGOOGL,30\n",
        "editable-save.csv",
        "Editable Save Model",
        "balanced",
        "Initial editable save model.",
    )
    client = _client()

    resp = client.post(
        f"/api/models/{model.id}/editor",
        json={
            "actor": "Advisor Console",
            "rebalance_to_target": True,
            "change_note": "Reduced concentration and rebalanced target weights.",
            "holdings": [
                {"ticker": "AAPL", "weight_pct": 35},
                {"ticker": "MSFT", "weight_pct": 35},
                {"ticker": "GOOGL", "weight_pct": 20},
                {"ticker": "AMZN", "weight_pct": 5},
                {"ticker": "NVDA", "weight_pct": 5},
            ],
        },
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["saved"] is True
    assert body["event"]["action"] == "model_edit"
    assert body["event"]["note"] == "Reduced concentration and rebalanced target weights."
    assert body["risk_limit_state"] == "within_limits"
    assert round(sum(item["weight_pct"] for item in body["model"]["holdings"]), 6) == 100.0

    updated = portfolio.get(model.id)
    assert updated is not None
    assert [holding.ticker for holding in updated.holdings] == ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]
    assert round(sum(holding.weight for holding in updated.holdings), 6) == 1.0

    governance = client.get("/api/model-governance").get_json()
    governed = next(row for row in governance["models"] if row["id"] == model.id)
    assert governed["latest_change_note"] == "Reduced concentration and rebalanced target weights."
    assert governed["version"] == 2
    assert governance["change_log"][0]["action"] == "model_edit"


def test_model_editor_rolls_back_model_when_governance_evidence_fails(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nAAPL,60\nMSFT,40\n",
        "atomic-editor.csv",
        "Atomic Editor Model",
        "balanced",
        "Original governed holdings.",
    )
    monkeypatch.setattr(
        model_governance.evidence,
        "capture",
        lambda **_kwargs: {"recorded": False, "warning": "simulated evidence failure"},
    )

    with pytest.raises(RuntimeError, match="simulated evidence failure"):
        model_governance.save_edit(
            model,
            holdings=[
                {"ticker": "AAPL", "weight_pct": 25},
                {"ticker": "MSFT", "weight_pct": 25},
                {"ticker": "NVDA", "weight_pct": 50},
            ],
            change_note="This attempted edit must remain atomic.",
        )

    assert [holding.ticker for holding in portfolio.get(model.id).holdings] == ["AAPL", "MSFT"]
    persisted = next(item for item in store.load_models() if item.id == model.id)
    assert [holding["ticker"] for holding in persisted.holdings] == ["AAPL", "MSFT"]
    assert store.model_governance_events(model.id, limit=10) == []


def test_model_replacement_upload_rolls_back_when_governance_fails(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)
    original = portfolio.parse_model_file(
        b"Ticker,Weight\nAAPL,60\nMSFT,40\n",
        "atomic-upload.csv",
        "Atomic Upload Model",
        "balanced",
        "Original uploaded holdings.",
    )
    monkeypatch.setattr(
        model_governance,
        "record_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("simulated governance failure")),
    )

    response = _client().post(
        "/api/model/upload",
        data={
            "name": original.name,
            "mandate": "balanced",
            "context": "Replacement attempt.",
            "file": (BytesIO(b"Ticker,Weight\nNVDA,70\nAVGO,30\n"), "replacement.csv"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert [holding.ticker for holding in portfolio.get(original.id).holdings] == ["AAPL", "MSFT"]
    persisted = next(item for item in store.load_models() if item.id == original.id)
    assert [holding["ticker"] for holding in persisted.holdings] == ["AAPL", "MSFT"]

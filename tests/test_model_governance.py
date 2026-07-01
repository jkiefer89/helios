import app as helios
from engine import data, persistence, portfolio
from tests.conftest import price_csv


def _use_db(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    persistence.reset_store_for_tests()
    store = persistence.get_store()
    assert store.available is True
    return store


def _client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def test_model_governance_tracks_versions_approval_risk_limits_and_history(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    data.parse_csv(price_csv(days=260), "REAL1", "Real One", source_filename="real1.csv")
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nREAL1,70\nREAL2,30\n",
        "concentrated.csv",
        "Concentrated Growth",
        "balanced",
        "Advisor-created model for governance review.",
    )
    client = _client()

    initial = client.get("/api/model-governance").get_json()

    row = next(item for item in initial["models"] if item["id"] == model.id)
    assert row["version"] == 1
    assert row["approval_status"] == "draft"
    assert row["risk_limit_state"] == "breach"
    assert row["risk_limit_violations"][0]["field"] == "max_single_position_pct"
    assert row["risk_limits"]["max_single_position_pct"] == 35.0
    assert row["rebalance_status"] == "not_recorded"
    assert initial["summary"]["breach_count"] == 1

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
    assert event["actor"] == "Advisor Console"
    assert event["snapshot"]["model"]["id"] == model.id
    assert event["snapshot"]["holdings"][0]["ticker"] == "REAL1"

    updated = client.get("/api/model-governance").get_json()
    governed = next(item for item in updated["models"] if item["id"] == model.id)
    assert governed["version"] == 2
    assert governed["approval_status"] == "approved"
    assert governed["approved_by"] == "Advisor Console"
    assert governed["latest_change_note"] == "Initial investment committee approval after reducing concentration."
    assert governed["rebalance_status"] == "recorded"
    assert governed["snapshot_count"] == 1
    assert governed["change_note_count"] == 1
    assert updated["snapshots"][0]["version"] == 2
    assert updated["rebalance_history"][0]["model_id"] == model.id
    assert updated["change_log"][0]["actor"] == "Advisor Console"
    assert updated["summary"]["approved_count"] == 1


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

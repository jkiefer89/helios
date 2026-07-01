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

"""HTTP-level locks for the decisions blueprint (deep-review D12)."""
from __future__ import annotations

import pytest

import app as helios
from engine import data, persistence
from tests.conftest import price_csv


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    yield helios.app.test_client()
    persistence.reset_store_for_tests()


def test_decisions_get_shape_and_limit_validation(client):
    # Out-of-range limits clamp; unparseable ones are a 400 (core contract).
    assert client.get("/api/decisions?limit=abc").status_code == 400
    resp = client.get("/api/decisions")
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body["decisions"], list)
    assert "scoreboard" in body and body["disclaimer"]


def test_decisions_post_validates_then_records(client):
    assert client.post("/api/decisions", data="not json",
                       content_type="application/json").status_code == 400
    bad_action = client.post("/api/decisions", json={
        "target_kind": "instrument", "target_id": "WEBDEC", "my_action": "YOLO"})
    assert bad_action.status_code == 400

    data.parse_csv(price_csv(days=320), "WEBDEC", "Web Dec", source_filename="webdec.csv")
    resp = client.post("/api/decisions", json={
        "target_kind": "instrument", "target_id": "WEBDEC", "my_action": "BUY",
        "rationale": "route contract test",
        "signal": {"action": "HOLD", "score": 0.1},
    })
    assert resp.status_code == 200
    entry = resp.get_json()["decision"]
    assert entry["decision_id"] and entry["outcome_status"] == "pending"
    assert entry["agreement"] in {"agree", "override", "partial", "unknown"}

    listed = client.get("/api/decisions").get_json()["decisions"]
    assert any(e["decision_id"] == entry["decision_id"] for e in listed)

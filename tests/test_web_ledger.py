"""HTTP-level locks for the ledger blueprint (deep-review D12).

The engine paths are covered in test_phase3_ledger.py; these pin the route
contracts — status codes, validation errors, and response shapes — so a
blueprint refactor cannot silently change what the UI sees.
"""
from __future__ import annotations

from io import BytesIO

import pytest

import app as helios
from engine import persistence


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    yield helios.app.test_client()
    persistence.reset_store_for_tests()


def _upload_fills(client, account_id="ACC-W1", rows=None):
    rows = rows if rows is not None else [
        "2026-06-01,JPM,BUY,100,200.00,1.50,",
        "2026-06-15,JPM,DIVIDEND,,,,55.00",
    ]
    raw = ("Trade Date,Symbol,Action,Quantity,Price,Fees,Amount\n" + "\n".join(rows)).encode()
    data = {"file": (BytesIO(raw), "fills.csv")}
    if account_id is not None:
        data["account_id"] = account_id
    return client.post("/api/ledger/fills/upload", data=data, content_type="multipart/form-data")


def test_fills_upload_validates_and_imports(client):
    no_file = client.post("/api/ledger/fills/upload", data={"account_id": "ACC-W1"},
                          content_type="multipart/form-data")
    assert no_file.status_code == 400
    assert "csv" in no_file.get_json()["error"].lower()

    no_account = _upload_fills(client, account_id=None)
    assert no_account.status_code == 400
    assert "account_id" in no_account.get_json()["error"]

    ok = _upload_fills(client)
    assert ok.status_code == 200
    body = ok.get_json()
    assert body["inserted"] == 2          # trade + typed non-trade row
    assert body["non_trade_recorded"] == 1
    assert body["disclaimer"]

    again = _upload_fills(client)
    assert again.get_json()["inserted"] == 0   # idempotent re-import


def test_positions_upload_records_snapshot(client):
    raw = b"Symbol,Quantity,Price,Market Value\nJPM,100,205.00,20500.00\nCASH,,,1000.00\n"
    resp = client.post(
        "/api/ledger/positions/upload",
        data={"file": (BytesIO(raw), "positions.csv"), "account_id": "ACC-W2",
              "as_of": "2026-06-30"},
        content_type="multipart/form-data")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["as_of"] == "2026-06-30"
    assert body["source_file"] == "positions.csv"


def test_accounts_map_and_delete_lifecycle(client):
    assert _upload_fills(client, account_id="ACC-W3").status_code == 200

    accounts = client.get("/api/ledger/accounts").get_json()["accounts"]
    assert any(a["account_id"] == "ACC-W3" for a in accounts)

    bad_body = client.post("/api/ledger/account/map", data="not json",
                           content_type="application/json")
    assert bad_body.status_code == 400
    unknown_model = client.post("/api/ledger/account/map",
                                json={"account_id": "ACC-W3", "model_id": "no-such-model"})
    assert unknown_model.status_code == 404
    mapped = client.post("/api/ledger/account/map",
                         json={"account_id": "ACC-W3", "display_name": "IRA"})
    assert mapped.status_code == 200
    row = next(a for a in mapped.get_json()["accounts"] if a["account_id"] == "ACC-W3")
    assert row["display_name"] == "IRA"

    deleted = client.delete("/api/ledger/account/ACC-W3")
    assert deleted.status_code == 200
    assert deleted.get_json()["deleted"] is True
    assert not any(a["account_id"] == "ACC-W3"
                   for a in client.get("/api/ledger/accounts").get_json()["accounts"])


def test_performance_requires_account_and_reports_honestly(client):
    assert client.get("/api/ledger/performance").status_code == 400

    _upload_fills(client, account_id="ACC-W4")
    store = persistence.get_store()
    store.record_account_snapshot({"account_id": "ACC-W4", "as_of": "2026-05-31",
                                   "cash": 25000.0, "total_value": 25000.0})
    store.record_account_snapshot({"account_id": "ACC-W4", "as_of": "2026-06-30",
                                   "cash": 5053.5, "total_value": 26000.0})
    resp = client.get("/api/ledger/performance?account=ACC-W4")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["actual"]["status"] == "measured"
    assert "Modified-Dietz" in body["actual"]["method"]
    # Cash reconciles exactly: 25000 − (20000 buy + 1.50 fee) + 55 dividend = 5053.50.
    assert body["actual"]["periods"][0]["reconciliation"]["status"] == "ok"
    assert body["actual"]["income_usd"] == 55.0
    assert body["paper"]["status"] == "no_model_mapped"
    assert isinstance(body["shortfall"], list)
    assert body["disclaimer"]


def test_flex_import_route_dormant_without_token(client, monkeypatch):
    monkeypatch.delenv("HELIOS_IBKR_FLEX_TOKEN", raising=False)
    monkeypatch.delenv("HELIOS_IBKR_FLEX_QUERY_ID", raising=False)
    resp = client.post("/api/ledger/flex/import")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "not_configured"
    assert "HELIOS_IBKR_FLEX_TOKEN" in body["note"]


def test_data_jobs_reports_freshness_and_audit(client):
    from engine import data as engine_data
    from tests.conftest import price_csv
    engine_data.parse_csv(price_csv(days=100), "JOBX", "Job X", source_filename="jobx.csv")
    resp = client.get("/api/data/jobs")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["audit_chain"]["status"] in {"intact", "unavailable"}
    assert "auto_live" in body and "refresh_log" in body
    assert isinstance(body["freshness"], list)
    assert "calendar days" in body["basis"]

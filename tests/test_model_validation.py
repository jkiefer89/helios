from io import BytesIO

import pandas as pd
import pytest

import app as helios
from engine import data, model_validation, persistence, portfolio
from tests.conftest import price_series


def _client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def _use_db(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    persistence.reset_store_for_tests()
    store = persistence.get_store()
    assert store.available is True
    return store


def _csv_for(daily: float, days: int = 760) -> bytes:
    series = price_series(days=days, daily=daily)
    return pd.DataFrame({
        "Date": series.index.strftime("%Y-%m-%d"),
        "Close": series.values,
    }).to_csv(index=False).encode("utf-8")


def _upload_price(client, symbol: str, daily: float):
    resp = client.post(
        "/api/upload",
        data={"file": (BytesIO(_csv_for(daily)), f"{symbol}.csv"), "symbol": symbol, "name": symbol},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200


def _upload_model(client, name: str, rows: bytes) -> str:
    resp = client.post(
        "/api/model/upload",
        data={"file": (BytesIO(rows), f"{name}.csv"), "name": name, "mandate": "pure_growth"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    return resp.get_json()["id"]


def test_model_validation_dashboard_ranks_champion_challengers_and_alerts(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    client = _client()
    for symbol, daily in {
        "QQQ": 0.0005,
        "LEADA": 0.0014,
        "LEADB": 0.0012,
        "DRIFTA": 0.0009,
        "DRIFTB": 0.0008,
    }.items():
        _upload_price(client, symbol, daily)
    champion_id = _upload_model(client, "Validation Champion", b"Ticker,Weight\nLEADA,55\nLEADB,45\n")
    challenger_id = _upload_model(client, "Validation Challenger", b"Ticker,Weight\nDRIFTA,55\nDRIFTB,45\n")
    client.post(
        f"/api/model-governance/{champion_id}/events",
        json={"actor": "Validation Desk", "action": "approval_update", "approval_status": "approved", "note": "Approved champion model."},
    )
    client.post(
        f"/api/model-governance/{challenger_id}/events",
        json={"actor": "Validation Desk", "action": "change_note", "note": "Challenger under validation."},
    )

    resp = client.get("/api/model-validation?horizon=21&train_window=126&step=21")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["summary"]["model_count"] == 2
    assert body["summary"]["eligible_count"] == 2
    assert body["summary"]["champion_model_id"] == champion_id
    assert body["champion"]["model_id"] == champion_id
    assert body["champion"]["role"] == "champion"
    assert body["champion"]["validation_score"] >= body["challengers"][0]["validation_score"]
    assert body["challengers"][0]["model_id"] == challenger_id
    assert body["challengers"][0]["role"] == "challenger"
    for row in body["models"]:
        assert row["walk_forward"]["window_count"] > 0
        assert row["false_positives"]["basis"]
        assert row["regime_sensitivity"]
        assert row["decay"]
        assert row["drift_alerts"]
        assert {"approval_status", "risk_limit_state", "version"} <= set(row["governance"])
        assert row["methodology"]["analysis_only"] is True
        assert row["methodology"]["paper_tracking_only"] is True
        assert "raw_price_history" not in row
    assert body["alerts"]
    assert "does not execute trades" in body["disclaimer"].lower()


def test_model_validation_dashboard_blocks_missing_real_history(monkeypatch, tmp_path):
    monkeypatch.setattr(data, "HAS_YF", False)
    _use_db(monkeypatch, tmp_path)
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nNOHIST1,60\nNOHIST2,40\n",
        "validation-blocked.csv",
        "Validation Blocked",
        "balanced",
        "Missing live/uploaded evidence.",
    )
    client = _client()

    resp = client.get("/api/model-validation")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["summary"]["model_count"] == 1
    assert body["summary"]["eligible_count"] == 0
    assert body["champion"] is None
    assert body["models"][0]["model_id"] == model.id
    assert body["models"][0]["validation_state"] == "blocked"
    assert body["models"][0]["evidence_unavailable"] is True
    assert body["models"][0]["drift_alerts"][0]["severity"] == "blocked"
    assert "real price history" in body["models"][0]["required_action"].lower()


def test_false_positive_rate_uses_only_measured_directional_windows():
    rows = [
        {"action_label": "BUY", "paper_hit": False, "false_positive": True, "alpha_pct": -1.0},
        {"action_label": "SELL", "paper_hit": True, "false_positive": False, "alpha_pct": 0.5},
        {"action_label": "BUY", "paper_hit": None, "false_positive": False, "alpha_pct": None},
        {"action_label": "HOLD", "paper_hit": None, "false_positive": False, "alpha_pct": None},
    ]

    stats = model_validation._segment_stats(rows)

    assert stats["directional_signal_count"] == 3
    assert stats["measured_directional_signal_count"] == 2
    assert stats["benchmark_coverage_pct"] == pytest.approx(66.67)
    assert stats["false_positive_rate_pct"] == 50.0

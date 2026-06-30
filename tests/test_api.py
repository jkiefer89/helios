from io import BytesIO

import pytest

import app as helios
from engine import data, portfolio
from tests.conftest import price_series


@pytest.fixture()
def client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def test_tickers_endpoint_returns_sample_universe(client):
    resp = client.get("/api/tickers")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["tickers"]
    assert any(t["symbol"] == "AAPL" for t in body["tickers"])


def test_mandates_endpoint_returns_presets(client):
    resp = client.get("/api/mandates")

    assert resp.status_code == 200
    assert any(m["key"] == "balanced" for m in resp.get_json()["mandates"])


def test_analyze_valid_sample_ticker(client):
    resp = client.get("/api/analyze?ticker=AAPL&horizon=10")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["symbol"] == "AAPL"
    assert body["forecast"]["horizon_days"] == 10


def test_analyze_invalid_ticker_returns_json_error(client):
    resp = client.get("/api/analyze?ticker=NOT_A_SAMPLE&horizon=10")

    assert resp.status_code == 404
    assert "error" in resp.get_json()


def test_analyze_invalid_horizon_returns_400_json_not_500(client):
    resp = client.get("/api/analyze?ticker=AAPL&horizon=banana")

    assert resp.status_code == 400
    assert resp.is_json
    assert "horizon" in resp.get_json()["error"].lower()


def test_not_found_routes_return_json(client):
    resp = client.get("/api/nope")

    assert resp.status_code == 404
    assert resp.is_json
    assert "error" in resp.get_json()


def test_favicon_request_returns_no_content_for_browser_noise(client):
    resp = client.get("/favicon.ico")

    assert resp.status_code == 204
    assert resp.data == b""


def test_live_malformed_json_returns_json_error(client, monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", True)
    resp = client.post("/api/live", data="{bad", content_type="application/json")

    assert resp.status_code == 400
    assert resp.is_json
    assert "error" in resp.get_json()


def test_auto_live_config_is_disabled_without_symbols(monkeypatch):
    monkeypatch.delenv("HELIOS_AUTO_LIVE_SYMBOLS", raising=False)

    config = helios._auto_live_config()

    assert config["enabled"] is False
    assert config["symbols"] == []


def test_auto_live_config_parses_core_universe_and_interval(monkeypatch):
    monkeypatch.setenv("HELIOS_AUTO_LIVE_SYMBOLS", "core")
    monkeypatch.setenv("HELIOS_AUTO_LIVE_REFRESH_SECONDS", "30")
    monkeypatch.setenv("HELIOS_AUTO_LIVE_MAX_WORKERS", "99")
    monkeypatch.setenv("HELIOS_AUTO_LIVE_PERIOD", "1y")

    config = helios._auto_live_config()

    assert config["enabled"] is True
    assert config["period"] == "1y"
    assert config["interval_seconds"] == 60
    assert config["max_workers"] == 16
    assert {"SPY", "QQQ", "AAPL"} <= set(config["symbols"])


def test_data_status_includes_auto_live_configuration(client, monkeypatch):
    monkeypatch.setenv("HELIOS_AUTO_LIVE_SYMBOLS", "SPY, QQQ")
    monkeypatch.setenv("HELIOS_AUTO_LIVE_REFRESH_SECONDS", "120")

    resp = client.get("/api/data/status")

    assert resp.status_code == 200
    auto_live = resp.get_json()["auto_live"]
    assert auto_live["enabled"] is True
    assert auto_live["symbols"] == ["SPY", "QQQ"]
    assert auto_live["interval_seconds"] == 120
    assert auto_live["max_workers"] == 6


def test_model_upload_and_analyze_smoke(client, monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", False)
    payload = {
        "file": (BytesIO(b"Ticker,Weight\nAAPL,60\nMSFT,40\n"), "model.csv"),
        "name": "Smoke Model",
        "mandate": "balanced",
        "context": "offline test",
    }

    upload = client.post("/api/model/upload", data=payload, content_type="multipart/form-data")
    assert upload.status_code == 200
    model_id = upload.get_json()["id"]

    analyze = client.get(f"/api/model/analyze?id={model_id}&horizon=21")
    assert analyze.status_code == 200
    body = analyze.get_json()
    assert body["id"] == model_id
    assert body["holdings"]
    assert body["forecast"]["horizon_days"] == 21


def test_model_analyze_invalid_horizon_returns_400_json_not_fallback(client, monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", False)
    payload = {
        "file": (BytesIO(b"Ticker,Weight\nAAPL,60\nMSFT,40\n"), "model.csv"),
        "name": "Bad Horizon Model",
        "mandate": "balanced",
    }
    upload = client.post("/api/model/upload", data=payload, content_type="multipart/form-data")
    assert upload.status_code == 200

    resp = client.get(f"/api/model/analyze?id={upload.get_json()['id']}&horizon=banana")

    assert resp.status_code == 400
    assert resp.is_json
    assert "horizon" in resp.get_json()["error"].lower()


def test_model_analyze_unavailable_long_horizon_clears_stale_label(client, monkeypatch):
    mdl = portfolio.Model(
        id="SHORT-HISTORY",
        name="Short History",
        mandate_key="balanced",
        mandate_context="",
        holdings=[portfolio.Holding("AAPL", 1.0)],
    )
    ps = portfolio.PortfolioSeries(
        close=price_series(130),
        holdings=[{
            "ticker": "AAPL",
            "weight": 1.0,
            "source": "sample",
            "window_return_pct": 0.0,
            "mrc_pct": 100.0,
            "excluded": False,
            "note": "",
        }],
        n_days=130,
        sources={"sample": 1},
        warnings=[],
        provenance={
            "n_holdings": 1,
            "n_kept": 1,
            "n_excluded": 0,
            "excluded": [],
            "n_live": 0,
            "n_sample": 1,
            "n_simulated": 0,
            "simulated_weight_pct": 0.0,
            "simulated_symbols": [],
            "honesty": "real",
        },
    )
    monkeypatch.setattr(portfolio, "get", lambda _model_id: mdl)
    monkeypatch.setattr(portfolio, "build_series", lambda _model: ps)

    resp = client.get("/api/model/analyze?id=SHORT-HISTORY&horizon=5Y")

    assert resp.status_code == 200
    horizon = resp.get_json()["horizon"]
    assert horizon["kind"] == "short"
    assert horizon["value"] == 21
    assert horizon["label"] is None

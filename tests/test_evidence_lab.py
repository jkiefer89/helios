from io import BytesIO

import pandas as pd

import app as helios
from engine import data, portfolio
from tests.conftest import price_series


def _client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def _register_upload(symbol: str, series: pd.Series):
    data.register(data.Instrument(symbol, symbol, pd.DataFrame({"close": series}, index=series.index), "upload", []))


def _csv_for(symbol: str, daily: float, days: int = 430) -> bytes:
    series = price_series(days=days, daily=daily)
    return pd.DataFrame({
        "Date": series.index.strftime("%Y-%m-%d"),
        "Close": series.values,
    }).to_csv(index=False).encode("utf-8")


def test_walk_forward_evidence_engine_reports_oos_metrics_for_real_model():
    from engine.evidence_lab import analyze_model

    benchmark = price_series(days=430, start=100, daily=0.0007)
    growth = price_series(days=430, start=100, daily=0.0012)
    quality = price_series(days=430, start=80, daily=0.0009)
    hedge = price_series(days=430, start=60, daily=0.0003)
    _register_upload("QQQ", benchmark)
    _register_upload("AAPL", growth)
    _register_upload("MSFT", quality)
    _register_upload("GLD", hedge)
    model = portfolio.Model(
        id="WF-MODEL",
        name="Walk Forward Model",
        mandate_key="pure_growth",
        mandate_context="walk-forward test",
        holdings=[
            portfolio.Holding("AAPL", 0.45),
            portfolio.Holding("MSFT", 0.35),
            portfolio.Holding("GLD", 0.20),
        ],
    )

    result = analyze_model(model, horizon_days=21, train_window=126, step=21)

    assert result["eligible_for_real_research"] is True
    assert result["target"]["kind"] == "model"
    assert result["benchmark"]["symbol"] == "QQQ"
    assert result["summary"]["window_count"] >= 8
    assert result["summary"]["measured_count"] == result["summary"]["window_count"]
    assert result["summary"]["hit_rate_pct"] is not None
    assert "avg_alpha_pct" in result["summary"]
    assert result["false_positives"]["count"] >= 0
    assert result["false_positives"]["rate_pct"] is not None
    assert result["confidence_bands"]["alpha_pct"]["p05"] <= result["confidence_bands"]["alpha_pct"]["p95"]
    assert {5, 21, 63} <= {row["horizon_days"] for row in result["decay"]}
    assert result["regime_sensitivity"]
    assert result["windows"]
    assert {"signal_date", "signal_score", "action_label", "forward_result_pct", "alpha_pct", "regime", "paper_hit"} <= set(result["windows"][0])
    assert result["methodology"]["no_lookahead"] is True
    assert result["methodology"]["analysis_only"] is True


def test_evidence_lab_endpoint_returns_real_model_walk_forward_payload():
    client = _client()
    for symbol, daily in {"QQQ": 0.0007, "AAPL": 0.0012, "MSFT": 0.0009, "GLD": 0.0003}.items():
        upload_price = client.post(
            "/api/upload",
            data={"file": (BytesIO(_csv_for(symbol, daily)), f"{symbol}.csv"), "symbol": symbol},
            content_type="multipart/form-data",
        )
        assert upload_price.status_code == 200
    upload = client.post(
        "/api/model/upload",
        data={
            "file": (BytesIO(b"Ticker,Weight\nAAPL,45\nMSFT,35\nGLD,20\n"), "wf-model.csv"),
            "name": "Walk Forward Endpoint Model",
            "mandate": "pure_growth",
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    model_id = upload.get_json()["id"]

    resp = client.get(f"/api/evidence-lab?kind=model&id={model_id}&horizon=21&train_window=126&step=21")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["target"]["id"] == model_id
    assert body["eligible_for_real_research"] is True
    assert body["summary"]["measured_count"] > 0
    assert body["decay"]
    assert body["regime_sensitivity"]
    assert body["confidence_bands"]["forward_result_pct"]


def test_evidence_lab_endpoint_blocks_missing_real_model_data(monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", False)
    client = _client()
    upload = client.post(
        "/api/model/upload",
        data={
            "file": (BytesIO(b"Ticker,Weight\nNOHIST1,60\nNOHIST2,40\n"), "blocked-wf.csv"),
            "name": "Blocked Evidence Model",
            "mandate": "balanced",
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    model_id = upload.get_json()["id"]

    resp = client.get(f"/api/evidence-lab?kind=model&id={model_id}")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["eligible_for_real_research"] is False
    assert body["evidence_unavailable"] is True
    assert "real price history" in body["required_action"].lower()
    assert body["methodology"]["analysis_only"] is True


def test_evidence_lab_endpoint_supports_real_instrument_signals():
    client = _client()
    for symbol, daily in {"SPY": 0.0007, "EVIDX": 0.0011}.items():
        upload_price = client.post(
            "/api/upload",
            data={"file": (BytesIO(_csv_for(symbol, daily)), f"{symbol}.csv"), "symbol": symbol},
            content_type="multipart/form-data",
        )
        assert upload_price.status_code == 200

    resp = client.get("/api/evidence-lab?kind=instrument&id=EVIDX&horizon=21&train_window=126&step=21")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["target"]["kind"] == "instrument"
    assert body["target"]["id"] == "EVIDX"
    assert body["benchmark"]["symbol"] == "SPY"
    assert body["summary"]["window_count"] > 0
    assert body["false_positives"]["rate_pct"] is not None

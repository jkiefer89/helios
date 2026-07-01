from io import BytesIO

import pandas as pd

import app as helios
from engine import data, portfolio
from tests.conftest import price_series


def _client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def _register_upload(symbol: str, series):
    data.register(data.Instrument(symbol, symbol, pd.DataFrame({"close": series}, index=series.index), "upload", []))


def test_risk_exposure_engine_reports_portfolio_analytics_for_real_model():
    from engine.risk_exposure import analyze_model_risk

    dates = pd.bdate_range("2024-01-02", periods=320)
    base = pd.Series(100 * (1.001 ** pd.Series(range(320)).to_numpy()), index=dates, name="close")
    tech = price_series(days=320, start=100, daily=0.0015)
    defense = price_series(days=320, start=90, daily=0.0008)
    gold = price_series(days=320, start=80, daily=0.0003)
    _register_upload("SPY", base)
    _register_upload("AAPL", tech)
    _register_upload("LMT", defense)
    _register_upload("GLD", gold)
    model = portfolio.Model(
        id="RISK-MODEL",
        name="Risk Model",
        mandate_key="balanced",
        mandate_context="risk test",
        holdings=[
            portfolio.Holding("AAPL", 0.50),
            portfolio.Holding("LMT", 0.30),
            portfolio.Holding("GLD", 0.20),
        ],
    )

    result = analyze_model_risk(model)

    assert result["eligible_for_real_research"] is True
    assert result["benchmark"]["symbol"] == "SPY"
    assert result["factor_exposure"]["growth"] > result["factor_exposure"]["defensive"]
    assert result["sector_exposure"][0]["weight_pct"] >= 20
    assert result["theme_exposure"]
    assert result["single_name_concentration"]["top_holding"]["ticker"] == "AAPL"
    assert result["single_name_concentration"]["hhi"] > 0
    assert result["volatility_budget"]["status"] in {"within_budget", "over_budget"}
    assert result["volatility_contribution"][0]["ticker"] in {"AAPL", "LMT", "GLD"}
    assert result["correlation_clusters"]
    assert result["drawdown_stress"]["max_drawdown_pct"] <= 0
    assert len(result["scenario_shocks"]) >= 4
    assert any(item["scenario"] == "Equity shock -10%" for item in result["scenario_shocks"])
    assert result["liquidity_flags"]["summary"]["flagged_count"] >= 0
    assert "beta" in result["benchmark_relative"]
    assert "active_vol_pct" in result["benchmark_relative"]
    assert result["methodology"]["analysis_only"] is True


def test_risk_exposure_endpoint_blocks_missing_real_model_data(monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", False)
    client = _client()
    upload = client.post(
        "/api/model/upload",
        data={
            "file": (BytesIO(b"Ticker,Weight\nNOHIST1,60\nNOHIST2,40\n"), "risk.csv"),
            "name": "Blocked Risk Model",
            "mandate": "balanced",
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    model_id = upload.get_json()["id"]

    resp = client.get(f"/api/model/risk?id={model_id}")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["eligible_for_real_research"] is False
    assert body["risk_exposure_unavailable"] is True
    assert "real price history" in body["required_action"].lower()
    assert body["methodology"]["analysis_only"] is True


def test_risk_exposure_endpoint_returns_real_payload_for_uploaded_model():
    client = _client()
    for symbol in ("SPY", "AAPL", "LMT", "GLD"):
        csv = pd.DataFrame({
            "Date": price_series(days=320).index.strftime("%Y-%m-%d"),
            "Close": price_series(days=320, daily=0.001 if symbol == "SPY" else 0.0008).values,
        }).to_csv(index=False).encode("utf-8")
        upload_price = client.post(
            "/api/upload",
            data={"file": (BytesIO(csv), f"{symbol}.csv"), "symbol": symbol},
            content_type="multipart/form-data",
        )
        assert upload_price.status_code == 200
    upload = client.post(
        "/api/model/upload",
        data={
            "file": (BytesIO(b"Ticker,Weight\nAAPL,50\nLMT,30\nGLD,20\n"), "risk-model.csv"),
            "name": "Risk Endpoint Model",
            "mandate": "balanced",
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    model_id = upload.get_json()["id"]

    resp = client.get(f"/api/model/risk?id={model_id}")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["id"] == model_id
    assert body["eligible_for_real_research"] is True
    assert body["sector_exposure"]
    assert body["scenario_shocks"]
    assert body["benchmark_relative"]["benchmark_symbol"] == "SPY"

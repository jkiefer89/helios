from __future__ import annotations

from io import BytesIO

import pandas as pd

import app as helios
from engine import data, portfolio
from tests.conftest import price_series


def _client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def _register_upload(symbol: str, series, volume: int | None = None):
    frame = pd.DataFrame({"close": series}, index=series.index)
    if volume is not None:
        frame["volume"] = volume
    data.register(data.Instrument(symbol, symbol, frame, "upload", []))


def test_risk_exposure_engine_reports_portfolio_analytics_for_real_model():
    from engine.risk_exposure import analyze_model_risk

    base = price_series(days=320, start=100, daily=0.001)  # same window as the holdings
    tech = price_series(days=320, start=100, daily=0.0015)
    defense = price_series(days=320, start=90, daily=0.0008)
    gold = price_series(days=320, start=80, daily=0.0003)
    _register_upload("SPY", base, volume=80_000_000)
    _register_upload("AAPL", tech, volume=40_000_000)
    _register_upload("LMT", defense, volume=1_400_000)
    _register_upload("GLD", gold, volume=7_000_000)
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
    pack = result["client_risk_pack"]
    assert pack["available"] is True
    assert pack["summary"]["model_id"] == "RISK-MODEL"
    assert pack["summary"]["benchmark_symbol"] == "SPY"
    assert pack["stress_scenarios"]
    assert pack["historical_stress_replay"]
    assert pack["historical_stress_replay"][0]["basis"] == "observed_model_history"
    assert pack["historical_stress_replay"][0]["window_days"] in {21, 63}
    assert any(row["scenario"] == "Growth multiple compression" for row in pack["stress_scenarios"])
    assert pack["benchmark_relative_drawdown"]["benchmark_symbol"] == "SPY"
    assert "relative_drawdown_pct" in pack["benchmark_relative_drawdown"]
    assert pack["concentration_warnings"]
    assert pack["liquidity_flags"]["summary"]["basis"]
    assert pack["liquidity_flags"]["summary"]["observed_count"] == 3
    assert pack["liquidity_flags"]["items"][0]["adv_source"] == "observed_60d_dollar_volume"
    assert pack["liquidity_flags"]["items"][0]["observed_adv_usd"] > 0
    assert pack["correlation_clusters"]
    assert pack["what_would_break_this_model"]
    assert all(row["evidence"] for row in pack["what_would_break_this_model"])
    assert all(row["trigger"] for row in pack["what_would_break_this_model"])
    assert any("growth" in row["driver"].lower() for row in pack["what_would_break_this_model"])
    assert pack["methodology"]["analysis_only"] is True
    assert pack["methodology"]["uses_observed_volume_when_available"] is True
    assert pack["methodology"]["includes_historical_stress_replay"] is True
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
    assert body["client_risk_pack"]["available"] is False
    assert "real price history" in body["client_risk_pack"]["required_action"].lower()
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
    assert body["client_risk_pack"]["available"] is True
    assert body["client_risk_pack"]["what_would_break_this_model"]
    assert body["benchmark_relative"]["benchmark_symbol"] == "SPY"

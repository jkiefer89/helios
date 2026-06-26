from io import BytesIO

import numpy as np
import pandas as pd

import app as helios
from engine import data, portfolio
from tests.conftest import price_series


def _client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def _model(model_id="CLINIC", mandate_key="balanced", holdings=None):
    holdings = holdings or [
        portfolio.Holding("AAPL", 0.70),
        portfolio.Holding("MSFT", 0.15),
        portfolio.Holding("SPY", 0.15),
    ]
    return portfolio.Model(
        id=model_id,
        name=model_id.title(),
        mandate_key=mandate_key,
        mandate_context="test model",
        holdings=holdings,
    )


def _register_upload(symbol: str, series=None):
    close = series if series is not None else price_series(days=320, daily=0.001)
    df = pd.DataFrame({"close": close}, index=close.index)
    data.register(data.Instrument(symbol, symbol, df, "upload", []))


def _volatile_series(days=320):
    idx = pd.bdate_range("2024-01-02", periods=days)
    daily = np.array([0.035, -0.03] * (days // 2 + 1))[:days]
    values = 100 * np.cumprod(1 + daily)
    return pd.Series(values, index=idx, name="close")


def test_clinic_suggests_trim_for_over_concentrated_position():
    from engine.portfolio_clinic import analyze_clinic

    for symbol in ("AAPL", "MSFT", "SPY"):
        _register_upload(symbol)
    result = analyze_clinic(_model())

    assert any(s["type"] == "trim" and s["ticker"] == "AAPL" for s in result["suggestions"])
    after = result["after"]["weights"]
    assert after["AAPL"] <= result["constraints"]["single_name_cap"] + 1e-9


def test_clinic_de_risks_cd_alternative_when_volatility_exceeds_budget():
    from engine.portfolio_clinic import analyze_clinic

    for symbol in ("BTC-USD", "NVDA", "TSLA"):
        _register_upload(symbol, _volatile_series())
    mdl = _model(
        model_id="CD-RISK",
        mandate_key="cd_alternative",
        holdings=[
            portfolio.Holding("BTC-USD", 0.34),
            portfolio.Holding("NVDA", 0.33),
            portfolio.Holding("TSLA", 0.33),
        ],
    )

    result = analyze_clinic(mdl)

    assert result["diagnostics"]["volatility_gap_pct"] > 0
    assert any(s["type"] == "de_risk" for s in result["suggestions"])


def test_clinic_simulated_heavy_model_warns_and_refuses_precision(monkeypatch):
    from engine.portfolio_clinic import analyze_clinic

    monkeypatch.setattr("engine.data.HAS_YF", False)
    mdl = _model(
        model_id="SIM-HEAVY",
        mandate_key="balanced",
        holdings=[portfolio.Holding("FAKEAAA", 0.50), portfolio.Holding("FAKEBBB", 0.50)],
    )

    result = analyze_clinic(mdl)

    assert result["eligible_for_real_research"] is False
    assert result["data_mode"] == "invalid_for_research"
    assert result["missing_tickers"] == ["FAKEAAA", "FAKEBBB"]
    assert any("real price history" in warning.lower() for warning in result["warnings"])
    assert result["refusals"]


def test_clinic_after_weights_are_long_only_and_respect_caps():
    from engine.portfolio_clinic import analyze_clinic

    for symbol in ("AAPL", "MSFT", "SPY"):
        _register_upload(symbol)
    result = analyze_clinic(_model())

    cap = result["constraints"]["single_name_cap"]
    assert all(weight >= 0 for weight in result["after"]["weights"].values())
    assert all(weight <= cap + 1e-9 for weight in result["after"]["weights"].values())
    assert abs(sum(result["after"]["weights"].values()) - 1.0) < 1e-6


def test_clinic_does_not_suggest_impossible_trim_when_cap_is_infeasible():
    from engine.portfolio_clinic import analyze_clinic

    for symbol in ("TWOA", "TWOB"):
        _register_upload(symbol)
    mdl = _model(
        model_id="TWO-HOLDING",
        holdings=[portfolio.Holding("TWOA", 0.60), portfolio.Holding("TWOB", 0.40)],
    )

    result = analyze_clinic(mdl)

    assert any("cap is infeasible" in warning.lower() for warning in result["warnings"])
    assert not any(s["type"] == "trim" for s in result["suggestions"])


def test_model_clinic_endpoint_returns_analysis_only_payload(monkeypatch):
    monkeypatch.setattr("engine.data.HAS_YF", False)
    client = _client()
    for symbol in ("AAPL", "MSFT", "SPY"):
        _register_upload(symbol)
    payload = {
        "file": (BytesIO(b"Ticker,Weight\nAAPL,70\nMSFT,15\nSPY,15\n"), "clinic.csv"),
        "name": "Clinic Model",
        "mandate": "balanced",
    }
    upload = client.post("/api/model/upload", data=payload, content_type="multipart/form-data")
    assert upload.status_code == 200
    model_id = upload.get_json()["id"]

    resp = client.get(f"/api/model/clinic?id={model_id}")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["id"] == model_id
    assert body["suggestions"]
    assert "analysis only" in body["disclaimer"].lower()

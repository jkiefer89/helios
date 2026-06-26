from io import BytesIO

import app as helios
from engine import data
from tests.conftest import price_csv, price_series


def _client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def test_regime_classifies_clear_risk_on_and_risk_off_series():
    from engine.regime import classify_regime

    risk_on = classify_regime(price_series(days=320, daily=0.0012), symbol="UP", source="sample")
    risk_off = classify_regime(price_series(days=320, daily=-0.0015), symbol="DOWN", source="sample")

    assert risk_on["label"] == "risk-on"
    assert risk_on["score"] > risk_off["score"]
    assert any("trend" in driver["name"].lower() for driver in risk_on["drivers"])
    assert risk_off["label"] == "risk-off"
    assert risk_off["warnings"] == []


def test_regime_warns_when_spy_proxy_is_not_available():
    from engine.regime import market_regime

    data._STORE.pop("SPY", None)

    result = market_regime(data.all_instruments())

    assert result["symbol"] != "SPY"
    assert any("SPY" in warning for warning in result["warnings"])


def test_command_center_response_shape_is_analysis_only():
    client = _client()
    resp = client.get("/api/command-center")

    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) >= {
        "regime",
        "top_opportunities",
        "top_risks",
        "model_alerts",
        "research_queue",
        "generated_at",
        "disclaimer",
    }
    assert body["regime"]["label"] in {"risk-on", "neutral", "risk-off"}
    assert isinstance(body["regime"]["drivers"], list)
    assert body["data_mode"] == "demo"
    assert body["eligible_for_real_research"] is False
    assert body["top_opportunities"] == []
    assert body["top_risks"] == []
    assert body["research_queue"]
    assert "upload real" in body["required_action"].lower()
    assert "analysis only" in body["disclaimer"].lower()
    assert "execution" in body["disclaimer"].lower()


def test_command_center_uses_uploaded_real_data_for_research_rankings():
    client = _client()
    upload = client.post(
        "/api/upload",
        data={
            "file": (BytesIO(price_csv(days=260)), "realx.csv"),
            "symbol": "REALX",
            "name": "Real Upload X",
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200

    resp = client.get("/api/command-center")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data_mode"] in {"real", "mixed"}
    assert body["eligible_for_real_research"] is True
    assert [item["symbol"] for item in body["top_opportunities"]] == ["REALX"]
    assert body["data_provenance"]["source_counts"]["upload"] == 1

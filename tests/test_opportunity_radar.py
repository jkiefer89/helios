from io import BytesIO

import app as helios
from tests.conftest import price_csv


def _client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def _candidate(**overrides):
    base = {
        "id": "instrument:TEST",
        "kind": "instrument",
        "name": "Test Asset",
        "symbol": "TEST",
        "source": "live",
        "action": "BUY",
        "signal_score": 0.55,
        "expected_return_pct": 6.0,
        "expected_vol_pct": 18.0,
        "max_drawdown_pct": -9.0,
        "forecast_quality": {"directional_accuracy": 0.62, "n_test": 80},
        "backtest": {"strategy_return_pct": 18.0, "benchmark_return_pct": 12.0, "sharpe": 1.2},
        "warnings": [],
    }
    base.update(overrides)
    return base


def test_simulated_data_penalty_lowers_opportunity_score():
    from engine.opportunity import score_candidate

    live = score_candidate(_candidate(source="live"))
    simulated = score_candidate(_candidate(source="sample"))

    assert simulated["data_quality"] < live["data_quality"]
    assert simulated["opportunity_score"] < live["opportunity_score"]
    assert any("sample" in warning.lower() for warning in simulated["warnings"])


def test_weak_forecast_quality_penalizes_evidence_and_score():
    from engine.opportunity import score_candidate

    strong = score_candidate(_candidate(forecast_quality={"directional_accuracy": 0.66, "n_test": 80}))
    weak = score_candidate(_candidate(forecast_quality={"directional_accuracy": 0.45, "n_test": 80}))

    assert weak["forecast_quality"] < strong["forecast_quality"]
    assert weak["evidence_score"] < strong["evidence_score"]
    assert weak["opportunity_score"] < strong["opportunity_score"]
    assert any("weak forecast" in warning.lower() for warning in weak["warnings"])


def test_high_drawdown_and_volatility_penalize_score_and_raise_risk():
    from engine.opportunity import score_candidate

    calm = score_candidate(_candidate(expected_vol_pct=16.0, max_drawdown_pct=-8.0))
    risky = score_candidate(_candidate(expected_vol_pct=55.0, max_drawdown_pct=-38.0))

    assert risky["risk_score"] > calm["risk_score"]
    assert risky["opportunity_score"] < calm["opportunity_score"]
    assert any("risk" in warning.lower() for warning in risky["warnings"])


def test_strong_evidence_ranks_above_weak_evidence():
    from engine.opportunity import rank_candidates

    strong = _candidate(symbol="STRONG", signal_score=0.65, expected_return_pct=8.0)
    weak = _candidate(
        symbol="WEAK",
        source="sample",
        signal_score=0.12,
        expected_return_pct=1.0,
        forecast_quality={"directional_accuracy": 0.48, "n_test": 40},
        backtest={"strategy_return_pct": -2.0, "benchmark_return_pct": 8.0, "sharpe": -0.1},
    )

    ranked = rank_candidates([weak, strong], regime={"label": "neutral"})

    assert [item["symbol"] for item in ranked] == ["STRONG", "WEAK"]
    assert ranked[0]["recommended_next_step"].startswith("Verify")


def test_opportunities_endpoint_blocks_demo_only_rankings():
    resp = _client().get("/api/opportunities?limit=4&min_score=0")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["items"] == []
    assert body["eligible_for_real_research"] is False
    assert body["data_mode"] == "demo"
    assert "upload real" in body["required_action"].lower()
    assert body["disclaimer"]
    assert "analysis only" in body["disclaimer"].lower()


def test_opportunities_endpoint_ranks_uploaded_real_data():
    client = _client()
    upload = client.post(
        "/api/upload",
        data={
            "file": (BytesIO(price_csv(days=260)), "realradar.csv"),
            "symbol": "REALRADAR",
            "name": "Real Radar",
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200

    resp = client.get("/api/opportunities?limit=4&min_score=0")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["eligible_for_real_research"] is True
    assert body["items"]
    item = body["items"][0]
    assert item["symbol"] == "REALRADAR"
    assert set(item) >= {
        "id",
        "kind",
        "name",
        "symbol",
        "action",
        "opportunity_score",
        "risk_score",
        "evidence_score",
        "forecast_quality",
        "backtest_quality",
        "data_quality",
        "top_positive_drivers",
        "top_negative_drivers",
        "plain_english_summary",
        "recommended_next_step",
        "warnings",
    }
    assert item["eligible_for_real_research"] is True

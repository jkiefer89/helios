from io import BytesIO

import numpy as np
import pandas as pd
import pytest

import app as helios
from tests.conftest import price_csv, price_series


def _client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def test_strategy_uses_next_day_position_and_does_not_capture_signal_day_jump():
    from engine.strategy import analyze_strategy

    idx = pd.bdate_range("2024-01-02", periods=140)
    values = np.r_[np.full(80, 100.0), [130.0], np.full(59, 130.0)]
    close = pd.Series(values, index=idx, name="close")

    result = analyze_strategy(close, cost_bps=0, slippage_bps=0)
    jump_i = result["dates"].index(idx[80].strftime("%Y-%m-%d"))

    assert result["position"][jump_i] == 0
    assert result["strategy_curve"][jump_i] == 1.0


def test_strategy_costs_and_slippage_reduce_total_return():
    from engine.strategy import analyze_strategy

    close = price_series(days=320, daily=0.001)
    free = analyze_strategy(close, cost_bps=0, slippage_bps=0)
    costly = analyze_strategy(close, cost_bps=25, slippage_bps=10)

    assert costly["strategy"]["total_return_pct"] < free["strategy"]["total_return_pct"]
    assert costly["assumptions"]["per_side_cost_bps"] == 35
    assert costly["assumptions"]["round_trip_cost_bps"] == 70


def test_strategy_charges_terminal_exit_and_closes_last_episode(monkeypatch):
    from engine.strategy import analyze_strategy

    close = pd.Series(
        np.full(80, 100.0),
        index=pd.bdate_range("2024-01-02", periods=80),
        name="close",
    )
    monkeypatch.setattr(
        "engine.strategy.signals.historical_signals",
        lambda px: pd.Series(np.ones(len(px)), index=px.index),
    )

    result = analyze_strategy(close, cost_bps=100, slippage_bps=0)

    assert result["trade_stats"]["turnover"] == 2.0
    assert result["trade_stats"]["completed_trades"] == 1
    assert result["trade_stats"]["open_position_episode_return_pct"] is None
    assert result["trade_stats"]["current_position"] == "long"
    assert result["trade_stats"]["evaluation_end_position"] == "cash"
    assert result["trade_stats"]["terminal_liquidation_applied"] is True
    assert result["strategy"]["total_return_pct"] == pytest.approx(-1.99)


def test_strategy_drawdown_curve_uses_negative_percentage_convention():
    from engine.strategy import analyze_strategy

    idx = pd.bdate_range("2024-01-02", periods=180)
    values = np.r_[np.linspace(100, 150, 90), np.linspace(150, 90, 90)]
    close = pd.Series(values, index=idx, name="close")

    result = analyze_strategy(close)

    assert min(result["drawdown_curve"]) <= 0
    assert max(result["drawdown_curve"]) == 0
    assert result["strategy"]["max_drawdown_pct"] <= 0


def test_rolling_sharpe_is_null_for_near_zero_variance_windows():
    from engine.strategy import _rolling_sharpe

    idx = pd.bdate_range("2024-01-02", periods=160)
    noise = np.where(np.arange(160) % 2 == 0, 1e-9, -1e-9)
    flat = pd.Series(noise, index=idx)
    real = pd.Series(np.where(np.arange(160) % 2 == 0, 0.01, -0.009), index=idx)

    assert _rolling_sharpe(flat).iloc[63:].isna().all()
    assert np.isfinite(_rolling_sharpe(real).iloc[63:]).all()


def test_strategy_rolling_sharpe_has_no_degenerate_outliers_on_uploaded_data(monkeypatch):
    monkeypatch.setattr("engine.data.HAS_YF", False)
    client = _client()
    upload = client.post(
        "/api/upload",
        data={
            "file": (BytesIO(price_csv(days=260)), "ROLL.csv"),
            "symbol": "ROLL",
            "name": "Rolling evidence",
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200

    resp = client.get("/api/strategy/analyze?ticker=ROLL")
    assert resp.status_code == 200
    curve = resp.get_json()["rolling_sharpe_curve"]
    finite = [v for v in curve if v is not None]

    assert finite
    assert all(abs(v) < 100 for v in finite)


def test_strategy_endpoint_blocks_ineligible_history(monkeypatch):
    monkeypatch.setattr("engine.data.HAS_YF", False)
    client = _client()

    instrument = client.get("/api/strategy/analyze?ticker=AAPL&cost_bps=5&slippage_bps=2")
    assert instrument.status_code == 200
    ibody = instrument.get_json()
    assert ibody["symbol"] == "AAPL"
    assert "beat_benchmark" not in ibody
    assert "strategy" not in ibody
    assert ibody["methodology"]["no_lookahead"] is True
    assert ibody["eligible_for_real_research"] is False
    assert ibody["data_mode"] == "invalid_for_research"
    assert "research blocked" in ibody["display_label"].lower()


def test_strategy_endpoints_work_with_uploaded_real_instrument_and_model(monkeypatch):
    monkeypatch.setattr("engine.data.HAS_YF", False)
    client = _client()
    for symbol in ("MODA", "MODB"):
        upload_price = client.post(
            "/api/upload",
            data={
                "file": (BytesIO(price_csv(days=260)), f"{symbol}.csv"),
                "symbol": symbol,
                "name": symbol,
            },
            content_type="multipart/form-data",
        )
        assert upload_price.status_code == 200

    instrument = client.get("/api/strategy/analyze?ticker=MODA&cost_bps=5&slippage_bps=2")
    assert instrument.status_code == 200
    ibody = instrument.get_json()
    assert ibody["eligible_for_real_research"] is True
    assert ibody["data_mode"] == "real"

    payload = {
        "file": (BytesIO(b"Ticker,Weight\nMODA,60\nMODB,40\n"), "strategy-model.csv"),
        "name": "Strategy Model",
        "mandate": "balanced",
    }
    upload = client.post("/api/model/upload", data=payload, content_type="multipart/form-data")
    assert upload.status_code == 200
    model_id = upload.get_json()["id"]

    model = client.get(f"/api/model/strategy/analyze?id={model_id}&cost_bps=5&slippage_bps=2")
    assert model.status_code == 200
    mbody = model.get_json()
    assert mbody["id"] == model_id
    assert mbody["series_kind"] == "model"
    assert mbody["eligible_for_real_research"] is True
    assert mbody["data_mode"] == "real"
    assert mbody["methodology"]["analysis_only"] is True

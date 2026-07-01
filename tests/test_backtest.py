"""Direct contract tests for engine.backtest.run.

backtest.run is a thin wrapper over strategy.analyze_strategy; these tests pin
its public payload (equity curves, strategy/benchmark stats, trade count, win
rate, exposure) and its equivalence with the Strategy Lab engine.
"""
import numpy as np
import pandas as pd
import pytest

from engine import backtest, strategy
from tests.conftest import price_series


def _reversal_series(up_days: int = 150, down_days: int = 150) -> pd.Series:
    """Uptrend followed by a downtrend, forcing at least one entry and exit."""
    idx = pd.bdate_range("2023-01-02", periods=up_days + down_days)
    rets = np.concatenate([np.full(up_days, 0.004), np.full(down_days, -0.005)])
    return pd.Series(100.0 * np.cumprod(1.0 + rets), index=idx, name="close")


def test_run_returns_public_contract_fields():
    result = backtest.run(price_series(days=300))

    assert set(result) >= {
        "dates", "strategy_curve", "benchmark_curve",
        "strategy", "benchmark", "n_trades", "win_rate_pct", "exposure_pct",
    }
    for side in ("strategy", "benchmark"):
        assert set(result[side]) >= {
            "total_return_pct", "annual_return_pct", "annual_vol_pct",
            "sharpe", "max_drawdown_pct",
        }
    assert len(result["dates"]) == len(result["strategy_curve"]) == len(result["benchmark_curve"])
    assert result["strategy_curve"][0] == 1.0
    assert result["benchmark_curve"][0] == 1.0
    assert isinstance(result["n_trades"], int)
    assert 0.0 <= result["win_rate_pct"] <= 100.0
    assert 0.0 <= result["exposure_pct"] <= 100.0


def test_run_matches_strategy_lab_single_source_of_truth():
    close = _reversal_series()

    bt = backtest.run(close)
    st = strategy.analyze_strategy(close)

    assert bt["strategy"] == st["strategy"]
    assert bt["benchmark"] == st["benchmark"]
    assert bt["dates"] == st["dates"]
    assert bt["strategy_curve"] == st["strategy_curve"]
    assert bt["benchmark_curve"] == st["benchmark_curve"]
    assert bt["n_trades"] == st["trade_stats"]["n_trades"]
    assert bt["win_rate_pct"] == st["trade_stats"]["win_rate_pct"]
    assert bt["exposure_pct"] == st["trade_stats"]["exposure_pct"]


def test_benchmark_return_matches_buy_and_hold():
    close = _reversal_series()

    result = backtest.run(close)

    expected = (float(close.iloc[-1]) / float(close.iloc[0]) - 1.0) * 100
    assert result["benchmark"]["total_return_pct"] == pytest.approx(expected, rel=1e-9)
    assert result["benchmark_curve"][-1] == pytest.approx(1.0 + expected / 100, rel=1e-6)


def test_strategy_return_is_consistent_with_its_equity_curve():
    result = backtest.run(_reversal_series())

    # Curve points are rounded to 6 decimals for transport; compare in pct terms.
    assert result["strategy"]["total_return_pct"] == pytest.approx(
        (result["strategy_curve"][-1] - 1.0) * 100, abs=1e-3
    )


def test_reversal_series_produces_trades_and_positive_win_rate():
    result = backtest.run(_reversal_series())

    assert result["n_trades"] >= 1
    assert 0.0 < result["exposure_pct"] < 100.0
    assert result["win_rate_pct"] > 0.0
    # The strategy exits into the drawdown, so it should lose less than buy-and-hold.
    assert result["strategy"]["max_drawdown_pct"] >= result["benchmark"]["max_drawdown_pct"]


def test_higher_costs_reduce_strategy_return():
    close = _reversal_series()

    free = backtest.run(close, cost_bps=0.0)
    costly = backtest.run(close, cost_bps=50.0)

    assert free["n_trades"] >= 1
    assert costly["strategy"]["total_return_pct"] < free["strategy"]["total_return_pct"]
    assert costly["benchmark"]["total_return_pct"] == pytest.approx(
        free["benchmark"]["total_return_pct"]
    )


def test_run_rejects_insufficient_history():
    with pytest.raises(ValueError):
        backtest.run(price_series(days=30))

"""Regression tests: vectorized historical_signals must match the per-row loop.

The vectorized implementation replaced a per-day Python loop (two DataFrame
.iloc lookups per day). These tests pin the vectorized output to the original
scalar per-row scoring on representative data, including the prefix property
the Evidence Lab walk-forward optimization relies on.
"""
import numpy as np
import pandas as pd

from engine import data, indicators, signals
from tests.conftest import price_series


def _loop_historical_signals(close: pd.Series) -> pd.Series:
    """The original per-row implementation, kept as the reference."""
    f = indicators.indicator_frame(close)
    scores = np.zeros(len(f))
    for i in range(len(f)):
        scores[i] = 0.6 * signals._trend_score(f, i) + 0.4 * signals._momentum_score(f, i)
    return pd.Series(scores, index=close.index)


def _noisy_series(days: int, seed: int = 11) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=days)
    rets = rng.normal(0.0004, 0.015, days)
    return pd.Series(100.0 * np.exp(np.cumsum(rets)), index=idx)


def _assert_matches_loop(close: pd.Series) -> None:
    vectorized = signals.historical_signals(close)
    reference = _loop_historical_signals(close)
    assert list(vectorized.index) == list(reference.index)
    np.testing.assert_allclose(vectorized.to_numpy(), reference.to_numpy(), rtol=0, atol=1e-12)


def test_vectorized_signals_match_loop_on_trending_series():
    _assert_matches_loop(price_series(days=260, daily=0.001))


def test_vectorized_signals_match_loop_on_noisy_series_with_full_indicators():
    # 760 rows: SMA200 becomes available mid-series, exercising both the
    # missing-indicator branches and every RSI band on realistic data.
    close = _noisy_series(760)
    scores = signals.historical_signals(close)
    assert len(scores) == 760
    _assert_matches_loop(close)


def test_vectorized_signals_match_loop_on_bundled_sample_instruments():
    data.load_samples()
    for symbol in ("AAPL", "TSLA", "BTC-USD"):
        inst = data.get(symbol)
        assert inst is not None
        _assert_matches_loop(inst.df["close"].dropna())


def test_vectorized_signals_match_loop_on_short_history():
    # Shorter than SMA50/SMA200 windows: trend components partially unavailable.
    _assert_matches_loop(_noisy_series(40, seed=3))


def test_vectorized_signals_handle_nan_closes_like_loop():
    close = _noisy_series(300, seed=5)
    close.iloc[[10, 50, 51, 200]] = np.nan
    _assert_matches_loop(close)


def test_latest_signal_score_equals_last_historical_value():
    for days in (40, 260, 760):
        close = _noisy_series(days, seed=days)
        expected = float(signals.historical_signals(close).iloc[-1])
        assert abs(signals.latest_signal_score(close) - expected) <= 1e-12


def test_signal_scores_are_prefix_consistent_for_walk_forward_windows():
    # The Evidence Lab computes the score series once on the full history and
    # indexes it per walk-forward window. That is only valid because the signal
    # is causal: the score at day i must equal the last score of the prefix
    # ending at day i.
    close = _noisy_series(600, seed=17)
    full = signals.historical_signals(close)
    for signal_index in (125, 251, 340, 430, 599):
        prefix = close.iloc[: signal_index + 1]
        prefix_last = float(signals.historical_signals(prefix).iloc[-1])
        assert abs(float(full.iloc[signal_index]) - prefix_last) <= 1e-12

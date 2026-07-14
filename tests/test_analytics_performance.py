"""Regression tests for the vectorized analytics hot paths and memo cache.

Covers three exact-equivalence contracts:
  1. signals.historical_signals (vectorized) == per-row reference loop.
  2. Per-prefix signal/regime computation == full-series computation sliced at
     the prefix end (the causality property Evidence Lab walk-forwards rely on).
  3. Cached analytics results == freshly computed results, with invalidation
     on data registration and bounded cache growth.
"""
import numpy as np
import pandas as pd
import pytest

from engine import analytics_cache, data, evidence_lab, regime, signals
from helios_web import analysis as web_analysis
from tests.conftest import price_series


def _random_walk(days: int, seed: int = 7, drift: float = 0.0004, vol: float = 0.012) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=days)
    values = 100.0 * np.cumprod(1.0 + rng.normal(drift, vol, days))
    return pd.Series(values, index=idx, name="close")


def _sample_series() -> dict[str, pd.Series]:
    trending = price_series(days=300, daily=0.0015)
    choppy = _random_walk(300, seed=11, drift=0.0, vol=0.02)
    short = price_series(days=12)
    downtrend = price_series(days=260, daily=-0.001)
    with_nans = _random_walk(280, seed=3).copy()
    with_nans.iloc[[0, 1, 40, 41, 150]] = np.nan
    flat = pd.Series(100.0, index=pd.bdate_range("2023-01-02", periods=90), name="close")
    empty = pd.Series(dtype=float, index=pd.DatetimeIndex([]), name="close")
    return {
        "trending": trending,
        "choppy": choppy,
        "short": short,
        "downtrend": downtrend,
        "with_nans": with_nans,
        "flat": flat,
        "empty": empty,
    }


# --------------------------------------------------------------------------- #
# (a) Vectorized historical_signals == reference loop
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", sorted(_sample_series()))
def test_historical_signals_matches_reference_loop_exactly(name):
    close = _sample_series()[name]
    vectorized = signals.historical_signals(close)
    reference = signals._historical_signals_loop(close)
    pd.testing.assert_series_equal(vectorized, reference, check_exact=True)


def test_latest_signal_score_equals_last_history_row():
    close = _random_walk(400, seed=21)
    assert signals.latest_signal_score(close) == float(signals.historical_signals(close).iloc[-1])


def test_latest_signal_score_rejects_empty_series():
    with pytest.raises(ValueError):
        signals.latest_signal_score(pd.Series(dtype=float))


# --------------------------------------------------------------------------- #
# (b) Backward-looking inputs: per-prefix == full-series slice
# --------------------------------------------------------------------------- #
def test_historical_signals_prefix_equals_full_series_slice():
    close = _random_walk(400, seed=5)
    full = signals.historical_signals(close)
    for i in (5, 59, 100, 251, 399):
        prefix_score = float(signals.historical_signals(close.iloc[: i + 1]).iloc[-1])
        assert prefix_score == float(full.iloc[i]), f"signal mismatch at index {i}"


def test_regime_labels_prefix_equals_full_series_slice():
    close = _random_walk(320, seed=13, drift=0.0006, vol=0.015)
    labels = evidence_lab._regime_labels(close)
    for i in list(range(0, 320, 13)) + [59, 60, 199, 200, 319]:
        expected = str(regime.classify_regime(close.iloc[: i + 1]).get("label") or "neutral")
        assert str(labels.iloc[i]) == expected, f"regime mismatch at index {i}"


def test_walk_forward_windows_match_per_prefix_reference():
    px = _random_walk(430, seed=9)
    bench = _random_walk(430, seed=17, drift=0.0003)
    result = evidence_lab.analyze_series(
        px,
        target={"kind": "instrument", "id": "REF", "name": "Reference"},
        data_provenance={"data_mode": "real", "eligible_for_real_research": True},
        benchmark_symbol="SPY",
        benchmark_close=bench,
        horizon_days=21,
        train_window=126,
        step=21,
    )
    windows = result["windows"]
    assert len(windows) >= 8
    for row in windows:
        signal_date = pd.Timestamp(row["signal_date"])
        idx = px.index.get_loc(signal_date)
        prefix = px.iloc[: idx + 1]
        expected_score = round(float(signals._historical_signals_loop(prefix).iloc[-1]), 4)
        assert row["signal_score"] == expected_score
        assert row["input_rows"] == min(126, idx + 1)
        expected_regime = str(regime.classify_regime(bench.loc[:signal_date]).get("label") or "neutral")
        assert row["regime"] == expected_regime


# --------------------------------------------------------------------------- #
# (c) Analytics memo cache
# --------------------------------------------------------------------------- #
def test_series_key_identifies_series_and_rejects_empty():
    close = price_series(days=30)
    key = analytics_cache.series_key("AAPL", close)
    assert key == ("AAPL", str(close.index[-1]), 30, round(float(close.iloc[-1]), 6))
    # An in-place revision of today's bar (same date, same rows) is a NEW key.
    revised = close.copy()
    revised.iloc[-1] = revised.iloc[-1] * 1.01
    assert analytics_cache.series_key("AAPL", revised) != key
    assert analytics_cache.series_key("AAPL", pd.Series(dtype=float)) is None
    assert analytics_cache.series_key("AAPL", None) is None


def test_cache_returns_copies_and_none_keys_never_hit():
    analytics_cache.invalidate()
    value = {"rows": [{"score": 1.0}]}
    analytics_cache.put("test", ("K", "2024-01-01", 5), value)
    value["rows"][0]["score"] = 99.0  # caller mutation after put must not leak in
    first = analytics_cache.get("test", ("K", "2024-01-01", 5))
    assert first == {"rows": [{"score": 1.0}]}
    first["rows"][0]["score"] = 42.0  # mutation after get must not leak back
    assert analytics_cache.get("test", ("K", "2024-01-01", 5)) == {"rows": [{"score": 1.0}]}
    assert analytics_cache.get("test", None) is None
    assert analytics_cache.get("other", ("K", "2024-01-01", 5)) is None


def test_cache_is_bounded_and_invalidate_clears():
    analytics_cache.invalidate()
    for i in range(analytics_cache.MAX_ENTRIES + 10):
        analytics_cache.put("test", (f"K{i}", "2024-01-01", 1), {"i": i})
    assert analytics_cache.size() == analytics_cache.MAX_ENTRIES
    assert analytics_cache.get("test", ("K0", "2024-01-01", 1)) is None  # oldest evicted
    analytics_cache.invalidate()
    assert analytics_cache.size() == 0


def test_data_register_invalidates_analytics_cache():
    analytics_cache.put("test", ("K", "2024-01-01", 5), {"cached": True})
    series = price_series(days=40)
    data.register(data.Instrument("CACHEINV", "Cache Inv", pd.DataFrame({"close": series}), "upload", []))
    assert analytics_cache.size() == 0


def test_analyze_series_memoizes_and_survives_caller_mutation():
    analytics_cache.invalidate()
    px = _random_walk(430, seed=23)
    bench = _random_walk(430, seed=29, drift=0.0003)

    def run():
        return evidence_lab.analyze_series(
            px,
            target={"kind": "instrument", "id": "MEMO", "name": "Memo"},
            data_provenance={"data_mode": "real", "eligible_for_real_research": True},
            benchmark_symbol="SPY",
            benchmark_close=bench,
            horizon_days=21,
            train_window=126,
            step=21,
        )

    first = run()
    assert analytics_cache.size() == 1
    first["windows"][0]["signal_score"] = 999.0  # must not corrupt the cache
    second = run()
    assert second["windows"][0]["signal_score"] != 999.0
    assert second["summary"] == run()["summary"]
    assert second["decay"] == run()["decay"]


def test_command_center_screen_uses_cache():
    analytics_cache.invalidate()
    series = price_series(days=300, daily=0.001)
    inst = data.Instrument("SCREEN1", "Screen One", pd.DataFrame({"close": series}), "upload", [])
    data.register(inst)  # register clears the cache before the first screen
    first = web_analysis._quick_instrument_screen(inst)
    assert first is not None
    assert analytics_cache.size() == 1  # memoized opportunity candidate evidence
    second = web_analysis._quick_instrument_screen(inst)
    assert second == first

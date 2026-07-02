"""Behavior of the keyed analytics memo cache and its invalidation hooks."""
import pandas as pd

import app as helios
from engine import analytics_cache, backtest, data, evidence_lab, persistence, portfolio, signal_journal
from tests.conftest import price_series


def _use_db(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    store = persistence.get_store()
    assert store.available is True
    return store


def _register_upload(symbol: str, series: pd.Series):
    data.register(data.Instrument(symbol, symbol, pd.DataFrame({"close": series}, index=series.index), "upload", []))


def test_memoize_caches_by_key_and_recomputes_after_invalidate():
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"nested": [1, 2, 3]}

    first = analytics_cache.memoize(("k",), compute)
    second = analytics_cache.memoize(("k",), compute)
    assert calls["n"] == 1
    assert second == first
    # Cached values are isolated: mutating a result must not corrupt later reads.
    second["nested"].append(99)
    assert analytics_cache.memoize(("k",), compute)["nested"] == [1, 2, 3]
    assert calls["n"] == 1

    analytics_cache.invalidate()
    analytics_cache.memoize(("k",), compute)
    assert calls["n"] == 2


def test_series_token_uses_last_date_and_row_count():
    series = price_series(days=100)
    assert analytics_cache.series_token(series) == (str(series.index[-1]), 100)
    assert analytics_cache.series_token(series.iloc[:60]) != analytics_cache.series_token(series)
    assert analytics_cache.series_token(None) == ("empty", 0)
    assert analytics_cache.series_token(pd.Series(dtype=float)) == ("empty", 0)


def test_data_register_invalidates_analytics_cache():
    marker = analytics_cache.memoize(("register-test",), lambda: "cached")
    assert marker == "cached"
    before = analytics_cache.version()
    _register_upload("CACHEBUMP", price_series(days=90))
    assert analytics_cache.version() > before


def test_evidence_lab_reuses_walk_forward_core_until_data_changes(monkeypatch):
    _register_upload("SPY", price_series(days=430, daily=0.0007))
    _register_upload("EVCACHE", price_series(days=430, daily=0.0011))
    inst = data.get("EVCACHE")

    calls = {"n": 0}
    real_walk = evidence_lab._walk_windows

    def counting_walk(*args, **kwargs):
        calls["n"] += 1
        return real_walk(*args, **kwargs)

    monkeypatch.setattr(evidence_lab, "_walk_windows", counting_walk)

    first = evidence_lab.analyze_instrument(inst, horizon_days=21, train_window=126, step=21)
    assert first["summary"]["window_count"] > 0
    walked = calls["n"]
    assert walked > 0

    second = evidence_lab.analyze_instrument(inst, horizon_days=21, train_window=126, step=21)
    assert calls["n"] == walked  # cache hit: no recomputation
    assert second == first

    # Different parameters miss the cache.
    evidence_lab.analyze_instrument(inst, horizon_days=42, train_window=126, step=21)
    assert calls["n"] > walked

    # New data invalidates the memo.
    walked = calls["n"]
    _register_upload("EVCACHE", price_series(days=440, daily=0.0011))
    evidence_lab.analyze_instrument(data.get("EVCACHE"), horizon_days=21, train_window=126, step=21)
    assert calls["n"] > walked


def test_command_center_card_is_memoized_per_series_state(monkeypatch):
    _register_upload("CCFAST", price_series(days=300, daily=0.0009))
    inst = data.get("CCFAST")

    calls = {"n": 0}
    real_run = backtest.run

    def counting_run(*args, **kwargs):
        calls["n"] += 1
        return real_run(*args, **kwargs)

    monkeypatch.setattr(backtest, "run", counting_run)

    first = helios._quick_instrument_screen(inst)
    assert first is not None and first["symbol"] == "CCFAST"
    assert calls["n"] == 1
    second = helios._quick_instrument_screen(inst)
    assert calls["n"] == 1
    assert second == first

    # Upload/refresh (register) invalidates the card.
    _register_upload("CCFAST", price_series(days=310, daily=0.0009))
    third = helios._quick_instrument_screen(data.get("CCFAST"))
    assert calls["n"] == 2
    assert third is not None


def test_journal_forward_refresh_is_gated_on_data_version(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    pending_close = price_series(days=80, start=100.0, daily=0.001)
    signal_journal.record_signal(
        target_kind="instrument",
        target_id="GATEX",
        target_name="Gate X",
        close=pending_close,
        input_close=pending_close,
        signal={"action": "BUY", "score": 0.5},
        horizon_days=21,
        benchmark="",
        source_counts={"upload": 1},
        eligible_for_real_research=True,
    )

    calls = {"n": 0}
    real_series_for_entry = signal_journal._series_for_entry

    def counting_series(entry):
        calls["n"] += 1
        return real_series_for_entry(entry)

    monkeypatch.setattr(signal_journal, "_series_for_entry", counting_series)

    signal_journal.list_entries(limit=100)
    assert calls["n"] == 1  # first pass scans the pending entry
    signal_journal.list_entries(limit=100)
    assert calls["n"] == 1  # same data version: scan skipped

    # New price data (register bumps the version) resolves the pending entry.
    _register_upload("GATEX", price_series(days=120, start=100.0, daily=0.001))
    entries = signal_journal.list_entries(limit=100)
    assert calls["n"] == 2
    assert entries[0]["forward_status"] == "measured"
    assert entries[0]["forward_result_pct"] is not None


def test_holdings_with_signals_uses_last_row_score(monkeypatch):
    _register_upload("HOLDX", price_series(days=260, daily=0.001))
    from engine import signals

    expected = float(signals.historical_signals(data.get("HOLDX").df["close"].dropna()).iloc[-1])
    assert abs(signals.latest_signal_score(data.get("HOLDX").df["close"].dropna()) - expected) <= 1e-12

    ps = portfolio.PortfolioSeries(
        close=pd.Series(dtype=float),
        holdings=[{"ticker": "HOLDX", "weight": 1.0}],
        n_days=0,
        sources={},
        warnings=[],
    )
    rows = helios._holdings_with_signals(ps, None)
    assert rows[0]["signal"] in {"BUY", "HOLD", "SELL"}

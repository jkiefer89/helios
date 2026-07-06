"""Regression tests for the data-layer audit fixes.

Covers: price-cache invalidation on register(), upload/live frame hygiene,
refresh-all batching, negative caching of failed live resolutions, persisted
window replacement, and schema-version fail-closed handling.
"""
import sqlite3
import threading
import time

import numpy as np
import pandas as pd
import pytest

import app as helios
from engine import data, persistence
from tests.conftest import price_csv, price_series


@pytest.fixture(autouse=True)
def reset_negative_resolve_cache():
    # getattr keeps the fixture importable on builds without the negative cache.
    getattr(data, "_NEGATIVE_RESOLVE", {}).clear()
    yield
    getattr(data, "_NEGATIVE_RESOLVE", {}).clear()


def _client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def _use_db(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    store = persistence.get_store()
    assert store.available is True
    return store


def _ohlcv(days=90, start=100.0, daily=0.001):
    close = price_series(days=days, start=start, daily=daily)
    return pd.DataFrame({
        "open": close.values,
        "high": close.values * 1.01,
        "low": close.values * 0.99,
        "close": close.values,
        "volume": [1000] * len(close),
    }, index=close.index)


def _csv_bytes(dates, closes):
    return pd.DataFrame({"Date": dates, "Close": closes}).to_csv(index=False).encode("utf-8")


# --------------------------------------------------------------------------- #
# Fix 1: register() invalidates stale _PRICE_CACHE entries
# --------------------------------------------------------------------------- #
def test_register_invalidates_stale_price_cache_after_upload(monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", False)
    stale = data.resolve_series("CACHEUP")
    assert stale.source == "simulated"
    assert "CACHEUP" in data._PRICE_CACHE

    data.parse_csv(price_csv(days=90), "CACHEUP", "Cache Upload")

    permissive = data.resolve_series("CACHEUP")
    strict = data.resolve_series("CACHEUP", allow_sample=False, allow_simulated=False)
    assert permissive.source == "upload"
    assert strict.source == "upload"
    assert len(permissive.close) == 90


def test_register_invalidates_price_cache_after_live_refresh(monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", False)
    data.register(data.Instrument("CACHELV", "Cache Live", _ohlcv(days=90, start=100.0), "live", []))
    before = data.resolve_series("CACHELV", allow_sample=False, allow_simulated=False)
    assert before.source == "live"
    assert len(before.close) == 90

    def fake_fetch(symbol):
        return data.Instrument(symbol, "Cache Live", _ohlcv(days=120, start=250.0), "live", [])

    result = data.refresh_live_symbol("CACHELV", fetcher=fake_fetch)
    assert result["status"] == "ok"

    after = data.resolve_series("CACHELV", allow_sample=False, allow_simulated=False)
    assert len(after.close) == 120
    assert after.close.iloc[0] != before.close.iloc[0]


# --------------------------------------------------------------------------- #
# Fix 2: upload/live frame hygiene
# --------------------------------------------------------------------------- #
def test_parse_csv_dedupes_duplicate_dates_keeping_last():
    dates = list(pd.bdate_range("2024-01-02", periods=35).strftime("%Y-%m-%d"))
    closes = [100.0 + i for i in range(35)]
    # Duplicate an early date at the end of the file with a superseding value.
    dates.append(dates[3])
    closes.append(999.0)

    inst = data.parse_csv(_csv_bytes(dates, closes), "DUPDATE")

    assert len(inst.df) == 35
    assert not inst.df.index.duplicated().any()
    assert inst.df["close"].loc[pd.Timestamp(dates[3])] == 999.0
    assert inst.df.index.is_monotonic_increasing


def test_parse_csv_drops_zero_negative_and_non_finite_closes():
    dates = pd.bdate_range("2024-01-02", periods=40).strftime("%Y-%m-%d")
    closes = [100.0 + i for i in range(40)]
    closes[5] = 0.0
    closes[10] = -25.0
    closes[15] = np.inf
    closes[20] = -np.inf

    inst = data.parse_csv(_csv_bytes(dates, closes), "BADROWS")

    assert len(inst.df) == 36
    assert (inst.df["close"] > 0).all()
    assert np.isfinite(inst.df["close"]).all()


def test_parse_csv_rejects_when_cleaning_leaves_fewer_than_30_rows():
    dates = pd.bdate_range("2024-01-02", periods=40).strftime("%Y-%m-%d")
    closes = [100.0 + i for i in range(40)]
    for i in range(12):  # 40 raw rows, only 28 valid after cleaning
        closes[i] = 0.0 if i % 2 else np.inf

    with pytest.raises(ValueError, match="at least 30"):
        data.parse_csv(_csv_bytes(dates, closes), "TOOFEW")


def test_fetch_live_cleans_duplicate_dates_and_bad_closes(monkeypatch):
    idx = list(pd.bdate_range("2024-01-02", periods=40))
    idx.append(idx[2])  # duplicate date, last occurrence wins
    closes = [100.0 + i for i in range(40)] + [555.0]
    closes[7] = np.nan
    closes[9] = np.inf
    closes[11] = 0.0
    hist = pd.DataFrame({
        "Open": closes, "High": closes, "Low": closes,
        "Close": closes, "Volume": [1000] * 41,
    }, index=pd.DatetimeIndex(idx))

    class FakeTicker:
        news = []
        info = {"shortName": "Fake Live"}

        def __init__(self, symbol):
            pass

        def history(self, period="2y", auto_adjust=True, timeout=10):
            return hist

    monkeypatch.setattr(data, "HAS_YF", True)
    monkeypatch.setattr(data, "_yf", type("FakeYF", (), {"Ticker": FakeTicker}))

    inst = data.fetch_live("FAKELV", persist=False)

    assert len(inst.df) == 37  # 41 rows - 3 bad closes - 1 duplicate
    assert not inst.df.index.duplicated().any()
    assert (inst.df["close"] > 0).all()
    assert np.isfinite(inst.df["close"]).all()
    assert inst.df["close"].loc[pd.Timestamp(idx[2])] == 555.0


def test_normalise_price_frame_drops_non_finite_closes():
    idx = pd.bdate_range("2024-01-02", periods=4)
    frame = pd.DataFrame({"close": [100.0, np.inf, -np.inf, np.nan]}, index=idx)

    clean = persistence._normalise_price_frame(frame)

    assert list(clean["close"]) == [100.0]


# --------------------------------------------------------------------------- #
# Fix 3: refresh-all batches with bounded workers and one journal refresh
# --------------------------------------------------------------------------- #
def test_refresh_all_runs_concurrently_with_single_journal_refresh(monkeypatch):
    symbols = [f"BATCH{i}" for i in range(8)]
    for symbol in symbols:
        data.register(data.Instrument(symbol, symbol, _ohlcv(days=90), "live", []))
    monkeypatch.setattr(data, "HAS_YF", True)

    active = 0
    max_active = 0
    journal_calls = []
    lock = threading.Lock()

    def fake_fetch(symbol, period="2y", persist=True):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return data.Instrument(symbol, f"{symbol} Live", _ohlcv(days=95), "live", [])

    monkeypatch.setattr(data, "fetch_live", fake_fetch)
    monkeypatch.setattr(data, "_refresh_signal_journal_forward_results", lambda: journal_calls.append(1))

    resp = _client().post("/api/data/refresh", json={"all": True})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["requested"] == "all"
    assert body["refreshed"] == 8
    assert body["failed"] == 0
    assert body["skipped"] == 0
    assert {item["symbol"] for item in body["results"]} == set(symbols)
    assert {"requested", "refreshed", "failed", "skipped", "results", "warnings", "data_status"} <= set(body)
    assert max_active > 1  # batched refreshes overlap instead of running serially
    assert len(journal_calls) == 1  # forward refresh ran once for the batch


def test_single_symbol_refresh_keeps_per_symbol_journal_refresh(monkeypatch):
    data.register(data.Instrument("SOLO1", "Solo One", _ohlcv(days=90), "live", []))
    journal_calls = []
    monkeypatch.setattr(data, "_refresh_signal_journal_forward_results", lambda: journal_calls.append(1))

    def fake_fetch(symbol):
        return data.Instrument(symbol, "Solo One", _ohlcv(days=95), "live", [])

    result = data.refresh_live_symbol("SOLO1", fetcher=fake_fetch)
    assert result["status"] == "ok"
    assert len(journal_calls) == 1

    result = data.refresh_live_symbol("SOLO1", fetcher=fake_fetch, refresh_journal=False)
    assert result["status"] == "ok"
    assert len(journal_calls) == 1  # skipped when the caller batches


# --------------------------------------------------------------------------- #
# Fix 4: failed live resolutions are negative-cached with a TTL
# --------------------------------------------------------------------------- #
class _FailingYF:
    def __init__(self):
        self.calls = 0

    def Ticker(self, symbol):
        self.calls += 1
        raise RuntimeError("provider down")


def test_failed_live_resolution_is_negative_cached(monkeypatch):
    fake = _FailingYF()
    monkeypatch.setattr(data, "HAS_YF", True)
    monkeypatch.setattr(data, "_yf", fake)

    with pytest.raises(ValueError, match="No eligible real price history"):
        data.resolve_series("NEGT", allow_sample=False, allow_simulated=False)
    assert fake.calls == 1

    with pytest.raises(ValueError, match="No eligible real price history"):
        data.resolve_series("NEGT", allow_sample=False, allow_simulated=False)
    assert fake.calls == 1  # inside the TTL the provider is not retried

    # After the TTL expires the live attempt is retried.
    data._NEGATIVE_RESOLVE["NEGT"] = time.monotonic() - 1.0
    with pytest.raises(ValueError, match="No eligible real price history"):
        data.resolve_series("NEGT", allow_sample=False, allow_simulated=False)
    assert fake.calls == 2


def test_register_clears_negative_resolution_cache(monkeypatch):
    fake = _FailingYF()
    monkeypatch.setattr(data, "HAS_YF", True)
    monkeypatch.setattr(data, "_yf", fake)

    with pytest.raises(ValueError, match="No eligible real price history"):
        data.resolve_series("NEGREG", allow_sample=False, allow_simulated=False)
    assert "NEGREG" in data._NEGATIVE_RESOLVE

    data.register(data.Instrument("NEGREG", "Neg Reg", _ohlcv(days=90), "upload", []))

    assert "NEGREG" not in data._NEGATIVE_RESOLVE
    ps = data.resolve_series("NEGREG", allow_sample=False, allow_simulated=False)
    assert ps.source == "upload"
    assert fake.calls == 1  # resolved from the store, no further provider calls


# --------------------------------------------------------------------------- #
# Fix 5: persist_instrument replaces the prior persisted window
# --------------------------------------------------------------------------- #
def test_persist_instrument_replaces_prior_window(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    store = _use_db(monkeypatch, tmp_path)

    window_a = _ohlcv(days=300, start=100.0)
    result = store.persist_instrument(symbol="REBASE", name="Rebase", source="live", frame=window_a)
    assert result["persisted"] is True and result["rows"] == 300

    # Overlapping, re-based window (e.g. new adjustment basis after a dividend).
    window_b = _ohlcv(days=250, start=80.0).set_axis(window_a.index[50:], axis=0)
    result = store.persist_instrument(symbol="REBASE", name="Rebase", source="live", frame=window_b)
    assert result["persisted"] is True and result["rows"] == 250

    loaded = {inst.symbol: inst for inst in store.load_instruments()}["REBASE"]
    assert len(loaded.df) == 250  # exactly window B, not the 300-row union
    assert loaded.df.index.min() == window_b.index.min()
    assert loaded.df["close"].iloc[0] == pytest.approx(window_b["close"].iloc[0])


# --------------------------------------------------------------------------- #
# Fix 6: schema version gate
# --------------------------------------------------------------------------- #
def test_future_schema_version_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    store = _use_db(monkeypatch, tmp_path)
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE schema_version SET version = ?", (persistence.SCHEMA_VERSION + 94,))
    conn.close()

    persistence.reset_store_for_tests()
    reopened = persistence.get_store()

    assert reopened.available is False
    assert "schema version" in reopened.warning
    assert reopened.status()["available"] is False
    # Fail-closed open leaves the newer database untouched.
    with sqlite3.connect(reopened.path) as conn:
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_version")]
    conn.close()
    assert versions == [persistence.SCHEMA_VERSION + 94]


def test_old_schema_version_is_stamped_to_current(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    store = _use_db(monkeypatch, tmp_path)
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE schema_version SET version = 1")
    conn.close()

    persistence.reset_store_for_tests()
    reopened = persistence.get_store()

    assert reopened.available is True
    with sqlite3.connect(reopened.path) as conn:
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_version")]
    conn.close()
    assert versions == [persistence.SCHEMA_VERSION]

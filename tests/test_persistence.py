import sqlite3
import threading
import time
from io import BytesIO

import pandas as pd

import app as helios
from engine import data, persistence, portfolio
from tests.conftest import price_csv, price_series


def _use_db(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    store = persistence.get_store()
    assert store.available is True
    return store


def _client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def test_sqlite_schema_is_created_with_version(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)

    with sqlite3.connect(store.path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]

    assert {"instruments", "price_history", "models", "holdings", "refresh_log", "signal_journal"} <= tables
    assert version == persistence.SCHEMA_VERSION


def test_uploaded_price_history_survives_process_store_reload(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)

    inst = data.parse_csv(price_csv(days=90), "PERSIST", "Persisted Upload", source_filename="persist.csv")
    assert inst.source == "upload"
    data._STORE.pop("PERSIST")

    data.load_persisted_instruments()
    restored = data.get("PERSIST")

    assert restored is not None
    assert restored.source == "upload"
    assert len(restored.df) == 90


def test_uploaded_model_survives_reload_and_reports_missing_tickers(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    data.parse_csv(price_csv(days=90), "REAL1", "Real One", source_filename="real1.csv")
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nREAL1,60\nNEEDPRICE,40\n",
        "client-model.csv",
        "Client Model",
        "balanced",
        "taxable review",
    )
    portfolio._MODELS.clear()

    portfolio.load_persisted_models()
    restored = portfolio.get(model.id)
    status = _client().get("/api/data/status").get_json()

    assert restored is not None
    assert [h.ticker for h in restored.holdings] == ["REAL1", "NEEDPRICE"]
    assert status["missing_data"]["missing_tickers"] == ["NEEDPRICE"]
    assert status["missing_data"]["models"][0]["coverage_state"] == "mixed"


def test_data_status_endpoint_reports_database_and_counts(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    data.parse_csv(price_csv(days=90), "STATUSX", "Status X", source_filename="statusx.csv")

    resp = _client().get("/api/data/status")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["database"]["available"] is True
    assert body["real_instrument_count"] == 1
    assert body["source_counts"]["upload"] == 1
    assert "data_mode_summary" in body


def test_refresh_endpoint_fails_gracefully_without_live_provider(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    live = data.Instrument("LIVE1", "Live One", _ohlcv(days=90), "live", [])
    data.register(live)
    data._persist_instrument(live, adjusted=True)
    monkeypatch.setattr(data, "HAS_YF", False)

    resp = _client().post("/api/data/refresh", json={"symbol": "LIVE1"})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["failed"] == 1
    assert body["results"][0]["status"] == "error"
    assert "unavailable" in body["results"][0]["message"].lower()


def test_refresh_endpoint_uses_mocked_live_fetch_without_network(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    live = data.Instrument("MOCKLIVE", "Mock Live", _ohlcv(days=90), "live", [])
    data.register(live)
    data._persist_instrument(live, adjusted=True)
    monkeypatch.setattr(data, "HAS_YF", True)

    def fake_fetch(symbol, period="2y", persist=True):
        assert symbol == "MOCKLIVE"
        return data.Instrument(symbol, "Mock Live", _ohlcv(days=95), "live", [])

    monkeypatch.setattr(data, "fetch_live", fake_fetch)

    resp = _client().post("/api/data/refresh", json={"symbol": "MOCKLIVE"})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["refreshed"] == 1
    assert body["results"][0]["rows_added"] == 5
    assert body["data_status"]["last_refresh"]["status"] == "ok"


def test_auto_live_bootstrap_fetches_and_persists_live_symbols(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)
    calls = []

    def fake_fetch(symbol, period="2y", persist=True):
        calls.append((symbol, period, persist))
        return data.Instrument(symbol, f"{symbol} Live", _ohlcv(days=95), "live", [])

    result = data.ensure_live_symbols(["AAPL", "SPY"], period="1y", fetcher=fake_fetch)

    assert result["requested"] == ["AAPL", "SPY"]
    assert result["refreshed"] == 2
    assert result["failed"] == 0
    assert result["results"][0]["status"] == "ok"
    assert calls == [("AAPL", "1y", False), ("SPY", "1y", False)]
    assert data.get("AAPL").source == "live"
    assert data.get("SPY").source == "live"
    assert store.status()["real_instrument_count"] == 2
    assert store.refresh_log(limit=2)[0]["status"] == "ok"


def test_auto_live_bootstrap_fetches_symbols_concurrently(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)
    active = 0
    max_active = 0
    lock = threading.Lock()
    calls = []

    def fake_fetch(symbol, period="2y", persist=True):
        nonlocal active, max_active
        with lock:
            calls.append((symbol, period, persist))
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return data.Instrument(symbol, f"{symbol} Live", _ohlcv(days=95), "live", [])

    result = data.ensure_live_symbols(
        ["AAPL", "SPY", "MSFT", "NVDA"],
        period="1y",
        fetcher=fake_fetch,
        max_workers=4,
    )

    assert result["requested"] == ["AAPL", "SPY", "MSFT", "NVDA"]
    assert [item["symbol"] for item in result["results"]] == result["requested"]
    assert result["refreshed"] == 4
    assert result["failed"] == 0
    assert max_active > 1
    assert {call[0] for call in calls} == set(result["requested"])
    assert all(call[1:] == ("1y", False) for call in calls)
    assert store.status()["real_instrument_count"] == 4


def test_auto_live_bootstrap_keeps_sample_when_fetch_fails(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)
    original = data.get("AAPL")

    def failing_fetch(symbol, period="2y", persist=True):
        raise RuntimeError("provider unavailable")

    result = data.ensure_live_symbols(["AAPL"], fetcher=failing_fetch)

    assert result["refreshed"] == 0
    assert result["failed"] == 1
    assert result["results"][0]["status"] == "error"
    assert data.get("AAPL") is original
    assert data.get("AAPL").source == "sample"
    assert store.status()["real_instrument_count"] == 0


def test_starter_models_live_preset_covers_theme_holdings():
    symbols = set(data.expand_live_symbols("starter_models"))

    assert {"AVGO", "VST", "CIBR", "XLV", "QUAL"} <= symbols
    assert len(symbols) > len(data.expand_live_symbols("core"))


def test_sample_and_simulated_sources_are_not_persisted_as_real(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)
    sample = data.get("AAPL")

    sample_result = store.persist_instrument(
        symbol=sample.symbol,
        name=sample.name,
        source=sample.source,
        frame=sample.df,
    )
    simulated_result = store.persist_instrument(
        symbol="SIMONLY",
        name="Simulated Only",
        source="simulated",
        frame=_ohlcv(days=90),
    )

    assert sample_result["persisted"] is False
    assert simulated_result["persisted"] is False
    assert store.status()["real_instrument_count"] == 0
    assert store.load_instruments() == []


def test_in_memory_store_keeps_institutional_sized_live_universe():
    for idx in range(80):
        symbol = f"LV{idx:03d}"
        data.register(data.Instrument(symbol, symbol, _ohlcv(days=90), "live", []))

    loaded = {inst.symbol for inst in data.all_instruments() if inst.source == "live"}

    assert "LV000" in loaded
    assert "LV079" in loaded
    assert len([symbol for symbol in loaded if symbol.startswith("LV")]) == 80


def test_app_works_with_default_db_path(monkeypatch, tmp_path):
    monkeypatch.delenv("HELIOS_DB_PATH", raising=False)
    monkeypatch.setattr(persistence, "DEFAULT_DB_PATH", tmp_path / "default-helios.db")
    persistence.reset_store_for_tests()

    resp = _client().get("/api/data/status")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["database"]["configured"] is True
    assert body["database"]["available"] is True


def test_invalid_db_path_returns_warning_without_stack(monkeypatch, tmp_path):
    bad_path = tmp_path / "not-a-db-dir"
    bad_path.mkdir()
    monkeypatch.setenv("HELIOS_DB_PATH", str(bad_path))
    persistence.reset_store_for_tests()

    resp = _client().get("/api/data/status")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["database"]["available"] is False
    assert "SQLite persistence unavailable" in body["database"]["warning"]
    assert "Traceback" not in body["database"]["warning"]


def test_raw_upload_content_and_secret_tokens_are_not_stored(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)
    raw = (
        b"Date,Close,api_key\n"
        b"2024-01-02,100,SECRET_TOKEN_SHOULD_NOT_STORE\n"
        b"2024-01-03,101,SECRET_TOKEN_SHOULD_NOT_STORE\n"
    )
    rows = pd.DataFrame({
        "Date": pd.bdate_range("2024-01-02", periods=65).strftime("%Y-%m-%d"),
        "Close": price_series(days=65).values,
        "api_key": ["SECRET_TOKEN_SHOULD_NOT_STORE"] * 65,
    }).to_csv(index=False).encode("utf-8")
    data.parse_csv(rows, "SECRET1", "Secret Test", source_filename="api_key=SECRET_TOKEN_SHOULD_NOT_STORE.csv")
    portfolio.parse_model_file(
        BytesIO(b"Ticker,Weight\nSECRET1,100\n").getvalue(),
        "token=SECRET_TOKEN_SHOULD_NOT_STORE.csv",
        "Secret Model",
        "balanced",
        "api_key=SECRET_TOKEN_SHOULD_NOT_STORE",
    )

    with sqlite3.connect(store.path) as conn:
        text = "\n".join(
            str(row)
            for table in ("instruments", "models", "holdings", "price_history")
            for row in conn.execute(f"SELECT * FROM {table}").fetchall()
        )

    assert raw.decode("utf-8") not in text
    assert "SECRET_TOKEN_SHOULD_NOT_STORE" not in text


def _ohlcv(days=90):
    close = price_series(days=days)
    return pd.DataFrame({
        "open": close.values,
        "high": close.values * 1.01,
        "low": close.values * 0.99,
        "close": close.values,
        "volume": [1000] * len(close),
    }, index=close.index)

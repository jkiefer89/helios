import sqlite3
import threading
import time
from contextlib import closing
from io import BytesIO

import pandas as pd
import pytest
from cryptography.fernet import Fernet

import app as helios
from engine import data, evidence, persistence, portfolio
from tests.conftest import price_csv, price_series

SQLITE_HEADER = b"SQLite format 3\x00"


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
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    store = _use_db(monkeypatch, tmp_path)

    with closing(sqlite3.connect(store.path)) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]

    assert {
        "instruments", "price_history", "models", "holdings", "refresh_log",
        "signal_journal", "account_snapshot_revisions", "evidence_snapshots",
    } <= tables
    assert version == persistence.SCHEMA_VERSION


def test_account_snapshot_corrections_are_append_only(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)
    initial = {
        "account_id": "ACC-LINEAGE", "as_of": "2026-07-10", "cash": 1000,
        "total_value": 5000, "source": "custodian_upload",
    }
    first = store.record_account_snapshot(initial, [{"ticker": "AAPL", "shares": 10, "price": 400}])
    duplicate = store.record_account_snapshot(initial, [{"ticker": "AAPL", "shares": 10, "price": 400}])
    corrected = store.record_account_snapshot(
        {**initial, "total_value": 5100, "correction_reason": "Custodian restatement."},
        [{"ticker": "AAPL", "shares": 10, "price": 410}],
    )

    revisions = store.account_snapshot_revisions("ACC-LINEAGE", "2026-07-10")
    assert first["revision"] == 1
    assert duplicate["duplicate"] is True
    assert corrected["revision"] == 2
    assert [row["revision"] for row in revisions] == [2, 1]
    assert revisions[0]["supersedes_snapshot_id"] == revisions[1]["snapshot_id"]
    assert revisions[0]["correction_reason"] == "Custodian restatement."
    assert revisions[0]["content_hash"] != revisions[1]["content_hash"]
    assert store.account_snapshots("ACC-LINEAGE")[0]["total_value"] == 5100

    persistence.reset_store_for_tests()
    reopened = persistence.get_store()
    restarted = reopened.account_snapshot_revisions("ACC-LINEAGE", "2026-07-10")
    assert [(row["revision"], row["content_hash"]) for row in restarted] == [
        (row["revision"], row["content_hash"]) for row in revisions
    ]
    assert restarted[0]["positions"] == revisions[0]["positions"]
    assert restarted[1]["positions"] == revisions[1]["positions"]


def test_evidence_snapshot_replays_and_detects_tampering(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)
    evidence_id = evidence.new_id("test")
    result = evidence.capture(
        evidence_id=evidence_id,
        artifact_kind="test",
        target_kind="instrument",
        target_id="AAPL",
        input_payload={"rows": [["2026-07-10", "100"]]},
        output_payload={"action": "HOLD", "score": 0.25},
        evidence_manifest=evidence.manifest(
            source="upload", transformations=("unit_test",),
        ),
    )
    assert result["recorded"] is True
    verified = evidence.verify(evidence_id)
    assert verified["status"] == "verified"
    assert verified["manifest"]["calculation_version"] == evidence.CALCULATION_VERSION

    with store._connect() as conn:
        conn.execute(
            "UPDATE evidence_snapshots SET output_json = ? WHERE evidence_id = ?",
            (store._store_json({"action": "BUY", "score": 99}), evidence_id),
        )
    assert evidence.verify(evidence_id)["status"] == "hash_mismatch"


def test_evidence_envelope_detects_manifest_tampering_and_redacts_secrets(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)
    evidence_id = evidence.new_id("secret-test")
    secret_shaped_sentinel = "sk-" + "ant-api03-should-never-persist"
    result = evidence.capture(
        evidence_id=evidence_id,
        artifact_kind="test",
        target_kind="instrument",
        target_id="AAPL",
        input_payload={
            "api_key": "must-not-persist",
            "nested": {"authorization": "Bearer must-not-persist"},
            "note": f"credential {secret_shaped_sentinel}",
        },
        output_payload={"status": "ok"},
        evidence_manifest=evidence.manifest(source="upload", provider="licensed-primary"),
    )
    assert result["recorded"] is True
    stored = store.get_evidence_snapshot(evidence_id)
    assert stored["input"]["api_key"] == "[redacted]"
    assert stored["input"]["nested"]["authorization"] == "[redacted]"
    assert "must-not-persist" not in evidence.canonical_text(stored)
    assert secret_shaped_sentinel not in evidence.canonical_text(stored)

    with store._connect() as conn:
        manifest = store._store_json({**stored["manifest"], "provider": "tampered-provider"})
        conn.execute(
            "UPDATE evidence_snapshots SET manifest_json = ? WHERE evidence_id = ?",
            (manifest, evidence_id),
        )
    assert evidence.verify(evidence_id)["status"] == "hash_mismatch"


def test_point_in_time_inputs_never_read_later_revisions(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)
    monkeypatch.setattr(persistence, "utc_now", lambda: "2026-07-13T12:00:00+00:00")
    store.record_fundamentals_snapshot({
        "symbol": "PITX", "as_of": "2026-07-12", "forward_pe": 20,
        "source": "provider-a",
    })
    store.record_fundamentals_snapshot({
        "symbol": "PITX", "as_of": "2026-07-13", "forward_pe": 30,
        "source": "provider-a",
    })
    store.record_corporate_actions("PITX", [
        {"action_type": "dividend", "ex_date": "2026-07-12", "value": 0.5, "source": "provider-a"},
        {"action_type": "split", "ex_date": "2026-07-14", "value": 2.0, "source": "provider-a"},
    ])

    assert store.fundamentals_as_of("PITX", "2026-07-12")["forward_pe"] == 20
    assert store.fundamentals_as_of("PITX", "2026-07-13")["forward_pe"] == 30
    assert [row["action_type"] for row in store.corporate_actions_as_of("PITX", "2026-07-13")] == ["dividend"]


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
    assert body["results"][0]["reason_code"] == "provider_unavailable"
    assert body["results"][0]["retryable"] is False
    assert body["results"][0]["stale_result_preserved"] is True
    assert "approved live price provider" in body["results"][0]["next_step"]
    assert body["data_status"]["refresh_log"][0]["reason_code"] == "provider_unavailable"


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


def test_refresh_all_preserves_reason_codes_for_mixed_batch_results(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    monkeypatch.setenv("HELIOS_LIVE_REFRESH_WORKERS", "1")
    for symbol in ("BATCHOK", "BATCHFAIL", "BATCHSKIP"):
        instrument = data.Instrument(symbol, symbol, _ohlcv(days=90), "live", [])
        data.register(instrument)
        data._persist_instrument(instrument, adjusted=True)

    def fake_refresh(symbol, refresh_journal=False):
        assert refresh_journal is False
        if symbol == "BATCHOK":
            return {"symbol": symbol, "status": "ok", "rows_added": 1}
        if symbol == "BATCHFAIL":
            return {
                "symbol": symbol, "status": "error", "message": "Provider timed out.",
                "reason_code": "provider_timeout", "retryable": True,
                "next_step": "Retry after the provider recovers.",
                "stale_result_preserved": True,
            }
        return {
            "symbol": symbol, "status": "skipped", "message": "No newer bar.",
            "reason_code": "already_current", "retryable": False,
            "next_step": "Wait for the next market bar.",
            "stale_result_preserved": True,
        }

    monkeypatch.setattr(data, "refresh_live_symbol", fake_refresh)
    monkeypatch.setattr(data, "_refresh_signal_journal_forward_results", lambda: None)

    response = _client().post("/api/data/refresh", json={"all": True})

    assert response.status_code == 200
    body = response.get_json()
    assert (body["refreshed"], body["failed"], body["skipped"]) == (1, 1, 1)
    by_symbol = {row["symbol"]: row for row in body["results"]}
    assert by_symbol["BATCHFAIL"]["reason_code"] == "provider_timeout"
    assert by_symbol["BATCHFAIL"]["retryable"] is True
    assert by_symbol["BATCHFAIL"]["stale_result_preserved"] is True
    assert by_symbol["BATCHFAIL"]["next_step"] == "Retry after the provider recovers."
    assert by_symbol["BATCHSKIP"]["reason_code"] == "already_current"
    assert by_symbol["BATCHSKIP"]["retryable"] is False
    assert by_symbol["BATCHSKIP"]["stale_result_preserved"] is True
    assert by_symbol["BATCHSKIP"]["next_step"] == "Wait for the next market bar."
    assert body["warnings"]


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

    store.flush()
    text = store.path.read_bytes().decode("utf-8", errors="ignore")

    assert raw.decode("utf-8") not in text
    assert "SECRET_TOKEN_SHOULD_NOT_STORE" not in text


def test_data_quality_alert_tracking_redacts_secret_like_text(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)

    result = store.sync_data_quality_alerts(
        [{
            "id": "data_quality:refresh_failures:SECRET1",
            "category": "refresh_failures",
            "severity": "warning",
            "target": "SECRET1",
            "detail": "Provider failed with api_key=SECRET_TOKEN_SHOULD_NOT_STORE",
            "next_step": "Retry refresh after provider recovers.",
        }],
        generated_at="2026-07-01T12:00:00+00:00",
    )

    assert result["tracking_available"] is True
    assert result["summary"]["active_count"] == 1
    assert result["active"][0]["status"] == "active"
    assert "SECRET_TOKEN_SHOULD_NOT_STORE" not in result["active"][0]["detail"]
    assert "redacted" in result["active"][0]["detail"].lower()
    store.flush()
    assert "SECRET_TOKEN_SHOULD_NOT_STORE" not in store.path.read_bytes().decode("utf-8", errors="ignore")


def test_local_persistence_encrypts_sensitive_payloads_when_key_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "required")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
    store = _use_db(monkeypatch, tmp_path)

    data.parse_csv(price_csv(days=90), "ENC1", "Sensitive Alpha Sleeve", source_filename="sensitive-alpha.csv")
    portfolio.parse_model_file(
        b"Ticker,Weight\nENC1,100\n",
        "private-model.csv",
        "Private Growth Mandate",
        "balanced",
        "client context should be sealed",
    )

    store.flush()
    raw_db = store.path.read_bytes()
    assert not raw_db.startswith(SQLITE_HEADER)
    assert b"schema_version" not in raw_db
    with pytest.raises(sqlite3.DatabaseError):
        with closing(sqlite3.connect(store.path)) as conn:
            conn.execute("SELECT name FROM sqlite_master").fetchall()
    assert b"Sensitive Alpha Sleeve" not in raw_db
    assert b"Private Growth Mandate" not in raw_db
    assert b"client context should be sealed" not in raw_db
    assert store.status()["encryption"]["enabled"] is True
    assert store.status()["encryption"]["required"] is True
    assert store.status()["encryption"]["at_rest_format"] == "fernet-sqlite-snapshot"
    assert store.status()["encryption"]["plaintext_lookup_keys"] == []

    data._STORE.pop("ENC1")
    portfolio._MODELS.clear()
    data.load_persisted_instruments()
    portfolio.load_persisted_models()

    assert data.get("ENC1").name == "Sensitive Alpha Sleeve"
    assert portfolio.all_models()[0].name == "Private Growth Mandate"
    assert portfolio.all_models()[0].mandate_context == "client context should be sealed"


def test_encrypted_snapshot_loader_falls_back_when_deserialize_cannot_open(monkeypatch, tmp_path):
    source = tmp_path / "source.sqlite"
    with closing(sqlite3.connect(source)) as conn, conn:
        conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        conn.execute("INSERT INTO marker(value) VALUES ('ok')")
    payload = source.read_bytes()
    target = sqlite3.connect(":memory:")

    def fail_deserialize(_conn, _payload):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(persistence, "_deserialize_sqlite_payload", fail_deserialize)

    loaded = persistence._load_sqlite_payload_into_memory(target, payload, tmp_path)

    value = loaded.execute("SELECT value FROM marker").fetchone()[0]
    leftovers = list(tmp_path.glob(".helios-decrypted-*"))
    loaded.close()

    assert value == "ok"
    assert leftovers == []


def test_encrypted_snapshot_loader_validates_deserialize_before_accepting(monkeypatch, tmp_path):
    source = tmp_path / "source.sqlite"
    with closing(sqlite3.connect(source)) as conn, conn:
        conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        conn.execute("INSERT INTO marker(value) VALUES ('ok')")
    payload = source.read_bytes()
    target = sqlite3.connect(":memory:")

    def false_success(_conn, _payload):
        return None

    def fail_validation(_conn):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(persistence, "_deserialize_sqlite_payload", false_success)
    monkeypatch.setattr(persistence, "_validate_loaded_sqlite_payload", fail_validation)

    loaded = persistence._load_sqlite_payload_into_memory(target, payload, tmp_path)

    value = loaded.execute("SELECT value FROM marker").fetchone()[0]
    loaded.close()

    assert value == "ok"


def test_encryption_migrates_existing_plaintext_sqlite_file(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    store = _use_db(monkeypatch, tmp_path)
    data.parse_csv(price_csv(days=90), "MIGRATE", "Migration Test", source_filename="migrate.csv")
    assert store.path.read_bytes().startswith(SQLITE_HEADER)

    data._STORE.pop("MIGRATE")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "required")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
    persistence.reset_store_for_tests()
    encrypted_store = persistence.get_store()
    data.load_persisted_instruments()

    raw_db = encrypted_store.path.read_bytes()
    assert encrypted_store.available is True
    assert data.get("MIGRATE").name == "Migration Test"
    assert not raw_db.startswith(SQLITE_HEADER)
    assert b"Migration Test" not in raw_db


def test_required_encryption_without_key_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "required")
    monkeypatch.delenv("HELIOS_DB_ENCRYPTION_KEY", raising=False)
    persistence.reset_store_for_tests()

    store = persistence.get_store()

    assert store.available is False
    assert "encryption" in store.warning.lower()
    assert store.status()["encryption"]["required"] is True


def test_encryption_key_rotation_reads_previous_key_and_rewrites_with_active_key(monkeypatch, tmp_path):
    old_key = Fernet.generate_key().decode("ascii")
    new_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "required")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY", old_key)
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY_ID", "key-old")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY_VERSION", "1")
    store = _use_db(monkeypatch, tmp_path)
    store.audit_append("rotation.seed", {"status": "old-key"}, required=True)
    store.flush(force=True)
    persistence.reset_store_for_tests()

    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY", new_key)
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_PREVIOUS_KEYS", old_key)
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY_ID", "key-new")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY_VERSION", "2")
    monkeypatch.setenv("HELIOS_DB_KEY_CUSTODY_MODE", "kms_hsm")
    monkeypatch.setenv("HELIOS_DB_KEY_CUSTODY_ATTESTED", "1")
    rotated = persistence.get_store()

    status = rotated.status()["encryption"]
    assert rotated.available is True
    assert status["key_id"] == "key-new"
    assert status["key_version"] == "2"
    assert status["rotation_configured"] is True
    assert status["recovery_key_count"] == 1
    assert status["recovery_key_used"] is True
    assert status["external_custody"] is True
    assert status["custody_attested"] is True
    assert old_key not in str(status)
    assert new_key not in str(status)

    rotated.audit_append("rotation.complete", {"status": "new-key"}, required=True)
    rotated.flush(force=True)
    persistence.reset_store_for_tests()
    monkeypatch.delenv("HELIOS_DB_ENCRYPTION_PREVIOUS_KEYS")
    reloaded = persistence.get_store()
    assert reloaded.available is True
    assert reloaded.audit_verify()["entries"] == 2


def test_encrypted_database_does_not_load_as_plaintext(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "required")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
    store = _use_db(monkeypatch, tmp_path)
    data.parse_csv(price_csv(days=90), "LOCKED", "Locked Store", source_filename="locked.csv")
    data._STORE.pop("LOCKED")

    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    monkeypatch.delenv("HELIOS_DB_ENCRYPTION_KEY", raising=False)
    persistence.reset_store_for_tests()
    plaintext_store = persistence.get_store()

    assert plaintext_store.load_instruments() == []
    assert plaintext_store.available is False
    assert "encrypted sqlite persistence" in plaintext_store.warning.lower()


def test_startup_warns_on_unencrypted_sqlite_files_in_db_directory(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "required")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
    _use_db(monkeypatch, tmp_path)

    stray = tmp_path / "qa-rehearsal.sqlite"
    with closing(sqlite3.connect(stray)) as conn:
        conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")
    nested = tmp_path / "corrupt-backups" / "helios.db.corrupt-123"
    nested.parent.mkdir()
    with closing(sqlite3.connect(nested)) as conn:
        conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")

    capsys.readouterr()
    persistence.reset_store_for_tests()
    store = persistence.get_store()

    assert store.available is True
    assert store.plaintext_artifacts == [str(nested), str(stray)]
    assert store.status()["plaintext_artifacts"] == [str(nested), str(stray)]
    stderr = capsys.readouterr().err
    assert "unencrypted SQLite file(s)" in stderr
    assert "qa-rehearsal.sqlite" in stderr
    assert "helios.db.corrupt-123" in stderr


def test_plaintext_scan_skips_cloud_evicted_placeholder_files(monkeypatch, tmp_path):
    evicted = tmp_path / "evicted.sqlite"
    with closing(sqlite3.connect(evicted)) as conn:
        conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")
    local = tmp_path / "local.sqlite"
    with closing(sqlite3.connect(local)) as conn:
        conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")

    monkeypatch.setattr(persistence, "_is_dataless", lambda candidate: candidate == evicted)

    found = persistence._plaintext_sqlite_artifacts(tmp_path, ignore=set())

    assert found == [local]


def test_configured_plaintext_store_is_not_flagged_as_artifact(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    store = _use_db(monkeypatch, tmp_path)

    assert store.path.read_bytes().startswith(SQLITE_HEADER)
    assert store.plaintext_artifacts == []
    assert store.status()["plaintext_artifacts"] == []
    assert "unencrypted SQLite file(s)" not in capsys.readouterr().err


def _ohlcv(days=90):
    close = price_series(days=days)
    return pd.DataFrame({
        "open": close.values,
        "high": close.values * 1.01,
        "low": close.values * 0.99,
        "close": close.values,
        "volume": [1000] * len(close),
    }, index=close.index)


def test_malformed_env_encryption_key_is_fatal_even_in_auto_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    monkeypatch.delenv("HELIOS_DB_ENCRYPTION", raising=False)
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY", "not-a-valid-fernet-key")
    persistence.reset_store_for_tests()

    with pytest.raises(SystemExit, match="HELIOS_DB_ENCRYPTION_KEY"):
        persistence.get_store()


def test_malformed_key_file_is_fatal_instead_of_plaintext_fallback(monkeypatch, tmp_path):
    key_file = tmp_path / "helios.key"
    key_file.write_text("garbage-not-a-key\n", encoding="utf-8")
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    monkeypatch.delenv("HELIOS_DB_ENCRYPTION", raising=False)
    monkeypatch.delenv("HELIOS_DB_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY_PATH", str(key_file))
    persistence.reset_store_for_tests()

    with pytest.raises(SystemExit, match="key file"):
        persistence.get_store()


FERNET_TEST_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


def _use_encrypted_db(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "required")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY", FERNET_TEST_KEY)
    return _use_db(monkeypatch, tmp_path)


def test_encrypted_snapshot_writes_are_debounced_until_flush(monkeypatch, tmp_path):
    store = _use_encrypted_db(monkeypatch, tmp_path)
    calls = {"n": 0}
    original = store._persist_encrypted_snapshot

    def counting():
        calls["n"] += 1
        original()

    monkeypatch.setattr(store, "_persist_encrypted_snapshot", counting)
    for idx in range(3):
        result = store.persist_instrument(
            symbol=f"BATCH{idx}", name=f"Batch {idx}", source="live", frame=_ohlcv(days=90),
        )
        assert result["persisted"] is True

    assert calls["n"] == 0
    assert store._snapshot_dirty is True
    stale = store.path.read_bytes()

    store.flush()

    assert calls["n"] == 1
    assert store._snapshot_dirty is False
    assert store.path.read_bytes() != stale
    store.status()
    assert store._snapshot_dirty is False  # read-only work does not re-dirty

    persistence.reset_store_for_tests()
    reloaded = persistence.get_store()
    assert {inst.symbol for inst in reloaded.load_instruments()} == {"BATCH0", "BATCH1", "BATCH2"}


def test_required_encrypted_write_rolls_back_when_snapshot_cannot_persist(monkeypatch, tmp_path):
    store = _use_encrypted_db(monkeypatch, tmp_path)
    baseline = store.path.read_bytes()
    original = store._persist_encrypted_snapshot

    def fail_snapshot():
        raise OSError("simulated durable snapshot failure")

    monkeypatch.setattr(store, "_persist_encrypted_snapshot", fail_snapshot)
    with pytest.raises(RuntimeError, match="Could not persist encrypted snapshot"):
        store.audit_append("critical.test", {"state": "must-not-survive"}, required=True)

    assert store.audit_verify()["entries"] == 0
    assert store.path.read_bytes() == baseline

    monkeypatch.setattr(store, "_persist_encrypted_snapshot", original)
    persistence.reset_store_for_tests()
    assert persistence.get_store().audit_verify()["entries"] == 0


def test_dirty_snapshot_flushes_after_max_age(monkeypatch, tmp_path):
    monkeypatch.setattr(persistence, "SNAPSHOT_FLUSH_MAX_AGE_SECONDS", 0.05)
    store = _use_encrypted_db(monkeypatch, tmp_path)
    stale = store.path.read_bytes()

    store.persist_instrument(symbol="AGED1", name="Aged One", source="live", frame=_ohlcv(days=90))

    deadline = time.time() + 5
    while time.time() < deadline and (store.path.read_bytes() == stale or store._snapshot_dirty):
        time.sleep(0.02)
    assert store.path.read_bytes() != stale
    assert store._snapshot_dirty is False


def test_pending_snapshot_is_flushed_by_registered_shutdown_hook(monkeypatch, tmp_path):
    registered = []
    real_register = persistence.atexit.register
    monkeypatch.setattr(persistence.atexit, "register", lambda fn, *a, **kw: registered.append(fn) or fn)
    store = _use_encrypted_db(monkeypatch, tmp_path)
    assert any(getattr(fn, "__self__", None) is store and fn.__name__ == "flush" for fn in registered)

    store.persist_instrument(symbol="EXIT1", name="Exit One", source="live", frame=_ohlcv(days=90))
    stale = store.path.read_bytes()
    for hook in registered:
        hook()

    assert store._snapshot_dirty is False
    assert store.path.read_bytes() != stale
    monkeypatch.setattr(persistence.atexit, "register", real_register)
    persistence.reset_store_for_tests()
    assert {inst.symbol for inst in persistence.get_store().load_instruments()} == {"EXIT1"}


def test_startup_backup_is_created_pruned_and_reported(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "required")
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION_KEY", FERNET_TEST_KEY)
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    first = persistence.get_store()
    assert first.available is True
    assert first.backup_status() == {"count": 0, "latest": None}  # nothing existed before first start

    store = first
    for _ in range(7):
        persistence.reset_store_for_tests()
        store = persistence.get_store()

    backups = sorted((tmp_path / "backups").glob("helios-*.db"))
    assert len(backups) == 5
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in backups)
    assert all(path.read_bytes().startswith(persistence.ENCRYPTED_DB_MAGIC) for path in backups)
    status = store.status()
    assert status["backups"]["count"] == 5
    assert status["backups"]["latest"] == backups[-1].name


def test_backups_are_skipped_when_persistence_is_off(monkeypatch):
    monkeypatch.setenv("HELIOS_DB_PATH", "off")
    persistence.reset_store_for_tests()

    store = persistence.get_store()

    assert store.available is False
    assert store.status()["backups"] == {"count": 0, "latest": None}


def test_startup_warns_about_unencrypted_database_files(monkeypatch, tmp_path, capsys):
    plain = sqlite3.connect(str(tmp_path / "old-backup.sqlite"))
    plain.execute("CREATE TABLE t (x INTEGER)")
    plain.commit()
    plain.close()
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    persistence.reset_store_for_tests()

    store = persistence.get_store()

    assert store.available is True
    err = capsys.readouterr().err
    assert "Unencrypted database files" in err
    assert "old-backup.sqlite" in err

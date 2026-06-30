import app as helios
from engine import data, persistence, signal_journal
from tests.conftest import price_csv, price_series


def _use_db(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    store = persistence.get_store()
    assert store.available is True
    return store


def test_analysis_endpoint_records_signal_journal_entry(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    client = helios.app.test_client()
    data.parse_csv(price_csv(days=120), "SIGX", "Signal X", source_filename="sigx.csv")

    resp = client.get("/api/analyze?ticker=SIGX&horizon=21")

    assert resp.status_code == 200
    journal = client.get("/api/signal-journal").get_json()
    assert journal["entries"]
    entry = journal["entries"][0]
    assert entry["target_kind"] == "instrument"
    assert entry["target_id"] == "SIGX"
    assert entry["target_name"] == "Signal X"
    assert entry["action_label"] in {"BUY", "HOLD", "SELL"}
    assert isinstance(entry["score"], float)
    assert entry["benchmark"] == "SPY"
    assert entry["horizon_days"] == 21
    assert entry["input_start_date"] == "2024-01-02"
    assert entry["input_end_date"]
    assert entry["input_rows"] == 120
    assert entry["forward_status"] == "pending"
    assert entry["eligible_for_real_research"] is True
    assert "upload" in entry["source_counts"]


def test_signal_journal_measures_forward_result_when_history_exists(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    close = price_series(days=90, start=100.0, daily=0.002)
    input_close = close.iloc[:70]

    result = signal_journal.record_signal(
        target_kind="instrument",
        target_id="FORWARD",
        target_name="Forward Test",
        close=close,
        input_close=input_close,
        signal={"action": "BUY", "score": 0.42},
        horizon_days=10,
        benchmark="SPY",
        source_counts={"upload": 1},
        eligible_for_real_research=True,
    )

    assert result["forward_status"] == "measured"
    assert result["forward_result_pct"] is not None
    assert result["forward_end_date"] > result["input_end_date"]
    entries = signal_journal.list_entries()
    assert entries[0]["target_id"] == "FORWARD"
    assert entries[0]["forward_status"] == "measured"


def test_live_refresh_resolves_pending_forward_results(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)
    initial = price_series(days=80, start=100.0, daily=0.001)
    live = data.Instrument("JOURNALX", "Journal X", initial.to_frame("close"), "live", [])
    data.register(live)
    data._persist_instrument(live, adjusted=True)
    signal_journal.record_signal(
        target_kind="instrument",
        target_id="JOURNALX",
        target_name="Journal X",
        close=initial,
        input_close=initial,
        signal={"action": "BUY", "score": 0.55},
        horizon_days=5,
        benchmark="",
        source_counts={"live": 1},
        eligible_for_real_research=True,
        data_mode="real",
    )
    assert store.signal_journal()[0]["forward_status"] == "pending"

    refreshed = price_series(days=90, start=100.0, daily=0.001)

    def fake_fetch(symbol):
        assert symbol == "JOURNALX"
        return data.Instrument(symbol, "Journal X", refreshed.to_frame("close"), "live", [])

    result = data.refresh_live_symbol("JOURNALX", fetcher=fake_fetch)

    assert result["status"] == "ok"
    entry = store.signal_journal()[0]
    assert entry["forward_status"] == "measured"
    assert entry["forward_result_pct"] is not None
    assert entry["forward_end_date"] > entry["input_end_date"]

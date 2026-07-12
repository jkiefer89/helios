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
    assert entry["input_start_date"] and entry["input_end_date"] >= entry["input_start_date"]
    assert entry["input_rows"] == 120
    assert entry["forward_status"] == "pending"
    assert entry["eligible_for_real_research"] is True
    assert "upload" in entry["source_counts"]


def test_signal_journal_measures_forward_result_when_history_exists(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    # Input window ends TODAY (settlement guard) with synthetic future bars
    # beyond it so the forward window is immediately measurable offline.
    close = price_series(days=90, start=100.0, daily=0.002, future_days=20)
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
    initial = price_series(days=80, start=100.0, daily=0.001)  # ends today -> pending
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

    # Same start/daily, 10 synthetic future bars: the refresh now covers the
    # 5-bar forward window that was pending at record time.
    refreshed = price_series(days=90, start=100.0, daily=0.001, future_days=10)

    def fake_fetch(symbol):
        assert symbol == "JOURNALX"
        return data.Instrument(symbol, "Journal X", refreshed.to_frame("close"), "live", [])

    result = data.refresh_live_symbol("JOURNALX", fetcher=fake_fetch)

    assert result["status"] == "ok"
    entry = store.signal_journal()[0]
    assert entry["forward_status"] == "measured"
    assert entry["forward_result_pct"] is not None
    assert entry["forward_end_date"] > entry["input_end_date"]


def test_signal_journal_endpoint_summarizes_paper_performance_evidence(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    client = helios.app.test_client()
    benchmark = price_series(days=95, start=100.0, daily=0.0005, future_days=25)
    data.register(data.Instrument("BENCHX", "Benchmark X", benchmark.to_frame("close"), "live", []))
    buy_close = price_series(days=95, start=100.0, daily=0.003, future_days=25)
    model_close = price_series(days=95, start=100.0, daily=0.002, future_days=25)
    # Ends 3 business days before the measured MODEL-A entry's window: evidence
    # aggregation now counts each (target, input_end, horizon) window ONCE, so
    # the pending entry must occupy a DISTINCT forward window to be counted.
    pending_close = price_series(days=70, start=100.0, daily=0.001).iloc[:-3]

    signal_journal.record_signal(
        target_kind="instrument",
        target_id="BUYX",
        target_name="Buy X",
        close=buy_close,
        input_close=buy_close.iloc[:70],
        signal={"action": "BUY", "score": 0.62},
        horizon_days=10,
        benchmark="BENCHX",
        source_counts={"live": 1},
        eligible_for_real_research=True,
        data_mode="real",
    )
    signal_journal.record_signal(
        target_kind="model",
        target_id="MODEL-A",
        target_name="Model A",
        close=model_close,
        input_close=model_close.iloc[:70],
        signal={"action": "BUY", "score": 0.47},
        horizon_days=10,
        benchmark="BENCHX",
        source_counts={"live": 5},
        eligible_for_real_research=True,
        data_mode="real",
    )
    signal_journal.record_signal(
        target_kind="model",
        target_id="MODEL-A",
        target_name="Model A",
        close=pending_close,
        input_close=pending_close,
        signal={"action": "HOLD", "score": 0.12},
        horizon_days=10,
        benchmark="BENCHX",
        source_counts={"live": 5},
        eligible_for_real_research=True,
        data_mode="real",
    )

    resp = client.get("/api/signal-journal")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["count"] == 3
    assert body["summary"]["total_count"] == 3
    assert body["summary"]["measured_count"] == 2
    assert body["summary"]["pending_count"] == 1
    assert body["summary"]["hit_rate_pct"] is not None
    assert body["summary"]["avg_alpha_pct"] is not None
    assert body["benchmark_comparison"][0]["benchmark"] == "BENCHX"
    assert body["benchmark_comparison"][0]["measured_count"] == 2
    assert body["benchmark_comparison"][0]["avg_alpha_pct"] is not None
    model_row = next(row for row in body["model_evidence"] if row["target_id"] == "MODEL-A")
    assert model_row["signal_count"] == 2
    assert model_row["measured_count"] == 1
    assert model_row["pending_count"] == 1
    assert model_row["latest_action_label"] in {"BUY", "HOLD"}
    assert len(body["drift"]) == 3
    assert {"score", "forward_result_pct", "alpha_pct", "cumulative_measured_count"} <= set(body["drift"][-1])
    assert body["methodology"]["hit_rate_basis"]


def test_journal_read_path_does_not_refresh_or_fetch(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)
    pending = price_series(days=70, start=100.0, daily=0.001)
    signal_journal.record_signal(
        target_kind="instrument",
        target_id="READONLY",
        target_name="Read Only",
        close=pending,
        input_close=pending,
        signal={"action": "HOLD", "score": 0.1},
        horizon_days=21,
        benchmark="",
        source_counts={"upload": 1},
        eligible_for_real_research=True,
    )
    refresh_calls: list[int] = []
    write_calls: list[str] = []
    monkeypatch.setattr(signal_journal, "refresh_forward_results",
                        lambda *a, **k: refresh_calls.append(1))
    monkeypatch.setattr(store, "update_signal_forward_result",
                        lambda key, result: write_calls.append(key))

    client = helios.app.test_client()
    resp = client.get("/api/signal-journal")

    assert resp.status_code == 200
    assert resp.get_json()["entries"][0]["target_id"] == "READONLY"
    assert refresh_calls == []
    assert write_calls == []


def test_refresh_cannot_starve_old_pending_entries(monkeypatch, tmp_path):
    # A pending long-horizon entry buried under newer rows must still be
    # refreshed: the newest-first listing starved it forever (review finding).
    store = _use_db(monkeypatch, tmp_path)
    close = price_series(days=90, start=100.0, daily=0.002, future_days=20)
    signal_journal.record_signal(
        target_kind="instrument", target_id="OLDPEND", target_name="Old Pending",
        close=close.iloc[:70], input_close=close.iloc[:70],
        signal={"action": "BUY", "score": 0.5}, horizon_days=10, benchmark="",
        source_counts={"upload": 1}, eligible_for_real_research=True, data_mode="real",
    )
    assert store.signal_journal(limit=1)[0]["forward_status"] == "pending"
    # Bury it under newer measured entries (distinct scores -> distinct dedupe keys).
    for i in range(5):
        signal_journal.record_signal(
            target_kind="instrument", target_id=f"NEW{i}", target_name=f"New {i}",
            close=close, input_close=close.iloc[:70],
            signal={"action": "BUY", "score": 0.1 + i / 100.0}, horizon_days=10,
            benchmark="", source_counts={"upload": 1},
            eligible_for_real_research=True, data_mode="real",
        )
    # Register full history so the pending entry can now be measured.
    inst = data.Instrument("OLDPEND", "Old Pending", close.to_frame("close"), "upload", [])
    data.register(inst)
    # A tiny newest-first window would never reach the old row; the pending
    # queue (oldest first) must.
    pend = store.pending_signal_entries(limit=1)
    assert pend and pend[0]["target_id"] == "OLDPEND"
    signal_journal.refresh_forward_results(limit=2)
    statuses = {e["target_id"]: e["forward_status"] for e in store.signal_journal(limit=20)}
    assert statuses["OLDPEND"] == "measured"

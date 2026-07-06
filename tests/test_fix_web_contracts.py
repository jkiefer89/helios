"""Regression tests for the web-contracts audit fixes:

1. Instrument report degrades honestly on 30-59 row histories (no 500).
2. /api/model/strategy/analyze distinguishes window errors from provenance blocks.
3. /api/analyze and /api/model/analyze ship server-side provenance verdicts.
4. Signal Journal never measures signals or benchmarks on demo/sample/simulated
   prices, and headline stats aggregate only real-eligible entries.
5. A failed model editor save rolls the in-memory model back.
6. core.py hardening: JSON auth envelopes on /api paths, empty HELIOS_PASSWORD
   treated as unset, and numpy bools survive JSON sanitation.
"""
import json
from io import BytesIO

import numpy as np
import pytest

import app as helios
from engine import data, persistence, portfolio, signal_journal
from helios_web import core as web_core
from tests.conftest import price_csv, price_series


@pytest.fixture()
def client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def _use_db(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    store = persistence.get_store()
    assert store.available is True
    return store


def _upload(client, symbol: str, days: int = 260):
    resp = client.post(
        "/api/upload",
        data={
            "file": (BytesIO(price_csv(days=days)), f"{symbol.lower()}.csv"),
            "symbol": symbol,
            "name": f"{symbol} uploaded history",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    return resp


def _upload_model(client, csv: bytes, name: str):
    resp = client.post(
        "/api/model/upload",
        data={"file": (BytesIO(csv), "model.csv"), "name": name, "mandate": "balanced"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    return resp.get_json()["id"]


# --------------------------------------------------------------------------- #
# Fix 1 — instrument report on short (30-59 row) uploads
# --------------------------------------------------------------------------- #
def test_instrument_report_short_history_returns_honest_section_not_500(client):
    _upload(client, "SHORT40", days=40)

    resp = client.get("/api/report/instrument?ticker=SHORT40")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["kind"] == "instrument"
    section = body["sections"]["strategy"]
    assert section["available"] is False
    assert "60" in section["reason"]
    assert section["required_rows"] == 60
    assert any("60 rows" in warning for warning in body["warnings"])
    assert "analysis only" in body["disclaimer"].lower()


def test_instrument_report_with_full_history_marks_strategy_available(client):
    resp = client.get("/api/report/instrument?ticker=AAPL")

    assert resp.status_code == 200
    section = resp.get_json()["sections"]["strategy"]
    assert section["available"] is True
    assert "strategy_return_pct" in section


# --------------------------------------------------------------------------- #
# Fix 2 — /api/model/strategy/analyze window errors are not provenance blocks
# --------------------------------------------------------------------------- #
def test_model_strategy_window_error_returns_400_with_actual_message(client, monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", False)
    for symbol in ("WRA", "WRB"):
        _upload(client, symbol)
    model_id = _upload_model(client, b"Ticker,Weight\nWRA,60\nWRB,40\n", "Window Model")

    resp = client.get(f"/api/model/strategy/analyze?id={model_id}&start=2099-01-01")

    assert resp.status_code == 400
    body = resp.get_json()
    assert "60 rows" in body["error"]
    assert "missing" not in body["error"].lower()
    assert "missing_tickers" not in body


def test_model_strategy_blocked_model_keeps_provenance_block_shape(client, monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", False)
    model_id = _upload_model(client, b"Ticker,Weight\nFAKEQA,50\nFAKEQB,50\n", "Blocked Model")

    resp = client.get(f"/api/model/strategy/analyze?id={model_id}")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data_mode"] == "invalid_for_research"
    assert body["eligible_for_real_research"] is False
    assert body["missing_tickers"]
    assert "analysis only" in body["disclaimer"].lower()


# --------------------------------------------------------------------------- #
# Fix 3 — server-side provenance verdicts on the analyze endpoints
# --------------------------------------------------------------------------- #
_VERDICT_KEYS = {
    "data_mode", "display_label", "eligible_for_real_research",
    "reason", "required_action", "warnings",
}


def test_analyze_ships_demo_provenance_verdict_for_sample_data(client):
    resp = client.get("/api/analyze?ticker=AAPL&horizon=21")

    assert resp.status_code == 200
    verdict = resp.get_json()["data_provenance"]
    assert set(verdict) >= _VERDICT_KEYS | {"source_counts"}
    assert verdict["data_mode"] == "demo"
    assert verdict["eligible_for_real_research"] is False
    assert verdict["source_counts"] == {"sample": 1}
    assert verdict["required_action"]


def test_analyze_ships_real_provenance_verdict_for_uploads(client):
    _upload(client, "REALPV", days=120)

    resp = client.get("/api/analyze?ticker=REALPV&horizon=21")

    assert resp.status_code == 200
    verdict = resp.get_json()["data_provenance"]
    assert verdict["data_mode"] == "real"
    assert verdict["eligible_for_real_research"] is True
    assert verdict["source_counts"] == {"upload": 1}


def test_model_analyze_ships_blocked_verdict_for_pure_sample_model(client, monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", False)
    model_id = _upload_model(client, b"Ticker,Weight\nAAPL,60\nMSFT,40\n", "Sample Verdict Model")

    resp = client.get(f"/api/model/analyze?id={model_id}&horizon=21")

    assert resp.status_code == 200
    body = resp.get_json()
    # Raw provenance block is untouched for backward compatibility.
    assert "source_weight_pct" in body["provenance"]
    verdict = body["data_provenance"]
    assert set(verdict) >= _VERDICT_KEYS | {"source_weight_pct", "missing_tickers"}
    assert verdict["data_mode"] == "invalid_for_research"
    assert verdict["data_mode"] != "mixed"
    assert verdict["eligible_for_real_research"] is False


def test_model_analyze_ships_real_verdict_for_uploaded_model(client, monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", False)
    for symbol in ("RVA", "RVB"):
        _upload(client, symbol)
    model_id = _upload_model(client, b"Ticker,Weight\nRVA,55\nRVB,45\n", "Real Verdict Model")

    resp = client.get(f"/api/model/analyze?id={model_id}&horizon=21")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data_provenance"]["data_mode"] == "real"
    assert body["data_provenance"]["eligible_for_real_research"] is True
    assert body["provenance"]["real_weight_pct"] == 100.0


# --------------------------------------------------------------------------- #
# Fix 4a — synthetic sample benchmarks never supply alpha for real signals
# --------------------------------------------------------------------------- #
def test_sample_benchmark_never_supplies_alpha_for_real_signals(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    close = price_series(days=90, daily=0.002)

    entry = signal_journal.record_signal(
        target_kind="instrument",
        target_id="REALSIG",
        target_name="Real Sig",
        close=close,
        input_close=close.iloc[:70],
        signal={"action": "BUY", "score": 0.5},
        horizon_days=10,
        benchmark="SPY",  # bundled synthetic sample instrument
        source_counts={"upload": 1},
        eligible_for_real_research=True,
        data_mode="real",
    )

    assert entry["forward_status"] == "measured"
    assert entry["benchmark_result_pct"] is None
    assert entry["alpha_pct"] is None
    assert "withheld" in entry["metadata"]["benchmark_note"]


def test_real_benchmark_still_supplies_alpha(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    close = price_series(days=90, daily=0.002)
    benchmark = price_series(days=95, daily=0.0005)
    data.register(data.Instrument("BENCHR", "Bench R", benchmark.to_frame("close"), "live", []))

    entry = signal_journal.record_signal(
        target_kind="instrument",
        target_id="REALSIG2",
        target_name="Real Sig Two",
        close=close,
        input_close=close.iloc[:70],
        signal={"action": "BUY", "score": 0.5},
        horizon_days=10,
        benchmark="BENCHR",
        source_counts={"upload": 1},
        eligible_for_real_research=True,
        data_mode="real",
    )

    assert entry["forward_status"] == "measured"
    assert entry["benchmark_result_pct"] is not None
    assert entry["alpha_pct"] is not None


# --------------------------------------------------------------------------- #
# Fix 4b — model forward results are never measured on simulated prices
# --------------------------------------------------------------------------- #
def test_model_forward_results_are_not_measured_on_simulated_prices(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    monkeypatch.setattr(data, "HAS_YF", False)
    hist = price_series(days=70)
    data.register(data.Instrument("RJX", "Real J X", hist.to_frame("close"), "upload", []))
    mdl = portfolio.Model(
        id="JMODEL", name="J Model", mandate_key="balanced", mandate_context="",
        holdings=[portfolio.Holding("RJX", 1.0, "upload")],
    )
    portfolio.register(mdl)
    ps = portfolio.build_series(mdl, allow_sample=False, allow_simulated=False)
    entry = signal_journal.record_signal(
        target_kind="model",
        target_id="JMODEL",
        target_name="J Model",
        close=ps.close,
        input_close=ps.close,
        signal={"action": "BUY", "score": 0.4},
        horizon_days=10,
        benchmark="",
        source_counts={"upload": 1},
        eligible_for_real_research=True,
        data_mode="real",
    )
    assert entry["forward_status"] == "pending"

    # Simulate a fresh process where the upload is gone: the ticker would now
    # resolve to a deterministic simulated series covering the horizon.
    data._STORE.pop("RJX", None)
    data._PRICE_CACHE.clear()
    signal_journal.refresh_forward_results()

    refreshed = signal_journal.list_entries()[0]
    assert refreshed["forward_status"] == "pending"
    assert refreshed["forward_result_pct"] is None


# --------------------------------------------------------------------------- #
# Fix 4d — demo-recorded signals are never measured (even after live data)
# --------------------------------------------------------------------------- #
def test_demo_signal_is_marked_not_measurable_at_record_time(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    close = price_series(days=90)

    entry = signal_journal.record_signal(
        target_kind="instrument",
        target_id="CROSSX",
        target_name="Cross X",
        close=close,  # extends 20 sessions past the input window
        input_close=close.iloc[:70],
        signal={"action": "BUY", "score": 0.3},
        horizon_days=10,
        benchmark="",
        source_counts={"sample": 1},
        eligible_for_real_research=False,
        data_mode="demo",
    )

    assert entry["forward_status"] == signal_journal.NOT_MEASURABLE_STATUS
    assert entry["forward_result_pct"] is None
    assert entry["alpha_pct"] is None


def test_demo_recorded_entry_stays_unmeasured_after_real_history_arrives(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    pending = price_series(days=70)
    signal_journal.record_signal(
        target_kind="instrument",
        target_id="CROSSY",
        target_name="Cross Y",
        close=pending,
        input_close=pending,
        signal={"action": "BUY", "score": 0.3},
        horizon_days=10,
        benchmark="",
        source_counts={"sample": 1},
        eligible_for_real_research=False,
        data_mode="demo",
    )

    # Real uploaded history later covers the original horizon.
    real = price_series(days=90)
    data.register(data.Instrument("CROSSY", "Cross Y", real.to_frame("close"), "upload", []))
    signal_journal.refresh_forward_results()

    entry = signal_journal.list_entries()[0]
    assert entry["forward_status"] == signal_journal.NOT_MEASURABLE_STATUS
    assert entry["forward_result_pct"] is None
    assert entry["alpha_pct"] is None


# --------------------------------------------------------------------------- #
# Fix 4c — headline stats aggregate only real-eligible entries
# --------------------------------------------------------------------------- #
def _entry(**overrides):
    base = {
        "target_kind": "model",
        "target_id": "M1",
        "target_name": "Model One",
        "benchmark": "SPY",
        "action_label": "BUY",
        "score": 0.5,
        "forward_status": "measured",
        "forward_result_pct": 5.0,
        "benchmark_result_pct": 1.0,
        "alpha_pct": 4.0,
        "eligible_for_real_research": True,
        "data_mode": "real",
        "created_at": "2026-01-02T00:00:00",
        "input_end_date": "2026-01-01",
        "source_counts": {"upload": 1},
    }
    base.update(overrides)
    return base


def test_headline_stats_exclude_demo_entries_but_report_them():
    entries = [
        _entry(),
        _entry(
            eligible_for_real_research=False,
            data_mode="demo",
            forward_result_pct=50.0,
            alpha_pct=49.0,
            created_at="2026-01-03T00:00:00",
            input_end_date="2026-01-02",
            source_counts={"sample": 1},
        ),
    ]

    summary = signal_journal.summarize_entries(entries)
    assert summary["total_count"] == 2
    assert summary["measured_count"] == 1
    assert summary["demo_count"] == 1
    assert summary["demo_measured_count"] == 1
    assert summary["avg_alpha_pct"] == 4.0
    assert summary["avg_forward_result_pct"] == 5.0
    assert summary["hit_rate_pct"] == 100.0

    spy = next(row for row in signal_journal.benchmark_comparison(entries) if row["benchmark"] == "SPY")
    assert spy["measured_count"] == 1
    assert spy["avg_alpha_pct"] == 4.0
    assert spy["demo_measured_count"] == 1

    model_row = next(row for row in signal_journal.model_evidence(entries) if row["target_id"] == "M1")
    assert model_row["measured_count"] == 1
    assert model_row["demo_count"] == 1
    assert model_row["avg_alpha_pct"] == 4.0

    drift = signal_journal.drift_series(entries)
    assert drift[-1]["cumulative_measured_count"] == 1
    assert drift[-1]["cumulative_avg_alpha_pct"] == 4.0


def test_benchmark_comparison_reports_demo_only_benchmarks_with_zero_stats():
    entries = [_entry(eligible_for_real_research=False, data_mode="demo", benchmark="QQQ")]

    rows = signal_journal.benchmark_comparison(entries)

    qqq = next(row for row in rows if row["benchmark"] == "QQQ")
    assert qqq["measured_count"] == 0
    assert qqq["demo_measured_count"] == 1
    assert qqq["avg_alpha_pct"] is None
    assert qqq["hit_rate_pct"] is None


# --------------------------------------------------------------------------- #
# Fix 5 — failed model editor save rolls back in-memory holdings
# --------------------------------------------------------------------------- #
def test_failed_model_save_restores_in_memory_holdings(client):
    # HELIOS_DB_PATH=off (conftest default): persistence unavailable, save fails.
    assert persistence.get_store().available is False
    model = portfolio.parse_model_file(
        b"Ticker,Weight\nAAPL,60\nMSFT,40\n", "rollback.csv", "Rollback Model", "balanced", "")

    resp = client.post(
        f"/api/models/{model.id}/editor",
        json={
            "change_note": "Attempted edit while persistence is down.",
            "holdings": [
                {"ticker": "AAPL", "weight_pct": 10},
                {"ticker": "MSFT", "weight_pct": 90},
            ],
        },
    )

    assert resp.status_code == 400
    body = client.get("/api/models").get_json()
    row = next(item for item in body["models"] if item["id"] == model.id)
    weights = {holding["ticker"]: holding["weight_pct"] for holding in row["holdings"]}
    assert weights == {"AAPL": 60.0, "MSFT": 40.0}


# --------------------------------------------------------------------------- #
# Fix 6 — core.py hardening
# --------------------------------------------------------------------------- #
def _basic_auth_header(username: str, password: str) -> dict:
    import base64

    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture()
def auth_client(monkeypatch):
    monkeypatch.setattr(web_core, "AUTH_ENABLED", True)
    monkeypatch.setattr(web_core, "AUTH_USER", "advisor")
    monkeypatch.setattr(web_core, "AUTH_PASSWORD", "correct-horse-battery")
    monkeypatch.setattr(web_core, "_AUTH_FAILURE_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(web_core, "_AUTH_FAILURES", {})
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def test_unauthenticated_api_requests_get_json_error_envelope(auth_client):
    resp = auth_client.get("/api/tickers")

    assert resp.status_code == 401
    assert resp.is_json
    assert resp.get_json()["error"]
    assert resp.headers["WWW-Authenticate"].startswith("Basic")


def test_unauthenticated_page_requests_keep_browser_prompt(auth_client):
    resp = auth_client.get("/")

    assert resp.status_code == 401
    assert not resp.is_json
    assert resp.headers["WWW-Authenticate"].startswith("Basic")


def test_lockout_response_is_json_on_api_paths(auth_client):
    for _ in range(web_core._AUTH_LOCKOUT_THRESHOLD):
        auth_client.get("/api/tickers", headers=_basic_auth_header("advisor", "wrong"))

    locked = auth_client.get("/api/tickers", headers=_basic_auth_header("advisor", "wrong"))

    assert locked.status_code == 429
    assert locked.is_json
    assert "try again" in locked.get_json()["error"].lower()


def test_cross_site_api_post_rejected_with_json_403(client):
    resp = client.post("/api/live", json={"symbol": "AAPL"},
                       headers={"Sec-Fetch-Site": "cross-site"})

    assert resp.status_code == 403
    assert resp.is_json
    assert resp.get_json()["error"] == "Forbidden."


def test_empty_or_whitespace_password_env_is_treated_as_unset():
    for raw in (None, "", "   ", "\n\t"):
        password, generated = web_core._resolve_auth_password(raw)
        assert generated is True
        assert password.strip()
    assert web_core._resolve_auth_password("chosen-secret") == ("chosen-secret", False)


def test_generated_password_from_empty_env_authenticates(monkeypatch):
    password, generated = web_core._resolve_auth_password("")
    assert generated is True
    monkeypatch.setattr(web_core, "AUTH_ENABLED", True)
    monkeypatch.setattr(web_core, "AUTH_USER", "advisor")
    monkeypatch.setattr(web_core, "AUTH_PASSWORD", password)
    monkeypatch.setattr(web_core, "_AUTH_FAILURE_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(web_core, "_AUTH_FAILURES", {})
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    client = helios.app.test_client()

    ok_resp = client.get("/api/tickers", headers=_basic_auth_header("advisor", password))
    assert ok_resp.status_code == 200
    # The literal blank password from the env must NOT authenticate.
    blank = client.get("/api/tickers", headers=_basic_auth_header("advisor", ""))
    assert blank.status_code == 401


def test_clean_converts_numpy_bool_to_python_bool():
    cleaned = web_core.clean({"x": np.bool_(True), "y": [np.bool_(False)]})

    assert cleaned == {"x": True, "y": [False]}
    assert isinstance(cleaned["x"], bool)
    assert isinstance(cleaned["y"][0], bool)
    json.dumps(cleaned)  # round-trips without a serializer error

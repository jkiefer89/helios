from io import BytesIO

import pandas as pd
import pytest

import app as helios
from engine import data, persistence, portfolio
from tests.conftest import price_csv, price_series


@pytest.fixture()
def client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def test_tickers_endpoint_returns_sample_universe(client):
    resp = client.get("/api/tickers")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["tickers"]
    assert any(t["symbol"] == "AAPL" for t in body["tickers"])


def test_mandates_endpoint_returns_presets(client):
    resp = client.get("/api/mandates")

    assert resp.status_code == 200
    assert any(m["key"] == "balanced" for m in resp.get_json()["mandates"])


def test_analyze_valid_sample_ticker(client):
    resp = client.get("/api/analyze?ticker=AAPL&horizon=10")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["symbol"] == "AAPL"
    assert body["forecast"]["horizon_days"] == 10


def test_analyze_invalid_ticker_returns_json_error(client):
    resp = client.get("/api/analyze?ticker=NOT_A_SAMPLE&horizon=10")

    assert resp.status_code == 404
    assert "error" in resp.get_json()


def test_analyze_invalid_horizon_returns_400_json_not_500(client):
    resp = client.get("/api/analyze?ticker=AAPL&horizon=banana")

    assert resp.status_code == 400
    assert resp.is_json
    assert "horizon" in resp.get_json()["error"].lower()


def test_not_found_routes_return_json(client):
    resp = client.get("/api/nope")

    assert resp.status_code == 404
    assert resp.is_json
    assert "error" in resp.get_json()


def test_favicon_request_returns_no_content_for_browser_noise(client):
    resp = client.get("/favicon.ico")

    assert resp.status_code == 204
    assert resp.data == b""


def test_live_malformed_json_returns_json_error(client, monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", True)
    resp = client.post("/api/live", data="{bad", content_type="application/json")

    assert resp.status_code == 400
    assert resp.is_json
    assert "error" in resp.get_json()


def test_auto_live_config_defaults_to_core_universe(monkeypatch):
    monkeypatch.delenv("HELIOS_AUTO_LIVE_SYMBOLS", raising=False)
    monkeypatch.setenv("HELIOS_DB_PATH", ".helios/test-auto-live.db")

    config = helios._auto_live_config()

    assert config["enabled"] is True
    assert config["source"] == "core"
    assert {"SPY", "QQQ", "AAPL"} <= set(config["symbols"])


def test_auto_live_config_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("HELIOS_AUTO_LIVE_SYMBOLS", "off")

    config = helios._auto_live_config()

    assert config["enabled"] is False
    assert config["symbols"] == []
    assert config["source"] == "off"


def test_auto_live_config_parses_core_universe_and_interval(monkeypatch):
    monkeypatch.setenv("HELIOS_AUTO_LIVE_SYMBOLS", "core")
    monkeypatch.setenv("HELIOS_AUTO_LIVE_REFRESH_SECONDS", "30")
    monkeypatch.setenv("HELIOS_AUTO_LIVE_MAX_WORKERS", "99")
    monkeypatch.setenv("HELIOS_AUTO_LIVE_PERIOD", "1y")

    config = helios._auto_live_config()

    assert config["enabled"] is True
    assert config["period"] == "1y"
    assert config["interval_seconds"] == 60
    assert config["max_workers"] == 16
    assert {"SPY", "QQQ", "AAPL"} <= set(config["symbols"])


def test_data_status_includes_auto_live_configuration(client, monkeypatch):
    monkeypatch.setenv("HELIOS_AUTO_LIVE_SYMBOLS", "SPY, QQQ")
    monkeypatch.setenv("HELIOS_AUTO_LIVE_REFRESH_SECONDS", "120")

    resp = client.get("/api/data/status")

    assert resp.status_code == 200
    auto_live = resp.get_json()["auto_live"]
    assert auto_live["enabled"] is True
    assert auto_live["symbols"] == ["SPY", "QQQ"]
    assert auto_live["interval_seconds"] == 120
    assert auto_live["max_workers"] == 6


def test_data_quality_dashboard_reports_institutional_issue_categories(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    store = persistence.get_store()
    assert store.available is True

    short_live = pd.DataFrame({"close": price_series(days=45).values}, index=price_series(days=45).index)
    data.register(data.Instrument("SHORTLIVE", "Short Live", short_live, "live", []))
    store.record_refresh(
        symbol="SHORTLIVE",
        status="error",
        rows_added=0,
        message="upstream timeout during quality check",
        source="live",
    )
    upload = client.post(
        "/api/upload",
        data={
            "file": (BytesIO(price_csv(days=260)), "quality-upload.csv"),
            "symbol": "QUALUP",
            "name": "Quality Upload",
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    model_upload = client.post(
        "/api/model/upload",
        data={
            "file": (BytesIO(b"Ticker,Weight\nSHORTLIVE,35\nAAPL,35\nMISSINGQ,30\n"), "quality-model.csv"),
            "name": "Quality Model",
            "mandate": "balanced",
        },
        content_type="multipart/form-data",
    )
    assert model_upload.status_code == 200

    resp = client.get("/api/data-quality")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["research_ready"] is False
    assert body["summary"]["issue_count"] >= 5
    assert body["summary"]["research_ready_count"] >= 1
    assert body["thresholds"]["min_research_rows"] == 60
    categories = {issue["category"] for issue in body["issues"]}
    assert {
        "stale_symbols",
        "short_histories",
        "refresh_failures",
        "missing_data",
        "coverage_gaps",
        "source_conflicts",
    } <= categories
    assert any(row["symbol"] == "SHORTLIVE" and row["is_stale"] for row in body["symbols"])
    assert any(row["symbol"] == "SHORTLIVE" and not row["research_ready"] for row in body["symbols"])
    assert any(row["symbol"] == "MISSINGQ" for row in body["missing_data"])
    assert any(row["model_name"] == "Quality Model" for row in body["coverage_gaps"])
    assert any(row["symbol"] == "SHORTLIVE" for row in body["refresh_failures"])
    assert "Analysis only" in body["disclaimer"]


def test_data_quality_thresholds_are_configurable_and_refresh_observability_is_explicit(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    monkeypatch.setenv("HELIOS_DATA_QUALITY_STALE_DAYS", "999")
    monkeypatch.setenv("HELIOS_DATA_QUALITY_MIN_RESEARCH_ROWS", "100")
    monkeypatch.setenv("HELIOS_DATA_QUALITY_INSTITUTIONAL_ROWS", "300")
    persistence.reset_store_for_tests()

    live_history = price_series(days=260)
    data.register(data.Instrument("OBSERVE", "Observed Live", pd.DataFrame({"close": live_history}), "live", []))

    resp = client.get("/api/data-quality")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["thresholds"] == {
        "stale_days": 999,
        "min_research_rows": 100,
        "institutional_history_rows": 300,
    }
    assert body["threshold_config"]["source"] == "environment"
    assert body["summary"]["refresh_observability_gap_count"] >= 1
    categories = {issue["category"] for issue in body["issues"]}
    assert "refresh_observability_gaps" in categories
    observe = next(row for row in body["symbols"] if row["symbol"] == "OBSERVE")
    assert observe["research_ready"] is True
    assert observe["is_stale"] is False
    assert observe["is_short"] is True
    assert observe["freshness_basis"] == "price_history_last_date"
    assert observe["refresh_evidence"]["has_refresh_log"] is False
    assert any(row["symbol"] == "OBSERVE" for row in body["refresh_observability_gaps"])
    assert body["refresh_observability"]["gap_count"] >= 1


def test_data_quality_alerts_track_active_and_resolved_research_readiness(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    monkeypatch.setenv("HELIOS_DATA_QUALITY_STALE_DAYS", "999")
    persistence.reset_store_for_tests()
    store = persistence.get_store()
    assert store.available is True

    data.parse_csv(price_csv(days=260), "REAL1", "Real One", source_filename="real-one.csv")
    model_upload = client.post(
        "/api/model/upload",
        data={
            "file": (BytesIO(b"Ticker,Weight\nREAL1,60\nNEEDPRICE,40\n"), "alert-model.csv"),
            "name": "Alert Model",
            "mandate": "balanced",
        },
        content_type="multipart/form-data",
    )
    assert model_upload.status_code == 200

    first = client.get("/api/data-quality")

    assert first.status_code == 200
    first_body = first.get_json()
    first_alerts = first_body["alerts"]
    assert first_alerts["tracking_available"] is True
    assert first_alerts["summary"]["active_count"] >= 3
    assert first_alerts["summary"]["new_count"] >= 3
    active_categories = {alert["category"] for alert in first_alerts["active"]}
    assert {"coverage_gaps", "missing_data", "research_readiness"} <= active_categories
    readiness_alert = next(alert for alert in first_alerts["active"] if alert["category"] == "research_readiness")
    assert readiness_alert["status"] == "active"
    assert readiness_alert["first_seen_at"]
    assert readiness_alert["last_seen_at"]
    assert readiness_alert["occurrence_count"] == 1
    assert "research-ready" in readiness_alert["detail"].lower()

    second = client.get("/api/data-quality")

    assert second.status_code == 200
    second_alerts = second.get_json()["alerts"]
    assert second_alerts["summary"]["new_count"] == 0
    assert next(alert for alert in second_alerts["active"] if alert["id"] == readiness_alert["id"])["occurrence_count"] == 2

    data.parse_csv(price_csv(days=260), "NEEDPRICE", "Need Price", source_filename="need-price.csv")

    resolved = client.get("/api/data-quality")

    assert resolved.status_code == 200
    resolved_body = resolved.get_json()
    assert resolved_body["research_ready"] is True
    resolved_alerts = resolved_body["alerts"]
    assert resolved_alerts["summary"]["resolved_count"] >= 3
    resolved_categories = {alert["category"] for alert in resolved_alerts["resolved"]}
    assert {"coverage_gaps", "missing_data", "research_readiness"} <= resolved_categories
    resolved_readiness = next(alert for alert in resolved_alerts["resolved"] if alert["id"] == readiness_alert["id"])
    assert resolved_readiness["status"] == "resolved"
    assert resolved_readiness["resolved_at"]
    assert resolved_alerts["summary"]["active_count"] == 0


def test_model_upload_and_analyze_smoke(client, monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", False)
    payload = {
        "file": (BytesIO(b"Ticker,Weight\nAAPL,60\nMSFT,40\n"), "model.csv"),
        "name": "Smoke Model",
        "mandate": "balanced",
        "context": "offline test",
    }

    upload = client.post("/api/model/upload", data=payload, content_type="multipart/form-data")
    assert upload.status_code == 200
    model_id = upload.get_json()["id"]

    analyze = client.get(f"/api/model/analyze?id={model_id}&horizon=21")
    assert analyze.status_code == 200
    body = analyze.get_json()
    assert body["id"] == model_id
    assert body["holdings"]
    assert body["forecast"]["horizon_days"] == 21


def test_model_analyze_invalid_horizon_returns_400_json_not_fallback(client, monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", False)
    payload = {
        "file": (BytesIO(b"Ticker,Weight\nAAPL,60\nMSFT,40\n"), "model.csv"),
        "name": "Bad Horizon Model",
        "mandate": "balanced",
    }
    upload = client.post("/api/model/upload", data=payload, content_type="multipart/form-data")
    assert upload.status_code == 200

    resp = client.get(f"/api/model/analyze?id={upload.get_json()['id']}&horizon=banana")

    assert resp.status_code == 400
    assert resp.is_json
    assert "horizon" in resp.get_json()["error"].lower()


def test_model_analyze_unavailable_long_horizon_clears_stale_label(client, monkeypatch):
    mdl = portfolio.Model(
        id="SHORT-HISTORY",
        name="Short History",
        mandate_key="balanced",
        mandate_context="",
        holdings=[portfolio.Holding("AAPL", 1.0)],
    )
    ps = portfolio.PortfolioSeries(
        close=price_series(130),
        holdings=[{
            "ticker": "AAPL",
            "weight": 1.0,
            "source": "sample",
            "window_return_pct": 0.0,
            "mrc_pct": 100.0,
            "excluded": False,
            "note": "",
        }],
        n_days=130,
        sources={"sample": 1},
        warnings=[],
        provenance={
            "n_holdings": 1,
            "n_kept": 1,
            "n_excluded": 0,
            "excluded": [],
            "n_live": 0,
            "n_sample": 1,
            "n_simulated": 0,
            "simulated_weight_pct": 0.0,
            "simulated_symbols": [],
            "honesty": "real",
        },
    )
    monkeypatch.setattr(portfolio, "get", lambda _model_id: mdl)
    monkeypatch.setattr(portfolio, "build_series", lambda _model: ps)

    resp = client.get("/api/model/analyze?id=SHORT-HISTORY&horizon=5Y")

    assert resp.status_code == 200
    horizon = resp.get_json()["horizon"]
    assert horizon["kind"] == "short"
    assert horizon["value"] == 21
    assert horizon["label"] is None


# --------------------------------------------------------------------------- #
# CSRF protection (Sec-Fetch-Site / Origin checks in the before_request hook)
# --------------------------------------------------------------------------- #
def test_cross_site_post_is_rejected_with_generic_403(client):
    resp = client.post("/api/live", json={"symbol": "AAPL"},
                       headers={"Sec-Fetch-Site": "cross-site"})

    assert resp.status_code == 403
    assert b"Forbidden" in resp.data


def test_same_origin_and_direct_posts_are_allowed(client, monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", False)
    for headers in ({}, {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}):
        resp = client.post("/api/live", json={"symbol": "AAPL"}, headers=headers)
        # Reaches the handler (yfinance unavailable), not the CSRF gate.
        assert resp.status_code == 400
        assert "yfinance" in resp.get_json()["error"]


def test_mismatched_origin_post_is_rejected(client):
    resp = client.post("/api/live", json={"symbol": "AAPL"},
                       headers={"Origin": "https://evil.example"})

    assert resp.status_code == 403


def test_matching_origin_post_is_allowed(client, monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", False)
    resp = client.post("/api/live", json={"symbol": "AAPL"},
                       headers={"Origin": "http://localhost"})

    assert resp.status_code == 400
    assert "yfinance" in resp.get_json()["error"]


def test_cross_site_get_requests_remain_allowed(client):
    resp = client.get("/api/tickers", headers={"Sec-Fetch-Site": "cross-site"})

    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Basic-auth brute-force throttle
# --------------------------------------------------------------------------- #
def _basic_auth_header(username: str, password: str) -> dict:
    import base64

    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture()
def auth_client(monkeypatch):
    monkeypatch.setattr(helios, "AUTH_ENABLED", True)
    monkeypatch.setattr(helios, "AUTH_USER", "advisor")
    monkeypatch.setattr(helios, "AUTH_PASSWORD", "correct-horse-battery")
    monkeypatch.setattr(helios, "_AUTH_FAILURE_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(helios, "_AUTH_FAILURES", {})
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def test_failed_basic_auth_sleeps_and_logs(auth_client, monkeypatch, caplog):
    sleeps = []
    monkeypatch.setattr(helios, "_AUTH_FAILURE_DELAY_SECONDS", 0.3)
    monkeypatch.setattr(helios.time, "sleep", sleeps.append)

    with caplog.at_level("WARNING"):
        resp = auth_client.get("/api/tickers", headers=_basic_auth_header("advisor", "wrong"))

    assert resp.status_code == 401
    assert sleeps == [0.3]
    assert any("Failed Basic-auth" in record.message for record in caplog.records)


def test_missing_credentials_challenge_does_not_count_as_failure(auth_client):
    resp = auth_client.get("/api/tickers")

    assert resp.status_code == 401
    assert helios._AUTH_FAILURES == {}


def test_repeated_auth_failures_lock_out_the_remote_ip(auth_client):
    for _ in range(helios._AUTH_LOCKOUT_THRESHOLD):
        assert auth_client.get(
            "/api/tickers", headers=_basic_auth_header("advisor", "wrong")
        ).status_code == 401

    locked = auth_client.get("/api/tickers", headers=_basic_auth_header("advisor", "wrong"))
    assert locked.status_code == 429

    # Even correct credentials are refused while the lockout is active.
    still_locked = auth_client.get(
        "/api/tickers", headers=_basic_auth_header("advisor", "correct-horse-battery"))
    assert still_locked.status_code == 429


def test_lockout_expires_and_success_clears_failures(auth_client, monkeypatch):
    for _ in range(helios._AUTH_LOCKOUT_THRESHOLD):
        auth_client.get("/api/tickers", headers=_basic_auth_header("advisor", "wrong"))
    monkeypatch.setattr(helios, "_AUTH_LOCKOUT_SECONDS", 0.0)

    resp = auth_client.get(
        "/api/tickers", headers=_basic_auth_header("advisor", "correct-horse-battery"))

    assert resp.status_code == 200
    assert helios._AUTH_FAILURES == {}


# --------------------------------------------------------------------------- #
# Upload guardrails
# --------------------------------------------------------------------------- #
def test_upload_is_rejected_when_parse_semaphore_is_saturated(client):
    make_csv = price_csv
    acquired = []
    while helios._UPLOAD_SEMAPHORE.acquire(blocking=False):
        acquired.append(True)
    try:
        resp = client.post(
            "/api/upload",
            data={"file": (BytesIO(make_csv(days=90)), "busy.csv"), "symbol": "BUSY"},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 429
        assert "busy" in resp.get_json()["error"].lower()
    finally:
        for _ in acquired:
            helios._UPLOAD_SEMAPHORE.release()

    ok_resp = client.post(
        "/api/upload",
        data={"file": (BytesIO(make_csv(days=90)), "busy.csv"), "symbol": "BUSY"},
        content_type="multipart/form-data",
    )
    assert ok_resp.status_code == 200


def test_analyze_sanitizes_ticker_and_requires_symbol(client):
    lower = client.get("/api/analyze?ticker=aapl&horizon=10")
    assert lower.status_code == 200
    assert lower.get_json()["symbol"] == "AAPL"

    injected = client.get("/api/analyze?ticker=aa%20pl%24%0a&horizon=10")
    assert injected.status_code == 200
    assert injected.get_json()["symbol"] == "AAPL"

    missing = client.get("/api/analyze")
    assert missing.status_code == 400
    assert "ticker" in missing.get_json()["error"].lower()


# --------------------------------------------------------------------------- #
# CSP scoping — CDN exception only for the legacy dashboard pages
# --------------------------------------------------------------------------- #
def test_csp_allows_cdn_only_on_legacy_dashboard(client):
    legacy = client.get("/legacy")
    assert "https://cdn.jsdelivr.net" in legacy.headers["Content-Security-Policy"]

    api = client.get("/api/tickers")
    csp = api.headers["Content-Security-Policy"]
    assert "cdn.jsdelivr.net" not in csp
    assert "script-src 'self';" in csp

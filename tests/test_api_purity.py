"""Read endpoints render retained evidence and never acquire or record data."""
from __future__ import annotations

import app as helios

from engine import data, portfolio
from tests.conftest import price_csv


def _forbidden(name: str):
    def fail(*_args, **_kwargs):
        raise AssertionError(f"GET attempted forbidden side effect: {name}")

    return fail


def test_research_gets_do_not_fetch_sync_or_record(monkeypatch):
    from engine import fundamentals, holdings, macro_events, persistence, sec_events, signal_journal

    data.parse_csv(price_csv(days=260), "PURE", "Pure Read", source_filename="pure.csv")
    portfolio.register(portfolio.Model(
        id="PURE-MODEL",
        name="Pure Model",
        mandate_key="balanced",
        mandate_context="",
        holdings=[portfolio.Holding("PURE", 1.0)],
    ))

    monkeypatch.setattr(fundamentals, "fetch", _forbidden("fundamentals.fetch"))
    monkeypatch.setattr(holdings, "fetch_lookthrough", _forbidden("holdings.fetch_lookthrough"))
    monkeypatch.setattr(macro_events, "macro_snapshot", _forbidden("macro_events.macro_snapshot"))
    monkeypatch.setattr(sec_events, "events_for", _forbidden("sec_events.events_for"))
    monkeypatch.setattr(signal_journal, "record_signal", _forbidden("signal_journal.record_signal"))
    monkeypatch.setattr(
        persistence.SQLiteStore,
        "sync_data_quality_alerts",
        _forbidden("sync_data_quality_alerts"),
    )

    client = helios.app.test_client()
    routes = [
        "/api/analyze?ticker=PURE",
        "/api/model/analyze?id=PURE-MODEL",
        "/api/report/instrument?ticker=PURE",
        "/api/report/model?id=PURE-MODEL",
        "/api/lookthrough?ticker=PURE",
        "/api/model/lookthrough?id=PURE-MODEL",
        "/api/macro",
        "/api/data-quality",
        "/api/providers",
        "/api/operations/status",
        "/api/security/status",
        "/api/trials",
        "/api/trials/missing-trial",
        "/api/models/PURE-MODEL/independent-validation",
        "/api/model-governance/PURE-MODEL/approval-packet",
        "/api/research-context?target_id=PURE",
    ]
    for route in routes:
        response = client.get(route)
        assert response.status_code in {200, 404, 503}, (route, response.get_json())


def test_side_effecting_research_routes_reject_get():
    client = helios.app.test_client()
    for route in [
        "/api/data-quality/sync",
        "/api/data/price-reconciliation?ticker=SPY",
        "/api/lookthrough/refresh?ticker=SPY",
        "/api/model/lookthrough/refresh?id=missing",
        "/api/model/forward?id=missing",
        "/api/signals/record",
        "/api/model/signals/record",
    ]:
        assert client.get(route).status_code in {404, 405}, route

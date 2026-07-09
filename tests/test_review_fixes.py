"""Regression locks for the 2026-07 multi-agent review findings.

Each test pins the CORRECT behavior for a verified data-corruption bug so it
can never silently regress: wrong units, wrong-entity joins, stale dates, and
cache poisoning in the fundamentals/FIGI/macro spine.
"""
from __future__ import annotations

import datetime

import pytest

from engine import cma, figi, fundamentals, macro


# --------------------------------------------------------------------------- #
# Finding 1: yfinance debtToEquity is ALWAYS percent — no >20 heuristic.
# --------------------------------------------------------------------------- #
def test_yfinance_debt_to_equity_always_normalized(monkeypatch):
    class FakeTicker:
        info = {"debtToEquity": 6.555, "dividendYield": 0.4, "sector": "Technology"}

    class FakeYF:
        Ticker = staticmethod(lambda symbol: FakeTicker())

    monkeypatch.setattr(fundamentals.data, "HAS_YF", True)
    monkeypatch.setattr(fundamentals.data, "_yf", FakeYF())
    raw = fundamentals._yfinance_provider("NVDA")
    assert raw["debt_to_equity"] == pytest.approx(0.06555)  # 6.555% -> 0.066x, NOT 6.5x


# --------------------------------------------------------------------------- #
# Finding 2: earnings_growth is a fraction — >150% values must survive intact
# (the CMA growth cap clips at the scoring layer, not the ingestion layer).
# --------------------------------------------------------------------------- #
def test_earnings_growth_over_150pct_not_divided():
    f = fundamentals.fetch("HYPER", provider=lambda t: {
        "earnings_growth": 5.8, "forward_pe": 30.0, "sector": "technology", "source": "fake"})
    assert f.earnings_growth == pytest.approx(5.8)          # not 0.058
    fwd = cma.instrument_forward("HYPER", f)
    # The CMA clips to its documented +25% growth cap — honest, not corrupted.
    assert fwd["blocks_pct"]["earnings_growth"] == pytest.approx(25.0)


# --------------------------------------------------------------------------- #
# Finding 3: OpenFIGI jobs constrain to the US composite equity line.
# --------------------------------------------------------------------------- #
def test_figi_request_constrains_to_us_equity():
    captured = {}

    def fake_post(url, headers, body):
        import json
        captured["jobs"] = json.loads(body)
        return [{"data": [{"ticker": "CNQ"}]}]

    figi.set_default_post(fake_post)
    try:
        out = figi.map_cusips(["136385101"], http_post=fake_post)
        job = captured["jobs"][0]
        assert job["exchCode"] == "US"
        assert job["marketSecDes"] == "Equity"
        assert out == {"136385101": "CNQ"}
    finally:
        figi.set_default_post(None)


# --------------------------------------------------------------------------- #
# Finding 7: transient FIGI failures back off and stay retryable (no cache
# poisoning), then succeed after the cooldown.
# --------------------------------------------------------------------------- #
def test_figi_transient_failure_not_cached(monkeypatch):
    calls = {"n": 0}

    def flaky_post(url, headers, body):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("429 rate limited")
        return [{"data": [{"ticker": "AAPL"}]}]

    figi.set_default_post(flaky_post)
    try:
        assert figi.map_cusips(["037833100"], http_post=flaky_post) == {}
        # Within the cooldown window nothing is retried...
        assert figi.map_cusips(["037833100"], http_post=flaky_post) == {}
        assert calls["n"] == 1
        # ...after the cooldown the SAME cusip maps (was never poisoned).
        monkeypatch.setattr(figi, "_BACKOFF_UNTIL", 0.0)
        assert figi.map_cusips(["037833100"], http_post=flaky_post) == {"037833100": "AAPL"}
    finally:
        figi.set_default_post(None)


# --------------------------------------------------------------------------- #
# Finding 4: next-earnings must be today or later — stale unfilled past
# report dates are not "upcoming".
# --------------------------------------------------------------------------- #
def test_next_earnings_ignores_stale_past_dates():
    yesterday = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    future = (datetime.date.today() + datetime.timedelta(days=20)).isoformat()
    rows = [
        {"date": yesterday, "epsActual": None},   # FMP hasn't backfilled — NOT upcoming
        {"date": future, "epsActual": None},
    ]
    assert fundamentals._fmp_next_earnings(rows) == future


# --------------------------------------------------------------------------- #
# Finding 5: past-only estimate rows never produce "forward" figures.
# --------------------------------------------------------------------------- #
def test_fmp_forward_returns_nothing_for_past_only_estimates():
    past_rows = [{"date": "2001-12-31", "epsAvg": 4.0}, {"date": "2003-12-31", "epsAvg": 6.0}]
    forward_pe, growth = fundamentals._fmp_forward(past_rows, price=100.0)
    assert forward_pe is None and growth is None


# --------------------------------------------------------------------------- #
# Finding 6: unusable fetches are negative-cached briefly (no repeated stalls)
# but a recovered upstream is re-probed after the short TTL.
# --------------------------------------------------------------------------- #
def test_empty_fundamentals_negative_cached(monkeypatch):
    calls = {"n": 0}

    def empty_provider(t):
        calls["n"] += 1
        return {}

    fundamentals.invalidate_cache()
    fundamentals.set_default_provider(empty_provider)
    try:
        assert fundamentals.fetch("GHOST").source == "none"
        assert fundamentals.fetch("GHOST").source == "none"
        assert calls["n"] == 1                      # second call served from negative cache
        # After the short TTL expires, the upstream is probed again.
        key = "GHOST"
        ts, result = fundamentals._FETCH_CACHE[key]
        fundamentals._FETCH_CACHE[key] = (ts - fundamentals._FETCH_CACHE_EMPTY_TTL_S - 1, result)
        fundamentals.fetch("GHOST")
        assert calls["n"] == 2
    finally:
        fundamentals.set_default_provider(None)
        fundamentals.invalidate_cache()


# --------------------------------------------------------------------------- #
# Finding 8: repo sleeves are cash-like; bond labels hit the debt branch.
# --------------------------------------------------------------------------- #
def test_asset_class_anchors_for_repo_and_bond_labels():
    rf = macro.risk_free()
    assert macro.asset_class_return("Repurchase agreement") == pytest.approx(rf)
    assert macro.asset_class_return("Bond") == pytest.approx(rf + 0.005)
    assert macro.asset_class_return("Fixed income") == pytest.approx(rf + 0.005)
    # Unknown sleeves still earn the coarse half-ERP premium (unchanged).
    assert macro.asset_class_return("Derivatives") > rf + 0.005

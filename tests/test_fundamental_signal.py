"""Dual-track (tactical/strategic) signal + instrument-level CMA tests.

All offline: fundamentals are constructed directly, news uses an injected HTTP
fake, and price series are synthetic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import cma, forecast, fundamentals, mandate, news, signals


def _close(n=300, drift=0.05, seed=7) -> pd.Series:
    idx = pd.bdate_range("2024-01-02", periods=n)
    rng = np.random.default_rng(seed)
    steps = rng.normal(drift / 252, 0.01, n)
    return pd.Series(100 * np.exp(np.cumsum(steps)), index=idx)


def _fnd(**kw) -> fundamentals.Fundamentals:
    base = dict(ticker="TEST", dividend_yield=0.02, forward_pe=14.0,
                earnings_growth=0.09, sector="financials", source="test")
    base.update(kw)
    return fundamentals.Fundamentals(**base)


_SENT = {"aggregate_score": 0.0, "aggregate_label": "neutral", "count": 0}


# --------------------------------------------------------------------------- #
# cma.instrument_forward
# --------------------------------------------------------------------------- #
def test_instrument_forward_building_blocks():
    fwd = cma.instrument_forward("TEST", _fnd())
    assert fwd["usable"] is True
    assert fwd["basis"] == "fundamentals"
    # dy 2% + growth 9% + reversion ln(13/14)/5y ~ -1.48% => ~9.5%
    assert fwd["expected_return_pct"] == pytest.approx(9.52, abs=0.05)
    assert fwd["gap_vs_anchor_pct"] == pytest.approx(fwd["expected_return_pct"] - fwd["anchor_pct"], abs=0.01)


def test_instrument_forward_unusable_without_fundamentals():
    fwd = cma.instrument_forward("TEST", fundamentals.Fundamentals(ticker="TEST", source="none"))
    assert fwd["usable"] is False
    assert "expected_return_pct" not in fwd


def test_instrument_forward_analyst_context_is_optional():
    fwd = cma.instrument_forward("TEST", _fnd(current_price=100.0, target_mean_price=112.0, n_analysts=9))
    assert fwd["analyst"]["implied_upside_pct"] == pytest.approx(12.0, abs=0.01)
    assert "analyst" not in cma.instrument_forward("TEST", _fnd())


# --------------------------------------------------------------------------- #
# signals.evaluate — dual track
# --------------------------------------------------------------------------- #
def test_legacy_output_unchanged_without_fundamentals():
    close = _close()
    fc = forecast.forecast(close, horizon=21, n_paths=200)
    # Pin measured-edge quality: this asserts the legacy weight layout, and
    # the synthetic series' real walk-forward accuracy would otherwise gate
    # the forecast weight (covered in tests/test_phase1_validation.py).
    fc = {**fc, "quality": {"directional_accuracy": 0.60, "n_test": 100}}
    sig = signals.evaluate(close, fc, _SENT)
    names = [c["name"] for c in sig["components"]]
    assert names == ["trend", "momentum", "forecast", "sentiment"]
    assert sig["strategic"]["usable"] is False
    assert any("technical blend only" in c for c in sig["caveats"])
    # Effective weights are exactly the legacy base weights.
    weights = {c["name"]: c["effective_weight"] for c in sig["components"]}
    assert weights == pytest.approx(signals.WEIGHTS)


def test_fundamentals_component_joins_blend_and_weights_renormalize():
    close = _close()
    fc = forecast.forecast(close, horizon=21, n_paths=200)
    # Pin measured-edge quality: this test asserts the UNGATED weight algebra;
    # the edge gate (below-chance forecasts earn zero weight) is covered in
    # tests/test_phase1_validation.py.
    fc = {**fc, "quality": {"directional_accuracy": 0.60, "n_test": 100}}
    fwd = cma.instrument_forward("TEST", _fnd())
    sig = signals.evaluate(close, fc, _SENT, fundamental_result=fwd)
    weights = {c["name"]: c["effective_weight"] for c in sig["components"]}
    share = mandate.fundamentals_share(mandate.DEFAULT)
    assert weights["fundamentals"] == pytest.approx(share)
    assert sum(weights.values()) == pytest.approx(1.0)
    for name in ("trend", "momentum", "forecast", "sentiment"):
        assert weights[name] == pytest.approx(signals.WEIGHTS[name] * (1 - share))
    assert sig["strategic"]["usable"] is True
    assert "fundamentals" in {c["name"] for c in sig["components"]}


def test_strategic_track_is_horizon_independent():
    """The strategic action must not move when only the chart horizon changes."""
    close = _close()
    fwd = cma.instrument_forward("TEST", _fnd())
    strategic_actions = set()
    for horizon in (5, 21, 90):
        fc = forecast.forecast(close, horizon=horizon, n_paths=200)
        sig = signals.evaluate(close, fc, _SENT, fundamental_result=fwd)
        strategic_actions.add(sig["strategic"]["action"])
        assert sig["strategic"]["expected_return_pct"] == fwd["expected_return_pct"]
    assert len(strategic_actions) == 1


def test_rich_fundamentals_tilt_toward_buy_and_poor_toward_sell():
    close = _close(drift=0.0)
    fc = forecast.forecast(close, horizon=21, n_paths=200)
    cheap = cma.instrument_forward("CHEAP", _fnd(dividend_yield=0.04, earnings_growth=0.12, forward_pe=10.0))
    rich = cma.instrument_forward("RICH", _fnd(dividend_yield=0.0, earnings_growth=-0.05, forward_pe=60.0))
    sig_cheap = signals.evaluate(close, fc, _SENT, fundamental_result=cheap)
    sig_rich = signals.evaluate(close, fc, _SENT, fundamental_result=rich)
    assert sig_cheap["strategic"]["action"] == "BUY"
    assert sig_rich["strategic"]["action"] == "SELL"
    assert sig_cheap["score"] > sig_rich["score"]


def test_headline_reports_track_divergence():
    close = _close(drift=0.30, seed=3)  # strong uptrend => tactical BUY
    fc = forecast.forecast(close, horizon=21, n_paths=200)
    rich = cma.instrument_forward("RICH", _fnd(dividend_yield=0.0, earnings_growth=-0.05, forward_pe=60.0))
    sig = signals.evaluate(close, fc, _SENT, fundamental_result=rich)
    if sig["tactical"]["action"] != sig["strategic"]["action"]:
        assert "Tracks diverge" in sig["headline_rationale"]


# --------------------------------------------------------------------------- #
# forecast_long anchor override
# --------------------------------------------------------------------------- #
def test_forecast_long_accepts_cma_anchor():
    close = _close(n=400)
    generic = forecast.forecast_long(close, 1260, "balanced")
    anchored = forecast.forecast_long(close, 1260, "balanced",
                                      anchor_return=0.12, anchor_basis="lookthrough_cma_blend")
    assert generic["params"]["anchor_basis"] == "mandate_capm"
    assert anchored["params"]["anchor_basis"] == "lookthrough_cma_blend"
    assert anchored["params"]["mu_anchor_pct"] == pytest.approx(12.0)
    # A higher anchor must not lower the long-run drift.
    assert anchored["params"]["mu_long_pct"] >= generic["params"]["mu_long_pct"]


# --------------------------------------------------------------------------- #
# news (GDELT) — injected HTTP, offline-safe
# --------------------------------------------------------------------------- #
def test_gdelt_headlines_parse_and_dedupe():
    news.invalidate_cache()
    canned = {"articles": [
        {"title": "Acme Corp beats earnings estimates"},
        {"title": "Acme Corp beats earnings estimates"},   # dup dropped
        {"title": "  Acme   expands   into Europe  "},
        {"notitle": True},
    ]}
    news.set_http(lambda url: canned)
    try:
        titles = news.headlines_for("ACME", "Acme Corp")
        assert titles == ["Acme Corp beats earnings estimates", "Acme expands into Europe"]
    finally:
        news.set_http(None)
        news.invalidate_cache()


def test_gdelt_offline_returns_empty():
    news.invalidate_cache()
    def boom(url):
        raise OSError("offline")
    news.set_http(boom)
    try:
        assert news.headlines_for("ACME", "Acme Corp") == []
    finally:
        news.set_http(None)
        news.invalidate_cache()


# --------------------------------------------------------------------------- #
# fundamentals extras
# --------------------------------------------------------------------------- #
def test_fundamentals_quality_fields_pass_through():
    provider = lambda t: {  # noqa: E731
        "dividend_yield": 0.02, "forward_pe": 15.0, "sector": "technology",
        "source": "fake", "roe": 1.6, "profit_margin": 0.25, "debt_to_equity": 1.4,
        "revenue_growth": 0.08, "current_price": 200.0, "target_mean_price": 230.0,
        "analyst_rating": 1.8, "n_analysts": 30,
    }
    fnd = fundamentals.fetch("AAPL", provider=provider)
    assert fnd.roe == pytest.approx(1.6)          # buyback-inflated ROE survives
    assert fnd.profit_margin == pytest.approx(0.25)
    assert fnd.debt_to_equity == pytest.approx(1.4)
    assert fnd.target_mean_price == pytest.approx(230.0)
    assert fnd.n_analysts == 30


# --------------------------------------------------------------------------- #
# Intrinio provider + merge chain
# --------------------------------------------------------------------------- #
def _intrinio_fake(responses):
    def fake(url):
        for fragment, value in responses.items():
            if fragment in url:
                return value
        return {"error": "Cannot look up this item/identifier combination"}
    return fake


def test_intrinio_provider_parses_data_points_and_maps_sic_sector(monkeypatch):
    monkeypatch.setenv("HELIOS_INTRINIO_KEY", "test-key")
    fundamentals.set_intrinio_http(_intrinio_fake({
        "/companies/JPM/data_point/pricetoearnings": 15.2,
        "/companies/JPM/data_point/dividendyield": 0.021,
        "/companies/JPM/data_point/roe": 0.17,
        "/companies/JPM/data_point/debttoequity": 1.4,
        "/companies/JPM/data_point/profitmargin": 0.32,
        "/companies/JPM/data_point/epsgrowth": 0.08,
        "/companies/JPM/data_point/revenuegrowth": 0.06,
        "/companies/JPM/data_point/close_price": 300.0,
        # SIC division name would fuzzy-match "real estate" without the mapping.
        "/companies/JPM?": {"ticker": "JPM", "sector": "Finance, Insurance, And Real Estate",
                            "industry_category": "Banking"},
    }))
    try:
        raw = fundamentals._intrinio_provider("JPM")
        assert raw["source"] == "intrinio"
        assert raw["sector"] == "financials"          # Banking -> financials, NOT real estate
        assert raw["trailing_pe"] == pytest.approx(15.2)
        assert raw["dividend_yield"] == pytest.approx(0.021)
        assert raw["earnings_growth"] == pytest.approx(0.08)
        assert raw["debt_to_equity"] == pytest.approx(1.4)
    finally:
        fundamentals.set_intrinio_http(None)


def test_intrinio_missing_tags_are_skipped_not_fabricated(monkeypatch):
    monkeypatch.setenv("HELIOS_INTRINIO_KEY", "test-key")
    fundamentals.set_intrinio_http(_intrinio_fake({
        "/companies/XYZ/data_point/pricetoearnings": 20.0,
        "/companies/XYZ?": {"ticker": "XYZ", "industry_category": "Oil & Gas Extraction"},
    }))
    try:
        raw = fundamentals._intrinio_provider("XYZ")
        assert raw["trailing_pe"] == pytest.approx(20.0)
        assert raw["sector"] == "energy"
        assert "dividend_yield" not in raw            # unavailable tag stays absent
    finally:
        fundamentals.set_intrinio_http(None)


def test_auto_provider_merges_intrinio_with_yfinance_gap_fill(monkeypatch):
    monkeypatch.delenv("HELIOS_FMP_KEY", raising=False)
    monkeypatch.setenv("HELIOS_INTRINIO_KEY", "test-key")
    fundamentals.set_intrinio_http(_intrinio_fake({
        "/companies/AAPL/data_point/pricetoearnings": 30.0,
        "/companies/AAPL/data_point/dividendyield": 0.005,
        "/companies/AAPL?": {"ticker": "AAPL", "industry_category": "Computer Hardware"},
    }))
    monkeypatch.setattr(fundamentals, "_yfinance_provider", lambda t: {
        "trailing_pe": 31.0,          # must NOT override Intrinio's value
        "forward_pe": 27.0,           # fills the gap Intrinio can't serve
        "target_mean_price": 250.0,   # analyst context only from yfinance
        "sector": "Technology",
        "source": "yfinance",
    })
    try:
        fnd = fundamentals.fetch("AAPL")
        assert fnd.trailing_pe == pytest.approx(30.0)   # Intrinio wins on overlap
        assert fnd.forward_pe == pytest.approx(27.0)    # yfinance fills the gap
        assert fnd.target_mean_price == pytest.approx(250.0)
        assert fnd.sector == "technology"
        assert fnd.source == "intrinio+yfinance"        # provenance names both
    finally:
        fundamentals.set_intrinio_http(None)


def test_merge_keeps_single_source_when_filler_adds_nothing():
    merged = fundamentals._merge_raw(
        {"trailing_pe": 12.0, "source": "intrinio"},
        {"trailing_pe": 13.0, "source": "yfinance"},
    )
    assert merged["trailing_pe"] == 12.0
    assert merged["source"] == "intrinio"


def test_leveraged_product_surfaces_caveat_and_refuses_strategic():
    """SOXL-class holdings: the strategic track refuses (no fabricated rating)
    and a SPECIFIC leveraged caveat surfaces for the operator + copilot."""
    close = _close()
    fc = forecast.forecast(close, horizon=21, n_paths=200)
    fc = {**fc, "quality": {"directional_accuracy": 0.60, "n_test": 100}}
    lev = cma.instrument_forward(
        "SOXL", _fnd(ticker="SOXL", name="Direxion Daily Semiconductor Bull 3X ETF",
                     forward_pe=30.0, sector="Financial Services"))
    sig = signals.evaluate(close, fc, _SENT, fundamental_result=lev)
    assert sig["strategic"]["usable"] is False
    assert sig["strategic"].get("product_structure") == "leveraged_daily_reset"
    assert "leveraged" in sig["strategic"]["reason"].lower()
    assert any("leveraged" in c.lower() and "daily-reset" in c.lower() for c in sig["caveats"])
    # The composite still rates on the technical blend (never blocked, just honest).
    names = [c["name"] for c in sig["components"]]
    assert names == ["trend", "momentum", "forecast", "sentiment"]


def test_fund_wrapper_surfaces_caveat_and_refuses_strategic():
    """ETF holdings: strategic track refuses (no fabricated basket rating) with
    a specific fund-wrapper caveat the operator + copilot can see."""
    close = _close()
    fc = forecast.forecast(close, horizon=21, n_paths=200)
    fc = {**fc, "quality": {"directional_accuracy": 0.60, "n_test": 100}}
    fund = cma.instrument_forward(
        "SOXX", _fnd(ticker="SOXX", name="iShares Semiconductor ETF", is_fund=True,
                     forward_pe=40.0, sector="Financial Services"))
    sig = signals.evaluate(close, fc, _SENT, fundamental_result=fund)
    assert sig["strategic"]["usable"] is False
    assert sig["strategic"].get("product_structure") == "fund_wrapper"
    assert any("fund wrapper" in c.lower() for c in sig["caveats"])

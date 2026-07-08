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

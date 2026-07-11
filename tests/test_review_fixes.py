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


# --------------------------------------------------------------------------- #
# Pass-2 finding: bond/commodity funds must never receive a fabricated equity
# growth block. Provider-stamped sector labels (FMP tags BND "Financial
# Services") do NOT count as equity evidence — only a real P/E or growth does.
# --------------------------------------------------------------------------- #
def test_yield_only_fund_refuses_equity_building_block():
    bnd_shaped = fundamentals.Fundamentals(
        ticker="BND", dividend_yield=0.0399, sector="Financial Services", source="fmp")
    fwd = cma.instrument_forward("BND", bnd_shaped)
    assert fwd["usable"] is False
    assert fwd["basis"] == "generic"
    assert "expected_return_pct" not in fwd   # nothing fabricated
    # A real equity with the same yield + sector but a P/E gets scored.
    equity = fundamentals.Fundamentals(
        ticker="JPM", dividend_yield=0.02, trailing_pe=14.0,
        sector="financials", source="fmp")
    assert cma.instrument_forward("JPM", equity)["usable"] is True


# --------------------------------------------------------------------------- #
# Pass-3 findings
# --------------------------------------------------------------------------- #
def test_annualized_return_is_geometric_cagr():
    """A flat-but-volatile round trip must read ~0%, not +34.8% (arithmetic
    mean compounding ignored volatility drag)."""
    import pandas as pd
    from engine import indicators
    idx = pd.bdate_range("2024-01-02", periods=253)
    prices = [100.0]
    for i in range(252):
        prices.append(prices[-1] * (1.05 if i % 2 == 0 else 1 / 1.05))
    close = pd.Series(prices, index=idx)
    result = indicators.annualized_return(close)
    assert abs(result) < 0.01  # true CAGR ~0, was +0.348


def test_cma_aggregate_uses_book_level_denominator():
    """40% look-through visibility must report ~40% coverage, blending the
    unseen 60% to the mandate anchor — not extrapolate the thin slice."""
    fnd = fundamentals.Fundamentals(ticker="AAA", dividend_yield=0.02,
                                    forward_pe=12.0, earnings_growth=0.10,
                                    sector="financials", source="fake")
    underlyings = [{"ticker": "AAA", "weight_pct": 40.0, "asset_class": "Equity (common)"}]
    out = cma.aggregate(underlyings, {"AAA": fnd}, "balanced", total_weight_pct=100.0)
    assert out["coverage_pct"] == pytest.approx(40.0)
    covered = out["expected_return_covered_pct"]
    anchor = out["generic_anchor_pct"]
    expected_blend = 0.4 * covered + 0.6 * anchor
    assert out["expected_return_pct"] == pytest.approx(expected_blend, abs=0.05)
    # Without the basis the legacy behavior is preserved (supplied-weight coverage).
    legacy = cma.aggregate(underlyings, {"AAA": fnd}, "balanced")
    assert legacy["coverage_pct"] == pytest.approx(100.0)


def test_reit_maps_to_real_estate_anchor():
    # The live Intrinio shape for SPG/O/PLD/AMT: only industry_group says REIT.
    assert fundamentals._intrinio_sector(
        {"industry_category": "Trading", "industry_group": "REIT",
         "sector": "Finance, Insurance, And Real Estate"}) == "real estate"
    assert fundamentals._intrinio_sector(
        {"industry_category": "Real Estate Investment Trusts"}) == "real estate"
    assert fundamentals._intrinio_sector(
        {"industry_category": "Equity Real Estate Investment Trusts (REITs)"}) == "real estate"
    assert fundamentals._intrinio_sector(
        {"industry_category": "Banking"}) == "financials"
    assert fundamentals._intrinio_sector(
        {"sector": "Finance, Insurance, And Real Estate"}) == "financials"


def test_analytics_cache_generation_drops_stale_puts():
    """A slow computation that started before invalidate() must not publish
    its stale result after it (in-flight /api/model/forward vs model edit)."""
    from engine import analytics_cache
    analytics_cache.invalidate()
    gen = analytics_cache.generation()
    analytics_cache.put("t", ("k",), {"v": 1}, if_generation=gen)
    assert analytics_cache.get("t", ("k",)) == {"v": 1}
    # Simulate the race: capture gen, invalidate mid-flight, then try to publish.
    gen = analytics_cache.generation()
    analytics_cache.invalidate()
    analytics_cache.put("t", ("k",), {"v": "stale"}, if_generation=gen)
    assert analytics_cache.get("t", ("k",)) is None
    # A put without the token keeps the old unconditional behavior.
    analytics_cache.put("t", ("k",), {"v": 2})
    assert analytics_cache.get("t", ("k",)) == {"v": 2}
    analytics_cache.invalidate()


def test_rsi_monotonic_rise_reads_100_not_neutral_50():
    import pandas as pd
    from engine import indicators
    up = pd.Series([100.0 + i for i in range(60)])
    assert float(indicators.rsi(up).iloc[-1]) == pytest.approx(100.0)
    down = pd.Series([100.0 - i * 0.5 for i in range(60)])
    assert float(indicators.rsi(down).iloc[-1]) == pytest.approx(0.0)
    flat = pd.Series([100.0] * 60)
    assert float(indicators.rsi(flat).iloc[-1]) == pytest.approx(50.0)


def test_momentum_score_is_continuous_at_rsi_bands():
    import pandas as pd
    from engine import signals as sig

    def score(r):
        f = pd.DataFrame({"rsi": [r]})
        return sig._momentum_score(f, 0)

    # Continuity at the bands (was a 0.16-point jump), correct extremes.
    assert abs(score(69.9) - score(70.1)) < 0.02
    assert abs(score(29.9) - score(30.1)) < 0.02
    assert score(0.0) == pytest.approx(1.0)
    assert score(100.0) == pytest.approx(-1.0)
    assert score(50.0) == pytest.approx(0.0)


def test_dividend_yield_not_percent_coerced():
    # Covered-call ETFs legitimately yield >150%; the percent heuristic
    # double-divided 2.77 (=277%) into a fake 2.77% (review finding).
    f = fundamentals.fetch("MSTYX", provider=lambda t: {
        "dividend_yield": 2.77, "trailing_pe": 10.0, "sector": "technology",
        "source": "fake"})
    assert f.dividend_yield == pytest.approx(2.77)
    fwd = cma.instrument_forward("MSTYX", f)
    # The CMA's DY cap clips to the honest 12% ceiling instead.
    assert fwd["blocks_pct"]["dividend_yield"] == pytest.approx(12.0)


def test_fmp_forward_skips_thin_far_year_estimates():
    import datetime as dt
    y = dt.date.today().year
    rows = [
        {"date": f"{y + 1}-12-31", "epsAvg": 10.0, "numAnalystsEps": 30},
        {"date": f"{y + 2}-12-31", "epsAvg": 11.5, "numAnalystsEps": 25},
        {"date": f"{y + 4}-12-31", "epsAvg": 40.0, "numAnalystsEps": 2},  # placeholder row
    ]
    _, growth = fundamentals._fmp_forward(rows, price=100.0)
    # Growth comes from the 30->25 analyst rows (~15%), not the 2-analyst
    # FY+4 row that implied ~59%/yr.
    assert growth == pytest.approx(0.15, abs=0.01)
    # Rows without analyst counts (legacy v3) still work.
    legacy = [{"date": f"{y + 1}-12-31", "estimatedEpsAvg": 10.0},
              {"date": f"{y + 3}-12-31", "estimatedEpsAvg": 12.1}]
    _, g2 = fundamentals._fmp_forward(legacy, price=100.0)
    assert g2 is not None and g2 > 0


def test_quarterly_yoy_growth_is_shrunk_toward_sector_anchor():
    # One rebound quarter (+50% YoY) must not read as a perpetual CAGR.
    rebound = fundamentals.fetch("CYCL", provider=lambda t: {
        "earnings_growth": 0.50, "growth_basis": "trailing_quarter_yoy",
        "trailing_pe": 15.0, "sector": "technology", "source": "fake"})
    assert rebound.growth_basis == "trailing_quarter_yoy"
    fwd = cma.instrument_forward("CYCL", rebound)
    g = fwd["blocks_pct"]["earnings_growth"]
    anchor_g = 100.0 * cma._macro.sector_anchor("technology")["growth"]
    assert g < 25.0  # nowhere near the raw 50, and below the growth cap
    assert abs(g - (anchor_g + 0.3 * (50.0 - anchor_g))) < 0.5  # 70% shrunk
    # Forward consensus figures pass through un-shrunk.
    consensus = fundamentals.fetch("CONS", provider=lambda t: {
        "earnings_growth": 0.18, "growth_basis": "forward_consensus_cagr",
        "trailing_pe": 15.0, "sector": "technology", "source": "fake"})
    fwd2 = cma.instrument_forward("CONS", consensus)
    assert fwd2["blocks_pct"]["earnings_growth"] == pytest.approx(18.0)


def test_cma_blocks_include_asset_class_anchor_and_reconcile():
    eq = fundamentals.Fundamentals(ticker="EQ", dividend_yield=0.02,
                                   forward_pe=12.0, earnings_growth=0.09,
                                   growth_basis="forward_consensus_cagr",
                                   sector="financials", source="fake")
    unders = [{"ticker": "EQ", "weight_pct": 50.0, "asset_class": "Equity (common)"},
              {"ticker": "", "weight_pct": 50.0, "asset_class": "Bond"}]
    out = cma.aggregate(unders, {"EQ": eq}, "balanced", total_weight_pct=100.0)
    blocks = out["weighted_blocks_pct"]
    assert "asset_class_anchor" in blocks and blocks["asset_class_anchor"] > 0
    assert sum(blocks.values()) == pytest.approx(out["expected_return_covered_pct"], abs=0.05)


def test_zero_forward_return_not_masked_by_anchor():
    z = fundamentals.Fundamentals(ticker="ZERO", dividend_yield=0.02,
                                  earnings_growth=-0.02, trailing_pe=None,
                                  growth_basis="forward_consensus_cagr",
                                  sector="", source="fake")
    unders = [{"ticker": "ZERO", "weight_pct": 60.0, "asset_class": "Equity (common)"},
              {"ticker": "OTH", "weight_pct": 40.0, "asset_class": "Equity (common)"}]
    out = cma.aggregate(unders, {"ZERO": z}, "balanced")
    zero_row = next(c for c in out["top_contributions"] if c["ticker"] == "ZERO")
    assert zero_row["expected_return_pct"] == pytest.approx(0.0)   # not the 4.7% anchor

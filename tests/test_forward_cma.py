"""Forward expected-return spine: macro anchors, fundamentals, building-block CMA.

Offline. Fundamentals come from an injected fake provider and look-through from a
fake EDGAR client, so the real CMA math and roll-up run without any network.
"""
import json

import pytest

import app as helios
from engine import cma, edgar, figi, fundamentals, holdings, macro, mandate, portfolio


# --------------------------------------------------------------------------- #
# macro
# --------------------------------------------------------------------------- #
def test_sector_anchor_fuzzy_and_default():
    assert macro.sector_anchor("Technology")["fair_pe"] == 24.0
    assert macro.sector_anchor("Financial Services")["fair_pe"] == 13.0
    assert macro.sector_anchor("Nonsense Sector") == {"fair_pe": 17.0, "growth": 0.06}


def test_asset_class_return_by_sleeve():
    rf = macro.risk_free()
    assert macro.asset_class_return("Short-term / cash") == rf
    assert macro.asset_class_return("Debt") == pytest.approx(rf + 0.005)
    assert macro.asset_class_return("Real estate") == pytest.approx(rf + 0.03)


# --------------------------------------------------------------------------- #
# fundamentals
# --------------------------------------------------------------------------- #
def test_fundamentals_coercion_and_usability():
    # All wired providers deliver dividend_yield as a TRUE FRACTION; the old
    # >1.5 percent heuristic double-divided legitimate covered-call yields
    # (2.77 = 277%, verified live) into fake 2.77% figures. Values pass
    # through numerically; the CMA's DY cap clips extremes at scoring time.
    f = fundamentals.fetch("AAPL", provider=lambda t: {
        "dividend_yield": 0.018, "forward_pe": 28.0, "trailing_pe": -5.0,
        "earnings_growth": 0.11, "sector": "Technology"})
    assert f.dividend_yield == pytest.approx(0.018)
    assert f.forward_pe == 28.0
    assert f.trailing_pe is None                       # negative P/E rejected
    assert f.usable
    high = fundamentals.fetch("MSTY", provider=lambda t: {
        "dividend_yield": 2.77, "sector": "Technology", "trailing_pe": 8.0})
    assert high.dividend_yield == pytest.approx(2.77)  # 277%, NOT re-divided


def test_fundamentals_offline_returns_empty():
    f = fundamentals.fetch("AAPL", provider=lambda t: {})
    assert f.source == "none" and f.usable is False


def test_fmp_provider_derives_consensus_growth_and_forward_pe(monkeypatch):
    # FMP is preferred when a key is set; growth is a CAGR across the forward
    # estimate window (cleaner than yfinance's single noisy quarter).
    fundamentals.set_default_provider(None)
    monkeypatch.setenv("HELIOS_FMP_KEY", "testkey")

    def fake_fmp(url):
        if "/profile/" in url:
            return [{"sector": "Technology", "price": 100.0, "lastDiv": 2.0}]
        if "/ratios-ttm/" in url:
            return [{"dividendYieldTTM": 0.02, "peRatioTTM": 25.0}]
        if "/analyst-estimates/" in url:
            return [{"date": "2099-12-31", "estimatedEpsAvg": 6.0},
                    {"date": "2097-12-31", "estimatedEpsAvg": 4.0}]
        return None

    fundamentals.set_fmp_http(fake_fmp)
    # Keep the gap-fill chain offline/deterministic: no Intrinio key, empty yfinance.
    monkeypatch.delenv("HELIOS_INTRINIO_KEY", raising=False)
    monkeypatch.setattr(fundamentals, "_yfinance_provider", lambda t: {})
    f = fundamentals.fetch("AAPL")
    assert f.source == "fmp" and f.usable
    assert f.dividend_yield == pytest.approx(0.02)
    assert f.forward_pe == pytest.approx(25.0)        # price / nearest-year EPS = 100/4
    assert f.earnings_growth == pytest.approx((6.0 / 4.0) ** 0.5 - 1.0, abs=1e-4)
    assert f.sector == "Technology"


def test_fetch_falls_back_when_no_fmp_key(monkeypatch):
    fundamentals.set_default_provider(None)
    monkeypatch.delenv("HELIOS_FMP_KEY", raising=False)
    monkeypatch.setattr(fundamentals.data, "HAS_YF", False)  # keep the fallback offline
    f = fundamentals.fetch("AAPL")
    assert f.source == "none"  # yfinance offline -> empty, never fabricated


# --------------------------------------------------------------------------- #
# cma building blocks
# --------------------------------------------------------------------------- #
def _f(**kw):
    return fundamentals.Fundamentals(ticker=kw.pop("ticker", "X"), source="fake", **kw)


def test_holding_return_equity_uses_building_blocks():
    f = _f(ticker="AAPL", dividend_yield=0.005, forward_pe=28.0, earnings_growth=0.10, sector="Technology")
    hr = cma.holding_expected_return("AAPL", 60.0, "Equity (common)", f)
    assert hr.basis == "fundamentals" and hr.usable
    assert hr.blocks["valuation_reversion"] < 0          # 28 P/E vs 24 fair -> reverts down
    assert 0.0 < hr.expected_return < 0.15


def test_holding_return_nonequity_uses_asset_class():
    hr = cma.holding_expected_return("", 100.0, "Debt", None)
    assert hr.basis == "asset_class" and hr.usable
    assert hr.expected_return == pytest.approx(macro.risk_free() + 0.005)


def test_holding_return_equity_without_fundamentals_defers():
    hr = cma.holding_expected_return("XYZ", 10.0, "Equity (common)", None)
    assert hr.basis == "generic" and hr.usable is False and hr.expected_return is None


def test_aggregate_zero_coverage_reproduces_generic_anchor():
    unders = [{"ticker": "XYZ", "weight_pct": 100.0, "asset_class": "Equity (common)"}]
    out = cma.aggregate(unders, {}, "balanced")
    assert out["coverage_pct"] == 0.0
    assert out["expected_return_pct"] == round(mandate.anchor_return("balanced") * 100.0, 2)


def test_aggregate_blends_covered_and_uncovered():
    unders = [
        {"ticker": "AAPL", "weight_pct": 50.0, "asset_class": "Equity (common)"},
        {"ticker": "XYZ", "weight_pct": 50.0, "asset_class": "Equity (common)"},  # no fundamentals
    ]
    fmap = {"AAPL": _f(ticker="AAPL", dividend_yield=0.01, forward_pe=20.0, earnings_growth=0.08, sector="Technology")}
    out = cma.aggregate(unders, fmap, "balanced")
    assert out["coverage_pct"] == 50.0
    # Model return sits between the covered estimate and the generic anchor.
    lo = min(out["expected_return_covered_pct"], out["generic_anchor_pct"])
    hi = max(out["expected_return_covered_pct"], out["generic_anchor_pct"])
    assert lo <= out["expected_return_pct"] <= hi


# --------------------------------------------------------------------------- #
# research hypotheses (unified with the clinic's schema)
# --------------------------------------------------------------------------- #
def test_research_hypotheses_emit_tilt_and_trim_in_clinic_schema():
    forward = {
        "coverage_pct": 100.0, "mandate_anchor_pct": 4.7,
        "top_contributions": [
            {"ticker": "CHEAP", "weight_pct": 20.0, "expected_return_pct": 9.0, "basis": "fundamentals"},
            {"ticker": "RICH", "weight_pct": 15.0, "expected_return_pct": 2.0, "basis": "fundamentals"},
        ],
    }
    hyps = cma.research_hypotheses(forward, "balanced")
    types = {h["type"] for h in hyps}
    assert types == {"forward_valuation_tilt", "forward_valuation_trim"}
    # Same schema the Portfolio Clinic emits, so one surface can render both.
    for h in hyps:
        assert set(h) >= {"type", "ticker", "current_weight", "suggested_weight", "rationale"}
    tilt = next(h for h in hyps if h["type"] == "forward_valuation_tilt")
    assert tilt["ticker"] == "CHEAP" and tilt["suggested_weight"] > tilt["current_weight"]


def test_research_hypotheses_gated_on_coverage():
    thin = {"coverage_pct": 20.0, "mandate_anchor_pct": 4.7,
            "top_contributions": [{"ticker": "A", "weight_pct": 50.0, "expected_return_pct": 9.0,
                                   "basis": "fundamentals"}]}
    assert cma.research_hypotheses(thin, "balanced") == []


# --------------------------------------------------------------------------- #
# endpoint
# --------------------------------------------------------------------------- #
def _nport(rows):
    body = "".join(
        f"<invstOrSec><name>{n}</name><identifiers><ticker value=\"{t}\"/></identifiers>"
        f"<pctVal>{p}</pctVal><assetCat>EC</assetCat></invstOrSec>" for n, t, p in rows)
    return ('<?xml version="1.0"?><edgarSubmission xmlns="http://www.sec.gov/edgar/nport"><formData>'
            "<genInfo><repPdDate>2099-01-01</repPdDate></genInfo>"
            f"<invstOrSecs>{body}</invstOrSecs></formData></edgarSubmission>")


_EQ_CIK = 555111
_MF = {"fields": ["cik", "seriesId", "classId", "symbol"], "data": [[_EQ_CIK, "S1", "C1", "VEQ"]]}
_SUBS = {"cik": _EQ_CIK, "name": "Equity Fund", "formerNames": [],
         "filings": {"recent": {"accessionNumber": ["0009-25-000001"], "form": ["NPORT-P"],
                                "primaryDocument": ["primary_doc.xml"], "filingDate": ["2099-02-01"],
                                "reportDate": ["2099-01-01"]}}}


def _fake_edgar():
    atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><content>'
            "<accession-number>0009-25-000001</accession-number><filing-type>NPORT-P</filing-type>"
            "<filing-date>2099-02-01</filing-date></content></entry></feed>")
    url_map = {
        edgar.MF_TICKERS_URL: json.dumps(_MF),
        edgar.STOCK_TICKERS_URL: json.dumps({}),
        edgar.submissions_url(_EQ_CIK): json.dumps(_SUBS),
        edgar.browse_series_url("S1"): atom,
        edgar.archives_doc_url(_EQ_CIK, "0009-25-000001", "primary_doc.xml"):
            _nport([("Apple", "AAPL", "60.0"), ("Microsoft", "MSFT", "40.0")]),
    }
    return edgar.EdgarClient(http_get=lambda u, h: url_map[u] if u in url_map else _raise(u))


def _raise(u):
    raise KeyError(u)


def _fake_fund_provider(t):
    return {
        "AAPL": {"dividend_yield": 0.005, "forward_pe": 28.0, "earnings_growth": 0.10, "sector": "Technology"},
        "MSFT": {"dividend_yield": 0.008, "forward_pe": 32.0, "earnings_growth": 0.12, "sector": "Technology"},
    }.get(t.upper(), {})


@pytest.fixture(autouse=True)
def _inject_providers():
    holdings.set_default_client(_fake_edgar())
    fundamentals.set_default_provider(_fake_fund_provider)
    figi.set_default_post(None)  # clear figi cache between tests
    yield
    holdings.set_default_client(None)
    fundamentals.set_default_provider(None)
    fundamentals.set_fmp_http(None)
    figi.set_default_post(None)


def _nport_cusip(rows):
    body = "".join(f"<invstOrSec><name>{n}</name><cusip>{c}</cusip><identifiers></identifiers>"
                   f"<pctVal>{p}</pctVal><assetCat>EC</assetCat></invstOrSec>" for n, c, p in rows)
    return ('<?xml version="1.0"?><edgarSubmission xmlns="http://www.sec.gov/edgar/nport"><formData>'
            "<genInfo><repPdDate>2099-01-01</repPdDate></genInfo>"
            f"<invstOrSecs>{body}</invstOrSecs></formData></edgarSubmission>")


_CUS_CIK = 556222


def _fake_edgar_cusip():
    mf = {"fields": ["cik", "seriesId", "classId", "symbol"], "data": [[_CUS_CIK, "SX", "CX", "VCUS"]]}
    subs = {"cik": _CUS_CIK, "name": "Cusip Fund", "formerNames": [],
            "filings": {"recent": {"accessionNumber": ["0001-25-1"], "form": ["NPORT-P"],
                                   "primaryDocument": ["primary_doc.xml"], "filingDate": ["2099-02-01"],
                                   "reportDate": ["2099-01-01"]}}}
    atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><content>'
            "<accession-number>0001-25-1</accession-number><filing-type>NPORT-P</filing-type>"
            "<filing-date>2099-02-01</filing-date></content></entry></feed>")
    um = {edgar.MF_TICKERS_URL: json.dumps(mf), edgar.STOCK_TICKERS_URL: json.dumps({}),
          edgar.submissions_url(_CUS_CIK): json.dumps(subs), edgar.browse_series_url("SX"): atom,
          edgar.archives_doc_url(_CUS_CIK, "0001-25-1", "primary_doc.xml"):
              _nport_cusip([("NVIDIA", "67066G104", "100.0")])}
    return edgar.EdgarClient(http_get=lambda u, h: um[u] if u in um else _raise(u))


def test_figi_map_cusips_injected_and_cached():
    figi.set_default_post(None)
    calls = {"n": 0}

    def fake_post(url, headers, body):
        calls["n"] += 1
        jobs = json.loads(body.decode())
        m = {"67066G104": "NVDA", "037833100": "AAPL"}
        return [{"data": [{"ticker": m.get(j["idValue"], "")}]} for j in jobs]

    out = figi.map_cusips(["67066G104", "037833100"], http_post=fake_post)
    assert out == {"67066G104": "NVDA", "037833100": "AAPL"} and calls["n"] == 1
    assert figi.map_cusips(["67066G104"], http_post=fake_post) == {"67066G104": "NVDA"}  # cached
    assert calls["n"] == 1
    figi.set_default_post(None)


@pytest.fixture()
def client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def test_endpoint_model_forward(client):
    portfolio.register(portfolio.Model(id="MFWD", name="Fwd Model", mandate_key="balanced",
                                       mandate_context="", holdings=[portfolio.Holding("VEQ", 1.0)]))
    body = client.post("/api/model/forward?id=MFWD").get_json()
    fr = body["forward_return"]
    assert fr["coverage_pct"] == 100.0
    assert fr["method"] == "building_block_cma"
    assert fr["n_usable"] == 2
    assert 0.0 < fr["expected_return_pct"] < 15.0
    assert "blended_anchor_pct" in body
    assert isinstance(body["research_hypotheses"], list)   # unified clinic-schema hypotheses


def test_endpoint_model_forward_unknown(client):
    assert client.post("/api/model/forward?id=NOPE").status_code == 404


def test_forward_enriches_cusip_only_holdings_via_figi(client):
    # N-PORT usually omits tickers; the CUSIP->ticker bridge is what gives the CMA
    # real coverage instead of falling back to the generic anchor.
    holdings.set_default_client(_fake_edgar_cusip())
    figi.set_default_post(lambda url, headers, body:
                          [{"data": [{"ticker": "NVDA"}]} if j["idValue"] == "67066G104" else {"data": []}
                           for j in json.loads(body.decode())])
    fundamentals.set_default_provider(lambda t: {"NVDA": {"dividend_yield": 0.0, "forward_pe": 40.0,
                                                          "earnings_growth": 0.25, "sector": "Technology"}}.get(t.upper(), {}))
    portfolio.register(portfolio.Model(id="MCUS", name="Cusip Model", mandate_key="balanced",
                                       mandate_context="", holdings=[portfolio.Holding("VCUS", 1.0)]))
    fr = client.post("/api/model/forward?id=MCUS").get_json()["forward_return"]
    assert fr["coverage_pct"] == 100.0 and fr["n_usable"] == 1
    assert any(c["ticker"] == "NVDA" and c["basis"] == "fundamentals" for c in fr["top_contributions"])


def test_fmp_stable_api_fields_and_next_earnings(monkeypatch):
    """Post-Aug-2025 FMP accounts get the /stable API: different paths, different
    field names (epsAvg, priceToEarningsRatioTTM, lastDividend) + earnings dates."""
    fundamentals.set_default_provider(None)
    monkeypatch.setenv("HELIOS_FMP_KEY", "testkey")
    monkeypatch.delenv("HELIOS_INTRINIO_KEY", raising=False)
    monkeypatch.setattr(fundamentals, "_yfinance_provider", lambda t: {})

    def fake_fmp(url):
        if "/api/v3/" in url:
            return {"Error Message": "Legacy Endpoint : no longer supported"}
        if "/stable/profile" in url:
            return [{"sector": "Technology", "price": 100.0, "lastDividend": 2.0}]
        if "/stable/ratios-ttm" in url:
            return [{"dividendYieldTTM": 0.02, "priceToEarningsRatioTTM": 25.0,
                     "netProfitMarginTTM": 0.24, "debtToEquityRatioTTM": 1.1}]
        if "/stable/analyst-estimates" in url:
            return [{"date": "2099-12-31", "epsAvg": 6.0},
                    {"date": "2097-12-31", "epsAvg": 4.0}]
        if "/stable/earnings" in url:
            return [{"date": "2097-01-30", "epsActual": None, "epsEstimated": 1.9},
                    {"date": "2096-10-30", "epsActual": 1.8, "epsEstimated": 1.7}]
        return None

    fundamentals.set_fmp_http(fake_fmp)
    try:
        f = fundamentals.fetch("AAPL")
        assert f.source == "fmp" and f.usable
        assert f.dividend_yield == pytest.approx(0.02)
        assert f.trailing_pe == pytest.approx(25.0)
        assert f.forward_pe == pytest.approx(25.0)        # 100 / nearest-year eps 4.0
        assert f.earnings_growth == pytest.approx((6.0 / 4.0) ** 0.5 - 1.0, abs=1e-4)
        assert f.profit_margin == pytest.approx(0.24)
        assert f.next_earnings_date == "2097-01-30"       # first not-yet-reported date
    finally:
        fundamentals.set_fmp_http(None)


def test_instrument_forward_flags_imminent_earnings():
    fnd = fundamentals.Fundamentals(
        ticker="X", dividend_yield=0.02, forward_pe=15.0, earnings_growth=0.08,
        sector="technology", source="fmp", next_earnings_date="2099-01-01")
    fwd = cma.instrument_forward("X", fnd)
    assert fwd["earnings"]["next_date"] == "2099-01-01"
    assert fwd["earnings"]["imminent"] is False           # far future, not imminent


# --------------------------------------------------------------------------- #
# Leveraged / daily-reset product refusal (SOXL et al.) — fundamentals,
# sector anchors, and valuation reversion do not model daily-reset compounding,
# so the strategic track must REFUSE rather than fabricate a confident rating.
# --------------------------------------------------------------------------- #
from engine import assumptions as _assumptions


def test_is_leveraged_product_name_detects_issuer_conventions():
    for name in ("Direxion Daily Semiconductor Bull 3X Shares",
                 "Direxion Daily Semiconductor Bull 3X ETF",
                 "ProShares UltraPro QQQ", "ProShares Ultra S&P500",
                 "ProShares Short S&P500",
                 "Daily Junior Gold Miners Index Bear 2X Shares",
                 "MicroSectors FANG+ Index 3X Leveraged ETN"):
        assert _assumptions.is_leveraged_product_name(name) is True, name
    for name in ("JPMorgan Chase & Co.", "NVIDIA Corporation",
                 "iShares MSCI USA Quality Factor ETF", "Invesco QQQ Trust",
                 "SPDR S&P 500 ETF Trust", "Ultragenyx Pharmaceutical Inc.",
                 "Fidelity Contrafund", ""):
        assert _assumptions.is_leveraged_product_name(name) is False, name


def test_leveraged_wrapper_refuses_equity_block_despite_fake_pe():
    # FMP tags SOXL a "Financial Services" company with a (meaningless) P/E, so
    # usable fundamentals alone must NOT admit the equity block — the name is
    # the tell. Refuse it despite a positive P/E and dividend.
    f = _f(ticker="SOXL", name="Direxion Daily Semiconductor Bull 3X ETF",
           trailing_pe=30.0, dividend_yield=0.0013, sector="Financial Services")
    hr = cma.holding_expected_return("SOXL", 100.0, "Equity (common)", f)
    assert hr.usable is False and hr.expected_return is None
    assert hr.blocks.get("product_structure") == "leveraged_daily_reset"
    assert "leveraged" in hr.blocks.get("reason", "").lower()


def test_instrument_forward_flags_leveraged_and_surfaces_reason():
    f = _f(ticker="SOXL", name="Direxion Daily Semiconductor Bull 3X ETF",
           trailing_pe=30.0, dividend_yield=0.0013, sector="Financial Services")
    fwd = cma.instrument_forward("SOXL", f)
    assert fwd["usable"] is False
    assert fwd.get("product_structure") == "leveraged_daily_reset"
    assert "leveraged" in (fwd.get("reason") or "").lower()
    assert "expected_return_pct" not in fwd          # never a fabricated number


def test_real_operating_company_unaffected_by_leverage_guard():
    f = _f(ticker="JPM", name="JPMorgan Chase & Co.", trailing_pe=13.0,
           dividend_yield=0.021, earnings_growth=0.06, sector="Financial Services")
    fwd = cma.instrument_forward("JPM", f)
    assert fwd["usable"] is True and fwd.get("product_structure") is None
    assert fwd.get("expected_return_pct") is not None


# --------------------------------------------------------------------------- #
# Fund-wrapper guard + mandate anchor control (ETF sector contamination fix)
# --------------------------------------------------------------------------- #
def test_fund_wrapper_refuses_single_stock_fundamentals():
    # FMP mis-tags equity ETFs "Financial Services" with a P/E; a single-stock
    # reversion/sector anchor is a category error on a basket. Refuse it even
    # though a P/E is present (is_fund is the tell).
    f = _f(ticker="SOXX", name="iShares Semiconductor ETF", is_fund=True,
           trailing_pe=40.0, dividend_yield=0.005, sector="Financial Services")
    hr = cma.holding_expected_return("SOXX", 100.0, "equity", f)
    assert hr.usable is False and hr.expected_return is None
    assert hr.blocks.get("product_structure") == "fund_wrapper"
    fwd = cma.instrument_forward("SOXX", f)
    assert fwd["usable"] is False and fwd.get("product_structure") == "fund_wrapper"
    assert "fund wrapper" in (fwd.get("reason") or "").lower()
    assert "expected_return_pct" not in fwd          # never a fabricated number


def test_non_fund_stock_in_financials_still_rates():
    # A real bank (JPM) is legitimately Financial Services and NOT a fund — it
    # must keep a real fundamentals rating.
    f = _f(ticker="JPM", name="JPMorgan Chase & Co.", is_fund=False,
           trailing_pe=13.0, dividend_yield=0.021, earnings_growth=0.06,
           sector="Financial Services")
    fwd = cma.instrument_forward("JPM", f)
    assert fwd["usable"] is True and fwd.get("product_structure") is None
    assert fwd.get("expected_return_pct") is not None


def test_mandate_from_profile_matches_own_risk():
    assert mandate.mandate_from_profile(4.0) == "cd_alternative"
    assert mandate.mandate_from_profile(6.0) == "capital_preservation"
    assert mandate.mandate_from_profile(10.0) == "income"
    assert mandate.mandate_from_profile(12.0) == "balanced"
    assert mandate.mandate_from_profile(30.0) == "pure_growth"
    assert mandate.mandate_from_profile(None) == mandate.DEFAULT


def test_mandate_override_changes_the_anchor():
    f = _f(ticker="X", dividend_yield=0.01, forward_pe=20.0, earnings_growth=0.10,
           sector="Technology")
    bal = cma.instrument_forward("X", f, mandate_key="balanced")
    grw = cma.instrument_forward("X", f, mandate_key="pure_growth")
    # pure_growth is a HIGHER hurdle than balanced, so the same E[r] shows a
    # smaller (more demanding) gap — the honest direction.
    assert grw["anchor_pct"] > bal["anchor_pct"]
    assert grw["gap_vs_anchor_pct"] < bal["gap_vs_anchor_pct"]


# --------------------------------------------------------------------------- #
# Growth-and-quality-conditioned fair value (stop penalizing compounders for a
# multiple their growth earns; still discipline names stretched even for it)
# --------------------------------------------------------------------------- #
def test_conditioned_fair_pe_rewards_growth_and_quality():
    fair, d = cma._conditioned_fair_pe(24.0, 0.25, 0.12, 0.30, 0.25)
    assert d["growth_mult"] == pytest.approx(1.65, abs=0.01)   # 1 + 5*(0.25-0.12)
    assert d["quality_mult"] == pytest.approx(1.12, abs=0.01)
    assert fair == pytest.approx(24.0 * 1.65 * 1.12, rel=1e-3)
    assert fair > 24.0                                          # premium over the sector base


def test_conditioned_fair_pe_falls_back_to_sector_base_without_edge():
    fair, d = cma._conditioned_fair_pe(24.0, 0.12, 0.12, None, None)
    assert d["growth_mult"] == pytest.approx(1.0)
    assert d["quality_mult"] == pytest.approx(1.0)
    assert fair == pytest.approx(24.0)                          # unchanged when no edge


def test_conditioned_fair_pe_is_bounded():
    # Hyper-growth + buyback-inflated ROE cannot blow past the caps.
    fair, d = cma._conditioned_fair_pe(24.0, 1.0, 0.05, 1.5, 0.9)
    assert d["growth_mult"] == 2.5                     # growth cap binds
    # Quality maxes at 1.20 (0.8 * 0.25 max credit) — below the 1.25 belt cap.
    assert d["quality_mult"] == pytest.approx(1.20)
    assert fair == pytest.approx(24.0 * 2.5 * 1.20)


def test_growth_conditioning_rewards_fair_priced_compounder():
    # High growth + quality at a moderate multiple -> POSITIVE reversion, where
    # the static sector anchor (24) would have ignored/penalized a 30x multiple.
    f = _f(ticker="CMP", forward_pe=30.0, earnings_growth=0.25, roe=0.30,
           profit_margin=0.25, dividend_yield=0.0, sector="Technology")
    fwd = cma.instrument_forward("CMP", f)
    assert fwd["blocks_pct"]["valuation_reversion"] > 0
    assert fwd["fair_pe_conditioning"]["conditioned_fair_pe"] > 24.0


def test_growth_conditioning_still_disciplines_overstretched():
    # 110x for 17% growth stays stretched even after conditioning: -5% floor.
    f = _f(ticker="RICH", forward_pe=110.0, earnings_growth=0.17, roe=0.05,
           profit_margin=0.05, sector="Technology")
    fwd = cma.instrument_forward("RICH", f)
    assert fwd["blocks_pct"]["valuation_reversion"] == pytest.approx(-5.0, abs=0.01)

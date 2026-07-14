"""Fund look-through: EDGAR parsing, roll-up, forward provenance, endpoints.

Fully offline. A fake EDGAR client serves canned SEC payloads from a URL map, so
the *real* N-PORT parser and aggregation run without touching the network.
"""
import datetime
import json

import pytest

import app as helios
from engine import edgar, holdings, portfolio, provenance

FUND_CIK = 1234567
PART_CIK = 2345678
NEWFUND_CIK = 7654321
AAPL_CIK = 320193

# A recent report date so the realistic "ready" path is exercised regardless of
# when the suite runs (N-PORT staleness gating keys off this date).
_AS_OF = (datetime.date.today() - datetime.timedelta(days=25)).isoformat()


def _nport(as_of: str, rows) -> str:
    body = []
    for name, ticker, cusip, pct, cat in rows:
        ident = f'<ticker value="{ticker}"/>' if ticker else ""
        body.append(
            f"<invstOrSec><name>{name}</name><title>{name}</title>"
            f"<cusip>{cusip}</cusip><identifiers>{ident}</identifiers>"
            f"<valUSD>1000.0</valUSD><pctVal>{pct}</pctVal><assetCat>{cat}</assetCat></invstOrSec>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<edgarSubmission xmlns="http://www.sec.gov/edgar/nport"><formData>'
        f"<genInfo><repPdDate>{as_of}</repPdDate></genInfo>"
        "<fundInfo><netAssets>1000000000.00</netAssets></fundInfo>"
        "<invstOrSecs>" + "".join(body) + "</invstOrSecs></formData></edgarSubmission>"
    )


# Full-coverage fund: listed positions sum to ~100% of NAV.
_NPORT_XML = _nport(_AS_OF, [
    ("Microsoft Corp", "MSFT", "594918104", "45.0", "EC"),
    ("NVIDIA Corp", "NVDA", "67066G104", "35.0", "EC"),
    ("US Treasury Note 4.0% 2030", "", "91282CJL6", "20.0", "DBT"),
])

# Partial-coverage fund: listed positions sum to only 60% (rest cash/derivatives).
_NPORT_PART_XML = _nport(_AS_OF, [
    ("Microsoft Corp", "MSFT", "594918104", "40.0", "EC"),
    ("US Treasury Note 4.0% 2030", "", "91282CJL6", "20.0", "DBT"),
])

_MF_MAP = {"fields": ["cik", "seriesId", "classId", "symbol"],
           "data": [[FUND_CIK, "S000099999", "C000099999", "VTEST"],
                    [PART_CIK, "S000077777", "C000077777", "VPART"],
                    [NEWFUND_CIK, "S000088888", "C000088888", "VNEW"]]}

GOLD_CIK = 3456789    # commodity trust: stock ticker map, SIC 6221, no N-PORT
UIT_CIK = 4567890     # SPY-style UIT: stock ticker map, files registrant-level N-PORT

_STOCK_MAP = {"0": {"cik_str": AAPL_CIK, "ticker": "AAPL", "title": "Apple Inc."},
              "1": {"cik_str": GOLD_CIK, "ticker": "GLDT", "title": "Gold Shares Test Trust"},
              "2": {"cik_str": UIT_CIK, "ticker": "SPYT", "title": "Test UIT ETF Trust"}}


def _subs(cik, name, former, form, acc, prim, report_date):
    return {
        "cik": cik, "name": name,
        "formerNames": [{"name": former, "from": "2010-01-01", "to": "2024-12-31"}],
        "filings": {"recent": {
            "accessionNumber": [acc], "form": [form], "primaryDocument": [prim],
            "filingDate": ["2025-05-20"], "reportDate": [report_date],
        }},
    }


_SUBS_FUND = _subs(FUND_CIK, "Helios Test Fund", "Helios Legacy Closed Fund",
                   "NPORT-P", "0001111111-25-000001", "primary_doc.xml", _AS_OF)
_SUBS_PART = _subs(PART_CIK, "Helios Partial Fund", "Helios Old Partial Fund",
                   "NPORT-P", "0003333333-25-000003", "primary_doc.xml", _AS_OF)
# A newly-launched fund that has registered but not yet filed any N-PORT.
_SUBS_NEWFUND = _subs(NEWFUND_CIK, "Helios Brand New Fund", "Helios Predecessor Fund",
                      "485BPOS", "0002222222-25-000009", "prospectus.htm", "")
# An operating company (SIC 3571), a commodity trust (SIC 6221 — the live GLD/
# SLV/IAU/USO shape), and a UIT that files N-PORT at the REGISTRANT level with
# no series id (the live SPY shape).
_SUBS_AAPL = {**_subs(AAPL_CIK, "Apple Inc.", "Apple Computer Inc",
                      "10-K", "0000320193-25-000001", "aapl-10k.htm", "2025-09-27"),
              "sic": "3571"}
_SUBS_GOLD = {**_subs(GOLD_CIK, "Gold Shares Test Trust", "",
                      "10-K", "0004444444-25-000004", "gold-10k.htm", "2025-09-30"),
              "sic": "6221"}
_SUBS_UIT = {**_subs(UIT_CIK, "Test UIT ETF Trust", "",
                     "NPORT-P", "0005555555-25-000005", "primary_doc.xml", _AS_OF),
             "sic": "6726"}


def _atom(accession: str, ftype: str = "NPORT-P", date: str = "2025-05-20") -> str:
    return ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><content type="text/xml">'
            f"<accession-number>{accession}</accession-number><filing-type>{ftype}</filing-type>"
            f"<filing-date>{date}</filing-date></content></entry></feed>")


def _atom_empty() -> str:
    return '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'


def _url_map() -> dict:
    return {
        edgar.MF_TICKERS_URL: json.dumps(_MF_MAP),
        edgar.STOCK_TICKERS_URL: json.dumps(_STOCK_MAP),
        edgar.submissions_url(FUND_CIK): json.dumps(_SUBS_FUND),
        edgar.submissions_url(PART_CIK): json.dumps(_SUBS_PART),
        edgar.submissions_url(NEWFUND_CIK): json.dumps(_SUBS_NEWFUND),
        edgar.submissions_url(AAPL_CIK): json.dumps(_SUBS_AAPL),
        edgar.submissions_url(GOLD_CIK): json.dumps(_SUBS_GOLD),
        edgar.submissions_url(UIT_CIK): json.dumps(_SUBS_UIT),
        edgar.archives_doc_url(UIT_CIK, "0005555555-25-000005", "primary_doc.xml"): _NPORT_XML,
        # N-PORT is selected per series via the browse feed (multi-series trusts).
        edgar.browse_series_url("S000099999"): _atom("0001111111-25-000001"),
        edgar.browse_series_url("S000077777"): _atom("0003333333-25-000003"),
        edgar.browse_series_url("S000088888"): _atom_empty(),  # VNEW: no N-PORT yet
        edgar.archives_doc_url(FUND_CIK, "0001111111-25-000001", "primary_doc.xml"): _NPORT_XML,
        edgar.archives_doc_url(PART_CIK, "0003333333-25-000003", "primary_doc.xml"): _NPORT_PART_XML,
    }


def _fake_client(url_map=None):
    url_map = _url_map() if url_map is None else url_map

    def http_get(url, headers):
        if url not in url_map:
            raise KeyError(f"no canned response for {url}")
        return url_map[url]

    return edgar.EdgarClient(http_get=http_get)


@pytest.fixture(autouse=True)
def _reset_lookthrough_state():
    holdings.set_default_client(_fake_client())
    yield
    holdings.set_default_client(None)


def _model(*pairs):
    return portfolio.Model(
        id="MTEST", name="Test Model", mandate_key="balanced", mandate_context="",
        holdings=[portfolio.Holding(t, w) for t, w in pairs],
    )


# --------------------------------------------------------------------------- #
# Pure parser
# --------------------------------------------------------------------------- #
def test_parse_nport_extracts_positions_and_metadata():
    report = edgar.parse_nport(_NPORT_XML)
    assert report.as_of == _AS_OF
    assert report.total_net_assets == 1_000_000_000.0
    assert [p.ticker for p in report.positions] == ["MSFT", "NVDA", ""]  # sorted by weight desc
    assert report.positions[0].weight_pct == 45.0
    assert report.positions[2].asset_label == "Debt"
    assert report.positions[2].cusip == "91282CJL6"
    assert report.parse_errors == 0 and report.truncated is False


def test_parse_nport_bad_xml_raises():
    with pytest.raises(edgar.EdgarError):
        edgar.parse_nport("<not-valid")


def test_parse_browse_atom_selects_latest_nport():
    f = edgar.parse_browse_atom(_atom("0001234567-26-000001", date="2026-06-25"), ["NPORT-P", "NPORT-P/A"])
    assert f is not None and f.accession == "0001234567-26-000001"
    assert edgar.parse_browse_atom(_atom_empty(), ["NPORT-P"]) is None


def test_parse_nport_rejects_entity_declaration():
    bomb = ('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "x">]>'
            '<edgarSubmission><formData><invstOrSecs></invstOrSecs></formData></edgarSubmission>')
    with pytest.raises(edgar.EdgarError):
        edgar.parse_nport(bomb)


# --------------------------------------------------------------------------- #
# Resolution + single look-through
# --------------------------------------------------------------------------- #
def test_resolve_fund_and_stock():
    client = _fake_client()
    fund = client.resolve("vtest")
    assert fund.kind == "fund" and fund.series_id == "S000099999"
    stock = client.resolve("AAPL")
    assert stock.kind == "stock" and stock.cik == str(AAPL_CIK)


def test_fetch_lookthrough_fund_returns_real_positions():
    lt = holdings.fetch_lookthrough("VTEST", client=_fake_client())
    assert lt.resolved and lt.kind == "fund" and lt.source == "sec_nport"
    assert lt.as_of == _AS_OF
    assert {p["ticker"] for p in lt.positions} == {"MSFT", "NVDA", ""}
    assert lt.former_names and lt.former_names[0]["name"] == "Helios Legacy Closed Fund"


def test_fetch_lookthrough_stock_is_leaf():
    lt = holdings.fetch_lookthrough("AAPL", client=_fake_client())
    assert lt.resolved and lt.kind == "stock" and lt.source == "leaf"
    assert lt.positions == []


def test_newly_launched_fund_resolves_but_reports_no_nport_honestly():
    lt = holdings.fetch_lookthrough("VNEW", client=_fake_client())
    assert lt.kind == "fund" and lt.resolved is False
    assert lt.source == "none"
    assert "N-PORT" in lt.warning
    assert lt.former_names[0]["name"] == "Helios Predecessor Fund"


def test_commodity_trust_leaf_is_not_common_equity():
    # GLD/SLV/IAU/USO live in the STOCK ticker map; a 10% gold sleeve was
    # rolled up as full-confidence "Equity (common)" (review finding).
    lt = holdings.fetch_lookthrough("GLDT", client=_fake_client())
    assert lt.resolved and lt.kind == "stock"
    assert lt.asset_class == "Commodity trust / ETP (non-transparent)"
    roll = holdings.model_lookthrough(_model(("GLDT", 0.1), ("AAPL", 0.9)), client=_fake_client())
    classes = roll["exposure"]["asset_class_weights_pct"]
    assert classes["Equity (common)"] == pytest.approx(90.0, abs=0.01)
    assert classes["Commodity trust / ETP (non-transparent)"] == pytest.approx(10.0, abs=0.01)
    states = {p["ticker"]: p["state"] for p in roll["per_holding"]}
    assert states == {"GLDT": "leaf_etp", "AAPL": "leaf"}


def test_registrant_level_nport_filer_gets_real_lookthrough():
    # SPY files trust-level NPORT-P despite living in the stock ticker map:
    # it must get the real look-through, not an opaque equity leaf.
    lt = holdings.fetch_lookthrough("SPYT", client=_fake_client())
    assert lt.resolved and lt.kind == "fund" and lt.source == "sec_nport"
    assert {p["ticker"] for p in lt.positions} == {"MSFT", "NVDA", ""}


def test_mf_map_outage_refuses_to_guess_stock_vs_fund():
    # QQQ appears in BOTH ticker maps; guessing "stock" during a transient MF-map
    # outage froze the misclassification in the look-through cache forever.
    um = _url_map()
    del um[edgar.MF_TICKERS_URL]
    lt = holdings.fetch_lookthrough("AAPL", client=_fake_client(um), use_cache=False)
    assert lt.resolved is False and lt.kind == "unresolved"
    assert "refusing to guess" in lt.warning


def test_unresolved_symbol_is_flagged_not_fabricated():
    lt = holdings.fetch_lookthrough("ZZZZ", client=_fake_client())
    assert lt.resolved is False and lt.kind == "unresolved"
    assert lt.positions == [] and lt.warning


def test_network_failure_degrades_gracefully():
    def boom(url, headers):
        raise OSError("offline")

    lt = holdings.fetch_lookthrough("VTEST", client=edgar.EdgarClient(http_get=boom))
    assert lt.resolved is False and lt.positions == []


def test_summarize_weights_and_identification():
    lt = holdings.fetch_lookthrough("VTEST", client=_fake_client())
    s = holdings.summarize(lt)
    assert s["n_positions"] == 3
    assert s["covered_weight_pct"] == 100.0
    assert s["identified_weight_pct"] == 80.0   # MSFT + NVDA carry tickers; the bond does not
    assert s["uncovered_weight_pct"] == 0.0
    assert s["asset_class_weights_pct"]["Equity (common)"] == 80.0
    assert s["asset_class_weights_pct"]["Debt"] == 20.0
    assert s["warnings"] == []


def test_summarize_partial_coverage_warns():
    lt = holdings.fetch_lookthrough("VPART", client=_fake_client())
    s = holdings.summarize(lt)
    assert s["covered_weight_pct"] == 60.0
    assert s["uncovered_weight_pct"] == 40.0
    assert any("not represented" in w for w in s["warnings"])


# --------------------------------------------------------------------------- #
# Model roll-up + forward provenance
# --------------------------------------------------------------------------- #
def test_model_rollup_combines_lookthrough_and_leaf():
    roll = holdings.model_lookthrough(_model(("VTEST", 0.6), ("AAPL", 0.4)), client=_fake_client())
    cov = roll["coverage"]
    assert cov["looked_through_pct"] == 60.0
    assert cov["leaf_pct"] == 40.0
    assert cov["composition_coverage_pct"] == 100.0
    assert cov["uncovered_pct"] == 0.0
    weights = {d["ticker"]: d["weight_pct"] for d in roll["exposure"]["top_holdings"]}
    assert weights["AAPL"] == pytest.approx(40.0, abs=0.01)
    assert weights["MSFT"] == pytest.approx(27.0, abs=0.01)   # 0.6 * 45%
    eq = roll["exposure"]["asset_class_weights_pct"]["Equity (common)"]
    assert eq == pytest.approx(88.0, abs=0.05)


def test_model_rollup_counts_intra_fund_gap_as_uncovered():
    # VPART lists only 60% of its NAV -> the unseen 40% of that holding's weight
    # must register as uncovered, not be silently treated as fully characterized.
    roll = holdings.model_lookthrough(_model(("VPART", 1.0)), client=_fake_client())
    cov = roll["coverage"]
    assert cov["looked_through_pct"] == pytest.approx(60.0, abs=0.01)
    assert cov["uncovered_pct"] == pytest.approx(40.0, abs=0.01)


def test_model_rollup_records_uncovered_without_inventing():
    roll = holdings.model_lookthrough(_model(("VTEST", 0.5), ("ZZZZ", 0.5)), client=_fake_client())
    cov = roll["coverage"]
    assert cov["looked_through_pct"] == 50.0
    assert cov["uncovered_pct"] == 50.0
    assert any(u["ticker"] == "ZZZZ" for u in cov["unresolved"])


def test_forward_research_axis_states():
    fresh = {"oldest": _AS_OF, "newest": _AS_OF}
    ready = provenance.forward_research({"n_holdings": 2, "looked_through_pct": 60.0,
                                         "leaf_pct": 40.0, "uncovered_pct": 0.0,
                                         "composition_coverage_pct": 100.0, "as_of_range": fresh})
    assert ready["forward_mode"] == "ready" and ready["eligible_for_forward_research"]

    partial = provenance.forward_research({"n_holdings": 2, "looked_through_pct": 50.0,
                                           "leaf_pct": 0.0, "uncovered_pct": 50.0,
                                           "composition_coverage_pct": 50.0, "as_of_range": fresh,
                                           "unresolved": [{"ticker": "ZZZZ"}]})
    assert partial["forward_mode"] == "partial"
    assert partial["eligible_for_forward_research"] is False
    assert "ZZZZ" in partial["unresolved_tickers"]

    blocked = provenance.forward_research({"n_holdings": 0})
    assert blocked["forward_mode"] == "unavailable"


def test_forward_research_staleness_gate():
    cov = {"n_holdings": 1, "looked_through_pct": 100.0, "leaf_pct": 0.0, "uncovered_pct": 0.0,
           "composition_coverage_pct": 100.0, "as_of_range": {"oldest": "2024-01-01", "newest": "2024-01-01"}}
    assert provenance.forward_research(cov, now=datetime.date(2024, 2, 1))["forward_mode"] == "ready"

    warned = provenance.forward_research(cov, now=datetime.date(2024, 6, 1))  # ~152 days
    assert warned["forward_mode"] == "ready" and warned["warnings"]

    stale = provenance.forward_research(cov, now=datetime.date(2024, 11, 1))  # ~305 days
    assert stale["forward_mode"] == "partial" and stale["eligible_for_forward_research"] is False

    blocked = provenance.forward_research(cov, now=datetime.date(2025, 6, 1))  # ~517 days
    assert blocked["forward_mode"] == "unavailable"


# --------------------------------------------------------------------------- #
# Endpoints (offline via injected default client)
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def test_endpoint_lookthrough_fund(client):
    body = client.post("/api/lookthrough/refresh", json={"ticker": "VTEST"}).get_json()
    assert body["resolved"] and body["kind"] == "fund"
    assert body["forward"]["forward_mode"] == "ready"
    assert body["summary"]["n_positions"] == 3
    assert body["former_names"][0]["name"] == "Helios Legacy Closed Fund"


def test_endpoint_lookthrough_partial_fund_is_not_ready(client):
    body = client.post("/api/lookthrough/refresh", json={"ticker": "VPART"}).get_json()
    assert body["resolved"] and body["summary"]["covered_weight_pct"] == 60.0
    assert body["forward"]["forward_mode"] == "partial"


def test_endpoint_lookthrough_requires_symbol(client):
    assert client.get("/api/lookthrough").status_code == 400


def test_endpoint_model_lookthrough(client):
    portfolio.register(_model(("VTEST", 0.6), ("AAPL", 0.4)))
    body = client.post("/api/model/lookthrough/refresh", json={"id": "MTEST"}).get_json()
    assert body["coverage"]["composition_coverage_pct"] == 100.0
    assert body["forward"]["forward_mode"] == "ready"
    assert body["exposure"]["n_underlying"] >= 3


def test_endpoint_model_lookthrough_unknown_model(client):
    assert client.get("/api/model/lookthrough?id=NOPE").status_code == 404

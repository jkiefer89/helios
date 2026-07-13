"""IBKR Flex adapter (deep-review completion slice) — fully offline.

Injectable HTTP; parse -> ledger contract; cross-path dedupe against the CSV
importer; dormant/honest when unconfigured.
"""
from __future__ import annotations

import pytest

from engine import custodian, ledger, persistence

SEND_OK = """<FlexStatementResponse timestamp='12 July, 2026 10:00 PM EDT'>
<Status>Success</Status><ReferenceCode>1234567890</ReferenceCode>
<Url>https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement</Url>
</FlexStatementResponse>"""

IN_PROGRESS = """<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode>
<ErrorMessage>Statement generation in progress. Please try again shortly.</ErrorMessage>
</FlexStatementResponse>"""

STATEMENT = """<FlexQueryResponse queryName="helios" type="AF">
<FlexStatements count="1">
<FlexStatement accountId="U1234567" fromDate="20260601" toDate="20260630" period="LastMonth">
<Trades>
<Trade symbol="JPM" tradeDate="20260601" quantity="100" tradePrice="200.00"
       ibCommission="-1.50" netCash="-20001.50" buySell="BUY" ibExecID="0000e1a2.001"/>
<Trade symbol="MSFT" tradeDate="20260615" quantity="-50" tradePrice="400.00"
       ibCommission="-1.00" netCash="19999.00" buySell="SELL" tradeID="987654"/>
<Trade symbol="" tradeDate="20260616" quantity="10" tradePrice="5" buySell="BUY"/>
</Trades>
<CashTransactions>
<CashTransaction type="Dividends" symbol="JPM" dateTime="20260615" amount="55.00" transactionID="777001"/>
<CashTransaction type="Withholding Tax" symbol="JPM" dateTime="20260615" amount="-8.25"/>
<CashTransaction type="Deposits/Withdrawals" dateTime="20260620" amount="50000.00" transactionID="777003"/>
</CashTransactions>
<OpenPositions>
<OpenPosition symbol="JPM" position="100" markPrice="205.00" positionValue="20500.00" costBasisMoney="20001.50"/>
<OpenPosition symbol="MSFT" position="0" markPrice="400.00" positionValue="0"/>
</OpenPositions>
<CashReport>
<CashReportCurrency currency="BASE_SUMMARY" endingCash="55045.25"/>
</CashReport>
</FlexStatement>
</FlexStatements>
</FlexQueryResponse>"""


@pytest.fixture()
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    st = persistence.get_store()
    yield st
    persistence.reset_store_for_tests()


def _fake_http(responses: list[str]):
    calls: list[str] = []

    def get(url: str) -> str:
        calls.append(url)
        return responses.pop(0)

    get.calls = calls
    return get


def test_unconfigured_is_dormant_and_honest(monkeypatch):
    monkeypatch.delenv("HELIOS_IBKR_FLEX_TOKEN", raising=False)
    monkeypatch.delenv("HELIOS_IBKR_FLEX_QUERY_ID", raising=False)
    out = custodian.import_flex()
    assert out["status"] == "not_configured"
    assert "CSV import remains" in out["note"]


def test_fetch_retries_while_generating():
    get = _fake_http([SEND_OK, IN_PROGRESS, STATEMENT])
    xml = custodian.fetch_flex_statement("tok", "42", http_get=get, wait_s=0.0)
    assert "<FlexQueryResponse" in xml
    assert len(get.calls) == 3
    assert "t=tok" in get.calls[0] and "q=42" in get.calls[0]
    assert "q=1234567890" in get.calls[1]


def test_parse_maps_trades_cash_and_positions():
    parsed = custodian.parse_flex_statement(STATEMENT)
    assert parsed["account_id"] == "U1234567"
    assert parsed["as_of"] == "2026-06-30"
    sides = [f["side"] for f in parsed["fills"]]
    assert sides.count("buy") == 1 and sides.count("sell") == 1
    assert sides.count("non_trade") == 2 and sides.count("deposit") == 1
    buy = next(f for f in parsed["fills"] if f["side"] == "buy")
    assert buy["ticker"] == "JPM" and buy["shares"] == 100.0 and buy["fees"] == 1.5
    tax = next(f for f in parsed["fills"]
               if f["side"] == "non_trade" and f["metadata"]["subtype"] == "tax")
    assert tax["amount"] == -8.25
    assert parsed["cash"] == 55045.25
    assert parsed["total_value"] == pytest.approx(20500.0 + 55045.25)
    assert any("incomplete" in w for w in parsed["warnings"])   # the empty-symbol trade


def test_import_lands_in_ledger_and_is_idempotent(store, monkeypatch):
    monkeypatch.setenv("HELIOS_IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setenv("HELIOS_IBKR_FLEX_QUERY_ID", "42")
    out = custodian.import_flex(http_get=_fake_http([SEND_OK, STATEMENT]))
    assert out["status"] == "imported"
    assert out["inserted"] == 5 and out["duplicates"] == 0
    assert out["snapshot_recorded"] is True
    again = custodian.import_flex(http_get=_fake_http([SEND_OK, STATEMENT]))
    assert again["inserted"] == 0 and again["duplicates"] == 5
    perf = ledger.account_performance("U1234567")
    # One snapshot only -> honestly insufficient, but the data landed.
    assert perf["status"] == "insufficient_snapshots"
    assert store.account_positions("U1234567")[0]["ticker"] == "JPM"


def test_flex_trades_dedupe_against_csv_import(store):
    """The same trade arriving via CSV and via Flex must be ONE fill."""
    csv_raw = ("Trade Date,Symbol,Action,Quantity,Price,Fees,Amount,Exec Id\n"
               "2026-06-01,JPM,BUY,100,200.00,1.50,-20001.50,0000e1a2.001\n").encode()
    ledger.import_fills(csv_raw, "U1234567")
    parsed = custodian.parse_flex_statement(STATEMENT)
    flex_buy = next(f for f in parsed["fills"] if f["side"] == "buy")
    result = store.record_fills([flex_buy])
    assert result["inserted"] == 0 and result["duplicates"] == 1


# --------------------------------------------------------------------------- #
# Adversarial-review locks (confirmed defects, fixed)
# --------------------------------------------------------------------------- #
def test_flow_keys_match_the_csv_path(store):
    """A deposit arriving via CSV and via Flex must be ONE fill — duplicated
    flows double the Dietz external-flow terms (live-reproduced corruption)."""
    csv_raw = ("Trade Date,Symbol,Action,Quantity,Price,Fees,Amount\n"
               "2026-06-20,,DEPOSIT,,,,50000.00\n").encode()
    ledger.import_fills(csv_raw, "U1234567")
    parsed = custodian.parse_flex_statement(STATEMENT)
    deposit = next(f for f in parsed["fills"] if f["side"] == "deposit")
    result = store.record_fills([deposit])
    assert result["inserted"] == 0 and result["duplicates"] == 1


def test_cash_keys_stable_across_shifted_windows():
    """transactionID keying: prepending an extra CashTransaction (a shifted,
    overlapping query window) must not change existing rows' dedupe keys."""
    shifted = STATEMENT.replace(
        '<CashTransaction type="Dividends"',
        '<CashTransaction type="Broker Interest Received" dateTime="20260601" amount="1.23" '
        'transactionID="776000"/>\n<CashTransaction type="Dividends"', 1)
    def keyed(xml):
        return {f["dedupe_key"] for f in custodian.parse_flex_statement(xml)["fills"]
                if f["side"] != "non_trade" or "flex-" not in "|".join(f["metadata"].values())}
    def stable_keys(xml):
        out = {}
        for f in custodian.parse_flex_statement(xml)["fills"]:
            if f["side"] in {"buy", "sell", "deposit", "withdraw"}:
                out[(f["side"], f["ticker"], f["trade_date"])] = f["dedupe_key"]
            elif f["metadata"].get("subtype") == "dividend":   # has transactionID
                out[("dividend", f["ticker"], f["trade_date"])] = f["dedupe_key"]
        return out
    base = stable_keys(STATEMENT)
    moved = stable_keys(shifted)
    for key, dedupe in base.items():
        assert moved[key] == dedupe            # txid-keyed rows never shift
    # The transactionID-less tax row falls back positionally and WARNS.
    warnings = custodian.parse_flex_statement(STATEMENT)["warnings"]
    assert any("transactionID" in w for w in warnings)


def test_iso_dates_parse_and_garbage_dates_skip():
    """Dash-format Flex dates must parse; unparseable dates skip loudly rather
    than storing garbage that crashes the performance endpoint later."""
    iso = STATEMENT.replace('dateTime="20260615"', 'dateTime="2026-06-15;12:00:00"')
    parsed = custodian.parse_flex_statement(iso)
    div = next(f for f in parsed["fills"]
               if f["side"] == "non_trade" and f["metadata"]["subtype"] == "dividend")
    assert div["trade_date"] == "2026-06-15"
    garbage = STATEMENT.replace('dateTime="20260615"', 'dateTime="junkdate"')
    parsed2 = custodian.parse_flex_statement(garbage)
    assert any("unparseable date" in w for w in parsed2["warnings"])
    assert all(f["trade_date"] != "junkdate"[:10] for f in parsed2["fills"])


def test_network_and_xml_failures_are_labeled_not_generic():
    with pytest.raises(RuntimeError, match="non-XML"):
        custodian.fetch_flex_statement("tok", "42",
                                       http_get=_fake_http(["<html><meta>outage page"]))
    with pytest.raises(ValueError, match="not valid XML"):
        custodian.parse_flex_statement("<FlexQueryResponse><broken")


def test_ibkr_native_csv_headers_dedupe_against_flex(store):
    """Real IBKR export headers (IBExecID, NetCash, Buy/Sell, signed sells)
    must produce the same fill keys as the Flex path."""
    csv_raw = ("Trade Date,Symbol,Buy/Sell,Quantity,Price,Fees,NetCash,IBExecID\n"
               "2026-06-01,JPM,BUY,100,200.00,1.50,-20001.50,0000e1a2.001\n"
               "2026-06-15,MSFT,SELL,-50,400.00,1.00,19999.00,987654\n").encode()
    out = ledger.import_fills(csv_raw, "U1234567")
    assert out["inserted"] == 2                # signed sell imports, not skipped
    parsed = custodian.parse_flex_statement(STATEMENT)
    trades = [f for f in parsed["fills"] if f["side"] in {"buy", "sell"}]
    result = store.record_fills(trades)
    assert result["inserted"] == 0 and result["duplicates"] == 2

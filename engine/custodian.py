"""IBKR Flex Web Service adapter — custodian actuals without CSV round-trips.

Dormant until HELIOS_IBKR_FLEX_TOKEN and HELIOS_IBKR_FLEX_QUERY_ID are set
(IBKR: Performance & Reports > Flex Queries; the query should include Trades,
Cash Transactions, Open Positions, and the Cash Report). CSV import remains a
first-class path — this adapter feeds the SAME ledger contract. Trade and
deposit/withdrawal dedupe keys are constructed identically to the CSV path,
so those reconcile to no-ops across paths. Non-trade rows (dividends/fees)
use IBKR's transactionID, which a CSV export does not carry — mixing BOTH
paths over the same period can double-count non-trade income; the import
warns when that risk exists.

Two-step Flex protocol: SendRequest returns a reference code; GetStatement
returns the XML (retrying while IBKR generates it). All HTTP is injectable
for offline tests.
"""
from __future__ import annotations

import hashlib
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Callable

from . import data, persistence

_FLEX_BASE = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService"
_SEND_URL = _FLEX_BASE + ".SendRequest"
_GET_URL = _FLEX_BASE + ".GetStatement"
_MAX_XML_BYTES = 20 * 1024 * 1024
_GENERATION_RETRIES = 5
_GENERATION_WAIT_S = 3.0

_NON_TRADE_TYPE_MAP = (
    ("dividend", "dividend"),
    ("payment in lieu", "dividend"),
    ("interest", "interest"),
    ("fee", "fee"),
    ("tax", "tax"),
    ("withholding", "tax"),
)


def flex_configured() -> bool:
    return bool(os.environ.get("HELIOS_IBKR_FLEX_TOKEN", "").strip()
                and os.environ.get("HELIOS_IBKR_FLEX_QUERY_ID", "").strip())


def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Helios Research Terminal"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed https host)
            body = resp.read(_MAX_XML_BYTES + 1)
    except OSError as exc:   # URLError/HTTPError/timeouts — routine for a remote pull
        raise RuntimeError(f"IBKR Flex network error: {exc}") from exc
    if len(body) > _MAX_XML_BYTES:
        raise RuntimeError("IBKR Flex response exceeds the 20MB cap — narrow the query "
                           "window rather than importing a truncated statement.")
    return body.decode("utf-8", errors="replace")


def fetch_flex_statement(token: str, query_id: str,
                         http_get: Callable[[str], str] | None = None,
                         wait_s: float = _GENERATION_WAIT_S) -> str:
    """SendRequest -> poll GetStatement until IBKR finishes generating."""
    get = http_get or _http_get
    send = get(f"{_SEND_URL}?t={urllib.parse.quote(token)}"
               f"&q={urllib.parse.quote(query_id)}&v=3")
    try:
        root = ET.fromstring(send)
    except ET.ParseError as exc:
        raise RuntimeError(
            f"IBKR Flex returned non-XML (outage/rate-limit page?): {send[:120]!r}") from exc
    status = (root.findtext("Status") or "").strip()
    if status != "Success":
        raise RuntimeError(
            f"IBKR Flex SendRequest failed: {(root.findtext('ErrorMessage') or status or 'unknown error').strip()}")
    reference = (root.findtext("ReferenceCode") or "").strip()
    if not reference:
        raise RuntimeError("IBKR Flex SendRequest returned no reference code.")
    last_error = ""
    for attempt in range(_GENERATION_RETRIES):
        body = get(f"{_GET_URL}?q={urllib.parse.quote(reference)}"
                   f"&t={urllib.parse.quote(token)}&v=3")
        if "<FlexQueryResponse" in body:
            return body
        try:
            err_root = ET.fromstring(body)
            last_error = (err_root.findtext("ErrorMessage") or "").strip()
            code = (err_root.findtext("ErrorCode") or "").strip()
        except ET.ParseError:
            last_error, code = body[:200], ""
        if code not in {"1019", "1021"}:   # still generating / try later
            break
        if attempt < _GENERATION_RETRIES - 1:
            time.sleep(wait_s)
    raise RuntimeError(f"IBKR Flex statement not ready: {last_error or 'no response'}")


def _is_iso_date(value: str) -> bool:
    try:
        from datetime import date
        date.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def _num(value: str | None) -> float | None:
    try:
        return float(str(value).replace(",", "")) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _classify_cash_transaction(tx_type: str) -> str:
    text = (tx_type or "").strip().lower()
    if "deposit" in text or "withdraw" in text:
        return "flow"
    for word, subtype in _NON_TRADE_TYPE_MAP:
        if word in text:
            return subtype
    return "other"


def parse_flex_statement(xml_text: str) -> dict[str, Any]:
    """FlexQueryResponse XML -> the ledger's fill/position/snapshot contract.

    Trades map to buy/sell fills (dedupe keys IDENTICAL to the CSV path);
    CashTransactions map to typed non-trade rows or external flows; the Cash
    Report and OpenPositions become the account snapshot. Unknown sections are
    counted, never silently dropped.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"IBKR Flex statement is not valid XML: {exc}") from exc
    stmt = root.find(".//FlexStatement")
    if stmt is None:
        raise ValueError("No FlexStatement element in the IBKR response.")
    account_id = (stmt.get("accountId") or "").strip()
    if not account_id:
        raise ValueError("FlexStatement carries no accountId.")
    to_date = (stmt.get("toDate") or "").strip()
    as_of = f"{to_date[:4]}-{to_date[4:6]}-{to_date[6:8]}" if len(to_date) == 8 else ""
    warnings: list[str] = []
    fills: list[dict[str, Any]] = []

    for trade in stmt.iter("Trade"):
        symbol = data.clean_symbol(trade.get("symbol") or "", fallback="")
        side = {"BUY": "buy", "SELL": "sell"}.get((trade.get("buySell") or "").upper(), "")
        raw_date = (trade.get("tradeDate") or "").strip()
        trade_date = (f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                      if len(raw_date) == 8 else raw_date[:10])
        shares = _num(trade.get("quantity"))
        price = _num(trade.get("tradePrice"))
        fees = _num(trade.get("ibCommission"))
        net_cash = _num(trade.get("netCash"))
        exec_id = (trade.get("ibExecID") or trade.get("tradeID") or "").strip()
        if not symbol or not side or not trade_date or not shares or not price:
            warnings.append(f"Trade skipped (incomplete): symbol={trade.get('symbol')} "
                            f"date={raw_date} qty={trade.get('quantity')}")
            continue
        shares_abs = abs(float(shares))
        price_f = float(price)
        amount = net_cash if net_cash is not None else None
        # Same key construction as ledger.parse_fills_csv -> cross-path dedupe.
        key_raw = (f"{account_id}|{trade_date}|{symbol}|{side}|{shares_abs:.6f}"
                   f"|{price_f:.6f}|{(amount or 0):.2f}|{exec_id}")
        fills.append({
            "fill_id": hashlib.sha256((key_raw + "|id").encode()).hexdigest()[:32],
            "account_id": account_id,
            "trade_date": trade_date,
            "settle_date": "",
            "ticker": symbol,
            "side": side,
            "shares": shares_abs,
            "price": price_f,
            "fees": abs(float(fees or 0.0)),
            "amount": amount,
            "source_file": "ibkr_flex",
            "decision_id": "",
            "metadata": {"exec_id": exec_id} if exec_id else {},
            "dedupe_key": hashlib.sha256(key_raw.encode()).hexdigest()[:40],
        })

    non_trade = 0
    for index, tx in enumerate(stmt.iter("CashTransaction")):
        tx_type = tx.get("type") or ""
        kind = _classify_cash_transaction(tx_type)
        raw_date = (tx.get("dateTime") or tx.get("reportDate") or "").split(";")[0].strip()
        tx_date = (f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                   if len(raw_date) == 8 and raw_date.isdigit() else raw_date[:10])
        amount = _num(tx.get("amount"))
        symbol = data.clean_symbol(tx.get("symbol") or "", fallback="")
        if amount is None or not _is_iso_date(tx_date):
            warnings.append(f"CashTransaction skipped (incomplete/unparseable date "
                            f"'{raw_date}'): type={tx_type}")
            continue
        if kind == "flow":
            side = "deposit" if amount >= 0 else "withdraw"
            # SAME slot layout as ledger.parse_fills_csv's flow rows (which
            # carry no positional index): a Flex pull and a CSV export of the
            # same deposit reconcile to ONE fill. Duplicated flows double the
            # Dietz external-flow terms — the worst silent corruption here.
            key_raw = (f"{account_id}|{tx_date}||{side}|0.000000|0.000000|{amount:.2f}|")
            fills.append({
                "fill_id": hashlib.sha256((key_raw + "|id").encode()).hexdigest()[:32],
                "account_id": account_id, "trade_date": tx_date, "settle_date": "",
                "ticker": "", "side": side, "shares": 0.0, "price": 0.0, "fees": 0.0,
                "amount": float(amount), "source_file": "ibkr_flex", "decision_id": "",
                "metadata": {"raw_action": tx_type[:60]},
                "dedupe_key": hashlib.sha256(key_raw.encode()).hexdigest()[:40],
            })
            continue
        non_trade += 1
        tx_id = (tx.get("transactionID") or "").strip()
        if not tx_id:
            warnings.append(f"CashTransaction {tx_type} {tx_date} has no transactionID — "
                            "add it to the Flex query fields; falling back to positional "
                            "keying, which is only stable for identical query windows.")
        nt_key = (f"{account_id}|{tx_date}|{symbol}|non_trade|{kind}|{amount}"
                  f"|{tx_id or f'flex-{index}'}")
        fills.append({
            "fill_id": hashlib.sha256((nt_key + "|id").encode()).hexdigest()[:32],
            "account_id": account_id, "trade_date": tx_date,
            "ticker": symbol, "side": "non_trade",
            "shares": 0.0, "price": 0.0, "fees": 0.0,
            "amount": float(amount), "source_file": "ibkr_flex",
            "metadata": {"subtype": kind, "raw_action": tx_type[:60]},
            "dedupe_key": hashlib.sha256(nt_key.encode()).hexdigest()[:40],
        })

    positions = []
    for pos in stmt.iter("OpenPosition"):
        symbol = data.clean_symbol(pos.get("symbol") or "", fallback="")
        shares = _num(pos.get("position"))
        if not symbol or shares is None:
            continue
        positions.append({
            "ticker": symbol,
            "shares": float(shares),
            "price": _num(pos.get("markPrice")),
            "market_value": _num(pos.get("positionValue")),
            "cost_basis": _num(pos.get("costBasisMoney")),
        })

    cash = None
    report = stmt.find(".//CashReportCurrency[@currency='BASE_SUMMARY']")
    if report is None:
        report = stmt.find(".//CashReportCurrency")
    if report is not None:
        cash = _num(report.get("endingCash"))
    total_value = sum(p["market_value"] or 0.0 for p in positions) + (cash or 0.0)

    return {
        "account_id": account_id,
        "as_of": as_of,
        "fills": fills,
        "non_trade_recorded": non_trade,
        "positions": positions,
        "cash": cash,
        "total_value": round(total_value, 2),
        "warnings": warnings,
    }


def import_flex(http_get: Callable[[str], str] | None = None) -> dict[str, Any]:
    """Pull the configured Flex query and land it in the ledger store.

    Idempotent by construction (fill dedupe keys); the positions snapshot
    upserts by (account, as_of). Returns the same honest counters as the CSV
    import so both paths read identically in the UI.
    """
    if not flex_configured():
        return {
            "status": "not_configured",
            "note": ("Set HELIOS_IBKR_FLEX_TOKEN and HELIOS_IBKR_FLEX_QUERY_ID to enable "
                     "the IBKR Flex pull. CSV import remains fully supported."),
        }
    token = os.environ["HELIOS_IBKR_FLEX_TOKEN"].strip()
    query_id = os.environ["HELIOS_IBKR_FLEX_QUERY_ID"].strip()
    xml_text = fetch_flex_statement(token, query_id, http_get=http_get)
    parsed = parse_flex_statement(xml_text)
    store = persistence.get_store()
    store.upsert_ledger_account(parsed["account_id"])
    # Mixed-source honesty: non-trade rows cannot cross-dedupe against a CSV
    # import (CSV carries no transactionID), so flag the double-count risk
    # when this account already has CSV-imported activity.
    existing = store.fills(parsed["account_id"], limit=20000)
    if any((f.get("source_file") or "") not in ("", "ibkr_flex") for f in existing):
        parsed["warnings"].insert(0, (
            "This account already holds CSV-imported activity. Trades and "
            "deposits/withdrawals dedupe across paths; dividends/fees do NOT — "
            "verify non-trade income is not double-counted for overlapping periods."))
    result = store.record_fills(parsed["fills"])
    snapshot_recorded = False
    if parsed["as_of"] and (parsed["positions"] or parsed["cash"] is not None):
        store.record_account_snapshot(
            {"account_id": parsed["account_id"], "as_of": parsed["as_of"],
             "cash": parsed["cash"], "total_value": parsed["total_value"],
             "source": "ibkr_flex"},
            parsed["positions"])
        snapshot_recorded = True
    store.flush()
    return {
        "status": "imported",
        "account_id": parsed["account_id"],
        "as_of": parsed["as_of"],
        "parsed": len(parsed["fills"]),
        "inserted": result.get("inserted", 0),
        "duplicates": result.get("duplicates", 0),
        "non_trade_recorded": parsed["non_trade_recorded"],
        "snapshot_recorded": snapshot_recorded,
        "n_positions": len(parsed["positions"]),
        "warnings": parsed["warnings"][:25],
    }

"""SEC event layer: recent 8-K material events + Form 4 insider transactions.

Regulatory-grade, free, minutes-fresh: 8-Ks say WHAT happened (guidance,
impairments, officer exits, restatements); Form 4s say what insiders DID about
it (open-market buys vs sells). Both ride the existing EDGAR client (rate
limited, retried, honest User-Agent) and are TTL-cached per symbol so the
analyze/radar paths never hammer SEC.

Everything degrades honestly: offline or unresolvable symbols return
``{"available": False, "reason": ...}`` — never fabricated events.
"""
from __future__ import annotations

import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

from . import edgar
from .holdings import default_client

WINDOW_DAYS = 45
_FORM4_PARSE_BUDGET = 5      # newest Form 4 XMLs parsed per symbol per refresh
_MAX_8KS = 12
_CACHE_TTL_S = 3600.0
_CACHE_MAX = 256

_LOCK = threading.RLock()
_CACHE: dict[str, tuple[float, dict]] = {}

# 8-K item codes -> plain-language labels (the ones that move research).
ITEM_LABELS = {
    "1.01": "entered a material agreement",
    "1.02": "terminated a material agreement",
    "1.03": "bankruptcy or receivership",
    "1.05": "material cybersecurity incident",
    "2.01": "completed acquisition or disposition",
    "2.02": "results of operations (earnings)",
    "2.03": "created a direct financial obligation",
    "2.04": "triggering event accelerating a financial obligation",
    "2.05": "exit/disposal costs",
    "2.06": "material impairment",
    "3.01": "delisting or listing-standard notice",
    "4.01": "auditor change",
    "4.02": "non-reliance on prior financials (restatement risk)",
    "5.01": "change in control",
    "5.02": "officer/director departure or appointment",
    "5.03": "charter/bylaw or fiscal-year change",
    "5.07": "shareholder vote results",
    "7.01": "Regulation FD disclosure",
    "8.01": "other material event",
    "9.01": "exhibits",
}
# Items that should raise an eyebrow on their own.
NOTABLE_ITEMS = {"1.02", "1.03", "1.05", "2.04", "2.05", "2.06", "3.01", "4.01", "4.02",
                 "5.01", "5.02"}


def invalidate_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def _recent_rows(submissions: dict) -> list[dict]:
    recent = ((submissions.get("filings") or {}).get("recent")) or {}
    forms = recent.get("form") or []
    rows = []
    for i, form in enumerate(forms):
        def col(name):  # noqa: B023 — i is read immediately
            values = recent.get(name) or []
            return values[i] if i < len(values) else ""
        rows.append({
            "form": str(form).upper(),
            "filing_date": str(col("filingDate")),
            "accession": str(col("accessionNumber")),
            "primary_document": str(col("primaryDocument")),
            "items": str(col("items")),
        })
    return rows


def _within_window(date_text: str, cutoff: datetime) -> bool:
    try:
        return datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc) >= cutoff
    except ValueError:
        return False


def _parse_form4_xml(xml_text: str) -> dict[str, Any] | None:
    """Open-market purchase/sale totals from one Form 4 (codes P and S only)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    owner = (root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName") or "").strip()
    is_officer = (root.findtext(".//reportingOwner/reportingOwnerRelationship/isOfficer") or "").strip()
    buys = sells = 0
    buy_shares = sell_shares = 0.0
    # Both tables count: derivative-only Form 4s (options/warrants bought or
    # sold on the open market, codes P/S) previously read as "no signal"
    # (review finding).
    transactions = (root.findall(".//nonDerivativeTransaction")
                    + root.findall(".//derivativeTransaction"))
    for txn in transactions:
        code = (txn.findtext(".//transactionCoding/transactionCode") or "").strip().upper()
        try:
            shares = float(txn.findtext(".//transactionAmounts/transactionShares/value") or 0)
        except ValueError:
            shares = 0.0
        if code == "P":
            buys += 1
            buy_shares += shares
        elif code == "S":
            sells += 1
            sell_shares += shares
    if not buys and not sells:
        return None  # grants/exercises/gifts only — not an open-market signal
    return {
        "owner": owner[:80],
        "is_officer": is_officer in {"1", "true", "True"},
        "buys": buys, "sells": sells,
        "buy_shares": round(buy_shares), "sell_shares": round(sell_shares),
    }


def events_cached(symbol: str) -> dict[str, Any] | None:
    """Get-only cache read for latency-sensitive surfaces (the radar): the
    events dict if something already fetched it this hour, else None. Never
    makes a network call."""
    sym = (symbol or "").strip().upper()
    with _LOCK:
        hit = _CACHE.get(sym)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL_S:
        return dict(hit[1])
    return None


def events_for(symbol: str, window_days: int = WINDOW_DAYS, client=None) -> dict[str, Any]:
    """Recent 8-K events + Form 4 insider activity for one symbol (cached 1h)."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return {"available": False, "reason": "No symbol."}
    with _LOCK:
        hit = _CACHE.get(sym)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL_S:
            return dict(hit[1])
    client = client or default_client()
    try:
        res = client.resolve(sym)
        submissions = client.get_submissions(res.cik)
    except edgar.EdgarError as exc:
        return {"available": False, "reason": str(exc)}
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(window_days))
    rows = _recent_rows(submissions)

    eight_ks = []
    for row in rows:
        if row["form"] not in {"8-K", "8-K/A"} or not _within_window(row["filing_date"], cutoff):
            continue
        codes = [c.strip() for c in row["items"].replace(";", ",").split(",") if c.strip()]
        labels = [ITEM_LABELS.get(c, c) for c in codes]
        eight_ks.append({
            "filing_date": row["filing_date"],
            "form": row["form"],
            "items": codes,
            "labels": labels,
            "notable": any(c in NOTABLE_ITEMS for c in codes),
            "url": edgar.archives_doc_url(res.cik, row["accession"], row["primary_document"])
            if row["primary_document"] else "",
        })
        if len(eight_ks) >= _MAX_8KS:
            break

    form4_rows = [r for r in rows if r["form"] == "4" and _within_window(r["filing_date"], cutoff)]
    parsed, purchases, sales = [], 0, 0
    buy_shares = sell_shares = 0.0
    attempted = fetch_failures = 0
    for row in form4_rows[:_FORM4_PARSE_BUDGET]:
        doc = row["primary_document"].split("/")[-1]  # strip the xsl viewer prefix
        if not doc.lower().endswith(".xml"):
            continue
        attempted += 1
        try:
            xml_text = client.get_text(edgar.archives_doc_url(res.cik, row["accession"], doc))
        except edgar.EdgarError:
            fetch_failures += 1  # counted honestly — never claimed as parsed
            continue
        detail = _parse_form4_xml(xml_text)
        if detail:
            parsed.append({"filing_date": row["filing_date"], **detail})
            purchases += detail["buys"]
            sales += detail["sells"]
            buy_shares += detail["buy_shares"]
            sell_shares += detail["sell_shares"]

    # Direction by SHARE volume when known (a 50,000-share sale must outvote
    # three 100-share buys — review finding), transaction counts as fallback.
    if buy_shares or sell_shares:
        net_signal = ("buying" if buy_shares > sell_shares else
                      "selling" if sell_shares > buy_shares else "mixed")
    else:
        net_signal = ("buying" if purchases > sales else "selling" if sales > purchases
                      else "mixed" if purchases else "none")
    note = (f"Parsed {len(parsed)} of {len(form4_rows)} Form 4 filings "
            f"({attempted} attempted, {fetch_failures} fetch failures); direction is "
            "share-volume weighted; grants/exercises excluded — only open-market "
            "trades (codes P/S) count.")
    if fetch_failures and not parsed:
        net_signal = "unknown"
        note += " No filings could be parsed — insider direction is UNKNOWN, not calm."
    insider = {
        "filings_in_window": len(form4_rows),
        "parsed": parsed,
        "open_market_purchases": purchases,
        "open_market_sales": sales,
        "buy_shares": round(buy_shares),
        "sell_shares": round(sell_shares),
        "net_signal": net_signal,
        "note": note,
    }
    result = {
        "available": True,
        "symbol": sym,
        "window_days": int(window_days),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "eight_ks": eight_ks,
        "notable_8k": any(e["notable"] for e in eight_ks),
        "insider": insider,
        "source": "sec_edgar",
    }
    with _LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)), None)
        _CACHE[sym] = (time.monotonic(), result)
    return dict(result)

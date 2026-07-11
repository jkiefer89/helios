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
# Failures are cached too, briefly: a degraded EDGAR is probed once per window
# instead of on every analyze call (review finding — no negative caching).
_CACHE_NEG_TTL_S = 300.0
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
    # isinstance guards throughout: a malformed EDGAR payload ({"filings":
    # ["oops"]}) crashed the whole analyze route, and a scalar column (string
    # filingDate) silently yielded per-character garbage (review finding).
    filings = submissions.get("filings") if isinstance(submissions, dict) else None
    recent = filings.get("recent") if isinstance(filings, dict) else None
    recent = recent if isinstance(recent, dict) else {}
    forms = recent.get("form")
    forms = forms if isinstance(forms, list) else []
    rows = []
    for i, form in enumerate(forms):
        def col(name):  # noqa: B023 — i is read immediately
            values = recent.get(name)
            return values[i] if isinstance(values, list) and i < len(values) else ""
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
    """Open-market purchase/sale totals from one Form 4 (codes P and S only).

    Returns ``None`` for UNUSABLE documents (bad XML, wrong document type) and
    ``{}`` for genuinely parsed grants-only filings — callers must count the
    two differently, or all-failed parsing reads as benign calm (review
    finding)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    if root.tag.split("}")[-1] != "ownershipDocument":
        return None  # well-formed XHTML error page / wrong document — NOT parsed
    owner = (root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName") or "").strip()
    is_officer = (root.findtext(".//reportingOwner/reportingOwnerRelationship/isOfficer") or "").strip()
    buys = sells = 0
    buy_shares = sell_shares = 0.0
    unquantified = 0
    # Both tables count: derivative-only Form 4s (options/warrants bought or
    # sold on the open market, codes P/S) previously read as "no signal"
    # (review finding).
    transactions = (root.findall(".//nonDerivativeTransaction")
                    + root.findall(".//derivativeTransaction"))
    for txn in transactions:
        code = (txn.findtext(".//transactionCoding/transactionCode") or "").strip().upper()
        raw_shares = (txn.findtext(".//transactionAmounts/transactionShares/value") or "").strip()
        try:
            shares = float(raw_shares) if raw_shares else None
        except ValueError:
            shares = None
        if code in {"P", "S"} and shares is None:
            # Footnote-only/formatted share counts: coercing to 0.0 let one
            # tiny parsed buy outvote an unparseable large sale in the
            # share-volume vote (review finding). Count the trade, flag the
            # missing quantity.
            unquantified += 1
            shares = 0.0
        if code == "P":
            buys += 1
            buy_shares += shares
        elif code == "S":
            sells += 1
            sell_shares += shares
    if not buys and not sells:
        return {}  # grants/exercises/gifts only — parsed fine, just no open-market signal
    return {
        "owner": owner[:80],
        "is_officer": is_officer in {"1", "true", "True"},
        "buys": buys, "sells": sells,
        "buy_shares": round(buy_shares), "sell_shares": round(sell_shares),
        "unquantified": unquantified,
        # Supersede key: a 4/A amendment replaces the original it corrects.
        "owner_key": (root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerCik") or owner).strip(),
        "original_date": (root.findtext(".//dateOfOriginalSubmission") or "").strip(),
    }


def events_cached(symbol: str) -> dict[str, Any] | None:
    """Get-only cache read for latency-sensitive surfaces (the radar): the
    events dict if something already fetched it this hour, else None. Never
    makes a network call."""
    sym = (symbol or "").strip().upper()
    with _LOCK:
        hit = _CACHE.get(sym)
    if hit:
        ttl = _CACHE_TTL_S if hit[1].get("available") else _CACHE_NEG_TTL_S
        if time.monotonic() - hit[0] < ttl:
            return dict(hit[1])
    return None


def events_for(symbol: str, window_days: int = WINDOW_DAYS, client=None) -> dict[str, Any]:
    """Recent 8-K events + Form 4 insider activity for one symbol (cached 1h)."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return {"available": False, "reason": "No symbol."}
    with _LOCK:
        hit = _CACHE.get(sym)
        if hit:
            ttl = _CACHE_TTL_S if hit[1].get("available") else _CACHE_NEG_TTL_S
            if time.monotonic() - hit[0] < ttl:
                return dict(hit[1])
    client = client or default_client()
    try:
        result = _build_events(sym, int(window_days), client)
    except edgar.EdgarError as exc:
        result = {"available": False, "reason": str(exc)}
    except Exception as exc:  # malformed-but-JSON payloads must degrade, not 500 analyze
        result = {"available": False, "reason": f"Unexpected EDGAR payload shape: {exc!r}"}
    with _LOCK:
        if sym not in _CACHE and len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)), None)
        _CACHE[sym] = (time.monotonic(), result)
    return dict(result)


def _build_events(sym: str, window_days: int, client) -> dict[str, Any]:
    res = client.resolve(sym)
    submissions = client.get_submissions(res.cik)
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

    # 4/A amendments included: aggregating only the uncorrected original when
    # a correction exists reported the misfiled numbers (review finding).
    form4_rows = [r for r in rows if r["form"] in {"4", "4/A"}
                  and _within_window(r["filing_date"], cutoff)]
    parsed, purchases, sales = [], 0, 0
    buy_shares = sell_shares = 0.0
    parsed_ok = unquantified = 0
    attempted = fetch_failures = parse_failures = 0
    superseded: set[tuple[str, str]] = set()
    for row in form4_rows[:_FORM4_PARSE_BUDGET]:
        doc = row["primary_document"].split("/")[-1]  # strip the xsl viewer prefix
        if not doc.lower().endswith(".xml"):
            parse_failures += 1  # no structured doc to read — not a benign grant
            continue
        attempted += 1
        try:
            xml_text = client.get_text(edgar.archives_doc_url(res.cik, row["accession"], doc))
        except edgar.EdgarError:
            fetch_failures += 1  # counted honestly — never claimed as parsed
            continue
        detail = _parse_form4_xml(xml_text)
        if detail is None:
            parse_failures += 1  # unusable document — indistinguishable from
            continue             # grants-only would fabricate calm (review finding)
        parsed_ok += 1
        if detail:
            # Rows are newest-first, so an amendment is seen BEFORE the
            # original it corrects; skip the superseded original.
            key = (detail.get("owner_key", ""), detail.get("original_date", ""))
            if row["form"] == "4/A" and all(key):
                superseded.add(key)
            elif (detail.get("owner_key", ""), row["filing_date"]) in superseded:
                continue
            parsed.append({"filing_date": row["filing_date"], **detail})
            purchases += detail["buys"]
            sales += detail["sells"]
            buy_shares += detail["buy_shares"]
            sell_shares += detail["sell_shares"]
            unquantified += detail.get("unquantified", 0)

    # Direction by SHARE volume when known (a 50,000-share sale must outvote
    # three 100-share buys — review finding), transaction counts as fallback.
    # When any P/S trade lacks a parseable share count, the share vote would
    # silently exclude it — fall back to transaction counts, which include it.
    if (buy_shares or sell_shares) and not unquantified:
        net_signal = ("buying" if buy_shares > sell_shares else
                      "selling" if sell_shares > buy_shares else "mixed")
    else:
        net_signal = ("buying" if purchases > sales else "selling" if sales > purchases
                      else "mixed" if purchases else "none")
    note = (f"Parsed {parsed_ok} of {len(form4_rows)} Form 4 filings "
            f"({attempted} attempted, {fetch_failures} fetch failures, "
            f"{parse_failures} unusable documents); direction is "
            "share-volume weighted; grants/exercises excluded — only open-market "
            "trades (codes P/S) count.")
    if unquantified:
        note += (f" {unquantified} trade(s) had no parseable share count — direction "
                 "falls back to transaction counts.")
    if (fetch_failures or parse_failures) and not parsed_ok:
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
    return {
        "available": True,
        "symbol": sym,
        "window_days": int(window_days),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "eight_ks": eight_ks,
        "notable_8k": any(e["notable"] for e in eight_ks),
        "insider": insider,
        "source": "sec_edgar",
    }

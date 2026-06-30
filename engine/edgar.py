"""SEC EDGAR client — fund look-through and predecessor linkage.

This is the real-data spine for forward-looking fund evaluation. Helios today
treats every holding (a stock, an ETF, a mutual fund) as an opaque close-price
series and falls back to a *simulated* series when none exists. This module lets
Helios see what a fund actually holds:

  * resolve a fund ticker -> SEC registrant (CIK + series id),
  * pull the latest Form N-PORT-P portfolio holdings (security-level positions
    with weights and asset categories),
  * expose the registrant's FORMER NAMES so a newly-launched fund can be linked
    back to the closed fund it replaced.

Design constraints (this tool supports real money — honesty over coverage):
  * Free + keyless. SEC EDGAR needs only a descriptive User-Agent header.
  * Offline-safe. All network access goes through an injectable ``http_get`` so
    tests run fully offline; every call has a timeout; failures raise
    ``EdgarError`` for the caller to degrade gracefully. Nothing is fabricated.
  * Cache-light. Fetched JSON/XML is memoized in a small per-client bound cache.

SEC fair-access policy asks for a User-Agent that identifies you with contact
info and limits traffic to < 10 requests/second. Set ``HELIOS_SEC_USER_AGENT``
to a real ``"Name email@example.com"`` string before pulling at any volume.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Endpoints (exposed so tests can build identical cache/fetch keys)
# --------------------------------------------------------------------------- #
MF_TICKERS_URL = "https://www.sec.gov/files/company_tickers_mf.json"
STOCK_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

_DEFAULT_UA = (
    os.environ.get("HELIOS_SEC_USER_AGENT")
    or "Helios Research Terminal (set HELIOS_SEC_USER_AGENT to name email)"
)

# Resource bounds (this tool supports real money — never buffer/parse unbounded
# data from an upstream that could be slow, oversized, or compromised).
_MAX_RESPONSE_BYTES = 50 * 1024 * 1024   # cap any single EDGAR response
_MAX_XML_CHARS = 80 * 1024 * 1024        # cap XML handed to the parser
_MAX_NPORT_POSITIONS = 10_000            # generous (large bond funds) but bounded

# N-PORT <assetCat> codes -> human labels. Unknown codes bucket to "other".
ASSET_CATEGORIES = {
    "EC": "Equity (common)",
    "EP": "Equity (preferred)",
    "DBT": "Debt",
    "ABS-MBS": "Asset-backed (MBS)",
    "ABS-ABS": "Asset-backed",
    "ABS-O": "Asset-backed (other)",
    "ABS-APCP": "Asset-backed (commercial paper)",
    "ABS-CBDO": "Asset-backed (CDO)",
    "ABS-AB": "Asset-backed",
    "ABS-CDO": "Asset-backed (CDO)",
    "ST": "Structured note",
    "STIV": "Short-term / cash",
    "RA": "Repurchase agreement",
    "DIR": "Derivative",
    "DCO": "Derivative (commodity)",
    "DCR": "Derivative (credit)",
    "DE": "Derivative (equity)",
    "DFE": "Derivative (forward)",
    "DFC": "Derivative (future)",
    "DIR-CDS": "Derivative (CDS)",
    "DOP": "Derivative (option)",
    "DSWP": "Derivative (swap)",
    "COMM": "Commodity",
    "RE": "Real estate",
    "LON": "Loan",
}


class EdgarError(RuntimeError):
    """Raised when EDGAR data cannot be resolved or fetched. Callers degrade."""


@dataclass
class Resolution:
    symbol: str
    cik: str               # integer-as-string, e.g. "884394"
    kind: str              # "fund" | "stock"
    name: str = ""
    series_id: str = ""
    class_id: str = ""


@dataclass
class Filing:
    accession: str
    form: str
    primary_document: str
    filing_date: str = ""
    report_date: str = ""


@dataclass
class NportPosition:
    name: str
    title: str
    ticker: str
    cusip: str
    isin: str
    weight_pct: float | None
    value_usd: float | None
    asset_cat: str
    asset_label: str


@dataclass
class NportReport:
    as_of: str
    total_net_assets: float | None
    positions: list = field(default_factory=list)
    parse_errors: int = 0       # positions dropped because their XML was malformed
    truncated: bool = False     # filing had more positions than _MAX_NPORT_POSITIONS


# --------------------------------------------------------------------------- #
# URL builders
# --------------------------------------------------------------------------- #
def submissions_url(cik: str | int) -> str:
    return f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"


def archives_doc_url(cik: str | int, accession: str, document: str) -> str:
    acc_nodash = (accession or "").replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{document}"


def _urllib_get(url: str, headers: dict, timeout: float) -> str:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https only, fixed hosts)
        # Bounded read: request one byte past the cap so an oversized or slowly
        # streamed body never fully buffers into memory.
        data = resp.read(_MAX_RESPONSE_BYTES + 1)
    if len(data) > _MAX_RESPONSE_BYTES:
        raise EdgarError(f"EDGAR response exceeds {_MAX_RESPONSE_BYTES} bytes; refusing to buffer.")
    return data.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class EdgarClient:
    """Thin, cached, injectable EDGAR reader.

    ``http_get`` is ``callable(url, headers) -> str``; the default uses urllib
    with the configured User-Agent and timeout. Inject a fake in tests so no
    network is touched.
    """

    _CACHE_MAX = 64

    def __init__(self, http_get=None, user_agent: str | None = None, timeout: float = 12.0):
        self.user_agent = user_agent or _DEFAULT_UA
        self.timeout = float(timeout)
        if http_get is None:
            http_get = lambda url, headers: _urllib_get(url, headers, self.timeout)  # noqa: E731
        self._http_get = http_get
        self._cache: dict[str, str] = {}
        self._lock = threading.RLock()

    # -- raw fetch + cache ------------------------------------------------- #
    def get_text(self, url: str) -> str:
        with self._lock:
            if url in self._cache:
                return self._cache[url]
        headers = {"User-Agent": self.user_agent, "Accept": "application/json, text/xml, */*"}
        try:
            text = self._http_get(url, headers)
        except EdgarError:
            raise
        except Exception as exc:  # network, decode, HTTP error -> uniform failure
            raise EdgarError(f"EDGAR fetch failed for {url}: {exc}") from exc
        if not isinstance(text, str) or not text:
            raise EdgarError(f"EDGAR returned an empty response for {url}.")
        with self._lock:
            if url not in self._cache and len(self._cache) >= self._CACHE_MAX:
                self._cache.pop(next(iter(self._cache)), None)
            self._cache[url] = text
        return text

    def get_json(self, url: str) -> dict | list:
        try:
            return json.loads(self.get_text(url))
        except EdgarError:
            raise
        except Exception as exc:
            raise EdgarError(f"EDGAR returned invalid JSON for {url}: {exc}") from exc

    # -- ticker -> registrant --------------------------------------------- #
    def resolve(self, symbol: str) -> Resolution:
        sym = (symbol or "").strip().upper()
        if not sym:
            raise EdgarError("Empty ticker symbol.")
        # Mutual funds / ETFs first: the fund ticker map carries series + class.
        try:
            mf = self.get_json(MF_TICKERS_URL)
            for row in (mf.get("data") if isinstance(mf, dict) else None) or []:
                # row = [cik, seriesId, classId, symbol]
                if len(row) >= 4 and str(row[3]).strip().upper() == sym:
                    return Resolution(symbol=sym, cik=str(int(row[0])), kind="fund",
                                      series_id=str(row[1] or ""), class_id=str(row[2] or ""))
        except EdgarError:
            pass  # fall through to the stock map; surface a combined error below
        # Operating companies (a stock holding has no look-through; it is a leaf).
        stocks = self.get_json(STOCK_TICKERS_URL)
        values = stocks.values() if isinstance(stocks, dict) else []
        for entry in values:
            if str(entry.get("ticker", "")).strip().upper() == sym:
                return Resolution(symbol=sym, cik=str(int(entry["cik_str"])), kind="stock",
                                  name=str(entry.get("title", "")))
        raise EdgarError(f"Could not resolve {sym!r} to a SEC registrant (not a registered fund or filer).")

    # -- registrant metadata ---------------------------------------------- #
    def get_submissions(self, cik: str | int) -> dict:
        data = self.get_json(submissions_url(cik))
        if not isinstance(data, dict):
            raise EdgarError(f"Unexpected submissions payload for CIK {cik}.")
        return data

    def former_names(self, submissions: dict) -> list[dict]:
        out = []
        for fn in submissions.get("formerNames") or []:
            name = str(fn.get("name", "")).strip()
            if name:
                out.append({"name": name, "from": fn.get("from", ""), "to": fn.get("to", "")})
        return out

    def latest_filing(self, submissions: dict, forms) -> Filing | None:
        """Most recent filing whose form is in ``forms`` (recent-window only)."""
        wanted = {f.upper() for f in forms}
        recent = ((submissions.get("filings") or {}).get("recent")) or {}
        acc = recent.get("accessionNumber") or []
        frm = recent.get("form") or []
        prim = recent.get("primaryDocument") or []
        fdt = recent.get("filingDate") or []
        rdt = recent.get("reportDate") or []
        for i, form in enumerate(frm):
            if str(form).upper() in wanted:
                return Filing(
                    accession=acc[i] if i < len(acc) else "",
                    form=str(form),
                    primary_document=prim[i] if i < len(prim) else "",
                    filing_date=fdt[i] if i < len(fdt) else "",
                    report_date=rdt[i] if i < len(rdt) else "",
                )
        return None

    # -- N-PORT holdings --------------------------------------------------- #
    def fetch_nport(self, resolution: Resolution, submissions: dict | None = None) -> NportReport:
        if resolution.kind != "fund":
            raise EdgarError(f"{resolution.symbol} is not a registered fund; no N-PORT look-through.")
        subs = submissions or self.get_submissions(resolution.cik)
        filing = self.latest_filing(subs, ["NPORT-P", "NPORT-P/A"])
        if filing is None:
            raise EdgarError(
                f"No N-PORT-P filing found for {resolution.symbol} yet "
                "(a newly-launched fund may not have filed its first portfolio report)."
            )
        # N-PORT's structured file is canonically primary_doc.xml; prefer it.
        doc = filing.primary_document if filing.primary_document.endswith(".xml") else "primary_doc.xml"
        xml = self.get_text(archives_doc_url(resolution.cik, filing.accession, doc))
        report = parse_nport(xml)
        if not report.positions:
            raise EdgarError(f"N-PORT for {resolution.symbol} parsed to zero positions.")
        return report


# --------------------------------------------------------------------------- #
# Pure N-PORT XML parser (no network — directly unit-testable)
# --------------------------------------------------------------------------- #
def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el, name: str):
    for c in list(el):
        if _localname(c.tag) == name:
            return c
    return None


def _child_text(el, name: str, default: str = "") -> str:
    c = _child(el, name)
    if c is None or c.text is None:
        return default
    return c.text.strip()


def _to_float(text: str) -> float | None:
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_nport(xml_text: str) -> NportReport:
    """Parse an N-PORT-P primary_doc.xml into a NportReport.

    Namespace-agnostic (matches on local element names) because EDGAR's N-PORT
    namespace URIs vary across schema versions.
    """
    if not xml_text or len(xml_text) > _MAX_XML_CHARS:
        raise EdgarError("N-PORT XML is missing or too large to parse.")
    # Defense-in-depth against entity-expansion ("billion laughs"): stdlib
    # ElementTree expands internal general entities. N-PORT has no DTD, so any
    # DOCTYPE/ENTITY in the prolog is anomalous — refuse rather than expand it.
    head = xml_text[:65536].lower()
    if "<!doctype" in head or "<!entity" in head:
        raise EdgarError("N-PORT XML declares a DOCTYPE/ENTITY; refusing to parse.")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise EdgarError(f"Could not parse N-PORT XML: {exc}") from exc

    as_of, total_net_assets = "", None
    for el in root.iter():
        ln = _localname(el.tag)
        if ln == "repPdDate" and not as_of and el.text:
            as_of = el.text.strip()
        elif ln == "netAssets" and total_net_assets is None:
            total_net_assets = _to_float(el.text)

    container = None
    for el in root.iter():
        if _localname(el.tag) == "invstOrSecs":
            container = el
            break

    positions: list[NportPosition] = []
    parse_errors = 0
    truncated = False
    if container is not None:
        for sec in list(container):
            if _localname(sec.tag) != "invstOrSec":
                continue
            if len(positions) >= _MAX_NPORT_POSITIONS:
                truncated = True
                break
            try:
                positions.append(_parse_position(sec))
            except Exception:
                # One malformed position must not sink the whole look-through,
                # but it must be COUNTED so the gap is visible, not silent.
                parse_errors += 1
                continue
    positions.sort(key=lambda p: (p.weight_pct if p.weight_pct is not None else -1.0), reverse=True)
    return NportReport(as_of=as_of, total_net_assets=total_net_assets, positions=positions,
                       parse_errors=parse_errors, truncated=truncated)


def _parse_position(sec) -> NportPosition:
    name = _child_text(sec, "name")
    title = _child_text(sec, "title")
    cusip = _child_text(sec, "cusip")
    ticker, isin = "", ""
    ids = _child(sec, "identifiers")
    if ids is not None:
        for idnode in list(ids):
            ln = _localname(idnode.tag)
            value = (idnode.get("value") or idnode.text or "").strip()
            if ln == "ticker" and value:
                ticker = value.upper()
            elif ln == "isin" and value:
                isin = value.upper()
    asset_cat = _child_text(sec, "assetCat")
    return NportPosition(
        name=name or title or cusip or "(unnamed)",
        title=title,
        ticker=ticker,
        cusip=cusip,
        isin=isin,
        weight_pct=_to_float(_child_text(sec, "pctVal")),
        value_usd=_to_float(_child_text(sec, "valUSD")),
        asset_cat=asset_cat,
        asset_label=ASSET_CATEGORIES.get(asset_cat, "Other") if asset_cat else "Other",
    )

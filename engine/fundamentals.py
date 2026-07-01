"""Per-holding fundamentals — the provider seam for the forward (CMA) spine.

Returns valuation / yield / growth / sector for one security, the inputs the
building-block expected-return model consumes. The default provider uses
yfinance (free, already a dependency) and is fully OPTIONAL: with no network it
returns an empty ``Fundamentals`` (the CMA then falls back to sector/mandate
anchors and reports lower coverage — it never fabricates a number).

Provider selection is automatic: set ``HELIOS_FMP_KEY`` and the auto chain
prefers Financial Modeling Prep — analyst consensus forward EPS (a CAGR across
the forward estimate window, not one noisy quarter), forward P/E, TTM yield and
sector — falling back to yfinance per-ticker if FMP has no data. With no key it
is yfinance-only, and offline it returns an empty ``Fundamentals`` (the CMA then
uses sector/mandate anchors and reports lower coverage — never a fabricated one).

A provider is just ``callable(ticker) -> dict`` of raw fields; pass one to
``fetch`` (tests inject a deterministic fake, so this module is offline-testable).
"""
from __future__ import annotations

import datetime
import json
import os
import threading
import urllib.request
from dataclasses import dataclass

from . import data  # reuse the existing yfinance guard (_yf / HAS_YF)

_DEFAULT_PROVIDER = None
_PROVIDER_LOCK = threading.RLock()

# FMP HTTP seam (tests inject canned JSON; production uses urllib).
_FMP_HTTP = None
_FMP_MAX_BYTES = 8 * 1024 * 1024


def set_fmp_http(fn) -> None:
    """Test seam for the FMP HTTP getter (``callable(url) -> parsed JSON``)."""
    global _FMP_HTTP
    _FMP_HTTP = fn


def set_default_provider(provider) -> None:
    """Override the process fundamentals provider (tests inject a fake; a future
    FMP/Tiingo adapter registers here). ``None`` restores the yfinance default."""
    global _DEFAULT_PROVIDER
    with _PROVIDER_LOCK:
        _DEFAULT_PROVIDER = provider


@dataclass
class Fundamentals:
    ticker: str
    dividend_yield: float | None = None   # fraction, e.g. 0.018
    forward_pe: float | None = None
    trailing_pe: float | None = None
    earnings_growth: float | None = None  # fraction, e.g. 0.11
    sector: str = ""
    source: str = "none"                  # "yfinance" | "<provider>" | "none"

    @property
    def usable(self) -> bool:
        """Enough to compute an equity building-block return (a yield or a P/E)."""
        return self.source != "none" and (
            self.dividend_yield is not None
            or self.forward_pe is not None
            or self.trailing_pe is not None
            or self.earnings_growth is not None
        )


def _coerce_fraction(value) -> float | None:
    """Yahoo reports yields/growth inconsistently as 1.8 or 0.018 — normalize to
    a fraction. Values with |x| > 1.5 are treated as percentages."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f / 100.0 if abs(f) > 1.5 else f


def _coerce_pe(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f <= 0 or f > 1000:  # ignore non-positive / absurd multiples
        return None
    return f


def _num(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN guard


def _first(payload) -> dict:
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    return payload if isinstance(payload, dict) else {}


def _yfinance_provider(ticker: str) -> dict:
    if not data.HAS_YF:
        return {}
    try:
        info = data._yf.Ticker(ticker).info or {}
    except Exception:
        return {}
    # yfinance reports dividendYield as a PERCENT (AAPL 0.38 == 0.38%, JPM 1.82
    # == 1.82%); normalize to a fraction here so the CMA never reads a 0.4% payer
    # as a 38% one. earningsGrowth is already a fraction and passes through.
    dy = info.get("dividendYield")
    dy = dy / 100.0 if isinstance(dy, (int, float)) and dy == dy else None
    return {
        "dividend_yield": dy,
        "forward_pe": info.get("forwardPE"),
        "trailing_pe": info.get("trailingPE"),
        "earnings_growth": info.get("earningsGrowth"),
        "sector": info.get("sector") or "",
        "source": "yfinance",
    }


# --------------------------------------------------------------------------- #
# Financial Modeling Prep (cleaner forward/consensus estimates)
# --------------------------------------------------------------------------- #
def _urllib_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Helios Research Terminal"})
    with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310 (fixed https host)
        return json.loads(resp.read(_FMP_MAX_BYTES + 1).decode("utf-8", errors="replace"))


def _fmp_http_json(url: str):
    fn = _FMP_HTTP or _urllib_json
    try:
        return fn(url)
    except Exception:
        return None


def _fmp_forward(estimates, price):
    """Forward P/E (from the nearest-year consensus EPS) and a consensus EPS CAGR
    across the forward estimate window — cleaner than a single noisy quarter."""
    rows = []
    for e in estimates or []:
        if not isinstance(e, dict):
            continue
        eps = _num(e.get("estimatedEpsAvg"))
        date = str(e.get("date") or "")[:10]
        if eps is not None and len(date) >= 4 and date[:4].isdigit():
            rows.append((int(date[:4]), eps))
    if not rows:
        return None, None
    rows.sort()
    year_now = datetime.date.today().year
    future = [r for r in rows if r[0] >= year_now] or rows
    near, far = future[0], future[-1]
    growth = None
    if near[1] and far[1] and near[1] > 0 and far[1] > 0 and far[0] > near[0]:
        growth = (far[1] / near[1]) ** (1.0 / (far[0] - near[0])) - 1.0
    forward_pe = (price / near[1]) if price and near[1] and near[1] > 0 else None
    return forward_pe, growth


def _fmp_provider(ticker: str) -> dict:
    key = os.environ.get("HELIOS_FMP_KEY", "").strip()
    if not key:
        return {}
    base = os.environ.get("HELIOS_FMP_BASE_URL", "https://financialmodelingprep.com").rstrip("/")

    def url(path: str) -> str:
        sep = "&" if "?" in path else "?"
        return f"{base}{path}{sep}apikey={key}"

    profile = _first(_fmp_http_json(url(f"/api/v3/profile/{ticker}")))
    ratios = _first(_fmp_http_json(url(f"/api/v3/ratios-ttm/{ticker}")))
    estimates = _fmp_http_json(url(f"/api/v3/analyst-estimates/{ticker}?period=annual&limit=6"))
    if not profile and not ratios and not estimates:
        return {}

    price = _num(profile.get("price"))
    dy = _num(ratios.get("dividendYieldTTM"))
    if dy is None:
        last_div = _num(profile.get("lastDiv"))
        if price and last_div is not None and price > 0:
            dy = last_div / price
    trailing_pe = _num(ratios.get("peRatioTTM")) or _num(ratios.get("priceEarningsRatioTTM"))
    forward_pe, growth = _fmp_forward(estimates, price)
    return {
        "dividend_yield": dy,
        "forward_pe": forward_pe,
        "trailing_pe": trailing_pe,
        "earnings_growth": growth,
        "sector": profile.get("sector") or "",
        "source": "fmp",
    }


def _auto_provider(ticker: str) -> dict:
    """Prefer FMP consensus when a key is configured; fall back to yfinance."""
    if os.environ.get("HELIOS_FMP_KEY", "").strip():
        raw = _fmp_provider(ticker)
        if raw:
            return raw
    return _yfinance_provider(ticker)


def fetch(ticker: str, provider=None) -> Fundamentals:
    """Fetch + normalize fundamentals for one ticker.

    ``provider`` is ``callable(ticker) -> dict``; defaults to yfinance. Any
    failure or offline state yields an empty (``source='none'``) Fundamentals.
    """
    sym = (ticker or "").strip().upper()
    if not sym:
        return Fundamentals(ticker="", source="none")
    use_default = provider is None
    if provider is None:
        provider = _DEFAULT_PROVIDER or _auto_provider
    try:
        raw = provider(sym) or {}
    except Exception:
        raw = {}
    if not raw:
        return Fundamentals(ticker=sym, source="none")
    return Fundamentals(
        ticker=sym,
        dividend_yield=_coerce_fraction(raw.get("dividend_yield")),
        forward_pe=_coerce_pe(raw.get("forward_pe")),
        trailing_pe=_coerce_pe(raw.get("trailing_pe")),
        earnings_growth=_coerce_fraction(raw.get("earnings_growth")),
        sector=str(raw.get("sector") or ""),
        source=str(raw.get("source") or ("yfinance" if use_default else "provider")),
    )

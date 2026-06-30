"""Per-holding fundamentals — the provider seam for the forward (CMA) spine.

Returns valuation / yield / growth / sector for one security, the inputs the
building-block expected-return model consumes. The default provider uses
yfinance (free, already a dependency) and is fully OPTIONAL: with no network it
returns an empty ``Fundamentals`` (the CMA then falls back to sector/mandate
anchors and reports lower coverage — it never fabricates a number).

Cleaner consensus providers drop in behind the same ``fetch`` contract without
touching callers:
    * FMP / Tiingo  -> analyst forward EPS growth, forward P/E, dividends
      (key via HELIOS_FMP_KEY / HELIOS_TIINGO_KEY)
A provider is just ``callable(ticker) -> dict`` of raw fields; pass one to
``fetch`` (tests inject a deterministic fake, so this module is offline-testable).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from . import data  # reuse the existing yfinance guard (_yf / HAS_YF)

_DEFAULT_PROVIDER = None
_PROVIDER_LOCK = threading.RLock()


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


def _yfinance_provider(ticker: str) -> dict:
    if not data.HAS_YF:
        return {}
    try:
        info = data._yf.Ticker(ticker).info or {}
    except Exception:
        return {}
    return {
        "dividend_yield": info.get("dividendYield"),
        "forward_pe": info.get("forwardPE"),
        "trailing_pe": info.get("trailingPE"),
        "earnings_growth": info.get("earningsGrowth"),
        "sector": info.get("sector") or "",
    }


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
        provider = _DEFAULT_PROVIDER or _yfinance_provider
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

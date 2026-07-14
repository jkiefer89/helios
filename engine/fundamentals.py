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
from dataclasses import dataclass, field

from . import data  # reuse the existing yfinance guard (_yf / HAS_YF)

_DEFAULT_PROVIDER = None
_PROVIDER_LOCK = threading.RLock()

# Per-ticker wall-clock budget for the whole provider chain. The auto chain
# can issue up to ~16 sequential HTTP calls at 12s timeout each; one uncovered
# name against black-holed providers pinned a request thread for minutes
# (review finding). Thread-local so concurrent fetches don't share a deadline.
PER_TICKER_BUDGET_S = 20.0
_TICKER_DEADLINE = threading.local()


def _deadline_remaining() -> float | None:
    deadline = getattr(_TICKER_DEADLINE, "value", None)
    if deadline is None:
        return None
    import time
    return deadline - time.monotonic()

# FMP HTTP seam (tests inject canned JSON; production uses urllib).
_FMP_HTTP = None
_FMP_MAX_BYTES = 8 * 1024 * 1024


def set_fmp_http(fn) -> None:
    """Test seam for the FMP HTTP getter (``callable(url) -> parsed JSON``)."""
    global _FMP_HTTP
    _FMP_HTTP = fn
    invalidate_cache()  # a new upstream invalidates previously fetched answers


def set_default_provider(provider) -> None:
    """Override the process fundamentals provider (tests inject a fake; a future
    FMP/Tiingo adapter registers here). ``None`` restores the yfinance default."""
    global _DEFAULT_PROVIDER
    with _PROVIDER_LOCK:
        _DEFAULT_PROVIDER = provider
    invalidate_cache()  # cached results came from the old provider


@dataclass
class Fundamentals:
    ticker: str
    dividend_yield: float | None = None   # fraction, e.g. 0.018
    forward_pe: float | None = None
    trailing_pe: float | None = None
    earnings_growth: float | None = None  # fraction, e.g. 0.11
    # WHAT KIND of growth figure that is — the three providers measure three
    # different things and the CMA must not treat them alike (review finding):
    # "forward_consensus_cagr" (FMP), "trailing_annual" (Intrinio),
    # "trailing_quarter_yoy" (yfinance — one noisy comp, shrunk in the CMA).
    growth_basis: str = ""
    sector: str = ""
    source: str = "none"                  # "yfinance" | "<provider>" | "none"
    # Quality / analyst extras (all optional; None = provider had no value).
    roe: float | None = None              # return on equity, fraction
    profit_margin: float | None = None    # net margin, fraction
    debt_to_equity: float | None = None   # ratio (1.5 = 150% of equity)
    revenue_growth: float | None = None   # fraction
    current_price: float | None = None
    target_mean_price: float | None = None  # analyst consensus 12m target
    analyst_rating: float | None = None     # 1 strong buy .. 5 sell (Yahoo scale)
    n_analysts: int | None = None
    next_earnings_date: str = ""            # next scheduled report (YYYY-MM-DD)
    # Observation date (UTC fetch date): cached-vs-fresh becomes visible, and
    # the PIT snapshot table records what Helios KNEW on this date.
    as_of: str = ""
    # Cross-provider disagreements (>25% on overlapping numeric fields) —
    # warnings, never blocks; mechanizes the unit-semantics bug class.
    reconciliation_warnings: list = field(default_factory=list)

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
    # debtToEquity is uniformly reported as a percent (145.6 == 1.456x, 6.5 ==
    # 0.065x) — always normalize; a >N heuristic would overstate low-leverage
    # names 100x (review finding, verified live: NVDA 6.555 -> 0.066x).
    dte = _num(info.get("debtToEquity"))
    if dte is not None:
        dte = dte / 100.0
    out = {
        "dividend_yield": dy,
        "forward_pe": info.get("forwardPE"),
        "trailing_pe": info.get("trailingPE"),
        "earnings_growth": info.get("earningsGrowth"),
        "sector": info.get("sector") or "",
        "source": "yfinance",
        "roe": info.get("returnOnEquity"),                 # fraction
        "profit_margin": info.get("profitMargins"),        # fraction
        "debt_to_equity": dte,
        "revenue_growth": info.get("revenueGrowth"),       # fraction
        "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "target_mean_price": info.get("targetMeanPrice"),
        "analyst_rating": info.get("recommendationMean"),  # 1 buy .. 5 sell
        "n_analysts": info.get("numberOfAnalystOpinions"),
    }
    if out["earnings_growth"] is not None:
        # yfinance earningsGrowth is the LATEST QUARTER'S YoY — one noisy comp.
        out["growth_basis"] = "trailing_quarter_yoy"
    return out


# --------------------------------------------------------------------------- #
# Financial Modeling Prep (cleaner forward/consensus estimates)
# --------------------------------------------------------------------------- #
def _urllib_json(url: str):
    # The last in-flight call may not overshoot the per-ticker deadline.
    timeout = 12.0
    remaining = _deadline_remaining()
    if remaining is not None:
        timeout = max(0.5, min(timeout, remaining))
    req = urllib.request.Request(url, headers={"User-Agent": "Helios Research Terminal"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https host)
        return json.loads(resp.read(_FMP_MAX_BYTES + 1).decode("utf-8", errors="replace"))


def _fmp_http_json(url: str):
    remaining = _deadline_remaining()
    if remaining is not None and remaining <= 0:
        return None   # per-ticker budget exhausted — degrade, don't queue more calls
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
        # v3 legacy: estimatedEpsAvg; stable API: epsAvg.
        eps = _num(e.get("estimatedEpsAvg")) or _num(e.get("epsAvg"))
        n = _num(e.get("numAnalystsEps")) or _num(e.get("numberAnalystsEstimatedEps"))
        date = str(e.get("date") or "")[:10]
        if eps is not None and len(date) == 10 and date[:4].isdigit():
            rows.append((date, eps, n))
    if not rows:
        return None, None
    rows.sort()
    # Past-period rows must NOT masquerade as forward figures. Compare the FULL
    # fiscal-period-end date against today: a calendar-year compare let NVDA's
    # already-reported January-ending FY2026 pass as "forward" until December
    # (review finding — a trailing P/E labeled forward, ~38% overstated).
    today = datetime.date.today().isoformat()
    future = [r for r in rows if r[0] > today]
    if not future:
        return None, None
    near = future[0]
    # FY+4/FY+5 rows often carry 1-2 analysts or placeholder values, and the
    # farthest row single-handedly sets the growth CAGR (review finding). Use
    # the farthest future row whose analyst coverage is at least a third of
    # the near row's; rows with missing counts pass (legacy v3 payloads).
    near_n = near[2]
    deep_enough = [r for r in future
                   if r[2] is None or near_n is None or r[2] >= max(2.0, near_n / 3.0)]
    far = deep_enough[-1] if deep_enough else near
    near_year, far_year = int(near[0][:4]), int(far[0][:4])
    growth = None
    if near[1] and far[1] and near[1] > 0 and far[1] > 0 and far_year > near_year:
        growth = (far[1] / near[1]) ** (1.0 / (far_year - near_year)) - 1.0
    forward_pe = (price / near[1]) if price and near[1] and near[1] > 0 else None
    return forward_pe, growth


def _fmp_reject_legacy(payload):
    """FMP returns {"Error Message": "Legacy Endpoint ..."} dicts instead of
    HTTP errors; treat any error payload as no-data."""
    if isinstance(payload, dict) and payload.get("Error Message"):
        return None
    return payload


def _fmp_next_earnings(earnings) -> str:
    """Next scheduled report date from /stable/earnings — must be TODAY OR
    LATER (FMP sometimes lags backfilling epsActual on past dates; a stale
    past date must never be shown as the next report)."""
    today = datetime.date.today().isoformat()
    upcoming = []
    for row in earnings or []:
        if not isinstance(row, dict):
            continue
        date = str(row.get("date") or "")[:10]
        if len(date) == 10 and date >= today and row.get("epsActual") is None:
            upcoming.append(date)
    return min(upcoming) if upcoming else ""


def _fmp_provider(ticker: str) -> dict:
    """FMP consensus fundamentals. Accounts created after Aug 2025 use the
    ``/stable`` API; the ``/api/v3`` paths remain as a fallback for legacy
    subscriptions. Field names differ between the two — both are handled."""
    key = os.environ.get("HELIOS_FMP_KEY", "").strip()
    if not key:
        return {}
    base = os.environ.get("HELIOS_FMP_BASE_URL", "https://financialmodelingprep.com").rstrip("/")

    def url(path: str) -> str:
        sep = "&" if "?" in path else "?"
        return f"{base}{path}{sep}apikey={key}"

    # Stable API first (current accounts)...
    profile = _first(_fmp_reject_legacy(_fmp_http_json(url(f"/stable/profile?symbol={ticker}"))) or {})
    ratios = _first(_fmp_reject_legacy(_fmp_http_json(url(f"/stable/ratios-ttm?symbol={ticker}"))) or {})
    estimates = _fmp_reject_legacy(
        _fmp_http_json(url(f"/stable/analyst-estimates?symbol={ticker}&period=annual&limit=8")))
    earnings = _fmp_reject_legacy(_fmp_http_json(url(f"/stable/earnings?symbol={ticker}&limit=6")))
    # ...then the legacy v3 paths for pre-2025 subscriptions.
    if not profile and not ratios and not estimates:
        profile = _first(_fmp_reject_legacy(_fmp_http_json(url(f"/api/v3/profile/{ticker}"))) or {})
        ratios = _first(_fmp_reject_legacy(_fmp_http_json(url(f"/api/v3/ratios-ttm/{ticker}"))) or {})
        estimates = _fmp_reject_legacy(
            _fmp_http_json(url(f"/api/v3/analyst-estimates/{ticker}?period=annual&limit=6")))
    if not profile and not ratios and not estimates:
        return {}

    price = _num(profile.get("price"))
    dy = _num(ratios.get("dividendYieldTTM"))
    if dy is None:
        last_div = _num(profile.get("lastDividend")) or _num(profile.get("lastDiv"))
        if price and last_div is not None and price > 0:
            dy = last_div / price
    trailing_pe = (_num(ratios.get("priceToEarningsRatioTTM"))       # stable
                   or _num(ratios.get("peRatioTTM"))                 # v3
                   or _num(ratios.get("priceEarningsRatioTTM")))
    forward_pe, growth = _fmp_forward(estimates, price)
    out = {
        "dividend_yield": dy,
        "forward_pe": forward_pe,
        "trailing_pe": trailing_pe,
        "earnings_growth": growth,
        "sector": profile.get("sector") or "",
        "source": "fmp",
        "roe": _num(ratios.get("returnOnEquityTTM")),
        "profit_margin": _num(ratios.get("netProfitMarginTTM")),
        "debt_to_equity": _num(ratios.get("debtToEquityRatioTTM")) or _num(ratios.get("debtEquityRatioTTM")),
        "current_price": price,
    }
    if growth is not None:
        out["growth_basis"] = "forward_consensus_cagr"
    next_earnings = _fmp_next_earnings(earnings)
    if next_earnings:
        out["next_earnings_date"] = next_earnings
    return out


# --------------------------------------------------------------------------- #
# Intrinio (institutional-grade standardized fundamentals)
# --------------------------------------------------------------------------- #
_INTRINIO_HTTP = None
_INTRINIO_BASE = "https://api-v2.intrinio.com"
# Standardized data-point tags this integration reads -> our field names.
_INTRINIO_TAGS = {
    "pricetoearnings": "trailing_pe",
    "dividendyield": "dividend_yield",     # already a fraction
    "roe": "roe",
    "debttoequity": "debt_to_equity",      # already a ratio
    "profitmargin": "profit_margin",
    "epsgrowth": "earnings_growth",        # trailing annual EPS growth (fraction)
    "revenuegrowth": "revenue_growth",
    "close_price": "current_price",
}
# Intrinio reports SIC-style sectors/industries ("Finance, Insurance, And Real
# Estate"), which would fuzzy-match the WRONG macro anchor (that string contains
# "real estate"). Map keywords in industry_category first, then sector, to the
# anchor vocabulary macro.sector_anchor understands; unknown -> "" (market anchor).
_INTRINIO_SECTOR_KEYWORDS = (
    # ORDER MATTERS, in both directions (review findings):
    # - "reit"/"real estate" must beat "invest", or "Real Estate Investment
    #   Trusts" lands on the FINANCIALS anchor;
    # - financial keywords must beat the bare "real estate" containment in the
    #   SIC division string "Finance, Insurance, And Real Estate".
    ("reit", "real estate"),
    ("real estate investment", "real estate"),
    ("bank", "financials"), ("insur", "financials"), ("financ", "financials"),
    ("invest", "financials"), ("securit", "financials"),
    ("real estate", "real estate"),
    ("software", "technology"), ("computer", "technology"), ("semicond", "technology"),
    ("internet", "technology"), ("technology", "technology"),
    ("telecom", "communication services"), ("communicat", "communication services"),
    ("media", "communication services"), ("broadcast", "communication services"),
    ("drug", "healthcare"), ("pharma", "healthcare"), ("biotech", "healthcare"),
    ("medical", "healthcare"), ("health", "healthcare"),
    ("oil", "energy"), ("gas", "energy"), ("energy", "energy"), ("petrol", "energy"),
    ("utilit", "utilities"), ("electric services", "utilities"),
    ("food", "consumer staples"), ("beverage", "consumer staples"),
    ("tobacco", "consumer staples"), ("household", "consumer staples"),
    ("retail", "consumer discretionary"), ("apparel", "consumer discretionary"),
    ("restaurant", "consumer discretionary"), ("hotel", "consumer discretionary"),
    ("auto", "consumer discretionary"), ("leisure", "consumer discretionary"),
    ("chemical", "materials"), ("metal", "materials"), ("mining", "materials"),
    ("paper", "materials"), ("material", "materials"),
    ("aerospace", "industrials"), ("machinery", "industrials"),
    ("transport", "industrials"), ("construction", "industrials"),
    ("industrial", "industrials"), ("defense", "industrials"),
)


def set_intrinio_http(fn) -> None:
    """Test seam for the Intrinio HTTP getter (``callable(url) -> parsed JSON``)."""
    global _INTRINIO_HTTP
    _INTRINIO_HTTP = fn
    invalidate_cache()


def _intrinio_http_json(url: str):
    remaining = _deadline_remaining()
    if remaining is not None and remaining <= 0:
        return None   # per-ticker budget exhausted — degrade, don't queue more calls
    fn = _INTRINIO_HTTP or _urllib_json
    try:
        return fn(url)
    except Exception:
        return None


def _intrinio_sector(company: dict) -> str:
    # industry_group leads: it is the only field in Intrinio's live taxonomy
    # that actually says "REIT" (verified on SPG/O/PLD/AMT, where
    # industry_category is the useless "Trading" and the SIC division string
    # would misroute every REIT to the financials anchor).
    for field in ("industry_group", "industry_category", "sector"):
        text = str(company.get(field) or "").lower()
        if not text:
            continue
        for keyword, anchor in _INTRINIO_SECTOR_KEYWORDS:
            if keyword in text:
                return anchor
    return ""


def _intrinio_provider(ticker: str) -> dict:
    key = os.environ.get("HELIOS_INTRINIO_KEY", "").strip()
    if not key:
        return {}

    def url(path: str) -> str:
        sep = "&" if "?" in path else "?"
        return f"{_INTRINIO_BASE}{path}{sep}api_key={key}"

    company = _intrinio_http_json(url(f"/companies/{ticker}"))
    company = company if isinstance(company, dict) and not company.get("error") else {}
    out: dict = {}
    for tag, field in _INTRINIO_TAGS.items():
        value = _intrinio_http_json(url(f"/companies/{ticker}/data_point/{tag}/number"))
        # The /number endpoint returns a bare JSON number; error payloads are dicts.
        num = _num(value) if not isinstance(value, (dict, list)) else None
        if num is not None:
            out[field] = num
    if not out and not company:
        return {}
    if out.get("earnings_growth") is not None:
        out["growth_basis"] = "trailing_annual"   # Intrinio epsgrowth is trailing
    out["sector"] = _intrinio_sector(company)
    out["source"] = "intrinio"
    return out


def _merge_raw(primary: dict, filler: dict) -> dict:
    """Field-level merge: keep every primary value, fill gaps from the filler.

    The merged source names both providers so provenance stays honest about
    where the numbers came from."""
    if not primary:
        return dict(filler)
    if not filler:
        return dict(primary)
    merged = dict(primary)
    for k, v in filler.items():
        if k in ("source",):
            continue
        if merged.get(k) in (None, ""):
            merged[k] = v
    p_src, f_src = primary.get("source", ""), filler.get("source", "")
    if f_src and any(filler.get(k) not in (None, "") and primary.get(k) in (None, "")
                     for k in filler if k != "source"):
        merged["source"] = f"{p_src}+{f_src}" if p_src else f_src
    return merged


# Fields where two providers reporting the same thing >25% apart usually means
# a unit/semantics bug or stale vendor data — exactly the class of corruption
# previous review passes caught only by hand (D/E percent-vs-ratio, yield
# double-division). Warn, never block.
_RECONCILE_FIELDS = ("trailing_pe", "dividend_yield", "debt_to_equity")
_RECONCILE_TOLERANCE = 0.25


def _reconcile(raws: list[tuple[str, dict]]) -> list[str]:
    warnings: list[str] = []
    for field_name in _RECONCILE_FIELDS:
        vals = [(name, float(raw[field_name])) for name, raw in raws
                if isinstance(raw.get(field_name), (int, float))
                and not isinstance(raw.get(field_name), bool)]
        if len(vals) < 2:
            continue
        nums = [v for _, v in vals]
        lo, hi = min(nums), max(nums)
        if hi > 0 and lo >= 0 and (hi - lo) / hi > _RECONCILE_TOLERANCE:
            detail = "; ".join(f"{name} {value:.4g}" for name, value in vals)
            warnings.append(f"{field_name}: providers disagree ({detail}) — verify before relying on it.")
    return warnings


def _auto_provider(ticker: str) -> dict:
    """Provider chain, best-first with field-level gap filling:
    FMP consensus (if keyed) > Intrinio standardized (if keyed) > yfinance.
    Later providers only fill fields the earlier ones did not supply."""
    import time
    _TICKER_DEADLINE.value = time.monotonic() + PER_TICKER_BUDGET_S
    try:
        raws: list[tuple[str, dict]] = []
        merged: dict = {}
        from . import provider_registry

        if provider_registry.controls_required():
            readiness = provider_registry.domain_readiness("fundamentals")
            cutover = readiness.get("cutover") if readiness.get("passed") else None
            order = [
                key for key in (
                    str((cutover or {}).get("primary_provider") or ""),
                    str((cutover or {}).get("backup_provider") or ""),
                ) if key
            ]
        else:
            order = ["fmp", "intrinio", "yfinance"]
        adapters = {
            "fmp": ("HELIOS_FMP_KEY", _fmp_provider),
            "intrinio": ("HELIOS_INTRINIO_KEY", _intrinio_provider),
            "yfinance": ("", _yfinance_provider),
        }
        for provider in order:
            credential, adapter = adapters.get(provider, (None, None))
            if adapter is None or (credential and not os.environ.get(credential, "").strip()):
                continue
            raw = adapter(ticker)
            raws.append((provider, raw))
            merged = _merge_raw(merged, raw)
        recon = _reconcile([(name, r) for name, r in raws if r])
        if recon:
            merged["reconciliation_warnings"] = recon
        _vault_provider_payloads(ticker, raws)
        return merged
    finally:
        _TICKER_DEADLINE.value = None


def _vault_provider_payloads(ticker: str, raws: list[tuple[str, dict]]) -> None:
    """v10 raw vendor vault: each provider's response as received, hash-deduped
    (an unchanged payload stores once). Best-effort — never blocks a fetch."""
    try:
        import json as _json

        from . import persistence

        store = persistence.get_store()
        for name, raw in raws:
            if not raw:
                continue
            store.vault_payload(
                name, "fundamentals", ticker,
                _json.dumps(raw, sort_keys=True, default=str))
    except Exception:
        return


# Default-provider results are cached for a few hours: fundamentals move on
# earnings cadence, not tick cadence, and /api/analyze must not re-hit the
# provider on every request. Explicit-provider calls (tests) bypass the cache.
# Unusable (empty) results are negative-cached with a SHORT TTL so a degraded
# upstream is probed at most once per window instead of stalling every
# analyze call (worst case was ~9 sequential 12s timeouts per uncovered name).
_FETCH_CACHE: dict[str, tuple[float, "Fundamentals"]] = {}
_FETCH_CACHE_TTL_S = 6 * 3600.0
_FETCH_CACHE_EMPTY_TTL_S = 15 * 60.0
_FETCH_CACHE_MAX = 512


def _cache_now() -> float:
    import time
    return time.monotonic()


def invalidate_cache() -> None:
    with _PROVIDER_LOCK:
        _FETCH_CACHE.clear()


def _record_pit_snapshot(result: "Fundamentals") -> None:
    """Append today's observation to the point-in-time store (best-effort).

    Zero vendor cost: Helios's own daily operation becomes the PIT database
    that later validates the strategic track honestly — no retrospective
    backtest has to fake what was knowable (review finding). Lazy import
    avoids an engine import cycle; failure never blocks a fetch."""
    try:
        from . import persistence
        persistence.get_store().record_fundamentals_snapshot({
            "symbol": result.ticker,
            "as_of": result.as_of or datetime.date.today().isoformat(),
            "dividend_yield": result.dividend_yield,
            "forward_pe": result.forward_pe,
            "trailing_pe": result.trailing_pe,
            "earnings_growth": result.earnings_growth,
            "growth_basis": result.growth_basis,
            "sector": result.sector,
            "source": result.source,
            "roe": result.roe,
            "profit_margin": result.profit_margin,
            "debt_to_equity": result.debt_to_equity,
            "revenue_growth": result.revenue_growth,
            "current_price": result.current_price,
            "target_mean_price": result.target_mean_price,
            "analyst_rating": result.analyst_rating,
            "n_analysts": result.n_analysts,
            "next_earnings_date": result.next_earnings_date,
            "reconciliation_warnings": result.reconciliation_warnings,
        })
    except Exception:
        pass


def fetch_cached(ticker: str) -> "Fundamentals | None":
    """Get-only cache read: never touches the network (latency-sensitive
    callers degrade to this when the live-provider slots are contended)."""
    sym = (ticker or "").strip().upper()
    if not sym:
        return None
    with _PROVIDER_LOCK:
        hit = _FETCH_CACHE.get(sym)
        if hit:
            ttl = _FETCH_CACHE_TTL_S if hit[1].usable else _FETCH_CACHE_EMPTY_TTL_S
            if _cache_now() - hit[0] < ttl:
                return hit[1]
    return None


def fetch(ticker: str, provider=None) -> Fundamentals:
    """Fetch + normalize fundamentals for one ticker.

    ``provider`` is ``callable(ticker) -> dict``; defaults to yfinance. Any
    failure or offline state yields an empty (``source='none'``) Fundamentals.
    """
    sym = (ticker or "").strip().upper()
    if not sym:
        return Fundamentals(ticker="", source="none")
    use_default = provider is None
    if use_default:
        with _PROVIDER_LOCK:
            hit = _FETCH_CACHE.get(sym)
            if hit:
                ttl = _FETCH_CACHE_TTL_S if hit[1].usable else _FETCH_CACHE_EMPTY_TTL_S
                if _cache_now() - hit[0] < ttl:
                    return hit[1]
        provider = _DEFAULT_PROVIDER or _auto_provider
    try:
        raw = provider(sym) or {}
    except Exception:
        raw = {}
    if not raw:
        result = Fundamentals(ticker=sym, source="none")
    else:
        result = Fundamentals(
            ticker=sym,
            # All wired providers deliver dividend_yield as a TRUE FRACTION
            # (yfinance pre-divides, FMP TTM ratio and lastDividend/price are
            # fractions — MSTY 2.77 = 277% verified live, Intrinio is a
            # fraction). The percent heuristic double-divided legitimate
            # covered-call yields >150% into fake 2.5% yields and passed 0.9
            # through as 90% (review finding). The CMA's _DY_CAP clips extremes.
            dividend_yield=_num(raw.get("dividend_yield")),
            forward_pe=_coerce_pe(raw.get("forward_pe")),
            trailing_pe=_coerce_pe(raw.get("trailing_pe")),
            # All providers deliver growth as a true fraction and legitimate
            # values exceed 150% (recovery years); the percent heuristic would
            # corrupt 5.8 -> 0.058. The CMA's _GROWTH_CAP clips extremes.
            earnings_growth=_num(raw.get("earnings_growth")),
            growth_basis=str(raw.get("growth_basis") or ""),
            sector=str(raw.get("sector") or ""),
            source=str(raw.get("source") or ("yfinance" if use_default else "provider")),
            # roe/margins/revenue growth are true fractions from both providers and
            # can legitimately exceed 1.5 (buyback-inflated ROE), so no percent
            # heuristic — pass through numerically and clamp at the scoring layer.
            roe=_num(raw.get("roe")),
            profit_margin=_num(raw.get("profit_margin")),
            debt_to_equity=_num(raw.get("debt_to_equity")),
            revenue_growth=_num(raw.get("revenue_growth")),
            current_price=_num(raw.get("current_price")),
            target_mean_price=_num(raw.get("target_mean_price")),
            analyst_rating=_num(raw.get("analyst_rating")),
            n_analysts=(int(raw["n_analysts"])
                        if isinstance(raw.get("n_analysts"), (int, float))
                        and raw["n_analysts"] == raw["n_analysts"]          # NaN guard
                        and abs(raw["n_analysts"]) < 10_000 else None),
            next_earnings_date=str(raw.get("next_earnings_date") or ""),
            as_of=datetime.date.today().isoformat(),
            reconciliation_warnings=list(raw.get("reconciliation_warnings") or []),
        )
    if use_default and result.usable:
        _record_pit_snapshot(result)
    if use_default:  # empties negative-cache briefly; usable answers cache long
        with _PROVIDER_LOCK:
            if len(_FETCH_CACHE) >= _FETCH_CACHE_MAX:
                _FETCH_CACHE.pop(next(iter(_FETCH_CACHE)), None)
            _FETCH_CACHE[sym] = (_cache_now(), result)
    return result

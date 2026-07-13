"""Price-history data layer.

Holds an in-memory store of per-instrument OHLCV DataFrames. Data can come from:
  1. Bundled synthetic sample series — OPT-IN only (HELIOS_LOAD_SAMPLES=1;
     a fresh production start shows the honest empty state instead).
  2. A CSV uploaded by the user (client investment-model export).
  3. A live pull via yfinance, if the package is installed and the network is up.

Every DataFrame is indexed by a DatetimeIndex and exposes at least a 'close'
column; 'open'/'high'/'low'/'volume' are included when known.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import hashlib
import json as _json
import os
import urllib.request
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from . import analytics_cache

# Cap user-added instruments so repeated uploads/fetches can't grow memory
# without bound. Keep it high enough for a serious multi-theme research
# universe plus model holdings; samples are never counted against this limit.
MAX_USER_INSTRUMENTS = 250
DEFAULT_LIVE_REFRESH_WORKERS = 6
MAX_LIVE_REFRESH_WORKERS = 16
# Tickers only ever contain these characters (e.g. BRK-B, BTC-USD, ^GSPC, EURUSD=X).
# Sanitizing at the boundary neutralizes XSS-in-symbol and yfinance URL injection.
_SYMBOL_RE = re.compile(r"[^A-Z0-9.\-=^]")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
DEFAULT_LIVE_UNIVERSE = (
    "SPY", "QQQ", "IWM", "DIA",
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    "JPM", "XOM", "UNH", "BND", "TLT", "GLD", "BTC-USD",
)
STARTER_MODEL_LIVE_UNIVERSE = (
    "SPY", "QQQ", "IWM", "DIA",
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    "JPM", "XOM", "UNH", "BND", "TLT", "GLD", "BTC-USD",
    "AVGO", "AMD", "TSM", "SMH", "VRT",
    "VST", "CEG", "NEE", "XLU", "ETN", "GE", "PAVE", "GRID",
    "ITA", "XAR", "LMT", "RTX", "NOC", "GD", "CIBR", "PANW", "CRWD", "FTNT",
    "XLV", "VHT", "IHI", "IBB", "LLY", "ISRG", "SYK", "TMO", "ABBV",
    "XLE", "TIP", "SGOV", "QUAL", "USMV", "VIG",
)
LIVE_UNIVERSES = {
    "core": DEFAULT_LIVE_UNIVERSE,
    "advisor": DEFAULT_LIVE_UNIVERSE,
    "starter": STARTER_MODEL_LIVE_UNIVERSE,
    "starter_models": STARTER_MODEL_LIVE_UNIVERSE,
}


def clean_symbol(s: str, fallback: str = "CLIENT") -> str:
    return _SYMBOL_RE.sub("", (s or "").upper())[:20] or fallback


def clean_name(s: str, fallback: str = "") -> str:
    return _CTRL_RE.sub("", (s or "").strip())[:60] or fallback

# yfinance is optional. The platform is fully functional without it.
try:  # pragma: no cover - import guard
    import logging as _logging

    import yfinance as _yf
    # Quiet yfinance's own logging (404s for unknown tickers are expected and
    # handled via fallback — they shouldn't spam the server console).
    for _name in ("yfinance", "yfinance.data", "yfinance.utils"):
        _logging.getLogger(_name).setLevel(_logging.CRITICAL)
    HAS_YF = True
except Exception:  # pragma: no cover
    _yf = None
    HAS_YF = False

# Bound concurrent outbound provider calls process-wide so live lookups can't
# monopolize worker threads or hammer the upstream.
_YF_SEMAPHORE = threading.BoundedSemaphore(4)


def _bounded_provider_call(fn, timeout: float = 10.0):
    """Run a provider call that exposes no timeout knob (t.news / t.info) with a
    hard deadline. A hung upstream raises TimeoutError into the caller's
    existing failure path instead of pinning the request thread."""
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="helios-yf-call")
    try:
        return pool.submit(fn).result(timeout=timeout)
    finally:
        # Never wait on a hung call; the stray thread ends when the call returns.
        pool.shutdown(wait=False)


# --------------------------------------------------------------------------- #
# In-memory store
# --------------------------------------------------------------------------- #
@dataclass
class Instrument:
    symbol: str
    name: str
    df: pd.DataFrame
    source: str  # "sample" | "upload" | "live"
    headlines: list = field(default_factory=list)
    # Which price feed produced df ("fmp_eod_adjusted" | "yfinance" | "" for
    # non-live sources) — persisted so every history stays label-honest.
    price_provider: str = ""


_STORE: dict[str, Instrument] = {}
# The store is shared across all dashboard users (a shared advisor view, by
# design). The lock keeps concurrent waitress threads from corrupting it.
_STORE_LOCK = threading.RLock()


def _future_cutoff() -> pd.Timestamp:
    """Latest bar date accepted from ingestion: today (UTC) + 1 calendar day.

    The tolerance is load-bearing — same-day bars from markets ahead of UTC
    (ASX, Tokyo) are legitimately dated one day past a UTC clock."""
    from datetime import timezone
    return pd.Timestamp(datetime.now(timezone.utc).date()) + pd.Timedelta(days=1)


def get(symbol: str) -> Instrument | None:
    return _STORE.get((symbol or "").upper())


def all_instruments() -> list[Instrument]:
    with _STORE_LOCK:
        return list(_STORE.values())


def register(inst: Instrument) -> None:
    sym = inst.symbol.upper()
    with _STORE_LOCK:
        prev = _STORE.get(sym)
        _STORE[sym] = inst
        # Evict oldest user-added instruments (samples are never evicted).
        user_keys = [k for k, v in _STORE.items() if v.source != "sample"]
        for stale in user_keys[:-MAX_USER_INSTRUMENTS] if len(user_keys) > MAX_USER_INSTRUMENTS else []:
            _STORE.pop(stale, None)
    # Identical re-registration (the 5-minute auto-live cycle refetching an
    # unchanged series) must not destroy memoized analytics: full wipes up to
    # 17x per cycle forced Command Center/Radar to redo Monte-Carlo + backtests
    # near-constantly and silently flipped the model cone between the CMA
    # anchor and the generic mandate anchor (review finding).
    try:
        unchanged = (
            prev is not None and prev.source == inst.source
            and "close" in prev.df and "close" in inst.df
            and len(prev.df) == len(inst.df)
            and bool(prev.df.index.equals(inst.df.index))
            and bool(prev.df["close"].equals(inst.df["close"]))
        )
    except Exception:
        unchanged = False
    if unchanged:
        return
    # The replaced frame supersedes any memoized holding-price resolution (and
    # any failed-resolution back-off) for this symbol.
    _invalidate_resolution_caches(sym)
    # Any store change (upload, live fetch, refresh) invalidates memoized analytics.
    analytics_cache.invalidate()


# --------------------------------------------------------------------------- #
# Synthetic sample data
# --------------------------------------------------------------------------- #
# (symbol, name, annual_drift, annual_vol, start_price, regime_flips, seed)
_SAMPLE_SPEC = [
    ("AAPL", "Apple Inc.",            0.18, 0.26, 120.0, True,  1),
    ("MSFT", "Microsoft Corp.",       0.21, 0.24, 240.0, True,  2),
    ("NVDA", "NVIDIA Corp.",          0.42, 0.52,  45.0, True,  3),
    ("TSLA", "Tesla Inc.",            0.12, 0.58, 210.0, True,  4),
    ("SPY",  "S&P 500 ETF",           0.10, 0.16, 380.0, False, 5),
    ("BTC-USD", "Bitcoin / USD",      0.35, 0.70, 22000.0, True, 6),
]

# Canned headlines so the news/sentiment panel demonstrates value offline.
_SAMPLE_NEWS = {
    "AAPL": [
        "Apple beats earnings expectations as services revenue hits record",
        "Analysts upgrade Apple on strong iPhone demand and margin growth",
        "Supply chain probe weighs on Apple sentiment ahead of launch",
    ],
    "MSFT": [
        "Microsoft cloud growth surges, Azure outperforms estimates",
        "Microsoft raises guidance on robust AI and enterprise demand",
        "Regulators open antitrust probe into Microsoft bundling",
    ],
    "NVDA": [
        "NVIDIA rallies to record high on blowout data-center sales",
        "Analysts bullish as NVIDIA dominates AI accelerator market",
        "NVIDIA faces export restrictions, shares slip on China weakness",
    ],
    "TSLA": [
        "Tesla deliveries miss estimates, shares plunge in selloff",
        "Tesla cuts prices again, pressuring margins and profit outlook",
        "Tesla unveils new model, bulls cheer long-term growth story",
    ],
    "SPY": [
        "Stocks rally as inflation cools and Fed signals rate pause",
        "Market selloff deepens on recession fears and weak data",
        "Broad index recovers as earnings beat lowered expectations",
    ],
    "BTC-USD": [
        "Bitcoin surges past resistance on strong institutional inflows",
        "Crypto selloff accelerates as regulators tighten oversight",
        "Bitcoin rallies on ETF optimism and bullish on-chain signals",
    ],
}


def _simulate_series(drift, vol, start, flips, seed, days=760):
    """Geometric Brownian motion with optional regime shifts -> OHLCV frame."""
    rng = np.random.default_rng(seed)
    dt = 1.0 / 252.0
    mu = drift
    daily = np.empty(days)
    # Regime segments give the series realistic trending/choppy stretches.
    segments = 4 if flips else 1
    bounds = np.linspace(0, days, segments + 1).astype(int)
    regime_mult = rng.uniform(-1.2, 1.6, size=segments) if flips else np.array([1.0])
    for s in range(segments):
        a, b = bounds[s], bounds[s + 1]
        seg_mu = mu * (regime_mult[s] if flips else 1.0)
        seg_vol = vol * (1.0 + 0.4 * rng.standard_normal()) if flips else vol
        seg_vol = max(seg_vol, 0.05)
        n = b - a
        daily[a:b] = (seg_mu - 0.5 * seg_vol**2) * dt + seg_vol * np.sqrt(dt) * rng.standard_normal(n)

    close = start * np.exp(np.cumsum(daily))
    # Build plausible OHLC around the close path.
    intraday = np.abs(rng.standard_normal(days)) * vol * np.sqrt(dt) * close
    open_ = np.empty(days)
    open_[0] = start
    open_[1:] = close[:-1]
    high = np.maximum(open_, close) + 0.5 * intraday
    low = np.minimum(open_, close) - 0.5 * intraday
    volume = (rng.lognormal(15.5, 0.6, days)).astype(np.int64)

    end = datetime(2025, 6, 13)
    idx = pd.bdate_range(end=end, periods=days)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def load_samples() -> None:
    """Populate the store with the synthetic universe. Idempotent."""
    for symbol, name, drift, vol, start, flips, seed in _SAMPLE_SPEC:
        if symbol in _STORE:
            continue
        df = _simulate_series(drift, vol, start, flips, seed)
        register(Instrument(symbol, name, df, "sample", list(_SAMPLE_NEWS.get(symbol, []))))


# --------------------------------------------------------------------------- #
# CSV import
# --------------------------------------------------------------------------- #
_DATE_ALIASES = {"date", "timestamp", "time", "datetime", "day"}
_CLOSE_ALIASES = {"close", "adj close", "adj_close", "price", "last", "nav", "value"}
_OPEN_ALIASES = {"open"}
_HIGH_ALIASES = {"high"}
_LOW_ALIASES = {"low"}
_VOL_ALIASES = {"volume", "vol", "qty"}


def parse_csv(
    raw: bytes,
    symbol: str,
    name: str | None = None,
    source_filename: str | None = None,
) -> Instrument:
    """Parse an uploaded CSV into an Instrument.

    Accepts flexible column names. Requires a date-like column and a
    close/price-like column; OHLCV are used when present.
    """
    symbol = clean_symbol(symbol)
    name = clean_name(name, fallback=symbol)
    # Bound the parse: a pathological CSV shouldn't be able to pin a worker.
    df = pd.read_csv(io.BytesIO(raw), nrows=200_000)
    cols = {c.lower().strip(): c for c in df.columns}

    def pick(aliases):
        for a in aliases:
            if a in cols:
                return cols[a]
        return None

    date_col = pick(_DATE_ALIASES)
    close_col = pick(_CLOSE_ALIASES)
    if date_col is None or close_col is None:
        raise ValueError(
            "CSV needs a date column (Date/Timestamp) and a price column "
            "(Close/Price/NAV/Value). Found: " + ", ".join(df.columns)
        )

    out = pd.DataFrame()
    out["close"] = pd.to_numeric(df[close_col], errors="coerce")
    for tgt, aliases in (
        ("open", _OPEN_ALIASES),
        ("high", _HIGH_ALIASES),
        ("low", _LOW_ALIASES),
        ("volume", _VOL_ALIASES),
    ):
        c = pick(aliases)
        if c is not None:
            out[tgt] = pd.to_numeric(df[c], errors="coerce")

    out.index = pd.to_datetime(df[date_col], errors="coerce")
    out = out[~out.index.isna()]
    out.index = out.index.normalize()
    # Only finite, positive closes are analyzable; one row per calendar date
    # (keep='last', mirroring portfolio._to_daily) so duplicated exports can't
    # skew analytics.
    out = out[np.isfinite(out["close"]) & (out["close"] > 0)]
    out = out[~out.index.duplicated(keep="last")].sort_index()
    # Future-dated prices cannot be analyzed — they contaminate backtests and
    # let "prospective" journal entries settle against fabricated bars (review
    # finding, reproduced: a future CSV read as maximally fresh and a signal
    # measured +2% against bars that don't exist). Reject loudly rather than
    # trim: future dates almost always mean a broken export or DD/MM-vs-MM/DD
    # misparse that corrupted the past rows too. The +1 calendar-day tolerance
    # keeps legitimate same-day bars from markets ahead of UTC (ASX/Tokyo).
    cutoff = _future_cutoff()
    n_future = int((out.index > cutoff).sum())
    if n_future:
        raise ValueError(
            f"CSV contains {n_future} future-dated row(s) (latest "
            f"{out.index.max().date()}) — future prices cannot be analyzed; "
            "check the date column/format and re-export.")
    if len(out) < 30:
        raise ValueError("Need at least 30 valid rows of price history to analyze.")

    inst = Instrument(symbol, name, out, "upload", [])
    register(inst)
    _persist_instrument(
        inst,
        adjusted=None,
        metadata={"source_filename": source_filename or "", "imported_via": "price_csv"},
    )
    # Uploads extend history exactly like live refreshes do: without this, an
    # upload-only/offline workflow never measured its pending journal signals
    # even when the new history covered the full forward window (review
    # finding). Swallows its own exceptions — cannot break the upload.
    _refresh_signal_journal_forward_results()
    return inst


# --------------------------------------------------------------------------- #
# Live fetch (optional)
# --------------------------------------------------------------------------- #
_PERIOD_DAYS = {"1mo": 31, "3mo": 93, "6mo": 186, "1y": 366, "2y": 731, "5y": 1827, "10y": 3653, "max": 7300}
# FMP EOD is opt-in until a side-by-side reconciliation justifies cutover:
# HELIOS_PRICE_SOURCE=fmp makes the KEYED paid feed primary (dividend-adjusted,
# matching yfinance auto_adjust semantics) with yfinance as fallback.
_FMP_EOD_MAX_BYTES = 16 * 1024 * 1024


def _clean_price_frame(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Shared provider hygiene: finite positive closes, one row per date,
    future bars clamped (a provider/clock anomaly must not inject
    unanalyzable prices; the rest of a live series is still good)."""
    df = df.copy()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df[np.isfinite(df["close"]) & (df["close"] > 0)]
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df[df.index <= _future_cutoff()]
    if df.empty:
        raise RuntimeError(f"No live data returned for '{symbol}'.")
    return df


def _fmp_price_history(symbol: str, period: str = "2y") -> pd.DataFrame | None:
    """Dividend-adjusted EOD history from FMP's stable API, or None on any
    failure (caller falls back to yfinance — never a fabricated series)."""
    key = os.environ.get("HELIOS_FMP_KEY", "").strip()
    if not key:
        return None
    days = _PERIOD_DAYS.get((period or "2y").lower(), 731)
    start = (datetime.now() - timedelta(days=days)).date().isoformat()
    base = os.environ.get("HELIOS_FMP_BASE_URL", "https://financialmodelingprep.com").rstrip("/")
    url = (f"{base}/stable/historical-price-eod/dividend-adjusted"
           f"?symbol={symbol}&from={start}&apikey={key}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Helios Research Terminal"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (fixed https host)
            payload = _json.loads(resp.read(_FMP_EOD_MAX_BYTES + 1).decode("utf-8", errors="replace"))
    except Exception:
        return None
    rows = payload if isinstance(payload, list) else []
    records = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        records.append({
            "date": row["date"],
            "open": row.get("adjOpen"), "high": row.get("adjHigh"),
            "low": row.get("adjLow"), "close": row.get("adjClose"),
            "volume": row.get("volume"),
        })
    if not records:
        return None
    df = pd.DataFrame(records).set_index("date")
    try:
        return _clean_price_frame(df, symbol)
    except RuntimeError:
        return None


def reconcile_price_sources(symbol: str, period: str = "3mo") -> dict:
    """Side-by-side FMP vs yfinance closes — the evidence that justifies (or
    blocks) a price-source cutover. Never rewrites persisted history."""
    symbol = clean_symbol(symbol, fallback="")
    if not symbol:
        raise ValueError("Invalid ticker symbol.")
    fmp = _fmp_price_history(symbol, period)
    yf_df = None
    if HAS_YF:
        try:
            with _YF_SEMAPHORE:
                hist = _yf.Ticker(symbol).history(period=period, auto_adjust=True, timeout=10)
            if hist is not None and not hist.empty:
                yf_df = _clean_price_frame(hist.rename(columns=str.lower)[["close"]], symbol)
        except Exception:
            yf_df = None
    if fmp is None or yf_df is None:
        return {"symbol": symbol, "status": "unavailable",
                "fmp_rows": int(len(fmp)) if fmp is not None else 0,
                "yfinance_rows": int(len(yf_df)) if yf_df is not None else 0,
                "note": "Both sources must respond to reconcile — nothing is assumed."}
    joined = pd.concat({"fmp": fmp["close"], "yfinance": yf_df["close"]}, axis=1).dropna()
    if len(joined) < 5:
        return {"symbol": symbol, "status": "insufficient_overlap",
                "overlap_days": int(len(joined))}
    rel = ((joined["fmp"] - joined["yfinance"]).abs() / joined["yfinance"])
    worst_day = rel.idxmax()
    return {
        "symbol": symbol,
        "status": "ok",
        "overlap_days": int(len(joined)),
        "mean_abs_diff_pct": round(float(rel.mean()) * 100, 4),
        "max_abs_diff_pct": round(float(rel.max()) * 100, 4),
        "worst_day": str(worst_day.date()),
        "fmp_last": round(float(joined["fmp"].iloc[-1]), 4),
        "yfinance_last": round(float(joined["yfinance"].iloc[-1]), 4),
        "note": ("Dividend-adjusted closes compared over the overlap window. Cut over "
                 "(HELIOS_PRICE_SOURCE=fmp) only when diffs stay in adjustment-timing "
                 "noise (<~0.1% mean) across your book."),
    }


def fetch_live(symbol: str, period: str = "2y", persist: bool = True) -> Instrument:
    """Pull live history (and free news headlines).

    Provider order follows HELIOS_PRICE_SOURCE: 'fmp' prefers the keyed paid
    EOD feed with yfinance fallback; default stays yfinance-first until a
    reconciliation window justifies cutover (review roadmap, stage 3)."""
    symbol = clean_symbol(symbol, fallback="")
    if not symbol:
        raise ValueError("Invalid ticker symbol.")
    df = None
    provider_used = ""
    if os.environ.get("HELIOS_PRICE_SOURCE", "yfinance").strip().lower() == "fmp":
        df = _fmp_price_history(symbol, period)
        if df is not None:
            provider_used = "fmp_eod_adjusted"
    t = _yf.Ticker(symbol) if HAS_YF else None
    if df is None:
        if not HAS_YF:
            raise RuntimeError("yfinance is not installed; live data unavailable.")
        # Bounded outbound call so a slow/hung upstream can't pin a worker thread.
        with _YF_SEMAPHORE:
            hist = t.history(period=period, auto_adjust=True, timeout=10)
        if hist is None or hist.empty:
            raise RuntimeError(f"No live data returned for '{symbol}'.")
        hist = hist.rename(columns=str.lower)
        df = _clean_price_frame(hist[["open", "high", "low", "close", "volume"]], symbol)
        provider_used = "yfinance"

    # Headlines + display name stay yfinance best-effort regardless of the
    # price provider (FMP EOD carries neither).
    headlines = []
    name = symbol.upper()
    if t is not None:
        try:
            with _YF_SEMAPHORE:
                news_items = _bounded_provider_call(lambda: t.news) or []
            for item in news_items[:12]:
                title = (item.get("content", {}) or {}).get("title") or item.get("title")
                if title:
                    headlines.append(title)
        except Exception:
            pass
        try:
            with _YF_SEMAPHORE:
                info = _bounded_provider_call(lambda: t.info) or {}
            name = info.get("shortName") or name
        except Exception:
            pass

    inst = Instrument(symbol.upper(), name, df, "live", headlines, price_provider=provider_used)
    register(inst)
    if persist:
        _persist_instrument(inst, adjusted=True,
                            metadata={"period": period, "imported_via": "live_fetch",
                                      "price_provider": provider_used})
    return inst


def load_persisted_instruments() -> None:
    """Load persisted live/uploaded instruments into the process store."""
    try:
        from . import persistence

        store = persistence.get_store()
        for item in store.load_instruments():
            register(Instrument(item.symbol, item.name, item.df, item.source, []))
    except Exception:
        # Persistence is optional; callers can inspect /api/data/status for the warning.
        return


def refresh_live_symbol(symbol: str, fetcher=None, refresh_journal: bool = True) -> dict:
    """Refresh an existing live instrument and record a local refresh log.

    refresh_journal=False skips the signal-journal forward refresh so batch
    callers can run it once after the whole batch instead of per symbol.
    """
    sym = clean_symbol(symbol, fallback="")
    if not sym:
        return {"symbol": "", "status": "error", "rows_added": 0, "message": "Invalid ticker symbol."}
    current = get(sym)
    if current is None or current.source != "live":
        message = "Refresh skipped: only existing live instruments can be refreshed."
        _record_refresh(sym, "skipped", 0, message)
        return {"symbol": sym, "status": "skipped", "rows_added": 0, "message": message}
    before_dates = set(pd.to_datetime(current.df.index).normalize())
    try:
        if fetcher is None:
            # No HAS_YF pre-check: fetch_live itself fails loudly when neither
            # the configured price source nor yfinance can serve the symbol —
            # a hard yfinance requirement here would break FMP-source refreshes.
            inst = fetch_live(sym, persist=False)
        else:
            inst = fetcher(sym)
            if not isinstance(inst, Instrument):
                raise RuntimeError("Live refresh provider returned an invalid payload.")
            inst.symbol = sym
            inst.source = "live"
            register(inst)
        after_dates = set(pd.to_datetime(inst.df.index).normalize())
        rows_added = max(0, len(after_dates - before_dates))
        _persist_instrument(inst, adjusted=True,
                            metadata={"imported_via": "live_refresh",
                                      "price_provider": getattr(inst, "price_provider", "")})
        if refresh_journal:
            _refresh_signal_journal_forward_results()
        message = f"Refreshed {len(inst.df)} live rows."
        _record_refresh(sym, "ok", rows_added, message)
        return {"symbol": sym, "status": "ok", "rows_added": rows_added, "rows": len(inst.df), "message": message}
    except Exception as exc:
        message = str(exc) or "Live refresh failed."
        _record_refresh(sym, "error", 0, message)
        return {"symbol": sym, "status": "error", "rows_added": 0, "message": message}


def expand_live_symbols(raw: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """Return a de-duplicated live universe from a preset name or comma list."""
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text.lower() in {"0", "false", "off", "none", "disabled"}:
            return []
        tokens = LIVE_UNIVERSES.get(text.lower()) or re.split(r"[\s,]+", text)
    else:
        tokens = raw
    out: list[str] = []
    seen: set[str] = set()
    for item in tokens:
        symbol = clean_symbol(str(item), fallback="")
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out[:MAX_USER_INSTRUMENTS]


def _live_refresh_worker_count(max_workers: int | None, n_symbols: int) -> int:
    if n_symbols <= 0:
        return 0
    try:
        requested = int(max_workers or DEFAULT_LIVE_REFRESH_WORKERS)
    except (TypeError, ValueError):
        requested = DEFAULT_LIVE_REFRESH_WORKERS
    bounded = max(1, min(MAX_LIVE_REFRESH_WORKERS, requested))
    return min(n_symbols, bounded)


def ensure_live_symbols(
    symbols: list[str] | tuple[str, ...],
    period: str = "2y",
    fetcher=None,
    max_workers: int | None = None,
) -> dict:
    """Fetch or refresh a configured live universe and persist real provider data.

    Existing sample instruments are intentionally replaced only after the live
    provider returns a valid series. If a fetch fails, the prior sample/demo row
    remains in place and is not promoted to real research evidence.
    """
    requested = expand_live_symbols(list(symbols))
    worker_count = _live_refresh_worker_count(max_workers, len(requested))

    def fetch_one(symbol: str) -> dict:
        before = get(symbol)
        before_dates = set()
        if before is not None and before.source == "live":
            before_dates = set(pd.to_datetime(before.df.index).normalize())
        try:
            if fetcher is None:
                if not HAS_YF:
                    raise RuntimeError("yfinance is not installed; live data unavailable.")
                inst = fetch_live(symbol, period=period, persist=False)
            else:
                inst = fetcher(symbol, period=period, persist=False)
                if not isinstance(inst, Instrument):
                    raise RuntimeError("Live provider returned an invalid payload.")
            inst.symbol = symbol
            inst.source = "live"
            return {"symbol": symbol, "status": "ok", "instrument": inst, "before_dates": before_dates}
        except Exception as exc:
            message = str(exc) or "Auto live update failed."
            return {"symbol": symbol, "status": "error", "rows_added": 0, "message": message}

    if worker_count > 1:
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="helios-live") as executor:
            fetched = list(executor.map(fetch_one, requested))
    else:
        fetched = [fetch_one(symbol) for symbol in requested]

    results = []
    for item in fetched:
        symbol = item["symbol"]
        if item["status"] == "ok":
            inst = item["instrument"]
            register(inst)
            after_dates = set(pd.to_datetime(inst.df.index).normalize())
            before_dates = item["before_dates"]
            rows_added = max(0, len(after_dates - before_dates)) if before_dates else len(after_dates)
            _persist_instrument(inst, adjusted=True,
                                metadata={"period": period, "imported_via": "auto_live",
                                          "price_provider": getattr(inst, "price_provider", "")})
            _record_refresh(symbol, "ok", rows_added, f"Auto live updated {len(inst.df)} rows.")
            results.append({"symbol": symbol, "status": "ok", "rows_added": rows_added, "rows": len(inst.df), "message": f"Auto live updated {len(inst.df)} rows."})
        else:
            message = item["message"]
            _record_refresh(symbol, "error", 0, message)
            results.append({"symbol": symbol, "status": "error", "rows_added": 0, "message": message})
    if any(item["status"] == "ok" for item in results):
        _refresh_signal_journal_forward_results()
    return {
        "requested": requested,
        "refreshed": sum(1 for item in results if item["status"] == "ok"),
        "failed": sum(1 for item in results if item["status"] == "error"),
        "max_workers": worker_count,
        "results": results,
    }


def _persist_instrument(
    inst: Instrument,
    adjusted: bool | None = None,
    metadata: dict | None = None,
) -> dict:
    if inst.source not in {"live", "upload"}:
        return {"persisted": False, "reason": "Only live/uploaded instruments are persisted."}
    try:
        from . import persistence

        return persistence.get_store().persist_instrument(
            symbol=inst.symbol,
            name=inst.name,
            source=inst.source,
            frame=inst.df,
            adjusted=adjusted,
            metadata=metadata or {},
        )
    except Exception as exc:
        return {"persisted": False, "warning": str(exc)}


def _record_refresh(symbol: str, status: str, rows_added: int, message: str) -> None:
    try:
        from . import persistence

        persistence.get_store().record_refresh(
            symbol=symbol,
            status=status,
            rows_added=rows_added,
            message=message,
            source="live",
        )
    except Exception:
        return


def _refresh_signal_journal_forward_results() -> None:
    # Both journals measure forward outcomes when new bars arrive; scoring here
    # (the single choke point every upload/refresh path already calls) is what
    # lets the GET handlers stay pure reads.
    try:
        from . import signal_journal

        signal_journal.refresh_forward_results()
    except Exception:
        pass
    try:
        from . import decision_journal

        decision_journal.refresh_pending_outcomes(limit=250)
    except Exception:
        return


# --------------------------------------------------------------------------- #
# Holding-price resolution for portfolio models
#
# A model is a basket of (ticker, weight). We need a price series per ticker but
# do NOT want every holding cluttering the instrument sidebar, so resolved
# holding prices live in a separate cache. Resolution order:
#   sidebar store  ->  cache  ->  live (yfinance)  ->  deterministic simulation
# The chosen source is reported so the UI can honestly flag simulated data.
# --------------------------------------------------------------------------- #
_PRICE_CACHE: dict[str, "PriceSeries"] = {}
_CACHE_LOCK = threading.RLock()
# Failed live resolutions are negative-cached briefly so every analysis request
# doesn't retry a dead/unknown ticker against the provider.
NEGATIVE_RESOLVE_TTL_SECONDS = 300.0
_NEGATIVE_RESOLVE: dict[str, float] = {}  # symbol -> monotonic retry deadline


def _invalidate_resolution_caches(symbol: str) -> None:
    """Drop cached resolutions (and failed-resolution back-off) for a symbol
    whose instrument frame was just replaced, so analytics see the new data."""
    with _CACHE_LOCK:
        for key in [k for k, v in _PRICE_CACHE.items() if k == symbol or v.symbol == symbol]:
            _PRICE_CACHE.pop(key, None)
        _NEGATIVE_RESOLVE.pop(symbol, None)


def _live_resolution_backed_off(sym: str) -> bool:
    with _CACHE_LOCK:
        deadline = _NEGATIVE_RESOLVE.get(sym)
        if deadline is None:
            return False
        if time.monotonic() >= deadline:
            _NEGATIVE_RESOLVE.pop(sym, None)
            return False
        return True


def _record_failed_resolution(sym: str) -> None:
    with _CACHE_LOCK:
        if sym not in _NEGATIVE_RESOLVE and len(_NEGATIVE_RESOLVE) >= MAX_PRICE_CACHE:
            _NEGATIVE_RESOLVE.pop(next(iter(_NEGATIVE_RESOLVE)), None)
        _NEGATIVE_RESOLVE[sym] = time.monotonic() + NEGATIVE_RESOLVE_TTL_SECONDS


@dataclass
class PriceSeries:
    symbol: str
    close: pd.Series  # DatetimeIndex -> close
    source: str       # "sample" | "upload" | "live" | "simulated"


def _ticker_seed(ticker: str) -> int:
    # Deterministic per-ticker seed (stable across processes; no RNG-at-import).
    digest = hashlib.sha256(f"helios:{clean_symbol(ticker, fallback='')}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % (2**31)


def _simulate_for_ticker(ticker: str) -> pd.Series:
    """A plausible, deterministic price series for an unresolved ticker so a
    model stays analyzable offline. Clearly flagged 'simulated' upstream."""
    seed = _ticker_seed(ticker)
    rng = np.random.default_rng(seed)
    drift = rng.uniform(0.04, 0.22)          # 4%-22% annual
    vol = rng.uniform(0.14, 0.45)            # 14%-45% annual
    start = rng.uniform(20, 300)
    df = _simulate_series(drift, vol, start, flips=True, seed=seed)
    return df["close"]


MAX_PRICE_CACHE = 400


def _allowed_source(source: str, allow_sample: bool, allow_simulated: bool) -> bool:
    if source == "sample":
        return allow_sample
    if source == "simulated":
        return allow_simulated
    return source in {"upload", "live"}


def resolve_series(
    ticker: str,
    allow_live: bool = True,
    allow_sample: bool = True,
    allow_simulated: bool = True,
) -> PriceSeries:
    """Return a close-price series for one holding ticker, with fallback.

    allow_live=False skips the (potentially slow) yfinance call and goes straight
    to simulation when the ticker isn't already known — used to budget the number
    of live fetches a single portfolio analysis can trigger.
    """
    sym = clean_symbol(ticker, fallback="")
    if not sym:
        raise ValueError(f"Invalid ticker: {ticker!r}")

    with _CACHE_LOCK:
        if sym in _PRICE_CACHE and _allowed_source(_PRICE_CACHE[sym].source, allow_sample, allow_simulated):
            return _PRICE_CACHE[sym]

    # 1) a sidebar instrument already has good history
    inst = get(sym)
    if inst is not None and _allowed_source(inst.source, allow_sample, allow_simulated):
        ps = PriceSeries(sym, inst.df["close"].dropna(), inst.source)
    else:
        ps = None
        # 2) live prices (history only — skip the news/info round-trips).
        # Recently failed symbols are skipped for NEGATIVE_RESOLVE_TTL_SECONDS
        # and fall straight through to the same simulated/error path.
        if HAS_YF and allow_live and not _live_resolution_backed_off(sym):
            try:
                with _YF_SEMAPHORE:
                    hist = _yf.Ticker(sym).history(period="5y", auto_adjust=True, timeout=8)
                if hist is not None and not hist.empty:
                    close = hist["Close"].copy()
                    close.index = pd.to_datetime(close.index).tz_localize(None)
                    ps = PriceSeries(sym, close.dropna(), "live")
                else:
                    _record_failed_resolution(sym)
            except Exception:
                ps = None
                _record_failed_resolution(sym)
        # 3) deterministic simulation
        if ps is None and allow_simulated:
            ps = PriceSeries(sym, _simulate_for_ticker(sym), "simulated")
            # A simulation forced only by the live budget isn't cached, so a later
            # (un-budgeted) call can still resolve it live.
            if not allow_live:
                return ps
        if ps is None:
            raise ValueError(
                f"No eligible real price history for {sym}. Upload a CSV price history "
                "or fetch live data before using this ticker in Pro research."
            )

    with _CACHE_LOCK:
        # Bound cache growth (FIFO eviction), mirroring the instrument-store cap.
        if sym not in _PRICE_CACHE and len(_PRICE_CACHE) >= MAX_PRICE_CACHE:
            _PRICE_CACHE.pop(next(iter(_PRICE_CACHE)), None)
        _PRICE_CACHE[sym] = ps
    return ps

"""Price-history data layer.

Holds an in-memory store of per-instrument OHLCV DataFrames. Runtime data can
come only from an uploaded history or an explicit live-provider operation.
Synthetic series live exclusively in the offline test fixtures.

Every DataFrame is indexed by a DatetimeIndex and exposes at least a 'close'
column; 'open'/'high'/'low'/'volume' are included when known.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import json as _json
import os
import urllib.parse
import urllib.request
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from . import analytics_cache

# Cap user-added instruments so repeated uploads/fetches can't grow memory
# without bound. Keep it high enough for a serious multi-theme research
# universe plus model holdings.
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
    source: str  # "upload" | "live"
    headlines: list = field(default_factory=list)
    # Which price feed produced df ("fmp_eod_adjusted" | "yfinance" | "" for
    # non-live sources) — persisted so every history stays label-honest.
    price_provider: str = ""
    retrieved_at: str = ""


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


def snapshot_instruments() -> dict[str, Instrument]:
    """Return a cheap mapping snapshot for request-level rollback.

    Ingestion replaces Instrument objects rather than mutating their frames, so
    copying the mapping is sufficient and avoids duplicating large histories.
    """
    with _STORE_LOCK:
        return dict(_STORE)


def restore_instruments(snapshot: dict[str, Instrument]) -> None:
    """Restore an in-memory mapping after a privileged transaction rolls back."""
    with _STORE_LOCK:
        _STORE.clear()
        _STORE.update(snapshot)
    analytics_cache.invalidate()


def register(inst: Instrument) -> None:
    sym = inst.symbol.upper()
    with _STORE_LOCK:
        prev = _STORE.get(sym)
        _STORE[sym] = inst
        # Evict oldest user-added instruments; test-only fixtures remain isolated.
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
    _record_price_revisions(sym, prev, inst)
    # The replaced frame supersedes any memoized holding-price resolution (and
    # any failed-resolution back-off) for this symbol.
    _invalidate_resolution_caches(sym)
    # Any store change (upload, live fetch, refresh) invalidates memoized analytics.
    analytics_cache.invalidate()


_REVISION_THRESHOLD = 0.001  # 0.1%: below this, float/adjustment jitter


def _record_price_revisions(sym: str, prev: Instrument | None, inst: Instrument) -> None:
    """Silent vendor restatements of ALREADY-STORED bars become durable
    evidence (v10 spine). New bars are not revisions; only overlapping dates
    whose close moved more than the jitter threshold are recorded."""
    if prev is None or prev.source != "live" or inst.source != "live":
        return
    # A provider switch (FMP <-> yfinance fallback flip) changes adjustment
    # methodology wholesale — that is a labeled sourcing event, not a vendor
    # restatement; comparing across it floods the ledger with false positives
    # (adversarial-review finding).
    if getattr(prev, "price_provider", "") != getattr(inst, "price_provider", ""):
        return
    try:
        if "close" not in prev.df or "close" not in inst.df:
            return
        old_close = prev.df["close"].dropna()
        new_close = inst.df["close"].dropna()
        overlap = old_close.index.intersection(new_close.index)
        if overlap.empty:
            return
        # Exclude the most recent stored bar: same-day live quotes legitimately
        # move until the session closes.
        overlap = overlap[overlap < old_close.index.max()]
        if len(overlap) < 2:
            return
        ratios = (new_close[overlap] / old_close[overlap]).dropna()
        if ratios.empty:
            return
        # A dividend/split re-adjustment shifts EVERY bar by the same ratio —
        # expected and explained by the corporate_actions table, not a
        # restatement. Only bars deviating from the common shift are evidence.
        median_ratio = float(ratios.median())
        revisions = []
        for ts in overlap:
            old_px, new_px = float(old_close[ts]), float(new_close[ts])
            if old_px <= 0 or median_ratio <= 0:
                continue
            deviation = abs(new_px / old_px / median_ratio - 1.0)
            if deviation > _REVISION_THRESHOLD:
                revisions.append({
                    "bar_date": str(pd.Timestamp(ts).date()),
                    "old_close": round(old_px, 6), "new_close": round(new_px, 6),
                    "change_pct": round(deviation * 100, 4),
                })
        if revisions:
            from . import persistence

            persistence.get_store().record_price_revisions(
                sym, revisions, provider=getattr(inst, "price_provider", ""))
    except Exception:
        return


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
_TIINGO_EOD_MAX_BYTES = 16 * 1024 * 1024


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
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (configured HTTPS provider)
            raw = resp.read(_FMP_EOD_MAX_BYTES + 1)
        if len(raw) > _FMP_EOD_MAX_BYTES:
            return None
        payload = _json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return None
    records = []
    for row in payload if isinstance(payload, list) else []:
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
    try:
        return _clean_price_frame(pd.DataFrame(records).set_index("date"), symbol)
    except RuntimeError:
        return None


def _tiingo_price_history(symbol: str, period: str = "2y") -> pd.DataFrame | None:
    """Adjusted daily history from Tiingo, or None on provider failure."""
    key = os.environ.get("HELIOS_TIINGO_KEY", "").strip()
    if not key:
        return None
    days = _PERIOD_DAYS.get((period or "2y").lower(), 731)
    start = (datetime.now() - timedelta(days=days)).date().isoformat()
    base = os.environ.get("HELIOS_TIINGO_BASE_URL", "https://api.tiingo.com").rstrip("/")
    quoted_symbol = urllib.parse.quote(symbol, safe="")
    url = (
        f"{base}/tiingo/daily/{quoted_symbol}/prices?startDate={start}"
        f"&resampleFreq=daily&token={urllib.parse.quote(key, safe='')}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Helios Research Terminal"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (configured HTTPS provider)
            raw = resp.read(_TIINGO_EOD_MAX_BYTES + 1)
        if len(raw) > _TIINGO_EOD_MAX_BYTES:
            return None
        payload = _json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return None
    records = []
    for row in payload if isinstance(payload, list) else []:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        records.append({
            "date": row["date"],
            "open": row.get("adjOpen"),
            "high": row.get("adjHigh"),
            "low": row.get("adjLow"),
            "close": row.get("adjClose"),
            "volume": row.get("adjVolume") or row.get("volume"),
        })
    if not records:
        return None
    try:
        return _clean_price_frame(pd.DataFrame(records).set_index("date"), symbol)
    except RuntimeError:
        return None


def _provider_price_history(provider: str, symbol: str, period: str) -> pd.DataFrame | None:
    key = str(provider or "").strip().lower()
    if key == "fmp":
        return _fmp_price_history(symbol, period)
    if key == "tiingo":
        return _tiingo_price_history(symbol, period)
    if key == "yfinance" and HAS_YF:
        try:
            with _YF_SEMAPHORE:
                history = _yf.Ticker(symbol).history(period=period, auto_adjust=True, timeout=10)
            if history is not None and not history.empty:
                return _clean_price_frame(history.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]], symbol)
        except Exception:
            return None
    return None


def _price_provider_order() -> list[str]:
    configured = os.environ.get("HELIOS_PRICE_SOURCE", "yfinance").strip().lower()
    order = [configured] if configured in {"fmp", "tiingo", "yfinance"} else ["yfinance"]
    try:
        from . import provider_registry

        readiness = provider_registry.domain_readiness("prices")
        cutover = readiness.get("cutover") if isinstance(readiness, dict) else None
        if readiness.get("passed") and isinstance(cutover, dict):
            return [
                provider for provider in (
                    str(cutover.get("primary_provider") or ""),
                    str(cutover.get("backup_provider") or ""),
                ) if provider
            ]
        if provider_registry.controls_required():
            # Institutional mode never falls through to an unapproved public
            # adapter while provider controls are incomplete.
            return []
    except Exception:
        # A broken registry is itself a failed control. Institutional mode
        # must never turn that outage into permission to use a public fallback.
        institutional = os.environ.get("HELIOS_INSTITUTIONAL_CONTROLS", "1").strip().lower()
        if institutional not in {"0", "false", "no", "off"}:
            return []
    for provider in ("fmp", "tiingo", "yfinance"):
        if provider not in order:
            order.append(provider)
    return [provider for provider in order if provider]


def reconcile_price_sources(
    symbol: str,
    period: str = "3mo",
    primary_provider: str = "fmp",
    backup_provider: str = "yfinance",
) -> dict:
    """Side-by-side adjusted closes; never rewrites persisted history."""
    symbol = clean_symbol(symbol, fallback="")
    if not symbol:
        raise ValueError("Invalid ticker symbol.")
    primary_key = str(primary_provider or "").strip().lower()
    backup_key = str(backup_provider or "").strip().lower()
    if primary_key == backup_key or primary_key not in {"fmp", "tiingo", "yfinance"} or backup_key not in {"fmp", "tiingo", "yfinance"}:
        raise ValueError("Choose two different supported price providers.")
    try:
        from . import provider_registry

        for provider in (primary_key, backup_key):
            control = provider_registry.provider_contract_readiness(provider, "prices")
            if not control["passed"]:
                raise ValueError(control["detail"])
    except ValueError:
        raise
    except Exception as exc:
        if os.environ.get("HELIOS_INSTITUTIONAL_CONTROLS", "1").strip().lower() not in {
            "0", "false", "no", "off",
        }:
            raise RuntimeError(f"Provider controls unavailable; reconciliation blocked: {exc}") from exc
    primary = _provider_price_history(primary_key, symbol, period)
    backup = _provider_price_history(backup_key, symbol, period)
    if primary is None or backup is None:
        return {"symbol": symbol, "status": "unavailable",
                "primary_provider": primary_key,
                "backup_provider": backup_key,
                "primary_rows": int(len(primary)) if primary is not None else 0,
                "backup_rows": int(len(backup)) if backup is not None else 0,
                "note": "Both sources must respond to reconcile — nothing is assumed."}
    joined = pd.concat({"primary": primary["close"], "backup": backup["close"]}, axis=1).dropna()
    if len(joined) < 5:
        return {"symbol": symbol, "status": "insufficient_overlap",
                "overlap_days": int(len(joined))}
    rel = ((joined["primary"] - joined["backup"]).abs() / joined["backup"])
    worst_day = rel.idxmax()
    return {
        "symbol": symbol,
        "status": "ok",
        "primary_provider": primary_key,
        "backup_provider": backup_key,
        "overlap_days": int(len(joined)),
        "mean_abs_diff_pct": round(float(rel.mean()) * 100, 4),
        "max_abs_diff_pct": round(float(rel.max()) * 100, 4),
        "worst_day": str(worst_day.date()),
        "primary_last": round(float(joined["primary"].iloc[-1]), 4),
        "backup_last": round(float(joined["backup"].iloc[-1]), 4),
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "note": "Adjusted closes compared over the overlap window; approval is recorded separately.",
    }


def fetch_live(symbol: str, period: str = "2y", persist: bool = True) -> Instrument:
    """Pull live history (and free news headlines).

    Outside institutional mode, provider order follows HELIOS_PRICE_SOURCE.
    Institutional mode uses only the approved primary/backup pair and fails
    closed when no current cutover exists."""
    symbol = clean_symbol(symbol, fallback="")
    if not symbol:
        raise ValueError("Invalid ticker symbol.")
    df = None
    provider_used = ""
    for provider in _price_provider_order():
        df = _provider_price_history(provider, symbol, period)
        if df is not None:
            provider_used = {
                "fmp": "fmp_eod_adjusted",
                "tiingo": "tiingo_eod_adjusted",
                "yfinance": "yfinance",
            }[provider]
            break
    institutional = os.environ.get("HELIOS_INSTITUTIONAL_CONTROLS", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }
    # yfinance metadata/news/actions are a separate data use, not an innocent
    # side effect of an approved FMP/Tiingo price cutover. Keep that adapter
    # entirely outside governed institutional fetches.
    t = _yf.Ticker(symbol) if HAS_YF and not institutional else None
    if df is None:
        raise RuntimeError(
            f"Live data unavailable: no configured price provider returned data for '{symbol}'."
        )

    # Outside institutional mode, headlines + display name remain yfinance
    # best-effort. Governed mode keeps the approved provider boundary intact.
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

    retrieved_at = datetime.now().astimezone().isoformat(timespec="seconds")
    inst = Instrument(
        symbol.upper(), name, df, "live", headlines,
        price_provider=provider_used, retrieved_at=retrieved_at,
    )
    # Keep provider context transiently so batch callers can finish the same
    # persistence pipeline after concurrent network work completes.
    setattr(inst, "_provider_ticker", t)
    if persist:
        before = get(symbol)
        before_dates = _instrument_dates(before)
        _commit_live_instrument(
            inst,
            before_dates=before_dates,
            imported_via="live_fetch",
            period=period,
            refresh_journal=True,
            message_prefix="Fetched",
        )
    return inst


def live_provider_available() -> bool:
    available = {
        "yfinance": HAS_YF,
        "fmp": bool(os.environ.get("HELIOS_FMP_KEY", "").strip()),
        "tiingo": bool(os.environ.get("HELIOS_TIINGO_KEY", "").strip()),
    }
    return any(available.get(provider, False) for provider in _price_provider_order())


def _instrument_dates(inst: Instrument | None) -> set[pd.Timestamp]:
    if inst is None or inst.source != "live":
        return set()
    return set(pd.to_datetime(inst.df.index).normalize())


def _commit_live_instrument(
    inst: Instrument,
    *,
    before_dates: set[pd.Timestamp] | None,
    imported_via: str,
    period: str,
    refresh_journal: bool,
    message_prefix: str,
) -> dict:
    """Single validated persistence path for every provider result."""
    inst.symbol = clean_symbol(inst.symbol, fallback="")
    if not inst.symbol:
        raise ValueError("Live provider returned an invalid ticker symbol.")
    inst.source = "live"
    inst.df = _clean_price_frame(inst.df, inst.symbol)
    inst.retrieved_at = inst.retrieved_at or datetime.now().astimezone().isoformat(timespec="seconds")
    ticker_obj = getattr(inst, "_provider_ticker", None)
    if hasattr(inst, "_provider_ticker"):
        delattr(inst, "_provider_ticker")
    persisted = _persist_instrument(
        inst,
        adjusted=True,
        metadata={
            "period": period,
            "imported_via": imported_via,
            "price_provider": inst.price_provider,
            "retrieved_at": inst.retrieved_at,
            "validated": True,
            "reconciliation": "overlap_revision_ledger",
        },
    )
    if not persisted.get("persisted"):
        raise RuntimeError(persisted.get("warning") or "Validated live history could not be persisted.")
    register(inst)  # promote only after durable validation/persistence succeeds
    _capture_security_facts(inst, ticker_obj)
    after_dates = set(pd.to_datetime(inst.df.index).normalize())
    prior_dates = before_dates or set()
    rows_added = max(0, len(after_dates - prior_dates)) if prior_dates else len(after_dates)
    if refresh_journal:
        _refresh_signal_journal_forward_results()
    message = f"{message_prefix} {len(inst.df)} validated live rows via {inst.price_provider or 'configured provider'}."
    _record_refresh(inst.symbol, "ok", rows_added, message, source=inst.price_provider or "live")
    return {
        "symbol": inst.symbol,
        "status": "ok",
        "rows_added": rows_added,
        "rows": len(inst.df),
        "provider": inst.price_provider,
        "retrieved_at": inst.retrieved_at,
        "message": message,
    }


def _capture_security_facts(inst: Instrument, ticker_obj) -> None:
    """v10 spine, best-effort: identity row + corporate actions (splits and
    dividends the provider reports). Never blocks or slows a failed fetch."""
    try:
        from . import persistence

        store = persistence.get_store()
        store.record_security(inst.symbol, name=inst.name,
                              price_provider=getattr(inst, "price_provider", ""))
        if ticker_obj is None:
            return
        actions: list[dict] = []
        try:
            with _YF_SEMAPHORE:
                splits = _bounded_provider_call(lambda: ticker_obj.splits)
            for ts, ratio in (splits or {}).items() if splits is not None else []:
                if ratio:
                    actions.append({"action_type": "split", "ex_date": str(pd.Timestamp(ts).date()),
                                    "value": float(ratio), "source": "yfinance"})
        except Exception:
            pass
        try:
            with _YF_SEMAPHORE:
                dividends = _bounded_provider_call(lambda: ticker_obj.dividends)
            if dividends is not None and len(dividends):
                for ts, amount in dividends.tail(40).items():
                    if amount:
                        actions.append({"action_type": "dividend",
                                        "ex_date": str(pd.Timestamp(ts).date()),
                                        "value": float(amount), "source": "yfinance"})
        except Exception:
            pass
        if actions:
            store.record_corporate_actions(inst.symbol, actions)
    except Exception:
        return


def load_persisted_instruments() -> None:
    """Load persisted live/uploaded instruments into the process store."""
    try:
        from . import persistence

        store = persistence.get_store()
        for item in store.load_instruments():
            register(Instrument(
                item.symbol, item.name, item.df, item.source, [],
                price_provider=str((item.metadata or {}).get("price_provider") or ""),
                retrieved_at=str((item.metadata or {}).get("retrieved_at") or ""),
            ))
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
        return {"symbol": "", "status": "error", "rows_added": 0,
                **live_failure_metadata("Invalid ticker symbol.", preserved=False)}
    current = get(sym)
    if current is None or current.source != "live":
        message = "Refresh skipped: only existing live instruments can be refreshed."
        _record_refresh(sym, "skipped", 0, message)
        return {"symbol": sym, "status": "skipped", "rows_added": 0,
                **live_failure_metadata(message, preserved=current is not None)}
    before_dates = _instrument_dates(current)
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
        return _commit_live_instrument(
            inst,
            before_dates=before_dates,
            imported_via="live_refresh",
            period="2y",
            refresh_journal=refresh_journal,
            message_prefix="Refreshed",
        )
    except Exception as exc:
        message = str(exc) or "Live refresh failed."
        _record_refresh(sym, "error", 0, message)
        return {"symbol": sym, "status": "error", "rows_added": 0,
                **live_failure_metadata(message, preserved=current is not None)}


def live_failure_metadata(message: str, *, preserved: bool = False) -> dict[str, Any]:
    """Return stable, secret-free remediation metadata for provider failures."""
    from . import persistence

    safe_message = persistence.redact_secrets(str(message or "Live data operation failed."))
    text = safe_message.lower()
    if "invalid ticker" in text or "valid ticker" in text:
        reason_code, retryable = "invalid_symbol", False
        next_step = "Enter a valid exchange ticker symbol and try again."
    elif "only existing live" in text:
        reason_code, retryable = "not_live_managed", False
        next_step = "Fetch this symbol from the live provider first; uploaded histories are not overwritten."
    elif "no configured" in text or "provider is available" in text or "provider unavailable" in text:
        reason_code, retryable = "provider_unavailable", False
        next_step = "Configure an approved live price provider, license/entitlement controls, and credentials on the server."
    elif any(term in text for term in ("license", "entitle", "cutover", "approved provider")):
        reason_code, retryable = "provider_not_approved", False
        next_step = "Complete provider licensing, entitlement, reconciliation, and cutover approval in Data Quality."
    elif any(term in text for term in ("timeout", "timed out", "rate limit", "too many", "temporarily")):
        reason_code, retryable = "provider_temporarily_unavailable", True
        next_step = (
            "Retry after the provider recovers; Helios retained the last persisted good history."
            if preserved else "Retry after the provider recovers. No prior result is available for this symbol."
        )
    elif any(term in text for term in ("no live data", "no data", "empty", "not found", "delisted")):
        reason_code, retryable = "symbol_not_found", False
        next_step = "Verify the ticker, venue, and provider coverage before retrying."
    elif any(term in text for term in ("persist", "database", "sqlite", "encrypted snapshot")):
        reason_code, retryable = "persistence_failed", True
        next_step = (
            "Resolve local persistence or encryption custody, then retry; the prior stored history remains unchanged."
            if preserved else "Resolve local persistence or encryption custody, then retry the initial fetch."
        )
    else:
        reason_code, retryable = "provider_failed", True
        next_step = "Review provider diagnostics and retry. If failure persists, use the approved backup provider."
    return {
        "message": safe_message,
        "reason_code": reason_code,
        "retryable": retryable,
        "next_step": next_step,
        "stale_result_preserved": bool(preserved),
    }


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

    A failed fetch never creates or promotes an instrument. Only validated
    provider histories pass through the persistence commit pipeline.
    """
    requested = expand_live_symbols(list(symbols))
    worker_count = _live_refresh_worker_count(max_workers, len(requested))

    def fetch_one(symbol: str) -> dict:
        before = get(symbol)
        before_dates = set()
        if before is not None and before.source == "live":
            before_dates = _instrument_dates(before)
        try:
            if fetcher is None:
                if not live_provider_available():
                    raise RuntimeError("No configured live price provider is available.")
                inst = fetch_live(symbol, period=period, persist=False)
            else:
                inst = fetcher(symbol, period=period, persist=False)
                if not isinstance(inst, Instrument):
                    raise RuntimeError("Live provider returned an invalid payload.")
            inst.symbol = symbol
            return {"symbol": symbol, "status": "ok", "instrument": inst, "before_dates": before_dates}
        except Exception as exc:
            message = str(exc) or "Auto live update failed."
            return {"symbol": symbol, "status": "error", "rows_added": 0,
                    **live_failure_metadata(message)}

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
            try:
                results.append(_commit_live_instrument(
                    inst,
                    before_dates=item["before_dates"],
                    imported_via="auto_live",
                    period=period,
                    refresh_journal=False,
                    message_prefix="Auto live updated",
                ))
            except Exception as exc:
                message = str(exc) or "Auto live persistence failed."
                _record_refresh(symbol, "error", 0, message)
                results.append({"symbol": symbol, "status": "error", "rows_added": 0,
                                **live_failure_metadata(message)})
        else:
            message = item["message"]
            _record_refresh(symbol, "error", 0, message)
            results.append({"symbol": symbol, "status": "error", "rows_added": 0,
                            **live_failure_metadata(message)})
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


def _record_refresh(symbol: str, status: str, rows_added: int, message: str, source: str = "live") -> None:
    try:
        from . import persistence

        persistence.get_store().record_refresh(
            symbol=symbol,
            status=status,
            rows_added=rows_added,
            message=message,
            source=source,
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
# Holding-price resolution for portfolio models. Only validated, registered
# live/uploaded histories can enter this cache; reads never call providers.
# --------------------------------------------------------------------------- #
_PRICE_CACHE: dict[str, "PriceSeries"] = {}
_CACHE_LOCK = threading.RLock()
def _invalidate_resolution_caches(symbol: str) -> None:
    """Drop cached resolutions so analytics see a replaced instrument frame."""
    with _CACHE_LOCK:
        for key in [k for k, v in _PRICE_CACHE.items() if k == symbol or v.symbol == symbol]:
            _PRICE_CACHE.pop(key, None)


@dataclass
class PriceSeries:
    symbol: str
    close: pd.Series  # DatetimeIndex -> close
    source: str       # "upload" | "live"


MAX_PRICE_CACHE = 400


def _allowed_source(source: str) -> bool:
    return source in {"upload", "live"}


def resolve_series(
    ticker: str,
    allow_live: bool = False,
    allow_sample: bool = False,
    allow_simulated: bool = False,
) -> PriceSeries:
    """Return stored real price history for a holding ticker.

    Provider calls are intentionally excluded from this read path. Callers must
    use the explicit live-data POST workflow, which validates and persists the
    result before it can become research evidence. The legacy allow_* arguments
    remain temporarily source-compatible but cannot enable sample/simulated data.
    """
    sym = clean_symbol(ticker, fallback="")
    if not sym:
        raise ValueError(f"Invalid ticker: {ticker!r}")

    with _CACHE_LOCK:
        if sym in _PRICE_CACHE and _allowed_source(_PRICE_CACHE[sym].source):
            return _PRICE_CACHE[sym]

    # 1) a sidebar instrument already has good history
    inst = get(sym)
    if inst is not None and _allowed_source(inst.source):
        ps = PriceSeries(sym, inst.df["close"].dropna(), inst.source)
    else:
        raise ValueError(
            f"No eligible persisted real price history for {sym}. Use the live-data "
            "refresh workflow or upload a price history before research can run."
        )

    with _CACHE_LOCK:
        # Bound cache growth (FIFO eviction), mirroring the instrument-store cap.
        if sym not in _PRICE_CACHE and len(_PRICE_CACHE) >= MAX_PRICE_CACHE:
            _PRICE_CACHE.pop(next(iter(_PRICE_CACHE)), None)
        _PRICE_CACHE[sym] = ps
    return ps

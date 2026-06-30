"""Price-history data layer.

Holds an in-memory store of per-instrument OHLCV DataFrames. Data can come from:
  1. Bundled synthetic sample series (always available, fully offline).
  2. A CSV uploaded by the user (client investment-model export).
  3. A live pull via yfinance, if the package is installed and the network is up.

Every DataFrame is indexed by a DatetimeIndex and exposes at least a 'close'
column; 'open'/'high'/'low'/'volume' are included when known.
"""
from __future__ import annotations

import io
import hashlib
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# Cap user-added instruments so repeated uploads/fetches can't grow memory
# without bound. Keep it high enough for a serious multi-theme research
# universe plus model holdings; samples are never counted against this limit.
MAX_USER_INSTRUMENTS = 250
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


_STORE: dict[str, Instrument] = {}
# The store is shared across all dashboard users (a shared advisor view, by
# design). The lock keeps concurrent waitress threads from corrupting it.
_STORE_LOCK = threading.RLock()


def get(symbol: str) -> Instrument | None:
    return _STORE.get((symbol or "").upper())


def all_instruments() -> list[Instrument]:
    with _STORE_LOCK:
        return list(_STORE.values())


def register(inst: Instrument) -> None:
    with _STORE_LOCK:
        _STORE[inst.symbol.upper()] = inst
        # Evict oldest user-added instruments (samples are never evicted).
        user_keys = [k for k, v in _STORE.items() if v.source != "sample"]
        for stale in user_keys[:-MAX_USER_INSTRUMENTS] if len(user_keys) > MAX_USER_INSTRUMENTS else []:
            _STORE.pop(stale, None)


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
    out = out[~out["close"].isna()].sort_index()
    if len(out) < 30:
        raise ValueError("Need at least 30 valid rows of price history to analyze.")

    inst = Instrument(symbol, name, out, "upload", [])
    register(inst)
    _persist_instrument(
        inst,
        adjusted=None,
        metadata={"source_filename": source_filename or "", "imported_via": "price_csv"},
    )
    return inst


# --------------------------------------------------------------------------- #
# Live fetch (optional)
# --------------------------------------------------------------------------- #
def fetch_live(symbol: str, period: str = "2y", persist: bool = True) -> Instrument:
    """Pull live history (and free news headlines) via yfinance."""
    if not HAS_YF:
        raise RuntimeError("yfinance is not installed; live data unavailable.")
    symbol = clean_symbol(symbol, fallback="")
    if not symbol:
        raise ValueError("Invalid ticker symbol.")
    t = _yf.Ticker(symbol)
    # Bounded outbound call so a slow/hung upstream can't pin a worker thread.
    hist = t.history(period=period, auto_adjust=True, timeout=10)
    if hist is None or hist.empty:
        raise RuntimeError(f"No live data returned for '{symbol}'.")
    hist = hist.rename(columns=str.lower)
    df = hist[["open", "high", "low", "close", "volume"]].copy()
    df.index = pd.to_datetime(df.index).tz_localize(None)

    headlines = []
    try:
        for item in (t.news or [])[:12]:
            title = (item.get("content", {}) or {}).get("title") or item.get("title")
            if title:
                headlines.append(title)
    except Exception:
        pass

    name = symbol.upper()
    try:
        name = t.info.get("shortName") or name
    except Exception:
        pass

    inst = Instrument(symbol.upper(), name, df, "live", headlines)
    register(inst)
    if persist:
        _persist_instrument(inst, adjusted=True, metadata={"period": period, "imported_via": "live_fetch"})
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


def refresh_live_symbol(symbol: str, fetcher=None) -> dict:
    """Refresh an existing live instrument and record a local refresh log."""
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
            if not HAS_YF:
                raise RuntimeError("yfinance is not installed; live refresh unavailable.")
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
        _persist_instrument(inst, adjusted=True, metadata={"imported_via": "live_refresh"})
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


def ensure_live_symbols(symbols: list[str] | tuple[str, ...], period: str = "2y", fetcher=None) -> dict:
    """Fetch or refresh a configured live universe and persist real provider data.

    Existing sample instruments are intentionally replaced only after the live
    provider returns a valid series. If a fetch fails, the prior sample/demo row
    remains in place and is not promoted to real research evidence.
    """
    requested = expand_live_symbols(list(symbols))
    results = []
    for symbol in requested:
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
                register(inst)
            after_dates = set(pd.to_datetime(inst.df.index).normalize())
            rows_added = max(0, len(after_dates - before_dates)) if before_dates else len(after_dates)
            _persist_instrument(inst, adjusted=True, metadata={"period": period, "imported_via": "auto_live"})
            _record_refresh(symbol, "ok", rows_added, f"Auto live updated {len(inst.df)} rows.")
            results.append({"symbol": symbol, "status": "ok", "rows_added": rows_added, "rows": len(inst.df), "message": f"Auto live updated {len(inst.df)} rows."})
        except Exception as exc:
            message = str(exc) or "Auto live update failed."
            _record_refresh(symbol, "error", 0, message)
            results.append({"symbol": symbol, "status": "error", "rows_added": 0, "message": message})
    return {
        "requested": requested,
        "refreshed": sum(1 for item in results if item["status"] == "ok"),
        "failed": sum(1 for item in results if item["status"] == "error"),
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
        # 2) live prices (history only — skip the news/info round-trips)
        if HAS_YF and allow_live:
            try:
                hist = _yf.Ticker(sym).history(period="5y", auto_adjust=True, timeout=8)
                if hist is not None and not hist.empty:
                    close = hist["Close"].copy()
                    close.index = pd.to_datetime(close.index).tz_localize(None)
                    ps = PriceSeries(sym, close.dropna(), "live")
            except Exception:
                ps = None
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

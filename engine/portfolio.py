"""Portfolio "models": a basket of (ticker, weight) holdings plus a mandate.

This module owns parsing an uploaded Excel/CSV into a Model and the in-memory
Model store. The analytics that turn a Model into a portfolio return series,
signals, forecasts and insights live in their own modules and operate on what
`build_series` produces here.
"""
from __future__ import annotations

import io
import re
import threading
import time
import zipfile
from dataclasses import dataclass, field

import numpy as np
import openpyxl
import pandas as pd

from . import data

MAX_MODELS = 50
MAX_HOLDINGS = 60
# Decompression-bomb guards for uploaded spreadsheets: reject before handing the
# bytes to the XML parser if the archive expands beyond these limits.
_MAX_UNCOMPRESSED = 60 * 1024 * 1024   # 60 MB total uncompressed
_MAX_COMPRESSION_RATIO = 200           # uncompressed / compressed
# Per-analyze live-resolution budget so a model of many unknown tickers can't tie
# up a worker thread with sequential yfinance fetches.
_LIVE_FETCH_BUDGET = 40
_RESOLVE_TIME_BUDGET_S = 20.0


# --------------------------------------------------------------------------- #
# Model data structures + store
# --------------------------------------------------------------------------- #
@dataclass
class Holding:
    ticker: str
    weight: float          # fraction in [0, 1]
    source: str = ""       # filled at analysis time: sample | live | simulated


@dataclass
class Model:
    id: str
    name: str
    mandate_key: str               # e.g. "growth" (see engine.mandate)
    mandate_context: str           # free-text purpose/notes
    holdings: list                 # list[Holding]


_MODELS: dict[str, Model] = {}
_MODELS_LOCK = threading.RLock()


def get(model_id: str) -> Model | None:
    return _MODELS.get((model_id or "").upper())


def all_models() -> list[Model]:
    with _MODELS_LOCK:
        return list(_MODELS.values())


def register(model: Model) -> None:
    with _MODELS_LOCK:
        _MODELS[model.id.upper()] = model
        ids = list(_MODELS.keys())
        for stale in ids[:-MAX_MODELS] if len(ids) > MAX_MODELS else []:
            _MODELS.pop(stale, None)


# --------------------------------------------------------------------------- #
# Parsing an uploaded model file
# --------------------------------------------------------------------------- #
_TICKER_ALIASES = ("ticker", "symbol", "security", "holding", "asset", "fund", "stock", "name")
_WEIGHT_ALIASES = ("weight", "weighting", "allocation", "alloc", "percent", "percentage",
                   "%", "target", "target weight", "pct", "wt")
_ID_RE = re.compile(r"[^A-Z0-9]+")


def _guard_zip_bomb(raw: bytes) -> None:
    """Reject decompression bombs using the zip central directory (no decompression)."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            total = sum(i.file_size for i in z.infolist())
    except zipfile.BadZipFile:
        raise ValueError("File is not a readable Excel/zip workbook.")
    if total > _MAX_UNCOMPRESSED or (raw and total / max(len(raw), 1) > _MAX_COMPRESSION_RATIO):
        raise ValueError("Spreadsheet is unexpectedly large when decompressed; refusing to process.")


def _read_xlsx(raw: bytes) -> pd.DataFrame:
    """Stream an .xlsx in read-only mode with an early row break.

    read_only=True streams the worksheet lazily, and breaking after the row cap
    means we never decompress the whole sheet — the two fixes that bound a
    decompression bomb. data_only=True reads cached cell VALUES so formula cells
    (e.g. a weight computed by a formula) don't come back as the formula string.
    """
    _guard_zip_bomb(raw)
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    try:
        ws = wb.active
        header, rows = None, []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                header = [str(c).strip() if c is not None else f"col{j}" for j, c in enumerate(row)]
                continue
            rows.append(row[: len(header)] if header else row)
            if len(rows) >= MAX_HOLDINGS + 5:
                break
    finally:
        wb.close()
    if not header:
        raise ValueError("The spreadsheet has no header row.")
    return pd.DataFrame(rows, columns=header)


def _read_table(raw: bytes, filename: str) -> pd.DataFrame:
    name = (filename or "").lower()
    is_zip = raw[:4] == b"PK\x03\x04"
    if name.endswith((".xlsx", ".xlsm")) or (is_zip and not name.endswith((".csv", ".txt", ".tsv"))):
        return _read_xlsx(raw)
    sep = "\t" if name.endswith(".tsv") else None
    return pd.read_csv(io.BytesIO(raw), sep=sep, engine="python", nrows=MAX_HOLDINGS + 5)


def _pick(cols: dict, aliases) -> str | None:
    for a in aliases:
        if a in cols:
            return cols[a]
    # loose contains-match (e.g. "Target Weight (%)")
    for a in aliases:
        for low, orig in cols.items():
            if a in low:
                return orig
    return None


def model_id_from_name(name: str) -> str:
    base = _ID_RE.sub("-", (name or "MODEL").upper()).strip("-")
    return (base or "MODEL")[:24]


def parse_model_file(raw: bytes, filename: str, name: str,
                     mandate_key: str = "balanced", mandate_context: str = "") -> Model:
    """Parse an Excel/CSV of holdings into a Model.

    Requires a ticker-like column. A weight column is used when present;
    otherwise holdings are equal-weighted. Weights given as percentages
    (summing ~100) or fractions (summing ~1) are both accepted and normalized.
    """
    df = _read_table(raw, filename)
    if df is None or df.empty:
        raise ValueError("The file appears to be empty.")
    cols = {str(c).lower().strip(): c for c in df.columns}

    tcol = _pick(cols, _TICKER_ALIASES)
    if tcol is None:
        found = ", ".join(re.sub(r"[\x00-\x1f]", "", str(c))[:24] for c in list(df.columns)[:12])
        raise ValueError(
            "Could not find a ticker column. Include a column named "
            "Ticker/Symbol (and optionally Weight). Found: " + found)
    wcol = _pick(cols, _WEIGHT_ALIASES)

    raw_pairs = []
    for _, row in df.iterrows():
        tk = data.clean_symbol(str(row[tcol]), fallback="")
        if not tk:
            continue
        w = np.nan
        if wcol is not None:
            w = pd.to_numeric(re.sub(r"[%,\s]", "", str(row[wcol])), errors="coerce")
        raw_pairs.append((tk, float(w) if pd.notna(w) else np.nan))

    if not raw_pairs:
        raise ValueError("No valid tickers found in the file.")
    if len(raw_pairs) > MAX_HOLDINGS:
        raw_pairs = raw_pairs[:MAX_HOLDINGS]

    # Merge duplicate tickers (sum their weights).
    merged: dict[str, float] = {}
    for tk, w in raw_pairs:
        merged[tk] = merged.get(tk, 0.0) + (0.0 if np.isnan(w) else w)
    any_weights = any(not np.isnan(w) for _, w in raw_pairs)

    tickers = list(merged.keys())
    if not any_weights:
        weights = {tk: 1.0 / len(tickers) for tk in tickers}     # equal weight
    else:
        total = sum(max(0.0, v) for v in merged.values())
        if total <= 0:
            weights = {tk: 1.0 / len(tickers) for tk in tickers}
        else:
            weights = {tk: max(0.0, v) / total for tk, v in merged.items()}  # normalize to 1

    holdings = [Holding(tk, round(weights[tk], 6)) for tk in tickers]
    holdings.sort(key=lambda h: h.weight, reverse=True)

    model = Model(
        id=model_id_from_name(name),
        name=data.clean_name(name, fallback="Client Model"),
        mandate_key=mandate_key,
        mandate_context=re.sub(r"[\x00-\x1f\x7f]", "", (mandate_context or "").strip())[:400],
        holdings=holdings,
    )
    register(model)
    return model


# --------------------------------------------------------------------------- #
# Portfolio return series construction
# --------------------------------------------------------------------------- #
@dataclass
class PortfolioSeries:
    close: pd.Series        # portfolio NAV (base 100) over the common window
    holdings: list          # list[dict]: ticker, weight, source, window_return_pct, mrc
    n_days: int
    sources: dict           # count of holdings by data source
    warnings: list
    hhi: float = 0.0        # Herfindahl concentration index
    n_eff: float = 0.0      # effective number of holdings = 1/HHI
    corr_mean: float = 0.0  # mean off-diagonal pairwise correlation
    binding_ticker: str = ""  # holding with the shortest history (truncates the window)
    provenance: dict = field(default_factory=dict)  # data-source honesty block


def build_series(model: Model, base: float = 100.0, min_days: int = 200) -> PortfolioSeries:
    """Construct a fixed-weight (daily-rebalanced) portfolio NAV from holdings.

    Each holding's close is resolved (live/sample/simulated), aligned on the
    common date window, and combined as a weight-weighted sum of daily returns.
    The NAV behaves like any single-instrument close series, so the existing
    indicator / forecast / signal / backtest engine runs on it unchanged. Risk
    decomposition (HHI, effective N, marginal risk contributions, correlation)
    and data provenance are computed from the same return matrix.
    """
    closes, warnings = {}, []
    deadline = time.monotonic() + _RESOLVE_TIME_BUDGET_S
    live_used = 0
    for h in model.holdings:
        # Budget new live fetches so a model of many unknown tickers can't pin a
        # worker; cached/sample holdings are unaffected, the rest fall to simulated.
        allow_live = live_used < _LIVE_FETCH_BUDGET and time.monotonic() < deadline
        ps = data.resolve_series(h.ticker, allow_live=allow_live)
        if ps.source == "live":
            live_used += 1
        h.source = ps.source
        closes[h.ticker] = ps.close

    order = [h.ticker for h in model.holdings]
    weights0 = {h.ticker: h.weight for h in model.holdings}
    src_by = {h.ticker: h.source for h in model.holdings}

    # Resilient alignment: drop holdings with too little / non-overlapping history
    # (redistributing their weight) instead of letting one bad ticker wipe the join.
    common, keep, dropped, kw, daily = _align_returns(closes, order, weights0, min_overlap=60)
    dropped_map = dict(dropped)

    R = pd.DataFrame({tk: daily[tk].pct_change() for tk in keep}).reindex(common).dropna(how="any")
    w = np.array([kw[tk] for tk in keep], dtype=float)
    port_ret = (R[keep] * w).sum(axis=1)
    nav = base * (1 + port_ret).cumprod()

    if len(R) < min_days:
        warnings.append(f"Only {len(R)} overlapping sessions; metrics use this shorter window.")
    if dropped:
        dw = sum(weights0[tk] for tk, _ in dropped)
        warnings.append(
            f"Excluded {len(dropped)} holding(s) ({dw * 100:.0f}% of weight) with unusable price "
            "history — " + "; ".join(f"{tk} ({why})" for tk, why in dropped)
            + ". Remaining weights were rescaled to 100%.")

    binding = max(keep, key=lambda tk: daily[tk].index.min()) if keep else ""

    # Risk decomposition on the kept holdings (annualized covariance).
    sigma = R[keep].cov().to_numpy() * 252.0
    port_var = float(w @ sigma @ w)
    mrc = w * (sigma @ w) / port_var if port_var > 1e-12 else np.full(len(w), 1.0 / len(w))
    mrc_map = dict(zip(keep, mrc))
    corr = R[keep].corr().to_numpy()
    nk = len(keep)
    corr_mean = float((corr.sum() - nk) / (nk * (nk - 1))) if nk > 1 else 1.0
    hhi = float(np.sum(w**2))
    n_eff = float(1.0 / hhi) if hhi > 0 else float(nk)

    win_ret = closes_window_return({tk: daily[tk] for tk in keep}, R.index)
    holdings_out = []
    for h in model.holdings:
        excluded = h.ticker in dropped_map
        holdings_out.append({
            "ticker": h.ticker,
            "weight": h.weight,                       # original uploaded weight
            "source": "excluded" if excluded else h.source,
            "window_return_pct": win_ret.get(h.ticker, 0.0) * 100,
            "mrc_pct": None if excluded else float(mrc_map.get(h.ticker, 0.0)) * 100,
            "excluded": excluded,
            "note": dropped_map.get(h.ticker, ""),
        })

    # Provenance over the KEPT (analyzed) holdings.
    sources: dict = {}
    sim_weight = 0.0
    sim_syms = []
    for tk in keep:
        s = src_by[tk]
        sources[s] = sources.get(s, 0) + 1
        if s == "simulated":
            sim_weight += kw[tk]
            sim_syms.append(tk)
    provenance = {
        "n_holdings": len(model.holdings),
        "n_kept": len(keep),
        "n_excluded": len(dropped),
        "excluded": [{"ticker": tk, "reason": why} for tk, why in dropped],
        "n_live": sources.get("live", 0),
        "n_sample": sources.get("sample", 0),
        "n_simulated": sources.get("simulated", 0),
        "simulated_weight_pct": round(sim_weight * 100, 1),
        "simulated_symbols": sim_syms,
        "honesty": "real" if sim_weight == 0 else "simulated" if sim_weight >= 0.999 else "mixed",
    }

    return PortfolioSeries(close=nav, holdings=holdings_out, n_days=len(nav),
                           sources=sources, warnings=warnings, hhi=hhi, n_eff=n_eff,
                           corr_mean=corr_mean, binding_ticker=binding, provenance=provenance)


def _to_daily(s: pd.Series) -> pd.Series:
    """Normalize a price series to one row per calendar date (dedup, sorted)."""
    s = s.dropna()
    s.index = pd.to_datetime(s.index).normalize()
    return s[~s.index.duplicated(keep="last")].sort_index()


def _align_returns(closes: dict, order: list, weights: dict, min_overlap: int = 60):
    """Find a shared return window across holdings.

    Holdings with fewer than `min_overlap` days, or whose history doesn't overlap
    the rest, are dropped and their weight redistributed — so one bad ticker can't
    collapse the whole portfolio. Raises ValueError naming the culprits only when a
    usable window genuinely can't be formed (or >30% of weight would be dropped).

    Returns (common_index, kept, dropped[(ticker, reason)], kept_weights, daily_closes).
    """
    daily = {tk: _to_daily(closes[tk]) for tk in order}
    rets = {tk: daily[tk].pct_change().dropna() for tk in order}

    dropped: list = []
    keep = list(order)
    for tk in list(keep):                       # individually too short to annualize
        if len(rets[tk]) < min_overlap:
            dropped.append((tk, f"only {len(rets[tk])} days of price history"))
            keep.remove(tk)

    def common(keys):
        idx = None
        for tk in keys:
            idx = rets[tk].index if idx is None else idx.intersection(rets[tk].index)
        return idx if idx is not None else pd.DatetimeIndex([])

    cur = common(keep)
    while len(keep) > 1 and len(cur) < min_overlap:   # drop the worst window-breaker
        best_tk, best_len = None, len(cur)
        for tk in keep:
            c = common([k for k in keep if k != tk])
            if len(c) > best_len:
                best_len, best_tk = len(c), tk
        if best_tk is None:
            break
        dropped.append((best_tk, "price history doesn't overlap the other holdings"))
        keep.remove(best_tk)
        cur = common(keep)

    dropped_weight = sum(weights.get(tk, 0.0) for tk, _ in dropped)
    if len(keep) < 1 or len(cur) < min_overlap or dropped_weight > 0.30:
        names = "; ".join(f"{tk} — {why}" for tk, why in dropped) or "the holdings"
        raise ValueError(
            "Couldn't assemble a shared price history across the holdings. "
            f"Problem holding(s): {names}. Remove them or supply longer history, then retry."
        )
    w = np.array([weights[tk] for tk in keep], dtype=float)
    w = w / w.sum() if w.sum() > 0 else np.full(len(w), 1.0 / len(w))
    return cur, keep, dropped, dict(zip(keep, w)), daily


def closes_window_return(closes: dict, index) -> dict:
    out = {}
    for tk, s in closes.items():
        s2 = s.reindex(index).dropna()
        out[tk] = float(s2.iloc[-1] / s2.iloc[0] - 1) if len(s2) > 1 else 0.0
    return out

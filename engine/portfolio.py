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
_TICKER_ALIASES = ("ticker", "symbol", "security", "holding", "asset", "fund", "stock")
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
    sep = "\t" if name.endswith(".tsv") else ","
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
    _persist_model(model, source_filename=filename)
    return model


def load_persisted_models() -> None:
    """Load persisted client models into the in-memory model store."""
    try:
        from . import persistence

        for item in persistence.get_store().load_models():
            holdings = [
                Holding(h["ticker"], float(h["weight"]), str(h.get("source_state") or "pending"))
                for h in item.holdings
            ]
            register(Model(
                id=item.id,
                name=item.name,
                mandate_key=item.mandate_key,
                mandate_context=item.mandate_context,
                holdings=holdings,
            ))
    except Exception:
        return


def _persist_model(model: Model, source_filename: str = "") -> dict:
    try:
        from . import persistence

        return persistence.get_store().persist_model(
            model_id=model.id,
            name=model.name,
            mandate_key=model.mandate_key,
            mandate_context=model.mandate_context,
            source_filename=source_filename,
            holdings=[
                {"ticker": h.ticker, "weight": h.weight, "source_state": h.source or "pending"}
                for h in model.holdings
            ],
            metadata={"imported_via": "model_upload"},
        )
    except Exception as exc:
        return {"persisted": False, "warning": str(exc)}


# --------------------------------------------------------------------------- #
# Portfolio return series construction
# --------------------------------------------------------------------------- #
@dataclass
class PortfolioSeries:
    close: pd.Series        # portfolio NAV (base 100) over the union/rescaled analysis window
    holdings: list          # list[dict]: ticker, weight, source, window_return_pct, mrc
    n_days: int
    sources: dict           # count of holdings by data source
    warnings: list
    hhi: float = 0.0        # Herfindahl concentration index
    n_eff: float = 0.0      # effective number of holdings = 1/HHI
    corr_mean: float = 0.0  # mean off-diagonal pairwise correlation
    binding_ticker: str = ""  # shortest-history holding, used for guidance only
    provenance: dict = field(default_factory=dict)  # data-source honesty block


def build_series(
    model: Model,
    base: float = 100.0,
    min_days: int = 200,
    allow_sample: bool = True,
    allow_simulated: bool = True,
) -> PortfolioSeries:
    """Construct a weight-rescaled portfolio NAV from holdings.

    Each holding's close is resolved (live/sample/simulated), then daily returns
    are outer-joined on the union of available dates. On any given day the target
    weights are rescaled across holdings with data for that day. This is a
    forward-analysis basis for mixed-history models, not a performance track
    record. The NAV behaves like any single-instrument close series, so the
    existing indicator / forecast / signal / backtest engine runs on it
    unchanged. Risk decomposition and data provenance are computed from the same
    return matrix.
    """
    closes, warnings = {}, []
    src_by = {}
    resolution_errors = {}
    deadline = time.monotonic() + _RESOLVE_TIME_BUDGET_S
    live_used = 0
    for h in model.holdings:
        # Budget new live fetches so a model of many unknown tickers can't pin a
        # worker; cached/sample holdings are unaffected, the rest fall to simulated.
        allow_live = live_used < _LIVE_FETCH_BUDGET and time.monotonic() < deadline
        try:
            try:
                ps = data.resolve_series(
                    h.ticker,
                    allow_live=allow_live,
                    allow_sample=allow_sample,
                    allow_simulated=allow_simulated,
                )
            except TypeError as e:
                if "allow_sample" not in str(e) and "allow_simulated" not in str(e):
                    raise
                # Backward-compatible with narrow test doubles that still expose
                # the pre-Pro signature.
                ps = data.resolve_series(h.ticker, allow_live=allow_live)
            if ps.source == "live":
                live_used += 1
            src_by[h.ticker] = ps.source
            closes[h.ticker] = ps.close
        except ValueError as e:
            src_by[h.ticker] = "missing"
            resolution_errors[h.ticker] = str(e)
            closes[h.ticker] = pd.Series(dtype=float)

    order = [h.ticker for h in model.holdings]
    weights0 = {h.ticker: h.weight for h in model.holdings}
    daily = {tk: _to_daily(closes[tk]) for tk in order}

    # Per-holding daily returns aligned on the UNION of dates (outer join). No
    # shared/overlapping window is required: each day's portfolio return is the
    # weight-rescaled blend of whatever holdings have data that day, so holdings
    # with different inception dates or history lengths combine fine and one short
    # ticker can't collapse the analysis. This is a forward-analysis basis, not a
    # performance track record.
    R = pd.DataFrame({tk: daily[tk].pct_change() for tk in order})
    usable = [tk for tk in order if int(R[tk].notna().sum()) >= 2]
    excluded = [
        (tk, resolution_errors.get(tk, "no price data found"))
        for tk in order if tk not in usable
    ]
    dropped_map = dict(excluded)
    if not usable:
        names = "; ".join(f"{tk} ({why})" for tk, why in excluded) or "the holdings"
        raise ValueError(f"No price history could be found for any holding ({names}).")

    wser = pd.Series({tk: weights0[tk] for tk in usable}, dtype=float)
    wser = wser / wser.sum() if wser.sum() > 0 else pd.Series(1.0 / len(usable), index=usable)
    Rk = R[usable]
    present_w = Rk.notna().mul(wser, axis=1).sum(axis=1)
    port_ret = (Rk.fillna(0.0).mul(wser, axis=1).sum(axis=1) / present_w.replace(0.0, np.nan)).dropna()
    nav = base * (1.0 + port_ret).cumprod()

    if len(nav) < min_days:
        warnings.append(f"Working from {len(nav)} sessions of available history (shorter window).")
    if bool(Rk.notna().all(axis=1).sum() < len(nav)) and len(usable) > 1:
        late = max(usable, key=lambda tk: daily[tk].index.min())
        warnings.append(
            f"Holdings have different history lengths (shortest: {late}); the mix is weight-rescaled "
            "over the days each holding has data.")
    if excluded:
        warnings.append("No price data found for: " + ", ".join(tk for tk, _ in excluded)
                        + " — excluded from the analysis.")

    binding = max(usable, key=lambda tk: daily[tk].index.min()) if usable else ""

    # Risk decomposition from PAIRWISE covariance (tolerates mismatched histories;
    # pairs with no overlap get a default correlation rather than breaking).
    cov = (Rk.cov() * 252.0).to_numpy()
    d = np.sqrt(np.clip(np.diag(cov), 1e-12, None))
    for i in range(len(d)):
        for j in range(len(d)):
            if not np.isfinite(cov[i, j]):
                cov[i, j] = (1.0 if i == j else 0.4) * d[i] * d[j]
    wv = wser.to_numpy()
    port_var = float(wv @ cov @ wv)
    mrc = wv * (cov @ wv) / port_var if port_var > 1e-12 else np.full(len(wv), 1.0 / len(wv))
    mrc_map = dict(zip(usable, mrc))
    cc = Rk.corr().to_numpy()
    off = cc[~np.eye(len(cc), dtype=bool)] if len(cc) > 1 else np.array([1.0])
    corr_mean = float(np.nanmean(off)) if off.size else 1.0
    hhi = float(np.sum(wv**2))
    n_eff = float(1.0 / hhi) if hhi > 0 else float(len(usable))

    holdings_out = []
    for h in model.holdings:
        ex = h.ticker in dropped_map
        s = daily[h.ticker].tail(252)  # trailing return per holding (comparable across mixed histories)
        wret = float(s.iloc[-1] / s.iloc[0] - 1) * 100 if len(s) > 1 else 0.0
        holdings_out.append({
            "ticker": h.ticker,
            "weight": h.weight,
            "source": "excluded" if ex else src_by.get(h.ticker, "unknown"),
            "window_return_pct": 0.0 if ex else wret,
            "mrc_pct": None if ex else float(mrc_map.get(h.ticker, 0.0)) * 100,
            "excluded": ex,
            "note": dropped_map.get(h.ticker, ""),
        })

    # Provenance over the analyzed (usable) holdings.
    sources: dict = {}
    sim_weight = 0.0
    sim_syms = []
    source_weight: dict[str, float] = {}
    for h in model.holdings:
        s = src_by.get(h.ticker, "missing")
        source_weight[s] = source_weight.get(s, 0.0) + float(h.weight)

    for tk in usable:
        s = src_by[tk]
        sources[s] = sources.get(s, 0) + 1
        if s == "simulated":
            sim_weight += float(wser[tk])
            sim_syms.append(tk)
    provenance = {
        "n_holdings": len(model.holdings),
        "n_kept": len(usable),
        "n_excluded": len(excluded),
        "excluded": [{"ticker": tk, "reason": why} for tk, why in excluded],
        "n_live": sources.get("live", 0),
        "n_sample": sources.get("sample", 0),
        "n_simulated": sources.get("simulated", 0),
        "n_missing": sum(1 for s in src_by.values() if s == "missing"),
        "simulated_weight_pct": round(sim_weight * 100, 1),
        "simulated_symbols": sim_syms,
        "missing_symbols": [tk for tk, src in src_by.items() if src == "missing"],
        "source_weight_pct": {k: round(v * 100, 1) for k, v in source_weight.items()},
        "sample_weight_pct": round(source_weight.get("sample", 0.0) * 100, 1),
        "real_weight_pct": round((source_weight.get("live", 0.0) + source_weight.get("upload", 0.0)) * 100, 1),
        "non_real_weight_pct": round((
            source_weight.get("sample", 0.0)
            + source_weight.get("simulated", 0.0)
            + source_weight.get("missing", 0.0)
        ) * 100, 1),
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

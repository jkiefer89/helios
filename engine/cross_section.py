"""Cross-sectional relative-return ranking — the earned fifth signal leg.

Ranks the eligible universe by predicted RELATIVE 21-day return (excess over
the universe median) from cross-sectionally standardized price features. The
statistical case: absolute single-name direction is near coin-flip (measured
live: DA ~50-52%, Brier ~0.25), but *ordering* names within a universe cancels
market-wide noise and is where medium-term momentum/reversal structure lives.

Honesty contract:
- The component's composite weight is EARNED from walk-forward evidence
  (rank-IC t-stat across independent, non-overlapping windows) and is zero
  until the evidence clears the disclosed floor — mirroring the forecast
  edge gate. An unproven ranking can never move an action.
- The universe excludes mechanically-confounded instruments (leveraged
  wrappers, broad-index funds, crypto calendars, sub-equity-vol sleeves):
  ranking SOXL against SOXX or SPY against its own constituents measures
  beta, not selection skill (probe: index funds inflated IC +0.12 -> +0.18).
- Features are price-derived and point-in-time by construction (a value at
  date d uses only bars <= d). PIT fundamentals join as snapshot history
  accrues; they are NOT used for historical training rows (look-ahead).
- Expensive panel work happens in the auto-live warm loop; GET paths read
  cached-only and degrade honestly when cold.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np
import pandas as pd

from . import assumptions as _assumptions
from . import costs, data, provenance

HORIZON_D = 21               # forward relative-return horizon
STEP_D = 21                  # eval stride — non-overlapping windows, IID-honest
MIN_VOL_ANNUAL = 0.15        # equity-like risk floor (excludes cash/bond sleeves)
MIN_HISTORY_ROWS = 300
MIN_UNIVERSE = 12            # below this, ranking is not meaningful
FEATURE_WARMUP_ROWS = 210    # longest lookback (sma200) + margin
MIN_TRAIN_DATES = 6          # expanding walk-forward needs this many prior dates
RIDGE_LAMBDA = 1.0

# Earned-weight gate (disclosed): zero weight at IC t-stat <= 1.5, full at
# >= 2.5, linear between — and never any weight below the window floor.
# The floor is deliberately ABOVE conventional significance because the
# evidence panel is built from TODAY'S store membership (names that died or
# were dropped never enter historical windows — survivorship) and the
# vol/coverage screens see full-sample data; the measured IC is therefore an
# upper bound, and the gate prices that in.
GATE_MIN_DATES = 12
GATE_T_FLOOR = 1.5
GATE_T_FULL = 2.5

_PANEL_TTL_S = 24 * 3600.0   # daily panel rebuild in the warm loop
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"built_at": 0.0, "snapshot": None}


def _eligible_close_series() -> dict[str, pd.Series]:
    """Universe filter — every exclusion disclosed in the snapshot basis."""
    out: dict[str, pd.Series] = {}
    for inst in data.all_instruments():
        if not provenance.is_real_source(inst.source):
            continue
        if str(inst.symbol).upper().endswith("-USD"):
            continue                      # crypto calendar poisons the date grid
        # Structure detection needs the DISPLAY name; the store often holds the
        # bare symbol (SOXL slipped the leveraged filter that way — verified
        # live). The fundamentals cache carries the provider name; cached-only.
        name = inst.name or ""
        if not name or name == inst.symbol:
            try:
                from . import fundamentals as _fnd
                cached = _fnd.fetch_cached(inst.symbol)
                if cached is not None and cached.name:
                    name = cached.name
            except Exception:
                pass
        if _assumptions.is_leveraged_product_name(name):
            continue                      # levered echo of an underlying
        if _assumptions.is_broad_index_name(name):
            continue                      # beta proxy, not a selection bet
        if "close" not in inst.df:
            continue
        close = inst.df["close"].dropna()
        if len(close) < MIN_HISTORY_ROWS:
            continue
        vol = float(close.pct_change().std() * np.sqrt(252))
        if not np.isfinite(vol) or vol < MIN_VOL_ANNUAL:
            continue
        out[inst.symbol] = close
    return out


def _extend_history(symbol: str, close: pd.Series) -> tuple[pd.Series, str]:
    """Best-effort 5y backfill for the panel only (more independent eval
    windows -> a real verdict on the edge). Uses the CONFIGURED price-provider
    order — never a silent side-channel around the cutover/opt-in convention —
    and returns (series, source_label) so the ranking's data lineage is
    disclosed like every other history. The store's series stays untouched."""
    try:
        for provider in data._price_provider_order():
            frame = data._provider_price_history(provider, symbol, "5y")
            if frame is not None and "close" in frame and len(frame) > len(close):
                return frame["close"].dropna(), f"{provider}_5y_backfill"
    except Exception:
        pass
    return close, "store_history"


def _build_panel(extend: bool = True) -> pd.DataFrame | None:
    closes = _eligible_close_series()
    if len(closes) < MIN_UNIVERSE:
        return None
    history_sources: dict[str, int] = {}
    if extend:
        extended = {}
        for sym, c in closes.items():
            series, source = _extend_history(sym, c)
            extended[sym] = series
            history_sources[source] = history_sources.get(source, 0) + 1
        closes = extended
    px = pd.DataFrame(closes)
    # Majority calendar: drop dates most of the universe did not trade.
    px = px[px.notna().mean(axis=1) >= 0.6]
    px = px.dropna(axis=1, thresh=int(len(px) * 0.9)).ffill(limit=3)
    if px.shape[1] < MIN_UNIVERSE or len(px) < FEATURE_WARMUP_ROWS + HORIZON_D * 2:
        return None
    px.attrs["history_sources"] = history_sources
    return px


def _zx(df: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional z-score per date."""
    return df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1).replace(0, np.nan), axis=0)


def _features(px: pd.DataFrame) -> dict[str, pd.DataFrame]:
    rets = px.pct_change()
    return {
        "mom21": _zx(px.pct_change(21)),
        "mom63": _zx(px.pct_change(63)),
        "mom126": _zx(px.pct_change(126)),
        "rev5": _zx(-px.pct_change(5)),
        "vol63": _zx(rets.rolling(63).std()),
        "sma200": _zx(px / px.rolling(200).mean() - 1),
    }


def _eval_rows(px: pd.DataFrame) -> list[tuple[pd.Timestamp, pd.DataFrame, pd.Series]]:
    feats = _features(px)
    fwd = px.shift(-HORIZON_D) / px - 1
    fwd_ex = fwd.sub(fwd.median(axis=1), axis=0)
    rows = []
    cutoff = px.index[-1] - pd.Timedelta(days=int(HORIZON_D * 1.6))
    for d in px.index[FEATURE_WARMUP_ROWS::STEP_D]:
        if d > cutoff:
            break
        y = fwd_ex.loc[d]
        X = pd.DataFrame({k: v.loc[d] for k, v in feats.items()})
        ok = X.dropna().index.intersection(y.dropna().index)
        if len(ok) >= MIN_UNIVERSE:
            rows.append((d, X.loc[ok], y.loc[ok]))
    return rows


def _fit(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.linalg.solve(X.T @ X + RIDGE_LAMBDA * np.eye(X.shape[1]), X.T @ y)


def _walk_forward_metrics(rows) -> dict[str, Any]:
    ics, das, spreads = [], [], []
    for i in range(MIN_TRAIN_DATES, len(rows)):
        Xtr = pd.concat([r[1] for r in rows[:i]])
        ytr = pd.concat([r[2] for r in rows[:i]])
        _, Xte, yte = rows[i]
        w = _fit(Xtr.values, ytr.values)
        pred = pd.Series(Xte.values @ w, index=Xte.index)
        ic = pred.rank().corr(yte.rank())
        if not np.isfinite(ic):
            continue
        ics.append(float(ic))
        das.append(float(((pred > pred.median()) == (yte > 0)).mean()))
        q = pred.rank(pct=True)
        spreads.append(float(yte[q >= 0.8].mean() - yte[q <= 0.2].mean()))
    n = len(ics)
    if n < 3:
        return {"n_dates": n, "status": "insufficient_windows"}
    arr = np.asarray(ics)
    t = float(arr.mean() / (arr.std(ddof=1) / np.sqrt(n))) if arr.std(ddof=1) > 1e-12 else 0.0
    gross = float(np.mean(spreads) * 100)
    return {
        "status": "ok",
        "n_dates": n,
        "mean_rank_ic": round(float(arr.mean()), 4),
        "ic_t_stat": round(t, 2),
        "ic_positive_share": round(float((arr > 0).mean()), 3),
        "beat_median_da_pct": round(float(np.mean(das)) * 100, 1),
        "decile_spread_gross_pct": round(gross, 2),
        # One round trip to rotate a decile position at the horizon.
        "decile_spread_net_pct": round(gross - 2 * costs.ROUND_TRIP_COST_PCT, 2),
        "spread_cost_basis": costs.NET_ASSUMPTION_LABEL,
    }


def edge_multiplier(metrics: dict[str, Any] | None) -> float:
    """[0, 1] earned weight from measured walk-forward evidence. Disclosed
    floors: >= GATE_MIN_DATES independent windows, IC t-stat ramp 1.0 -> 2.0."""
    if not metrics or metrics.get("status") != "ok":
        return 0.0
    if int(metrics.get("n_dates") or 0) < GATE_MIN_DATES:
        return 0.0
    t = float(metrics.get("ic_t_stat") or 0.0)
    return float(np.clip((t - GATE_T_FLOOR) / (GATE_T_FULL - GATE_T_FLOOR), 0.0, 1.0))


def build_snapshot(extend_history: bool = True) -> dict[str, Any] | None:
    """Full panel + walk-forward evidence + current ranks. EXPENSIVE — call
    from the warm loop, never inside a GET."""
    px = _build_panel(extend=extend_history)
    if px is None:
        return None
    rows = _eval_rows(px)
    if len(rows) <= MIN_TRAIN_DATES:
        return None
    metrics = _walk_forward_metrics(rows)
    mult = edge_multiplier(metrics)

    # Current scores: train on every completed window, predict the LATEST bar.
    feats = _features(px)
    latest = px.index[-1]
    X_now = pd.DataFrame({k: v.loc[latest] for k, v in feats.items()}).dropna()
    Xtr = pd.concat([r[1] for r in rows])
    ytr = pd.concat([r[2] for r in rows])
    w = _fit(Xtr.values, ytr.values)
    pred = pd.Series(X_now.values @ w, index=X_now.index).sort_values(ascending=False)
    n = len(pred)
    scores = {
        sym: {
            "rank": int(i + 1),
            "n": n,
            "pctile": round(float(1.0 - i / max(n - 1, 1)), 4),
            "expected_rel_21d_pct": round(float(v) * 100, 2),
        }
        for i, (sym, v) in enumerate(pred.items())
    }
    return {
        "as_of": str(latest.date()),
        "horizon_days": HORIZON_D,
        "universe": sorted(px.columns),
        "universe_size": n,
        "metrics": metrics,
        "edge_multiplier": round(mult, 3),
        "scores": scores,
        "basis": (
            f"Relative 21d return vs the universe median, ridge on cross-sectionally "
            f"z-scored price features (momentum 21/63/126d, 5d reversal, 63d vol, "
            f"SMA200 distance). Walk-forward on {metrics.get('n_dates', 0)} independent "
            f"non-overlapping windows; component weight is EARNED (requires "
            f">={GATE_MIN_DATES} windows AND IC t>{GATE_T_FLOOR}, full at t>={GATE_T_FULL}). "
            f"Universe excludes leveraged wrappers, broad-index funds, crypto calendars, "
            f"and sub-{MIN_VOL_ANNUAL:.0%}-vol sleeves. CAVEAT: the panel is built from "
            f"TODAY'S store membership (survivor universe — names that died or were "
            f"dropped never enter historical windows), so the measured IC is an upper "
            f"bound; the gate's raised t-floor prices that in. "
            f"History sources: {px.attrs.get('history_sources', {})}."),
    }


def warm_snapshot() -> dict[str, Any] | None:
    """Build (or reuse) the daily snapshot — the auto-live loop's entry."""
    with _CACHE_LOCK:
        fresh = (time.monotonic() - _CACHE["built_at"]) < _PANEL_TTL_S
        if fresh and _CACHE["snapshot"] is not None:
            return _CACHE["snapshot"]
    snap = None
    try:
        snap = build_snapshot()
    except Exception:
        snap = None
    with _CACHE_LOCK:
        if snap is not None:
            _CACHE["snapshot"] = snap
            _CACHE["built_at"] = time.monotonic()
        return _CACHE["snapshot"]


def snapshot_cached() -> dict[str, Any] | None:
    """Cached-only read for GET paths. None when cold — degrade, never block."""
    with _CACHE_LOCK:
        return _CACHE["snapshot"]


def invalidate_cache() -> None:
    with _CACHE_LOCK:
        _CACHE["snapshot"] = None
        _CACHE["built_at"] = 0.0


def for_symbol(symbol: str) -> dict[str, Any] | None:
    """Per-instrument slice (rank + the evidence behind it) for the signal."""
    snap = snapshot_cached()
    if not snap:
        return None
    entry = (snap.get("scores") or {}).get(str(symbol).upper())
    if entry is None:
        return None
    return {
        **entry,
        "as_of": snap["as_of"],
        "horizon_days": snap["horizon_days"],
        "universe_size": snap["universe_size"],
        "metrics": snap["metrics"],
        "edge_multiplier": snap["edge_multiplier"],
        "basis": snap["basis"],
    }

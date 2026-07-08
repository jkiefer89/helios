"""Forward macro & valuation anchors for the building-block return model.

Supplies the market inputs the CMA needs — a risk-free rate, an equity risk
premium, per-sector "fair" valuation/growth anchors, and per-asset-class
expected returns for non-equity sleeves.

Every value has a STATIC OFFLINE FALLBACK (the same constants the engine has
always used: RF from HELIOS_RF, ERP=0.045), so the forward spine runs with no
network and no API keys. Live providers — FRED for the risk-free curve,
Damodaran's datasets for the implied ERP and sector tables — can override the
same functions later without changing any caller. Nothing here fabricates a
live observation; offline it returns the documented defaults and says so.
"""
from __future__ import annotations

from . import mandate as _mnd

# Sector valuation/growth anchors (approximate, Damodaran-style long-run values).
# fair_pe = the multiple a sector mean-reverts toward; growth = nominal long-run
# earnings growth. These are deliberately conservative round numbers; replace
# with a dated Damodaran ingest behind sector_anchor() for live precision.
_SECTOR_ANCHORS = {
    "technology": {"fair_pe": 24.0, "growth": 0.12},
    "information technology": {"fair_pe": 24.0, "growth": 0.12},
    "communication services": {"fair_pe": 18.0, "growth": 0.09},
    "consumer discretionary": {"fair_pe": 20.0, "growth": 0.09},
    "consumer cyclical": {"fair_pe": 20.0, "growth": 0.09},
    "consumer staples": {"fair_pe": 19.0, "growth": 0.05},
    "consumer defensive": {"fair_pe": 19.0, "growth": 0.05},
    "health care": {"fair_pe": 18.0, "growth": 0.09},
    "healthcare": {"fair_pe": 18.0, "growth": 0.09},
    "financials": {"fair_pe": 13.0, "growth": 0.07},
    "financial services": {"fair_pe": 13.0, "growth": 0.07},
    "industrials": {"fair_pe": 18.0, "growth": 0.07},
    "energy": {"fair_pe": 12.0, "growth": 0.04},
    "utilities": {"fair_pe": 17.0, "growth": 0.04},
    "materials": {"fair_pe": 15.0, "growth": 0.05},
    "basic materials": {"fair_pe": 15.0, "growth": 0.05},
    "real estate": {"fair_pe": 16.0, "growth": 0.05},
}
_MARKET_ANCHOR = {"fair_pe": 17.0, "growth": 0.06}


def risk_free() -> float:
    """Annual risk-free rate. Offline fallback = HELIOS_RF (default 2%)."""
    return _mnd.RF


# Live Treasury yields — DIAGNOSTICS, not silent inputs. Scoring stays on the
# configured HELIOS_RF so every anchor in the app moves together and only when
# the operator changes it; these functions make the drift VISIBLE (payloads +
# a data-quality alert) so the operator can decide when to update HELIOS_RF.
#
# Primary source: yfinance CBOE yield indices (^IRX 13-week, ^FVX 5Y, ^TNX 10Y,
# ^TYX 30Y — quoted in percent). All cached ~1h; offline returns None, never a
# fabricated rate.
_YIELD_TICKERS = {"3m": "^IRX", "5y": "^FVX", "10y": "^TNX", "30y": "^TYX"}
_YIELD_CACHE: dict[str, tuple[float, float]] = {}  # tenor -> (monotonic_ts, value)
_YIELD_TTL_S = 3600.0
# Configured-vs-market drift beyond this (in percentage points, vs the 3M bill)
# is worth an operator look: CD-alternative and income mandates benchmark off RF.
RF_DRIFT_ALERT_PP = 1.5


def treasury_yield(tenor: str) -> float | None:
    """Live Treasury yield for one tenor ('3m'|'5y'|'10y'|'30y') as a fraction,
    or None offline/unavailable."""
    import time

    from . import data as _data
    ticker = _YIELD_TICKERS.get((tenor or "").lower())
    if ticker is None:
        return None
    now = time.monotonic()
    hit = _YIELD_CACHE.get(tenor)
    if hit and now - hit[0] < _YIELD_TTL_S:
        return hit[1]
    if not _data.HAS_YF:
        return None
    try:
        raw = float(_data._yf.Ticker(ticker).fast_info["last_price"])
    except Exception:
        return None
    if not (0.0 < raw < 25.0):  # quoted in percent (4.4 == 4.4%)
        return None
    value = raw / 100.0
    _YIELD_CACHE[tenor] = (now, value)
    return value


def ten_year_yield() -> float | None:
    """Live 10Y Treasury yield as a fraction (e.g. 0.044), or None offline."""
    return treasury_yield("10y")


def cached_treasury_yield(tenor: str) -> float | None:
    """Get-only cache read: the yield if some live-path call already fetched it
    this hour, else None. Lets offline/deterministic surfaces (data-quality)
    report on live rates without ever making a network call themselves."""
    hit = _YIELD_CACHE.get((tenor or "").lower())
    if not hit:
        return None
    import time
    return hit[1] if time.monotonic() - hit[0] < _YIELD_TTL_S else None


def rf_drift() -> dict:
    """Configured HELIOS_RF vs the live 3M bill — the honesty check for every
    mandate benchmarked off the risk-free rate (CD-alt targets especially).

    Reads the cached yield only (no network): the cache is warmed by the analyze
    routes' rate_context calls, so this stays deterministic for offline runs."""
    live_3m = cached_treasury_yield("3m")
    out: dict = {
        "configured_rf_pct": round(_mnd.RF * 100.0, 2),
        "live_3m_pct": round(live_3m * 100.0, 2) if live_3m is not None else None,
        "drift_pp": None,
        "stale": False,
    }
    if live_3m is not None:
        drift = abs(live_3m - _mnd.RF) * 100.0
        out["drift_pp"] = round(drift, 2)
        out["stale"] = drift >= RF_DRIFT_ALERT_PP
    return out


def rate_context() -> dict:
    """Configured-vs-market rate snapshot for payload provenance."""
    curve = {}
    for tenor in ("3m", "5y", "10y", "30y"):
        val = treasury_yield(tenor)
        if val is not None:
            curve[tenor + "_pct"] = round(val * 100.0, 2)
    live_10y = curve.get("10y_pct")
    ctx = {
        "configured_rf_pct": round(_mnd.RF * 100.0, 2),
        "live_10y_pct": live_10y,
        "treasury_curve_pct": curve or None,
        "rf_drift": rf_drift(),
        "note": ("Scoring uses the configured risk-free rate (HELIOS_RF); live Treasury "
                 "yields are shown so drift is visible, never silently substituted."),
    }
    if curve.get("3m_pct") is not None and live_10y is not None:
        ctx["curve_slope_3m10y_pp"] = round(live_10y - curve["3m_pct"], 2)
    return ctx


def equity_risk_premium() -> float:
    """Long-run equity risk premium. Offline fallback = 4.5%."""
    return _mnd.ERP


def sector_anchor(sector: str | None) -> dict:
    """Fair P/E + nominal growth for a GICS-ish sector name (fuzzy, case-insensitive)."""
    key = (sector or "").strip().lower()
    if key in _SECTOR_ANCHORS:
        return dict(_SECTOR_ANCHORS[key])
    for name, anchor in _SECTOR_ANCHORS.items():
        if key and (key in name or name in key):
            return dict(anchor)
    return dict(_MARKET_ANCHOR)


def asset_class_return(asset_class: str | None) -> float:
    """Coarse forward expected return for a non-equity sleeve (debt, cash, …).

    Anchored off the live/fallback risk-free rate plus an asset-class premium, so
    a bond/cash sleeve is not handed an equity growth estimate.
    """
    rf = risk_free()
    label = (asset_class or "").strip().lower()
    if "cash" in label or "short-term" in label:
        return rf
    if "debt" in label:
        return rf + 0.005
    if "asset-backed" in label or "loan" in label or "structured" in label:
        return rf + 0.02
    if "real estate" in label:
        return rf + 0.03
    if "commodity" in label:
        return rf + 0.02
    # Derivatives / unknown sleeves: half the equity premium, flagged as coarse.
    return rf + 0.5 * equity_risk_premium()

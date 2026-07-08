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


# Live 10Y Treasury (^TNX) — a DIAGNOSTIC, not a silent input. Scoring stays on
# the configured HELIOS_RF so every anchor in the app moves together and only
# when the operator changes it; this function lets the UI/payloads show how far
# the configured rate has drifted from the market so the operator can decide.
_TNX_CACHE: list = [0.0, None]  # [monotonic_ts, value]
_TNX_TTL_S = 3600.0


def ten_year_yield() -> float | None:
    """Live 10Y Treasury yield as a fraction (e.g. 0.044), or None offline."""
    import time

    from . import data as _data
    now = time.monotonic()
    if _TNX_CACHE[1] is not None and now - _TNX_CACHE[0] < _TNX_TTL_S:
        return _TNX_CACHE[1]
    if not _data.HAS_YF:
        return None
    try:
        quote = _data._yf.Ticker("^TNX").fast_info
        raw = float(quote["last_price"])
    except Exception:
        return None
    if not (0.0 < raw < 25.0):  # ^TNX quotes the yield in percent (4.4 == 4.4%)
        return None
    value = raw / 100.0
    _TNX_CACHE[0], _TNX_CACHE[1] = now, value
    return value


def rate_context() -> dict:
    """Configured-vs-market rate snapshot for payload provenance."""
    live = ten_year_yield()
    return {
        "configured_rf_pct": round(_mnd.RF * 100.0, 2),
        "live_10y_pct": round(live * 100.0, 2) if live is not None else None,
        "note": ("Scoring uses the configured risk-free rate (HELIOS_RF); the live 10Y "
                 "is shown so drift is visible, never silently substituted."),
    }


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

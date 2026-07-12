"""Versioned static assumptions — the hand-set numbers, owned and dated.

Every static table Helios uses in place of a live market observation lives
here, wrapped with an as-of date and one honest methodology sentence, so
payloads can label the numbers instead of presenting them as market facts
(review finding: sector fair-PE anchors, factor loadings, and ADV proxies
were undated code constants). Behavior is unchanged — consumers import the
same values they always used; what changed is that the provenance travels
with them.

Updating a table = updating its ``as_of`` and bumping ASSUMPTIONS_VERSION.
"""
from __future__ import annotations

ASSUMPTIONS_VERSION = "2026-07"

# Sector valuation/growth anchors the CMA mean-reverts toward.
SECTOR_ANCHORS = {
    "as_of": "2026-07",
    "methodology": ("Hand-set Damodaran-style long-run approximations — "
                    "deliberately conservative round numbers, not live market data."),
    "values": {
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
    },
}
MARKET_ANCHOR = {"fair_pe": 17.0, "growth": 0.06}

# Non-equity sleeve premia over the configured risk-free rate.
ASSET_CLASS_PREMIA = {
    "as_of": "2026-07",
    "methodology": ("Hand-set spreads over the configured risk-free rate — "
                    "coarse sleeve anchors, not observed market yields."),
    "values": {
        "cash_like": 0.0,
        "investment_grade_debt": 0.005,
        "asset_backed_or_loans": 0.02,
        "real_estate": 0.03,
        "commodities": 0.02,
        # derivatives/unknown sleeves earn half the equity risk premium
    },
}

# Sector -> factor loadings for the risk view. These are hand-ASSIGNED
# taxonomy weights, not regression betas estimated from any history.
FACTOR_MAP = {
    "as_of": "2026-07",
    "methodology": ("Static hand-assigned sector-to-factor loadings — a "
                    "labeling heuristic, not regression betas."),
    "values": {
        "Technology": {"growth": 0.75, "quality": 0.35},
        "Communication Services": {"growth": 0.55, "quality": 0.25},
        "Consumer Discretionary": {"growth": 0.55, "cyclical": 0.35},
        "Industrials": {"cyclical": 0.35, "defensive": 0.20},
        "Utilities": {"defensive": 0.65, "income": 0.25},
        "Healthcare": {"defensive": 0.45, "quality": 0.35, "growth": 0.15},
        "Financials": {"value": 0.45, "cyclical": 0.35},
        "Energy": {"value": 0.35, "cyclical": 0.35, "real_assets": 0.25},
        "Commodities": {"real_assets": 0.75, "defensive": 0.15},
        "Fixed Income": {"income": 0.65, "defensive": 0.45},
        "Cash": {"defensive": 0.80, "income": 0.35},
        "Broad Market": {"quality": 0.25, "growth": 0.25, "value": 0.20, "defensive": 0.15},
        "Crypto": {"growth": 0.55, "cyclical": 0.55, "real_assets": 0.20},
    },
}

# Fallback average daily dollar volumes for liquidity scoring when no observed
# 60-day volume exists. Public ballpark figures, not a data feed.
LIQUIDITY_ADV_PROXIES = {
    "as_of": "2026-07",
    "methodology": ("Static public ballpark average daily dollar volumes — "
                    "used only when observed 60-day volume is unavailable, and "
                    "flagged adv_source=static_public_proxy wherever shown."),
    "values": {
        "AAPL": 7_000_000_000,
        "MSFT": 6_000_000_000,
        "NVDA": 12_000_000_000,
        "AMZN": 5_000_000_000,
        "GOOGL": 3_000_000_000,
        "META": 4_000_000_000,
        "SPY": 40_000_000_000,
        "QQQ": 20_000_000_000,
        "IWM": 7_000_000_000,
        "BND": 1_000_000_000,
        "TLT": 2_000_000_000,
        "GLD": 1_500_000_000,
        "SGOV": 700_000_000,
        "BTC-USD": 20_000_000_000,
    },
}

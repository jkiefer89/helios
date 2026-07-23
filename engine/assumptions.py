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

# Growth-and-quality conditioning of the fair P/E the CMA reverts toward.
# A STATIC sector-average fair P/E penalizes the compounders you want to own:
# it reverts a fast-growing, high-return franchise toward the same multiple as
# a no-growth peer, and — because growth is ALSO credited additively in E[r] —
# it taxes the market's pricing of the very growth the model rewards (a
# double-count). Conditioning makes the fair multiple RISE with a company's
# growth relative to its sector and with its capital quality, so the reversion
# penalizes only a multiple ABOVE what growth+quality justify — not the
# compounding itself. Everything is bounded, so a hyper-grower cannot earn an
# unbounded multiple and a rate/cycle shock still bites a genuinely stretched
# name. Growth uses the model's already-processed g (capped/shrunk), so a noisy
# single-quarter comp cannot inflate the anchor; a name with no growth data
# falls back to the static sector base (honest, never fabricated).
FAIR_VALUE_CONDITIONING = {
    "as_of": "2026-07",
    "methodology": ("fair_pe = sector_base × growth_mult × quality_mult, all "
                    "bounded. growth_mult scales with growth vs the sector norm; "
                    "quality_mult with ROE and net margin vs baselines. Hand-set "
                    "conservative sensitivities, not a fitted model."),
    # multiple premium per unit of growth above the sector norm
    "growth_sensitivity": 5.0,
    "growth_excess_clip": [-0.15, 0.30],   # (company_g − sector_g), bounded
    "growth_mult_clip": [0.60, 2.50],      # fair multiple is 0.6×–2.5× the sector base
    # capital-quality premium — gentler than growth; ROE/margin clamped so a
    # buyback-inflated ROE cannot earn unbounded credit.
    "quality_sensitivity": 0.8,
    "roe_baseline": 0.15,
    "roe_credit_clip": [0.0, 0.40],
    "margin_baseline": 0.10,
    "margin_credit_clip": [0.0, 0.35],
    "quality_mult_clip": [0.85, 1.25],
}

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


# --------------------------------------------------------------------------- #
# Leveraged / daily-reset product detection (as_of 2026-07)
#
# Methodology: name-pattern matching against issuer conventions for geared and
# inverse wrappers (Direxion "Daily ... Bull/Bear NX", ProShares "Ultra/
# UltraPro/Short", generic "2X/3X/-1X" share classes). These products reset
# leverage daily: point-in-time fundamentals, sector anchors, and long-horizon
# GBM cones do not model their compounding, so the strategic/fundamentals
# track must refuse rather than fabricate. Patterns are conservative —
# a miss means an unlabeled product, a false positive silences a usable
# strategic track — reviewed with each ASSUMPTIONS_VERSION bump.
# --------------------------------------------------------------------------- #
import re as _re

LEVERAGED_NAME_PATTERNS = (
    r"\b[123](?:\.\d+)?x\b",         # "3X Shares", "2x", "1.5x"
    r"\bultrapro\b",
    r"\bproshares ultra\b",
    r"\bproshares short\b",
    r"\bdirexion daily\b",
    r"\bdaily .{0,40}\b(bull|bear)\b",
    r"\bleveraged\b",
    r"\binverse\b",
)
_LEVERAGED_RE = _re.compile("|".join(LEVERAGED_NAME_PATTERNS), _re.IGNORECASE)


def is_leveraged_product_name(name: str | None) -> bool:
    """True when an instrument NAME matches a geared/inverse wrapper convention."""
    return bool(name) and bool(_LEVERAGED_RE.search(str(name)))


# Broad-index / market-proxy wrappers (as_of 2026-07): excluded from the
# cross-sectional ranking universe. Ranking a total-market fund against the
# single names it contains measures BETA, not selection skill — a probe on the
# operator's real book showed index funds inflating measured rank IC from
# +0.12 to +0.18. Same conservative name-pattern convention as the leveraged
# detector; reviewed with each ASSUMPTIONS_VERSION bump.
BROAD_INDEX_NAME_PATTERNS = (
    r"\bs&p\s*500\b",
    r"\bnasdaq[- ]?100\b",
    r"\brussell\s*[123]000\b",
    r"\bdow jones\b",
    r"\btotal (stock )?market\b",
    r"\bmsci\s+(world|acwi|eafe|usa)\b",
    r"\bqqq trust\b",
)
_BROAD_INDEX_RE = _re.compile("|".join(BROAD_INDEX_NAME_PATTERNS), _re.IGNORECASE)


def is_broad_index_name(name: str | None) -> bool:
    """True when an instrument NAME matches a broad-market index wrapper."""
    return bool(name) and bool(_BROAD_INDEX_RE.search(str(name)))

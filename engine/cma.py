"""Building-block forward expected return (Capital Market Assumptions).

Computes a forward expected return from real fundamentals + macro anchors —
NOT from price momentum — and rolls it up by look-through weight to a fund/model
estimate. This is the forward number that needs no price history, so it works
for a newly-launched fund the day its holdings are known.

Per equity holding, three transparent blocks:

    E[r] = dividend_yield + earnings_growth + valuation_reversion

where valuation_reversion = (1/H)·ln(fair_PE_sector / current_PE) mean-REVERTS
the multiple (pulls expensive names down, cheap names up) rather than
extrapolating the trend. Non-equity sleeves use asset-class anchors. Holdings
with no usable fundamentals fall back to the mandate's generic anchor, and the
model estimate is COVERAGE-WEIGHTED: partial data still improves the estimate
and zero data reproduces the generic anchor exactly. All blocks are clamped to
defensible bands so one bad input can't blow up the estimate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import assumptions as _assumptions
from . import macro as _macro
from . import mandate as _mnd

REVERSION_HORIZON_Y = 5.0
_DY_CAP = (0.0, 0.12)
_GROWTH_CAP = (-0.10, 0.25)
_REVERSION_CAP = (-0.05, 0.05)
_ER_CAP = (-0.05, 0.20)


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass
class HoldingReturn:
    ticker: str
    weight_pct: float
    expected_return: float | None
    basis: str                 # "fundamentals" | "asset_class" | "generic"
    usable: bool
    blocks: dict


def _display_name(ticker: str, fundamentals) -> str:
    """Best-effort instrument display name for structure detection: the
    in-memory store's name (yfinance shortName / upload label), else the
    fundamentals provider's name field when present."""
    try:
        from . import data as _data

        inst = _data.get(ticker)
        if inst is not None and inst.name and inst.name != ticker:
            return str(inst.name)
    except Exception:
        pass
    return str(getattr(fundamentals, "name", "") or "")


def holding_expected_return(ticker, weight_pct, asset_class, fundamentals,
                            mandate_key="balanced", horizon_years=REVERSION_HORIZON_Y) -> HoldingReturn:
    """Forward expected return for one underlying holding."""
    label = (asset_class or "").lower()
    is_equity = label.startswith("equity") or label in ("", "other")

    # Leveraged/daily-reset wrappers (Direxion Daily 3X, ProShares Ultra, ...):
    # the provider's "fundamentals" describe the TRUST (FMP tags SOXL a
    # financial-services company with a meaningless P/E — verified live), and
    # sector anchors / valuation reversion / long-horizon anchoring do not
    # model daily-reset compounding. Refuse the equity block outright; the
    # rating honestly falls back to the technical blend.
    display_name = _display_name(ticker, fundamentals)
    if _assumptions.is_leveraged_product_name(display_name):
        return HoldingReturn(ticker, weight_pct, None, "generic", False, {
            "reason": ("leveraged/daily-reset wrapper — equity fundamentals, sector "
                       "anchors, and valuation reversion do not apply; tactical "
                       "(technical) signals only"),
            "product_structure": "leveraged_daily_reset"})

    # Non-equity sleeve: anchor off asset class (we know the sleeve, not the name).
    if not is_equity:
        er = _macro.asset_class_return(asset_class)
        return HoldingReturn(ticker, weight_pct, er, "asset_class", True,
                             {"asset_class_anchor": round(er, 4)})

    if fundamentals is not None and getattr(fundamentals, "usable", False):
        # Fund wrappers (ETF/ETN/mutual fund): a single-security P/E mean-
        # reversion and a sector-growth anchor are a category error on a
        # diversified basket — and FMP mis-tags most equity ETFs "Financial
        # Services" (SOXX/QQQ/ARTY verified live), which fabricated ~2% E[r]
        # and spurious strategic SELLs across the operator's book. Refuse the
        # single-security block; the rating falls back to the technical blend,
        # and a real fundamentals view comes from importing the fund as a
        # model (its holdings look through to a genuine aggregate CMA).
        # (Non-equity fund sleeves — bond/commodity ETFs with a known
        # asset_class — are handled by the asset-class branch above and keep
        # their sleeve anchor.)
        if getattr(fundamentals, "is_fund", False):
            return HoldingReturn(ticker, weight_pct, None, "generic", False, {
                "reason": ("fund wrapper (ETF/ETN/fund) — single-security fundamentals "
                           "and sector anchors do not apply to a diversified basket; "
                           "technical signals only. Import it as a model for a "
                           "look-through fundamentals view."),
                "product_structure": "fund_wrapper"})
        pe = fundamentals.forward_pe or fundamentals.trailing_pe
        # Equity evidence means a real earnings signal: reported growth or a
        # positive P/E. A provider-stamped SECTOR label alone does not count —
        # FMP tags bond ETFs "Financial Services" (BND, verified live), and the
        # old guard then fabricated a 7%/yr financials growth block onto a bond
        # fund. Yield-only holdings refuse the equity building block entirely;
        # the rating honestly falls back to the technical blend.
        has_equity_evidence = bool(fundamentals.earnings_growth is not None
                                   or (pe and pe > 0))
        if not has_equity_evidence:
            return HoldingReturn(ticker, weight_pct, None, "generic", False, {
                "reason": ("yield-only fundamentals — no earnings evidence (P/E or "
                           "growth); refusing to apply an equity growth anchor")})
        dy = _clip(fundamentals.dividend_yield or 0.0, *_DY_CAP)
        raw_g = fundamentals.earnings_growth
        growth_basis = getattr(fundamentals, "growth_basis", "") or ""
        g_sector = _macro.sector_anchor(fundamentals.sector)["growth"]
        if raw_g is None:
            g_in, growth_basis = g_sector, "sector_anchor"   # substituted — flagged
        elif growth_basis == "trailing_quarter_yoy":
            # yfinance's figure is ONE quarter's YoY comp. Used raw as a
            # perpetual growth rate, a cyclical rebound quarter (+80% off a
            # weak comp) capped g at +25%/yr and saturated the strategic
            # track at maximal BUY (review finding). Shrink 70% toward the
            # sector anchor instead of pretending one comp is a CAGR.
            g_in = g_sector + 0.3 * (_clip(raw_g, -0.5, 0.8) - g_sector)
            growth_basis = "trailing_quarter_yoy_shrunk"
        else:
            g_in = raw_g
        g = _clip(g_in, *_GROWTH_CAP)
        rev = 0.0
        fair = None
        if pe and pe > 0:
            fair = _macro.sector_anchor(fundamentals.sector)["fair_pe"]
            rev = _clip(math.log(fair / pe) / horizon_years, *_REVERSION_CAP)
        er = _clip(dy + g + rev, *_ER_CAP)
        blocks = {
            "dividend_yield": round(dy, 4),
            "earnings_growth": round(g, 4),
            "valuation_reversion": round(rev, 4),
        }
        if growth_basis:
            blocks["growth_basis"] = growth_basis
        if fair is not None:
            # The reversion target is a dated static ASSUMPTION, not a market
            # observation — surface which anchor produced the block (review
            # finding: the payload never said what fair_pe was used).
            blocks["fair_pe_anchor"] = fair
            blocks["anchor_as_of"] = _macro.SECTOR_ANCHORS_AS_OF
        return HoldingReturn(ticker, weight_pct, er, "fundamentals", True, blocks)

    # Equity name with no usable fundamentals: defer to the generic anchor.
    return HoldingReturn(ticker, weight_pct, None, "generic", False,
                         {"reason": "no usable fundamentals"})


def instrument_forward(ticker: str, fnd, mandate_key: str = "balanced") -> dict:
    """Single-instrument forward view for the strategic rating track.

    Wraps :func:`holding_expected_return` for one name (treated as an equity
    leaf; funds without sector data score off the market anchor, which is the
    honest coarse answer) and adds the analyst-consensus context when the
    provider supplied it. Returns ``usable=False`` — never a fabricated number —
    when there are no usable fundamentals.
    """
    anchor = _mnd.anchor_return(mandate_key)
    hr = holding_expected_return(ticker, 100.0, "equity", fnd, mandate_key)
    out: dict = {
        "usable": bool(hr.usable and hr.expected_return is not None),
        "basis": hr.basis,
        "anchor_pct": round(anchor * 100.0, 2),
        "mandate": _mnd.key_or_default(mandate_key),
        "blocks_pct": {k: round(v * 100.0, 2) for k, v in hr.blocks.items()
                       if isinstance(v, (int, float)) and k != "fair_pe_anchor"},
        # Dated static assumption behind valuation_reversion (a P/E multiple,
        # NOT a percentage — kept out of blocks_pct's x100 scaling).
        "fair_pe_anchor": hr.blocks.get("fair_pe_anchor"),
        "anchor_as_of": hr.blocks.get("anchor_as_of", ""),
        "source": getattr(fnd, "source", "none"),
        "fundamentals_as_of": getattr(fnd, "as_of", "") or "",
        "sector": getattr(fnd, "sector", "") or "",
        "method": "building_block_cma",
        "assumptions_version": _macro.SECTOR_ANCHORS_AS_OF,
        "horizon_basis": "annualized — independent of the chart horizon",
    }
    if hr.blocks.get("product_structure"):
        out["product_structure"] = hr.blocks["product_structure"]
        out["reason"] = hr.blocks.get("reason", "")
    elif not out["usable"] and hr.blocks.get("reason"):
        out["reason"] = hr.blocks["reason"]
    if out["usable"]:
        out["expected_return_pct"] = round(hr.expected_return * 100.0, 2)
        out["gap_vs_anchor_pct"] = round((hr.expected_return - anchor) * 100.0, 2)
    recon = list(getattr(fnd, "reconciliation_warnings", None) or [])
    if recon:
        # Cross-provider disagreement rides with the number it questions.
        out["reconciliation_warnings"] = recon[:5]
    # Quality context (shown, and mildly scored, only when present).
    quality = {}
    for field in ("roe", "profit_margin", "debt_to_equity", "revenue_growth"):
        val = getattr(fnd, field, None)
        if val is not None:
            quality[field] = round(float(val), 4)
    if quality:
        out["quality"] = quality
    # Upcoming earnings (FMP calendar) — timing context, never scored.
    next_earnings = getattr(fnd, "next_earnings_date", "") or ""
    if next_earnings:
        import datetime as _dt
        try:
            days_until = (_dt.date.fromisoformat(next_earnings) - _dt.date.today()).days
            out["earnings"] = {
                "next_date": next_earnings,
                "days_until": days_until,
                "imminent": 0 <= days_until <= 7,
            }
        except ValueError:
            pass
    # Analyst consensus context (free via yfinance) — evidence, not a component.
    price = getattr(fnd, "current_price", None)
    target = getattr(fnd, "target_mean_price", None)
    if price and target and price > 0:
        out["analyst"] = {
            "target_mean_price": round(float(target), 2),
            "implied_upside_pct": round((float(target) / float(price) - 1.0) * 100.0, 2),
            "rating_mean": getattr(fnd, "analyst_rating", None),
            "n_analysts": getattr(fnd, "n_analysts", None),
            "note": "Consensus 12m target — context only, not a scored component.",
        }
    return out


def aggregate(underlyings, fundamentals_map, mandate_key="balanced",
              total_weight_pct: float | None = None) -> dict:
    """Roll per-holding expected returns up to a model forward expected return.

    ``underlyings``: list of ``{ticker, weight_pct, asset_class}`` (look-through
    exposure, weights in percent). ``fundamentals_map``: ticker -> Fundamentals
    (missing/None allowed). Weight not covered by a usable estimate is filled
    with the mandate's generic anchor, and ``coverage_pct`` reports how much of
    the supplied weight had a real (fundamentals or asset-class) estimate.
    """
    generic = _mnd.anchor_return(mandate_key)
    # Denominator honesty: the look-through DROPS unresolved holdings, the
    # unseen intra-fund remainder, and beyond-cap rows from `underlyings`, so
    # measuring coverage against the supplied sum extrapolated a thin covered
    # slice to the whole book (verified: 40% visibility reported coverage 100%
    # and full-weight E[r]). Callers pass the true basis (100.0 for a whole
    # model); it can only WIDEN the denominator, never shrink it.
    supplied_w = sum(max(0.0, float(u.get("weight_pct") or 0.0)) for u in underlyings)
    total_w = max(supplied_w, float(total_weight_pct or 0.0))
    if total_w <= 0:
        return _empty_result(generic, mandate_key)

    per, usable_w = [], 0.0
    mu_usable = 0.0
    # asset_class_anchor included: bond/cash sleeves add to usable_w, so a
    # breakdown missing their block could not reconcile with the covered E[r]
    # it claims to explain (review finding: blocks summed 6.82% vs 8.07%).
    agg_blocks = {"dividend_yield": 0.0, "earnings_growth": 0.0,
                  "valuation_reversion": 0.0, "asset_class_anchor": 0.0}
    for u in underlyings:
        w = max(0.0, float(u.get("weight_pct") or 0.0))
        if w <= 0:
            continue
        tk = (u.get("ticker") or "").upper()
        hr = holding_expected_return(tk, w, u.get("asset_class"),
                                     fundamentals_map.get(tk), mandate_key)
        per.append(hr)
        if hr.usable and hr.expected_return is not None:
            usable_w += w
            mu_usable += w * hr.expected_return
            for k in agg_blocks:
                if k in hr.blocks:
                    agg_blocks[k] += w * hr.blocks[k]

    coverage = usable_w / total_w
    mu_cma = (mu_usable / usable_w) if usable_w > 0 else generic
    # Coverage-weighted blend: uncovered weight earns the generic anchor.
    mu_model = (mu_usable + (total_w - usable_w) * generic) / total_w

    weighted_blocks = {k: round((v / usable_w), 4) for k, v in agg_blocks.items()} if usable_w > 0 else {}
    return {
        "expected_return_pct": round(mu_model * 100.0, 2),
        "expected_return_covered_pct": round(mu_cma * 100.0, 2),
        "generic_anchor_pct": round(generic * 100.0, 2),
        "uplift_vs_anchor_pct": round((mu_model - generic) * 100.0, 2),
        "coverage_pct": round(coverage * 100.0, 1),
        "weighted_blocks_pct": {k: round(v * 100.0, 2) for k, v in weighted_blocks.items()},
        "n_underlyings": len(per),
        "n_usable": sum(1 for h in per if h.usable),
        "top_contributions": [
            # Explicit None check: `or generic` displayed a holding whose
            # blocks genuinely sum to 0.0% as the anchor (review finding —
            # the truly worst name then dodged the trim hypothesis).
            {"ticker": h.ticker, "weight_pct": round(h.weight_pct, 2),
             "expected_return_pct": round(
                 (h.expected_return if h.expected_return is not None else generic) * 100.0, 2),
             "basis": h.basis}
            for h in sorted(per, key=lambda h: h.weight_pct, reverse=True)[:15]
        ],
        "mandate_anchor_pct": round(generic * 100.0, 2),
        "method": "building_block_cma",
        "assumptions_version": _macro.SECTOR_ANCHORS_AS_OF,
        "disclaimer": "Forward expected return from holdings fundamentals + macro anchors, "
                      "not a price forecast or guarantee. Uncovered weight uses the mandate anchor.",
    }


def research_hypotheses(forward_result: dict, mandate_key: str = "balanced",
                        min_coverage_pct: float = 50.0) -> list[dict]:
    """Forward-looking research hypotheses in the Portfolio Clinic's schema.

    The clinic's own ``_research_hypotheses`` are backward-looking (trailing
    return, realized marginal risk). These are the forward complement, derived
    from the building-block CMA, emitted in the SAME ``{type, ticker,
    current_weight, suggested_weight, rationale}`` shape so both feed one unified
    research-hypothesis surface instead of two parallel ones. Hypotheses only —
    never orders — and gated on real coverage so we never opine on thin data.
    """
    if forward_result.get("coverage_pct", 0.0) < min_coverage_pct:
        return []
    contribs = [c for c in forward_result.get("top_contributions", []) if c.get("basis") == "fundamentals"]
    if len(contribs) < 2:
        return []
    anchor = float(forward_result.get("mandate_anchor_pct", 0.0))
    best = max(contribs, key=lambda c: c["expected_return_pct"])
    worst = min(contribs, key=lambda c: c["expected_return_pct"])
    out: list[dict] = []
    if best["expected_return_pct"] > anchor:
        w = best["weight_pct"] / 100.0
        out.append({
            "type": "forward_valuation_tilt",
            "ticker": best["ticker"],
            "current_weight": round(w, 6),
            "suggested_weight": round(min(0.35, w + 0.02), 6),
            "rationale": (
                f"Test increasing exposure to {best['ticker']}: its forward building-block return "
                f"({best['expected_return_pct']:.1f}%) is the highest in the look-through and above the "
                f"{anchor:.1f}% mandate anchor. Forward research hypothesis only; confirm in Strategy Lab "
                "and Advisor Report before any change."
            ),
        })
    if worst["expected_return_pct"] < anchor and worst["ticker"] != best["ticker"]:
        w = worst["weight_pct"] / 100.0
        out.append({
            "type": "forward_valuation_trim",
            "ticker": worst["ticker"],
            "current_weight": round(w, 6),
            "suggested_weight": round(max(0.0, w - 0.02), 6),
            "rationale": (
                f"Test reducing exposure to {worst['ticker']}: its forward building-block return "
                f"({worst['expected_return_pct']:.1f}%) is the lowest in the look-through and below the "
                f"{anchor:.1f}% mandate anchor (rich valuation or weak forward growth). Forward research "
                "hypothesis only; review tax, mandate, and suitability outside Helios."
            ),
        })
    return out


def _empty_result(generic: float, mandate_key: str) -> dict:
    return {
        "expected_return_pct": round(generic * 100.0, 2),
        "expected_return_covered_pct": round(generic * 100.0, 2),
        "generic_anchor_pct": round(generic * 100.0, 2),
        "uplift_vs_anchor_pct": 0.0,
        "coverage_pct": 0.0,
        "weighted_blocks_pct": {},
        "n_underlyings": 0,
        "n_usable": 0,
        "top_contributions": [],
        "mandate_anchor_pct": round(generic * 100.0, 2),
        "method": "building_block_cma",
        "assumptions_version": _macro.SECTOR_ANCHORS_AS_OF,
        "disclaimer": "No look-through composition available; falling back to the mandate anchor.",
    }

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


def holding_expected_return(ticker, weight_pct, asset_class, fundamentals,
                            mandate_key="balanced", horizon_years=REVERSION_HORIZON_Y) -> HoldingReturn:
    """Forward expected return for one underlying holding."""
    label = (asset_class or "").lower()
    is_equity = label.startswith("equity") or label in ("", "other")

    # Non-equity sleeve: anchor off asset class (we know the sleeve, not the name).
    if not is_equity:
        er = _macro.asset_class_return(asset_class)
        return HoldingReturn(ticker, weight_pct, er, "asset_class", True,
                             {"asset_class_anchor": round(er, 4)})

    if fundamentals is not None and getattr(fundamentals, "usable", False):
        dy = _clip(fundamentals.dividend_yield or 0.0, *_DY_CAP)
        g = _clip(fundamentals.earnings_growth if fundamentals.earnings_growth is not None else
                  _macro.sector_anchor(fundamentals.sector)["growth"], *_GROWTH_CAP)
        pe = fundamentals.forward_pe or fundamentals.trailing_pe
        rev = 0.0
        if pe and pe > 0:
            fair = _macro.sector_anchor(fundamentals.sector)["fair_pe"]
            rev = _clip(math.log(fair / pe) / horizon_years, *_REVERSION_CAP)
        er = _clip(dy + g + rev, *_ER_CAP)
        return HoldingReturn(ticker, weight_pct, er, "fundamentals", True, {
            "dividend_yield": round(dy, 4),
            "earnings_growth": round(g, 4),
            "valuation_reversion": round(rev, 4),
        })

    # Equity name with no usable fundamentals: defer to the generic anchor.
    return HoldingReturn(ticker, weight_pct, None, "generic", False,
                         {"reason": "no usable fundamentals"})


def aggregate(underlyings, fundamentals_map, mandate_key="balanced") -> dict:
    """Roll per-holding expected returns up to a model forward expected return.

    ``underlyings``: list of ``{ticker, weight_pct, asset_class}`` (look-through
    exposure, weights in percent). ``fundamentals_map``: ticker -> Fundamentals
    (missing/None allowed). Weight not covered by a usable estimate is filled
    with the mandate's generic anchor, and ``coverage_pct`` reports how much of
    the supplied weight had a real (fundamentals or asset-class) estimate.
    """
    generic = _mnd.anchor_return(mandate_key)
    total_w = sum(max(0.0, float(u.get("weight_pct") or 0.0)) for u in underlyings)
    if total_w <= 0:
        return _empty_result(generic, mandate_key)

    per, usable_w = [], 0.0
    mu_usable = 0.0
    agg_blocks = {"dividend_yield": 0.0, "earnings_growth": 0.0, "valuation_reversion": 0.0}
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
            {"ticker": h.ticker, "weight_pct": round(h.weight_pct, 2),
             "expected_return_pct": round((h.expected_return or generic) * 100.0, 2),
             "basis": h.basis}
            for h in sorted(per, key=lambda h: h.weight_pct, reverse=True)[:15]
        ],
        "mandate_anchor_pct": round(generic * 100.0, 2),
        "method": "building_block_cma",
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
        "disclaimer": "No look-through composition available; falling back to the mandate anchor.",
    }

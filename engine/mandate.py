"""Investment-model mandates.

A mandate captures what a client model is *for* (pure growth, income, CD
alternative, ...) as a set of INTENTIONAL numeric parameters. Every number here
traces to a documented rationale (see .design_spec.json / docs) so nothing in
the signal, forecast, or insight layers is a magic constant.

Cross-cutting market constants:
  RF   risk-free / CD benchmark yield (also used by indicators.sharpe)
  ERP  equity-risk premium for the long-run CAPM anchor — deliberately set
       below the ~5.5% historical realized premium so projections under-promise.
"""
from __future__ import annotations

import os

RF = float(os.environ.get("HELIOS_RF", "0.02"))
ERP = 0.045

# Base composite-signal weights (signals.WEIGHTS). Each mandate tilts these;
# all preset weight sets are pre-normalized to sum to 1.0.
BASE_WEIGHTS = {"trend": 0.30, "momentum": 0.20, "forecast": 0.30, "sentiment": 0.20}


# key -> all parameters. growth_orientation drives the CAPM anchor; the weight
# tilts and risk budgets encode how each mandate should be judged and traded.
MANDATES: dict[str, dict] = {
    "pure_growth": {
        "label": "Pure Growth",
        "description": "Maximize long-horizon capital appreciation; tolerates "
                       "equity-or-higher volatility and deep multi-year drawdowns. "
                       "Income is irrelevant. Young accumulator, 5y+ horizon.",
        "target_vol_pct": 22.0,
        "max_drawdown_tolerance_pct": 40.0,
        "growth_orientation": 1.0,
        "income_orientation": 0.0,
        "single_name_cap": 0.35,
        "typical_horizons": ["1Y", "3Y", "5Y"],
        "weights": {"trend": 0.35, "momentum": 0.25, "forecast": 0.30, "sentiment": 0.10},
    },
    "balanced": {
        "label": "Balanced",
        "description": "Roughly equal growth and income, classic 60/40 risk budget. "
                       "The neutral anchor — uses the engine's base weights unchanged.",
        "target_vol_pct": 12.0,
        "max_drawdown_tolerance_pct": 22.0,
        "growth_orientation": 0.6,
        "income_orientation": 0.4,
        "single_name_cap": 0.35,
        "typical_horizons": ["6M", "1Y", "3Y"],
        "weights": {"trend": 0.30, "momentum": 0.20, "forecast": 0.30, "sentiment": 0.20},
    },
    "income": {
        "label": "Income Seeking",
        "description": "Prioritize durable distributions/yield with moderate price "
                       "risk; appreciation is a bonus. Retiree drawing ~4%.",
        "target_vol_pct": 10.0,
        "max_drawdown_tolerance_pct": 18.0,
        "growth_orientation": 0.25,
        "income_orientation": 0.85,
        "single_name_cap": 0.35,
        "typical_horizons": ["6M", "1Y", "3Y"],
        "weights": {"trend": 0.30, "momentum": 0.10, "forecast": 0.25, "sentiment": 0.35},
    },
    "capital_preservation": {
        "label": "Capital Preservation",
        "description": "Protect principal first; modest growth is a bonus. Money the "
                       "client cannot afford to lose.",
        "target_vol_pct": 6.0,
        "max_drawdown_tolerance_pct": 8.0,
        "growth_orientation": 0.10,
        "income_orientation": 0.50,
        "single_name_cap": 0.15,
        "typical_horizons": ["6M", "1Y"],
        "weights": {"trend": 0.40, "momentum": 0.10, "forecast": 0.20, "sentiment": 0.30},
    },
    "cd_alternative": {
        "label": "CD / Cash Alternative",
        "description": "Beat a CD/T-bill yield with near-cash stability. Benchmark is "
                       "the risk-free rate, not equities. Any real drawdown defeats it.",
        "target_vol_pct": 4.0,
        "max_drawdown_tolerance_pct": 4.0,
        "growth_orientation": 0.05,
        "income_orientation": 0.80,
        "single_name_cap": 0.15,
        "typical_horizons": ["6M", "1Y"],
        "weights": {"trend": 0.35, "momentum": 0.05, "forecast": 0.25, "sentiment": 0.35},
    },
}

DEFAULT = "balanced"


def get(key: str) -> dict:
    return MANDATES.get((key or "").lower(), MANDATES[DEFAULT])


def key_or_default(key: str) -> str:
    return (key or "").lower() if (key or "").lower() in MANDATES else DEFAULT


def weights(key: str) -> dict:
    """Effective composite-signal weights for the mandate (normalized to 1.0)."""
    w = dict(get(key)["weights"])
    s = sum(w.values()) or 1.0
    return {k: v / s for k, v in w.items()}


# Share of the composite the FUNDAMENTALS component takes when usable
# fundamentals exist (the four technical weights scale down by 1-share, so
# every preset stays normalized). Rationale: income/preservation mandates are
# valuation-and-yield judgements first; pure growth still leans on price
# confirmation. When fundamentals are unavailable the composite falls back to
# the pure-technical weights and says so — the share is never spent on a
# fabricated number.
_FUNDAMENTALS_SHARE = {
    "pure_growth": 0.25,
    "balanced": 0.30,
    "income": 0.35,
    "capital_preservation": 0.30,
    "cd_alternative": 0.25,
}


def fundamentals_share(key: str) -> float:
    return _FUNDAMENTALS_SHARE.get(key_or_default(key), 0.30)


def anchor_return(key: str) -> float:
    """Long-run CAPM anchor: rf + ERP * growth_orientation."""
    return RF + ERP * get(key)["growth_orientation"]


def mandate_from_profile(vol_annual_pct: float | None = None) -> str:
    """Infer the mandate whose risk budget matches an instrument's OWN realized
    volatility — the honest form of 'judge the fund on its own risk profile,
    not the portfolio it sits in'. Picks the mandate whose target_vol_pct is
    nearest the instrument's annualized vol (pure_growth 22 / balanced 12 /
    income 10 / capital_preservation 6 / cd_alternative 4); ties resolve toward
    the higher-risk mandate so a borderline holding is never under-stated.
    Falls back to the default when vol is unavailable (e.g. a brand-new fund).
    """
    if vol_annual_pct is None or vol_annual_pct <= 0:
        return DEFAULT
    return min(
        MANDATES,
        key=lambda k: (abs(MANDATES[k]["target_vol_pct"] - vol_annual_pct),
                       -MANDATES[k]["target_vol_pct"]),
    )


def blended_anchor(key: str, cma_return: float | None = None, coverage: float = 0.0) -> float:
    """Forward anchor that blends the generic CAPM anchor with a holdings-derived
    CMA expected return by look-through coverage.

    Backward-compatible seam for the forward spine: with ``cma_return=None`` or
    zero coverage this returns the unchanged generic anchor, so existing callers
    (and forecast_long) behave identically until a CMA estimate is supplied.
    ``coverage`` is the fraction (0..1) of model weight the CMA actually covers.
    """
    generic = anchor_return(key)
    if cma_return is None or coverage <= 0:
        return generic
    c = max(0.0, min(coverage, 1.0))
    return c * cma_return + (1.0 - c) * generic


def target_return(key: str) -> float:
    """Annualized return the mandate is expected to clear (for prob_meets_mandate)."""
    if key_or_default(key) == "cd_alternative":
        return RF
    return anchor_return(key)


def income_floor(key: str) -> float | None:
    k = key_or_default(key)
    if k in ("cd_alternative", "capital_preservation"):
        return RF
    if k == "income":
        return 0.035
    return None  # not income-oriented enough to gate


def hhi_thresholds(key: str) -> tuple[float, float]:
    """(moderate, high) HHI lines; tightened for low-risk mandates."""
    if key_or_default(key) in ("capital_preservation", "cd_alternative"):
        return 0.15, 0.20
    return 0.15, 0.25


def public_list() -> list[dict]:
    """Mandate presets for the UI dropdown (key + human-facing fields)."""
    out = []
    for k, m in MANDATES.items():
        out.append({
            "key": k,
            "label": m["label"],
            "description": m["description"],
            "target_vol_pct": m["target_vol_pct"],
            "max_drawdown_tolerance_pct": m["max_drawdown_tolerance_pct"],
            "typical_horizons": m["typical_horizons"],
            "target_return_pct": round(target_return(k) * 100, 2),
        })
    return out

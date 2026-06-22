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


def anchor_return(key: str) -> float:
    """Long-run CAPM anchor: rf + ERP * growth_orientation."""
    return RF + ERP * get(key)["growth_orientation"]


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

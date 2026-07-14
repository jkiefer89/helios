"""The single disclosed trading-cost assumption.

Paper evidence surfaces (Evidence Lab, Signal Journal, Decision Journal,
reports) measure RAW forward returns — a paper signal genuinely incurs no
costs, so the stored rows stay gross and immutable. The dishonesty the
2026-07-12 review flagged was the unlabeled word "alpha": readers assumed
net. Fix: every surface labels alpha gross-of-costs, and aggregate views
show a companion figure net of THIS default, computed at presentation time
(never written into journal rows).

Strategy Lab, backtest, and the API default all import
DEFAULT_COST_BPS_PER_SIDE so the surfaces cannot drift; Evidence Lab,
Signal Journal, Decision Journal, model validation, and report exports
attach the net companion + basis labels at presentation time.
"""
from __future__ import annotations

from typing import Any

# Per-side cost in basis points (spread + fees, no impact term). Matches the
# Strategy Lab default that has always been disclosed in its assumptions.
DEFAULT_COST_BPS_PER_SIDE = 5.0
ROUND_TRIP_COST_PCT = 2.0 * DEFAULT_COST_BPS_PER_SIDE / 100.0   # 0.10%

GROSS_LABEL = "gross of trading costs (spread, impact, fees)"
NET_ASSUMPTION_LABEL = (
    f"net of the default {DEFAULT_COST_BPS_PER_SIDE:.0f} bps/side round trip "
    "(presentation-level assumption — stored measurements stay gross)")


def net_of_default_costs(gross_pct: float | None) -> float | None:
    """Gross return/alpha minus one default round trip, or None passthrough."""
    if gross_pct is None:
        return None
    return round(float(gross_pct) - ROUND_TRIP_COST_PCT, 4)


def directional_alpha(action: str, alpha_pct: float | None) -> float | None:
    """Normalize benchmark alpha into the economic direction of the call."""
    if alpha_pct is None:
        return None
    label = str(action or "").upper()
    if label in {"BUY", "ADD", "OVERWEIGHT"}:
        return float(alpha_pct)
    if label in {"SELL", "TRIM", "REDUCE", "UNDERWEIGHT"}:
        return -float(alpha_pct)
    return None


def net_directional_alpha_default(action: str, alpha_pct: float | None) -> float | None:
    directional = directional_alpha(action, alpha_pct)
    return net_of_default_costs(directional)


IMPLEMENTATION_COST_FIELDS = (
    "commission_bps_per_side",
    "spread_bps_per_side",
    "slippage_bps_per_side",
    "market_impact_bps_per_side",
    "tax_drag_bps",
    "idle_cash_pct",
)


def implementation_costs(
    assumptions: dict[str, Any],
    *,
    benchmark_return_pct: float | None = None,
) -> dict[str, Any]:
    """Full proposed/paper implementation drag in percentage points."""
    values = {field: max(0.0, float(assumptions[field])) for field in IMPLEMENTATION_COST_FIELDS}
    trading_bps = 2.0 * sum(values[field] for field in IMPLEMENTATION_COST_FIELDS[:4])
    tax_drag_pct = values["tax_drag_bps"] / 100.0
    idle_cash_drag_pct = (
        max(0.0, float(benchmark_return_pct or 0.0))
        * values["idle_cash_pct"] / 100.0
    )
    total_pct = trading_bps / 100.0 + tax_drag_pct + idle_cash_drag_pct
    return {
        **values,
        "round_trip_trading_bps": round(trading_bps, 4),
        "trading_drag_pct": round(trading_bps / 100.0, 6),
        "tax_drag_pct": round(tax_drag_pct, 6),
        "idle_cash_drag_pct": round(idle_cash_drag_pct, 6),
        "total_drag_pct": round(total_pct, 6),
        "basis": "fees + spread + slippage + market impact + tax drag + idle-cash drag",
    }


def net_directional_alpha(
    action: str,
    alpha_pct: float | None,
    assumptions: dict[str, Any],
    *,
    benchmark_return_pct: float | None = None,
) -> float | None:
    if alpha_pct is None or str(action).upper() not in {"BUY", "SELL"}:
        return None
    directional = directional_alpha(action, alpha_pct)
    if directional is None:
        return None
    drag = implementation_costs(
        assumptions, benchmark_return_pct=benchmark_return_pct,
    )["total_drag_pct"]
    return round(directional - float(drag), 6)

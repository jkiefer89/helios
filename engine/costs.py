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

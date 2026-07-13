"""Historical validation of the signal rule.

Thin compatibility wrapper over :func:`engine.strategy.analyze_strategy` so
position construction, trading costs, and trade statistics have a single
implementation. Preserves the legacy backtest payload shape used by
/api/analyze and /api/model/analyze: equity curves plus strategy/benchmark
stats, trade count, win rate, and market exposure.
"""
from __future__ import annotations

import pandas as pd

from . import costs, strategy


def run(close: pd.Series, entry: float = 0.15, exit: float = -0.05, cost_bps: float = costs.DEFAULT_COST_BPS_PER_SIDE) -> dict:
    """Backtest the causal signal rule against buy-and-hold on ``close``.

    Delegates to strategy.analyze_strategy (the single source of truth) and
    reshapes its result. Raises ValueError when fewer than 60 rows of history
    are available, matching the strategy engine's minimum.
    """
    result = strategy.analyze_strategy(close, cost_bps=cost_bps, entry=entry, exit=exit)
    trade_stats = result["trade_stats"]
    return {
        "dates": result["dates"],
        "strategy_curve": result["strategy_curve"],
        "benchmark_curve": result["benchmark_curve"],
        "strategy": result["strategy"],
        "benchmark": result["benchmark"],
        "n_trades": trade_stats["n_trades"],
        "win_rate_pct": trade_stats["win_rate_pct"],
        "exposure_pct": trade_stats["exposure_pct"],
    }

"""Historical validation of the signal rule.

Strategy: hold the asset when the (causal) signal score is above an entry
threshold, otherwise sit in cash. Compared against buy-and-hold. Reports an
equity curve plus the usual performance stats and trade win rate. This is what
turns the trade signals from an assertion into something measurable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind
from . import signals as sig

TRADING_DAYS = 252


def run(close: pd.Series, entry: float = 0.15, exit: float = -0.05, cost_bps: float = 5.0) -> dict:
    close = close.dropna()
    score = sig.historical_signals(close)
    rets = close.pct_change().fillna(0.0)

    # Position: 1 when score crosses above entry, back to 0 when it falls below exit.
    pos = np.zeros(len(close))
    holding = False
    for i in range(len(close)):
        s = score.iloc[i]
        if not holding and s > entry:
            holding = True
        elif holding and s < exit:
            holding = False
        pos[i] = 1.0 if holding else 0.0

    position = pd.Series(pos, index=close.index).shift(1).fillna(0.0)  # act next day
    trades = position.diff().abs().fillna(0.0)
    cost = trades * (cost_bps / 10000.0)
    strat_rets = position * rets - cost

    strat_curve = (1 + strat_rets).cumprod()
    bench_curve = (1 + rets).cumprod()

    def stats(r: pd.Series, curve: pd.Series) -> dict:
        vol = r.std() * np.sqrt(TRADING_DAYS)
        return {
            "total_return_pct": float(curve.iloc[-1] - 1) * 100,
            "annual_return_pct": float((1 + r.mean()) ** TRADING_DAYS - 1) * 100,
            "annual_vol_pct": float(vol) * 100,
            "sharpe": float((r.mean() * TRADING_DAYS - 0.02) / vol) if vol > 1e-9 else 0.0,
            "max_drawdown_pct": float(((curve - curve.cummax()) / curve.cummax()).min()) * 100,
        }

    n_trades = int((trades > 0).sum())
    # Win rate over completed holding episodes.
    wins, total = _episode_wins(position, rets)

    # Downsample equity curves for transport (keep it light for the chart).
    idx = _downsample_index(len(close), 400)
    dates = [close.index[i].strftime("%Y-%m-%d") for i in idx]
    return {
        "dates": dates,
        "strategy_curve": [float(strat_curve.iloc[i]) for i in idx],
        "benchmark_curve": [float(bench_curve.iloc[i]) for i in idx],
        "strategy": stats(strat_rets, strat_curve),
        "benchmark": stats(rets, bench_curve),
        "n_trades": n_trades,
        "win_rate_pct": float(wins / total * 100) if total else 0.0,
        "exposure_pct": float(position.mean()) * 100,
    }


def _episode_wins(position: pd.Series, rets: pd.Series):
    wins = total = 0
    in_pos = False
    cum = 1.0
    for p, r in zip(position.values, rets.values):
        if p > 0 and not in_pos:
            in_pos, cum = True, 1.0
        if in_pos:
            cum *= 1 + r
        if p == 0 and in_pos:
            in_pos = False
            total += 1
            wins += 1 if cum > 1 else 0
    if in_pos:
        total += 1
        wins += 1 if cum > 1 else 0
    return wins, total


def _downsample_index(n: int, target: int) -> list[int]:
    if n <= target:
        return list(range(n))
    step = n / target
    idx = sorted({int(i * step) for i in range(target)} | {n - 1})
    return idx

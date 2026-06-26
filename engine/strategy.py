"""Strategy Lab evidence for causal signal backtests.

The strategy engine uses the existing historical signal score, converts it into
long-or-cash exposure, and shifts exposure by one session before applying
returns. That shift is the no-lookahead guard: a signal observed on day T can
only affect day T+1 performance.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import signals

TRADING_DAYS = 252


def analyze_strategy(
    close: pd.Series,
    cost_bps: float = 5.0,
    slippage_bps: float = 0.0,
    start: str | None = None,
    end: str | None = None,
    entry: float = 0.15,
    exit: float = -0.05,
) -> dict:
    px = _window(close, start, end)
    if len(px) < 60:
        raise ValueError("Need at least 60 rows of history for Strategy Lab analysis.")

    score = signals.historical_signals(px)
    raw_position = _position_from_scores(score, entry, exit)
    position = raw_position.shift(1).fillna(0.0)
    rets = px.pct_change().fillna(0.0)
    turnover = position.diff().abs().fillna(0.0)
    all_in_cost = max(0.0, float(cost_bps)) + max(0.0, float(slippage_bps))
    strategy_rets = position * rets - turnover * (all_in_cost / 10000.0)
    benchmark_rets = rets

    strategy_curve = (1.0 + strategy_rets).cumprod()
    benchmark_curve = (1.0 + benchmark_rets).cumprod()
    dd = _drawdown(strategy_curve)
    rolling = _rolling_sharpe(strategy_rets)
    episodes = _trade_episodes(position, rets)
    idx = _downsample_index(len(px), 420)
    dates = [px.index[i].strftime("%Y-%m-%d") for i in idx]
    trade_stats = _trade_stats(episodes, position, turnover)

    return {
        "dates": dates,
        "score": [round(float(score.iloc[i]), 4) for i in idx],
        "position": [int(position.iloc[i]) for i in idx],
        "strategy_curve": [round(float(strategy_curve.iloc[i]), 6) for i in idx],
        "benchmark_curve": [round(float(benchmark_curve.iloc[i]), 6) for i in idx],
        "drawdown_curve": [round(float(dd.iloc[i] * 100), 4) for i in idx],
        "rolling_sharpe_curve": [_finite_or_none(rolling.iloc[i]) for i in idx],
        "strategy": _metrics(strategy_rets, strategy_curve),
        "benchmark": _metrics(benchmark_rets, benchmark_curve),
        "trade_stats": trade_stats,
        "beat_benchmark": bool(strategy_curve.iloc[-1] > benchmark_curve.iloc[-1]),
        "assumptions": {
            "entry_threshold": entry,
            "exit_threshold": exit,
            "cost_bps": float(cost_bps),
            "slippage_bps": float(slippage_bps),
            "round_trip_cost_bps": float(all_in_cost),
            "cash_return_assumption_pct": 0.0,
        },
        "methodology": {
            "no_lookahead": True,
            "position_rule": "Signal is observed at close and applied starting the next session.",
            "benchmark": "Buy-and-hold on the same price series.",
            "costs": "Costs and slippage are deducted whenever exposure changes.",
            "analysis_only": True,
        },
    }


def _window(close: pd.Series, start: str | None, end: str | None) -> pd.Series:
    px = close.dropna().astype(float).sort_index()
    if start:
        px = px.loc[pd.to_datetime(start):]
    if end:
        px = px.loc[:pd.to_datetime(end)]
    return px


def _position_from_scores(score: pd.Series, entry: float, exit: float) -> pd.Series:
    values = []
    holding = False
    for s in score:
        if not holding and s > entry:
            holding = True
        elif holding and s < exit:
            holding = False
        values.append(1.0 if holding else 0.0)
    return pd.Series(values, index=score.index)


def _metrics(rets: pd.Series, curve: pd.Series) -> dict:
    total = float(curve.iloc[-1] - 1.0)
    years = max(len(rets) / TRADING_DAYS, 1 / TRADING_DAYS)
    cagr = float(curve.iloc[-1] ** (1.0 / years) - 1.0) if curve.iloc[-1] > 0 else -1.0
    vol = float(rets.std() * np.sqrt(TRADING_DAYS))
    downside = float(rets[rets < 0].std() * np.sqrt(TRADING_DAYS)) if (rets < 0).any() else 0.0
    ann_ret = float((1.0 + rets.mean()) ** TRADING_DAYS - 1.0)
    sharpe = (rets.mean() * TRADING_DAYS - 0.02) / vol if vol > 1e-9 else 0.0
    sortino = (rets.mean() * TRADING_DAYS - 0.02) / downside if downside > 1e-9 else 0.0
    max_dd = float(_drawdown(curve).min())
    calmar = cagr / abs(max_dd) if max_dd < -1e-9 else 0.0
    return {
        "total_return_pct": total * 100,
        "cagr_pct": cagr * 100,
        "annual_return_pct": ann_ret * 100,
        "annual_vol_pct": vol * 100,
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "calmar": float(calmar),
        "max_drawdown_pct": max_dd * 100,
    }


def _drawdown(curve: pd.Series) -> pd.Series:
    return (curve / curve.cummax() - 1.0).fillna(0.0)


def _rolling_sharpe(rets: pd.Series, window: int = 63) -> pd.Series:
    mean = rets.rolling(window).mean() * TRADING_DAYS
    vol = rets.rolling(window).std() * np.sqrt(TRADING_DAYS)
    return ((mean - 0.02) / vol.replace(0.0, np.nan)).replace([np.inf, -np.inf], np.nan)


def _trade_episodes(position: pd.Series, rets: pd.Series) -> list[float]:
    episodes: list[float] = []
    in_pos = False
    cumulative = 1.0
    for p, r in zip(position.values, rets.values):
        if p > 0 and not in_pos:
            in_pos = True
            cumulative = 1.0
        if in_pos:
            cumulative *= 1.0 + float(r)
        if p == 0 and in_pos:
            in_pos = False
            episodes.append(cumulative - 1.0)
    if in_pos:
        episodes.append(cumulative - 1.0)
    return episodes


def _trade_stats(episodes: list[float], position: pd.Series, turnover: pd.Series) -> dict:
    wins = [r for r in episodes if r > 0]
    losses = [r for r in episodes if r <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "n_trades": int((turnover > 0).sum()),
        "completed_trades": len(episodes),
        "win_rate_pct": (len(wins) / len(episodes) * 100) if episodes else 0.0,
        "avg_win_pct": (np.mean(wins) * 100) if wins else 0.0,
        "avg_loss_pct": (np.mean(losses) * 100) if losses else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 1e-12 else (float("inf") if gross_win > 0 else 0.0),
        "exposure_pct": float(position.mean() * 100),
        "turnover": float(turnover.sum()),
        "current_position": "long" if float(position.iloc[-1]) > 0 else "cash",
    }


def _downsample_index(n: int, target: int) -> list[int]:
    if n <= target:
        return list(range(n))
    step = n / target
    return sorted({int(i * step) for i in range(target)} | {n - 1})


def _finite_or_none(value: float) -> float | None:
    return None if not np.isfinite(value) else round(float(value), 4)


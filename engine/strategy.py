"""Strategy Lab evidence for causal signal backtests.

The strategy engine uses the existing historical signal score, converts it into
long-or-cash exposure, and shifts exposure by one session before applying
returns. That shift is the no-lookahead guard: a signal observed on day T can
only affect day T+1 performance.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import costs, mandate, signals

TRADING_DAYS = 252


def analyze_strategy(
    close: pd.Series,
    cost_bps: float = costs.DEFAULT_COST_BPS_PER_SIDE,
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
    terminal_liquidation = bool(float(position.iloc[-1]) > 0.0)
    if terminal_liquidation:
        # Backtest evidence is evaluated on a fully liquidated book so every
        # episode carries both entry and exit costs. The signal position is
        # retained separately in trade_stats for an honest as-of view.
        turnover.iloc[-1] += float(position.iloc[-1])
    all_in_cost = max(0.0, float(cost_bps)) + max(0.0, float(slippage_bps))
    strategy_rets = position * rets - turnover * (all_in_cost / 10000.0)
    benchmark_rets = rets

    strategy_curve = (1.0 + strategy_rets).cumprod()
    benchmark_curve = (1.0 + benchmark_rets).cumprod()
    dd = _drawdown(strategy_curve)
    rolling = _rolling_sharpe(strategy_rets)
    # Episodes compound NET strategy returns so per-trade stats reconcile with
    # the (net) equity curve rather than overstating raw asset moves.
    episodes, open_episode = _trade_episodes(
        position,
        strategy_rets,
        close_terminal=terminal_liquidation,
        include_exit_return=True,
    )
    idx = _downsample_index(len(px), 420)
    dates = [px.index[i].strftime("%Y-%m-%d") for i in idx]
    trade_stats = _trade_stats(
        episodes,
        open_episode,
        position,
        turnover,
        terminal_liquidated=terminal_liquidation,
    )

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
            "per_side_cost_bps": float(all_in_cost),
            "round_trip_cost_bps": float(2.0 * all_in_cost),
            "risk_free_rate_pct": float(mandate.RF * 100),
            "cash_return_assumption_pct": 0.0,
            "terminal_liquidation": terminal_liquidation,
        },
        "methodology": {
            "no_lookahead": True,
            "position_rule": "Signal is observed at close and applied starting the next session.",
            "benchmark": "Buy-and-hold on the same price series.",
            "costs": (
                "Costs and slippage are deducted whenever exposure changes; "
                "an open end-of-window signal is liquidated for evaluation."
            ),
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
    # Geometric, not arithmetic-mean compounding (volatility-drag bug — same
    # review finding as indicators.annualized_return); cagr is 3 lines up.
    ann_ret = cagr
    sharpe = (rets.mean() * TRADING_DAYS - mandate.RF) / vol if vol > 1e-9 else 0.0
    sortino = (rets.mean() * TRADING_DAYS - mandate.RF) / downside if downside > 1e-9 else 0.0
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
    # Windows with no real variance (flat or all-cash stretches) leave a
    # float-noise std ~1e-8 that blows the ratio up into the millions; Sharpe
    # is undefined there, so emit NaN. 1e-4 annualized sits orders of magnitude
    # between that noise and the smallest genuine trading-window vol.
    vol = vol.where(vol > 1e-4)
    return ((mean - mandate.RF) / vol).replace([np.inf, -np.inf], np.nan)


def _trade_episodes(
    position: pd.Series,
    rets: pd.Series,
    *,
    close_terminal: bool = False,
    include_exit_return: bool = False,
) -> tuple[list[float], float | None]:
    """Compound per-episode returns over exactly the exposure days.

    Returns (completed episodes, still-open episode or None). The flat day the
    exit lands on may carry an exit-side trading cost in an already-net return
    series. ``include_exit_return`` is explicit so callers passing raw asset
    returns never attribute a flat post-exit market move to the prior trade.
    """
    episodes: list[float] = []
    in_pos = False
    cumulative = 1.0
    for p, r in zip(position.values, rets.values):
        if p > 0:
            if not in_pos:
                in_pos = True
                cumulative = 1.0
            cumulative *= 1.0 + float(r)
        elif in_pos:
            if include_exit_return:
                cumulative *= 1.0 + float(r)
            in_pos = False
            episodes.append(cumulative - 1.0)
    if in_pos and close_terminal:
        episodes.append(cumulative - 1.0)
        in_pos = False
    return episodes, (cumulative - 1.0 if in_pos else None)


def _trade_stats(episodes: list[float], open_episode: float | None,
                 position: pd.Series, turnover: pd.Series,
                 *, terminal_liquidated: bool = False) -> dict:
    wins = [r for r in episodes if r > 0]
    losses = [r for r in episodes if r <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "n_trades": int((position.diff() > 0).sum()),
        "n_position_changes": int((turnover > 0).sum()),
        "completed_trades": len(episodes),
        "open_position_episode_return_pct": (open_episode * 100) if open_episode is not None else None,
        "win_rate_pct": (len(wins) / len(episodes) * 100) if episodes else 0.0,
        "avg_win_pct": (np.mean(wins) * 100) if wins else 0.0,
        "avg_loss_pct": (np.mean(losses) * 100) if losses else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 1e-12 else (float("inf") if gross_win > 0 else 0.0),
        "exposure_pct": float(position.mean() * 100),
        "turnover": float(turnover.sum()),
        "current_position": "long" if float(position.iloc[-1]) > 0 else "cash",
        "evaluation_end_position": "cash" if terminal_liquidated else (
            "long" if float(position.iloc[-1]) > 0 else "cash"
        ),
        "terminal_liquidation_applied": bool(terminal_liquidated),
    }


def _downsample_index(n: int, target: int) -> list[int]:
    if n <= target:
        return list(range(n))
    step = n / target
    return sorted({int(i * step) for i in range(target)} | {n - 1})


def _finite_or_none(value: float) -> float | None:
    return None if not np.isfinite(value) else round(float(value), 4)

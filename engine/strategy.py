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
OOS_WARMUP_SESSIONS = 252
OOS_FOLD_SESSIONS = 21
ENTRY_SENSITIVITY = (0.10, 0.15, 0.20)
EXIT_SENSITIVITY = (-0.10, -0.05, 0.0)


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

    path = _strategy_path(px, cost_bps, slippage_bps, entry, exit)
    score = path["score"]
    raw_position = path["raw_position"]
    position = path["position"]
    turnover = path["turnover"]
    strategy_rets = path["strategy_rets"]
    benchmark_rets = path["benchmark_rets"]
    strategy_curve = path["strategy_curve"]
    benchmark_curve = path["benchmark_curve"]
    dd = path["drawdown"]
    rolling = path["rolling_sharpe"]
    terminal_liquidation = path["terminal_liquidation"]
    all_in_cost = path["all_in_cost_bps"]
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
        "current_signal": _current_signal(
            score, raw_position, position, entry=entry, exit=exit,
        ),
        "path_evidence": _path_evidence(
            strategy_curve, dd, rolling, position, strategy_rets,
            terminal_liquidated=terminal_liquidation,
        ),
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


def analyze_oos_evidence(
    close: pd.Series,
    cost_bps: float = costs.DEFAULT_COST_BPS_PER_SIDE,
    slippage_bps: float = 0.0,
    start: str | None = None,
    end: str | None = None,
    entry: float = 0.15,
    exit: float = -0.05,
    warmup_sessions: int = OOS_WARMUP_SESSIONS,
    fold_sessions: int = OOS_FOLD_SESSIONS,
) -> dict:
    """Chronological, diagnostic-only evidence for the frozen threshold rule.

    The primary thresholds are never selected on the test rows. A fixed 3x3
    family is reported only to show whether the conclusion is fragile nearby.
    Signals are calculated on the continuous prefix and exposure is shifted one
    session exactly as in :func:`analyze_strategy`.
    """
    px = _window(close, start, end)
    warmup = max(60, int(warmup_sessions))
    fold = max(5, int(fold_sessions))
    complete_folds = max(0, (len(px) - warmup) // fold)
    policy = {
        "warmup_sessions": warmup,
        "fold_sessions": fold,
        "non_overlapping": True,
        "primary_entry_threshold": float(entry),
        "primary_exit_threshold": float(exit),
        "primary_locked_before_evaluation": True,
        "selected_on_test": False,
        "cost_bps": float(cost_bps),
        "slippage_bps": float(slippage_bps),
    }
    if complete_folds < 1:
        return {
            "status": "insufficient_data",
            "policy": policy,
            "available_sessions": int(len(px)),
            "required_sessions": warmup + fold,
            "fold_count": 0,
            "reason": (
                f"Need at least {warmup + fold} sessions for a {warmup}-session "
                f"warmup plus one complete {fold}-session out-of-sample fold."
            ),
        }

    evaluation_end = warmup + complete_folds * fold
    evaluated = px.iloc[:evaluation_end]
    variants = []
    baseline_folds: list[dict] = []
    entry_family = tuple(sorted(set((*ENTRY_SENSITIVITY, float(entry)))))
    exit_family = tuple(sorted(set((*EXIT_SENSITIVITY, float(exit)))))
    for candidate_entry in entry_family:
        for candidate_exit in exit_family:
            path = _strategy_path(
                evaluated, cost_bps, slippage_bps, candidate_entry, candidate_exit,
                liquidate_terminal=False,
            )
            folds = _oos_fold_rows(path, warmup=warmup, fold=fold)
            oos_rets = path["strategy_rets"].iloc[warmup:evaluation_end]
            benchmark_rets = path["benchmark_rets"].iloc[warmup:evaluation_end]
            strategy_curve = (1.0 + oos_rets).cumprod()
            benchmark_curve = (1.0 + benchmark_rets).cumprod()
            strategy_metrics = _metrics(oos_rets, strategy_curve)
            benchmark_metrics = _metrics(benchmark_rets, benchmark_curve)
            net_excess = (
                strategy_metrics["total_return_pct"]
                - benchmark_metrics["total_return_pct"]
            )
            variant = {
                "entry_threshold": float(candidate_entry),
                "exit_threshold": float(candidate_exit),
                "primary": bool(
                    abs(candidate_entry - entry) < 1e-12
                    and abs(candidate_exit - exit) < 1e-12
                ),
                "fold_count": len(folds),
                "oos_sessions": int(len(oos_rets)),
                "strategy_return_pct": round(float(strategy_metrics["total_return_pct"]), 4),
                "benchmark_return_pct": round(float(benchmark_metrics["total_return_pct"]), 4),
                "net_excess_return_pct": round(float(net_excess), 4),
                "sharpe": round(float(strategy_metrics["sharpe"]), 4),
                "max_drawdown_pct": round(float(strategy_metrics["max_drawdown_pct"]), 4),
                "profitable_fold_pct": _percentage(
                    sum(1 for row in folds if row["strategy_return_pct"] > 0), len(folds)
                ),
                "benchmark_beating_fold_pct": _percentage(
                    sum(1 for row in folds if row["net_excess_return_pct"] > 0), len(folds)
                ),
                "worst_fold_excess_pct": round(
                    min(row["net_excess_return_pct"] for row in folds), 4
                ),
            }
            variants.append(variant)
            if variant["primary"]:
                baseline_folds = folds

    primary = next(row for row in variants if row["primary"])
    ranked = sorted(
        variants,
        key=lambda row: row["net_excess_return_pct"],
        reverse=True,
    )
    return {
        "status": "ok",
        "policy": policy,
        "evaluation_start": str(evaluated.index[warmup].date()),
        "evaluation_end": str(evaluated.index[evaluation_end - 1].date()),
        "fold_count": complete_folds,
        "oos_sessions": complete_folds * fold,
        "primary": primary,
        "folds": baseline_folds,
        "sensitivity": {
            "diagnostic_only": True,
            "winner_selected": False,
            "variant_count": len(variants),
            "primary_rank_by_net_excess": ranked.index(primary) + 1,
            "positive_excess_variant_count": sum(
                1 for row in variants if row["net_excess_return_pct"] > 0
            ),
            "net_excess_range_pct": [
                round(min(row["net_excess_return_pct"] for row in variants), 4),
                round(max(row["net_excess_return_pct"] for row in variants), 4),
            ],
            "variants": variants,
            "basis": (
                "Fixed 3x3 neighboring threshold family evaluated on the same "
                "chronological folds. No variant is promoted or substituted for the "
                "pre-registered 0.15/-0.05 primary rule."
            ),
        },
        "methodology": {
            "no_lookahead": True,
            "continuous_position_path": True,
            "fold_boundaries_reset_position": False,
            "partial_tail_excluded": int(len(px) - evaluation_end),
            "analysis_only": True,
        },
    }


def _strategy_path(
    px: pd.Series,
    cost_bps: float,
    slippage_bps: float,
    entry: float,
    exit: float,
    *,
    liquidate_terminal: bool = True,
) -> dict:
    score = signals.historical_signals(px)
    raw_position = _position_from_scores(score, entry, exit)
    position = raw_position.shift(1).fillna(0.0)
    benchmark_rets = px.pct_change().fillna(0.0)
    turnover = position.diff().abs().fillna(0.0)
    terminal_liquidation = bool(liquidate_terminal and float(position.iloc[-1]) > 0.0)
    if terminal_liquidation:
        # Evaluation closes the final episode, but the current signal/position
        # remains separately reported as-of the last observed close.
        turnover.iloc[-1] += float(position.iloc[-1])
    all_in_cost = max(0.0, float(cost_bps)) + max(0.0, float(slippage_bps))
    strategy_rets = position * benchmark_rets - turnover * (all_in_cost / 10000.0)
    strategy_curve = (1.0 + strategy_rets).cumprod()
    benchmark_curve = (1.0 + benchmark_rets).cumprod()
    return {
        "score": score,
        "raw_position": raw_position,
        "position": position,
        "benchmark_rets": benchmark_rets,
        "turnover": turnover,
        "strategy_rets": strategy_rets,
        "strategy_curve": strategy_curve,
        "benchmark_curve": benchmark_curve,
        "drawdown": _drawdown(strategy_curve),
        "rolling_sharpe": _rolling_sharpe(strategy_rets),
        "terminal_liquidation": terminal_liquidation,
        "all_in_cost_bps": all_in_cost,
    }


def _current_signal(
    score: pd.Series,
    raw_position: pd.Series,
    position: pd.Series,
    *,
    entry: float,
    exit: float,
) -> dict:
    current_state = "long" if float(raw_position.iloc[-1]) > 0 else "cash"
    prior_state = (
        "long" if len(raw_position) > 1 and float(raw_position.iloc[-2]) > 0 else "cash"
    )
    if current_state == "long" and prior_state == "cash":
        action = "ENTER_LONG"
    elif current_state == "cash" and prior_state == "long":
        action = "EXIT_TO_CASH"
    elif current_state == "long":
        action = "MAINTAIN_LONG"
    else:
        action = "STAY_IN_CASH"
    return {
        "framework": "strategy_threshold_state",
        "action_label": action,
        "signal_state": current_state,
        "position_on_last_observed_session": (
            "long" if float(position.iloc[-1]) > 0 else "cash"
        ),
        "score": round(float(score.iloc[-1]), 4),
        "as_of_date": str(pd.Timestamp(score.index[-1]).date()),
        "effective_session": "next_trading_session",
        "entry_threshold": float(entry),
        "exit_threshold": float(exit),
        "basis": (
            "The score is observed at the as-of close. ENTER/EXIT changes take "
            "effect on the next trading session; this is not the separate Helios "
            "composite opportunity rating."
        ),
    }


def _path_evidence(
    strategy_curve: pd.Series,
    drawdown: pd.Series,
    rolling_sharpe: pd.Series,
    position: pd.Series,
    strategy_rets: pd.Series,
    *,
    terminal_liquidated: bool,
) -> dict:
    episodes = _trade_episode_rows(
        position,
        strategy_rets,
        terminal_liquidated=terminal_liquidated,
    )
    completed = [row for row in episodes if row["completed"]]
    winners = [row for row in completed if row["net_return_pct"] > 0]
    losers = [row for row in completed if row["net_return_pct"] <= 0]
    finite_sharpe = rolling_sharpe.replace([np.inf, -np.inf], np.nan).dropna()
    return {
        "date_range": {
            "first_date": str(pd.Timestamp(strategy_curve.index[0]).date()),
            "last_date": str(pd.Timestamp(strategy_curve.index[-1]).date()),
            "session_count": int(len(strategy_curve)),
        },
        "trade_summary": {
            "completed_count": len(completed),
            "winning_count": len(winners),
            "losing_count": len(losers),
            "best_trade": max(completed, key=lambda row: row["net_return_pct"]) if completed else None,
            "worst_trade": min(completed, key=lambda row: row["net_return_pct"]) if completed else None,
            "recent_trade_episodes": completed[-10:],
        },
        "drawdown_summary": {
            "maximum_drawdown_pct": round(float(drawdown.min() * 100.0), 4),
            "deepest_episodes": _drawdown_episode_rows(strategy_curve, drawdown)[:5],
        },
        "rolling_sharpe_summary": {
            "window_sessions": 63,
            "measured_window_count": int(len(finite_sharpe)),
            "latest": _finite_or_none(finite_sharpe.iloc[-1]) if len(finite_sharpe) else None,
            "minimum": _finite_or_none(finite_sharpe.min()) if len(finite_sharpe) else None,
            "median": _finite_or_none(finite_sharpe.median()) if len(finite_sharpe) else None,
            "maximum": _finite_or_none(finite_sharpe.max()) if len(finite_sharpe) else None,
            "negative_window_pct": _percentage(
                int((finite_sharpe < 0).sum()), int(len(finite_sharpe))
            ),
        },
        "privacy_basis": (
            "Derived timing and risk summaries are safe for AI review; full price, "
            "equity, drawdown, date, and rolling-statistic arrays remain local."
        ),
    }


def _trade_episode_rows(
    position: pd.Series,
    strategy_rets: pd.Series,
    *,
    terminal_liquidated: bool,
) -> list[dict]:
    rows: list[dict] = []
    active: dict | None = None
    cumulative = 1.0
    for index, (pos, ret) in enumerate(zip(position.values, strategy_rets.values)):
        date = str(pd.Timestamp(position.index[index]).date())
        if pos > 0:
            if active is None:
                active = {"entry_date": date, "exposure_sessions": 0}
                cumulative = 1.0
            active["exposure_sessions"] += 1
            cumulative *= 1.0 + float(ret)
            continue
        if active is None:
            continue
        # The flat transition row carries the exit-side cost in strategy_rets.
        cumulative *= 1.0 + float(ret)
        rows.append({
            **active,
            "exit_date": date,
            "net_return_pct": round(float((cumulative - 1.0) * 100.0), 4),
            "completed": True,
            "terminal_liquidation": False,
        })
        active = None
        cumulative = 1.0
    if active is not None:
        rows.append({
            **active,
            "exit_date": str(pd.Timestamp(position.index[-1]).date()) if terminal_liquidated else None,
            "net_return_pct": round(float((cumulative - 1.0) * 100.0), 4),
            "completed": bool(terminal_liquidated),
            "terminal_liquidation": bool(terminal_liquidated),
        })
    return rows


def _drawdown_episode_rows(
    strategy_curve: pd.Series,
    drawdown: pd.Series,
) -> list[dict]:
    episodes: list[dict] = []
    peak_index = 0
    active: dict | None = None
    for index, value in enumerate(drawdown.values):
        depth = float(value)
        if depth >= -1e-12:
            if active is not None:
                start_index = int(active.pop("_start_index"))
                active.pop("_trough_index", None)
                episodes.append({
                    **active,
                    "recovery_date": str(pd.Timestamp(drawdown.index[index]).date()),
                    "underwater_sessions": index - start_index + 1,
                    "recovered": True,
                })
                active = None
            peak_index = index
            continue
        if active is None:
            active = {
                "peak_date": str(pd.Timestamp(strategy_curve.index[peak_index]).date()),
                "trough_date": str(pd.Timestamp(drawdown.index[index]).date()),
                "depth_pct": round(depth * 100.0, 4),
                "_start_index": index,
                "_trough_index": index,
            }
        elif depth < float(active["depth_pct"]) / 100.0:
            active["trough_date"] = str(pd.Timestamp(drawdown.index[index]).date())
            active["depth_pct"] = round(depth * 100.0, 4)
            active["_trough_index"] = index
    if active is not None:
        start_index = int(active.pop("_start_index"))
        active.pop("_trough_index", None)
        episodes.append({
            **active,
            "recovery_date": None,
            "underwater_sessions": len(drawdown) - start_index,
            "recovered": False,
        })
    for row in episodes:
        row.pop("_trough_index", None)
    return sorted(episodes, key=lambda row: row["depth_pct"])


def _oos_fold_rows(path: dict, *, warmup: int, fold: int) -> list[dict]:
    rows = []
    strategy_rets = path["strategy_rets"]
    benchmark_rets = path["benchmark_rets"]
    position = path["position"]
    for fold_start in range(warmup, len(strategy_rets), fold):
        fold_end = fold_start + fold
        if fold_end > len(strategy_rets):
            break
        sr = strategy_rets.iloc[fold_start:fold_end]
        br = benchmark_rets.iloc[fold_start:fold_end]
        sret = float((1.0 + sr).prod() - 1.0) * 100.0
        bret = float((1.0 + br).prod() - 1.0) * 100.0
        rows.append({
            "test_start": str(pd.Timestamp(sr.index[0]).date()),
            "test_end": str(pd.Timestamp(sr.index[-1]).date()),
            "strategy_return_pct": round(sret, 4),
            "benchmark_return_pct": round(bret, 4),
            "net_excess_return_pct": round(sret - bret, 4),
            "start_position": "long" if float(position.iloc[fold_start]) > 0 else "cash",
            "end_position": "long" if float(position.iloc[fold_end - 1]) > 0 else "cash",
        })
    return rows


def _percentage(count: int, total: int) -> float | None:
    return round(float(count) / float(total) * 100.0, 2) if total else None


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

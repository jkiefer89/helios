"""Current-to-target rebalance proposals — constrained, honest, never an order.

Given an account's latest custodian positions and a target model, propose the
trade set that moves the account toward target subject to explicit constraints
(position caps, turnover, ADV participation, cash buffer, long-only). Solved
as a convex program via CVXPY when available; a deterministic capped
projection otherwise (labeled). When the constraint set is infeasible, the
proposal is BLOCKED and each violated constraint is named with its shortfall —
never silently relaxed.

Every output is a proposal for analysis. Helios does not execute trades; the
operator implements decisions in an external platform.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import costs, data, persistence, portfolio, provenance

try:  # optional dependency: the projection fallback keeps the feature honest without it
    import cvxpy as _cp
    HAS_CVXPY = True
except Exception:  # pragma: no cover - exercised via monkeypatch in tests
    _cp = None
    HAS_CVXPY = False

DEFAULT_CONSTRAINTS: dict[str, float] = {
    "max_single_position_pct": 25.0,   # max final weight per ticker
    "max_turnover_pct": 50.0,          # one-way turnover, % of portfolio value
    "min_cash_pct": 1.0,               # cash buffer to preserve
    "max_adv_participation_pct": 10.0, # of ADV per day, per ticker
    "trade_horizon_days": 5.0,         # days allowed to work the trades
    "min_trade_usd": 200.0,            # suppress dust orders below this
    "cost_bps_per_side": costs.DEFAULT_COST_BPS_PER_SIDE,
}

_PROPOSAL_DISCLAIMER = ("Proposal for analysis only — Helios never executes trades. "
                        "Implement decisions in your trading platform.")


def _adv_usd(ticker: str) -> float | None:
    """Observed 60d average daily dollar volume from REAL history, else None."""
    inst = data.get(ticker)
    if inst is None or not provenance.is_real_source(inst.source):
        return None
    if "close" not in inst.df or "volume" not in inst.df:
        return None
    frame = inst.df[["close", "volume"]].dropna().tail(60)
    if len(frame) < 20:
        return None
    dollar = (pd.to_numeric(frame["close"], errors="coerce")
              * pd.to_numeric(frame["volume"], errors="coerce"))
    dollar = dollar.replace([np.inf, -np.inf], np.nan).dropna()
    if len(dollar) < 20:
        return None
    return float(dollar.mean())


def _last_real_close(ticker: str) -> tuple[float | None, str]:
    """Last close from live/uploaded history — never a simulated price."""
    inst = data.get(ticker)
    if inst is None or not provenance.is_real_source(inst.source) or "close" not in inst.df:
        return None, ""
    close = inst.df["close"].dropna()
    if close.empty:
        return None, ""
    return float(close.iloc[-1]), str(pd.Timestamp(close.index[-1]).date())


def _merge_constraints(overrides: dict[str, Any] | None) -> tuple[dict[str, float], list[str]]:
    merged = dict(DEFAULT_CONSTRAINTS)
    warnings: list[str] = []
    for key, value in (overrides or {}).items():
        if key not in DEFAULT_CONSTRAINTS:
            warnings.append(f"Unknown constraint '{key}' ignored.")
            continue
        try:
            merged[key] = float(value)
        except (TypeError, ValueError):
            warnings.append(f"Constraint '{key}' must be numeric; default kept.")
    return merged, warnings


def propose_rebalance(account_id: str, model_id: str,
                      constraints: dict[str, Any] | None = None) -> dict[str, Any]:
    store = persistence.get_store()
    model = portfolio.get(model_id)
    if model is None:
        return {"status": "blocked", "reason": f"Unknown model '{model_id}'.",
                "disclaimer": _PROPOSAL_DISCLAIMER}
    snaps = store.account_snapshots(account_id)
    if not snaps:
        return {"status": "blocked",
                "reason": ("No custodian snapshot for this account — upload a positions CSV "
                           "(the proposal needs current holdings, cash, and total value)."),
                "disclaimer": _PROPOSAL_DISCLAIMER}
    snap = snaps[-1]
    # Positions dated to THE SAME snapshot as total_value. Mixing the latest
    # position rows with a newer cash-only snapshot silently proposed selling
    # holdings the account no longer owns (adversarial-review finding).
    positions = store.account_positions(account_id, as_of=str(snap["as_of"]))
    if not positions and store.account_positions(account_id):
        return {"status": "blocked",
                "reason": (f"The latest snapshot ({snap['as_of']}) has no position rows but "
                           "older snapshots do — re-upload positions for that date (or pull "
                           "a Flex query that includes Open Positions) before proposing."),
                "disclaimer": _PROPOSAL_DISCLAIMER}
    total_value = float(snap["total_value"] or 0.0)
    cash = float(snap["cash"] or 0.0)
    if total_value <= 0:
        return {"status": "blocked", "reason": "Latest snapshot has no positive total value.",
                "disclaimer": _PROPOSAL_DISCLAIMER}

    cons, warnings = _merge_constraints(constraints)
    target = {h.ticker.upper(): float(h.weight) for h in model.holdings}
    current: dict[str, float] = {}
    for pos in positions:
        ticker = str(pos["ticker"]).upper()
        if ticker in {"CASH", "USD"}:
            continue
        mv = pos.get("market_value")
        if mv is None and pos.get("price") is not None:
            mv = float(pos["shares"] or 0.0) * float(pos["price"])
        if mv is None:
            warnings.append(f"{ticker}: no market value or price in the snapshot — treated as 0.")
            mv = 0.0
        current[ticker] = current.get(ticker, 0.0) + float(mv) / total_value

    tickers = sorted(set(target) | set(current))
    prices: dict[str, float] = {}
    price_dates: dict[str, str] = {}
    unpriced: list[str] = []
    for ticker in tickers:
        px, as_of = _last_real_close(ticker)
        if px is None or px <= 0:
            unpriced.append(ticker)
        else:
            prices[ticker] = px
            price_dates[ticker] = as_of
    if unpriced:
        return {
            "status": "blocked",
            "reason": ("No real price history for: " + ", ".join(unpriced)
                       + ". Fetch live data or upload a price CSV — Helios does not "
                         "size trades on invented prices."),
            "unpriced_tickers": unpriced,
            "disclaimer": _PROPOSAL_DISCLAIMER,
        }

    adv: dict[str, float | None] = {t: _adv_usd(t) for t in tickers}
    for ticker, value in adv.items():
        if value is None:
            warnings.append(f"{ticker}: ADV unavailable — the liquidity cap is not enforced "
                            "for this ticker (flagged, not fabricated).")

    w_cur = np.array([current.get(t, 0.0) for t in tickers])
    w_tgt = np.array([target.get(t, 0.0) for t in tickers])
    max_w = cons["max_single_position_pct"] / 100.0
    max_turnover = cons["max_turnover_pct"] / 100.0
    min_cash = cons["min_cash_pct"] / 100.0
    part = cons["max_adv_participation_pct"] / 100.0
    horizon = max(cons["trade_horizon_days"], 0.25)
    # Per-ticker max weight-move budget from liquidity: participation × ADV × days.
    move_cap = np.array([
        (part * adv[t] * horizon / total_value) if adv[t] else np.inf
        for t in tickers
    ])

    solver_notes: list[str] = []
    if HAS_CVXPY:
        w = _cp.Variable(len(tickers))
        trade = w - w_cur
        cost_rate = cons["cost_bps_per_side"] / 10000.0
        objective = _cp.Minimize(_cp.sum_squares(w - w_tgt) + cost_rate * _cp.norm1(trade))
        constraint_list = [
            w >= 0,
            w <= max_w,
            _cp.sum(w) <= 1.0 - min_cash,
            _cp.norm1(trade) <= 2.0 * max_turnover,   # norm1 counts both sides
        ]
        finite = np.isfinite(move_cap)
        if finite.any():
            constraint_list.append(_cp.abs(trade)[finite] <= move_cap[finite])
        problem = _cp.Problem(objective, constraint_list)
        try:
            problem.solve()
        except Exception as exc:
            solver_notes.append(f"CVXPY solve failed ({exc}); deterministic projection used.")
            w_prop = _projection_fallback(w_cur, w_tgt, max_w, max_turnover, min_cash, move_cap)
            method = "capped_projection_fallback"
        else:
            if problem.status in ("optimal", "optimal_inaccurate") and w.value is not None:
                w_prop = np.clip(np.asarray(w.value).flatten(), 0.0, None)
                method = "cvxpy_qp"
                if problem.status == "optimal_inaccurate":
                    solver_notes.append("Solver reported reduced accuracy; weights are near-optimal.")
            else:
                violations = _constraint_violations(
                    tickers, w_cur, w_tgt, max_w, max_turnover, min_cash, move_cap, cons)
                if not violations:
                    violations = [{"constraint": "unknown",
                                   "detail": (f"Solver returned '{problem.status}' without an "
                                              "identifiable binding constraint — review the "
                                              "constraint set.")}]
                return {
                    "status": "infeasible",
                    "reason": "The constraint set cannot be satisfied as configured.",
                    "violations": violations,
                    "constraints": cons,
                    "warnings": warnings,
                    "disclaimer": _PROPOSAL_DISCLAIMER,
                }
    else:
        w_prop = _projection_fallback(w_cur, w_tgt, max_w, max_turnover, min_cash, move_cap)
        method = "capped_projection_fallback"
        solver_notes.append("cvxpy not installed — deterministic capped projection used.")

    proposal = _build_proposal(account_id, model, snap, tickers, w_cur, w_tgt, w_prop,
                               prices, price_dates, adv, total_value, cash, cons,
                               method, warnings, solver_notes)
    # A solved QP can still be unable to REACH the target (it lands on the
    # binding caps). Name those caps rather than letting a quietly-shrunk
    # proposal read as "done" — the review's resize-or-block requirement.
    if proposal["summary"]["residual_to_target_pct"] > 0.25:
        proposal["target_reachable"] = False
        proposal["violations"] = _constraint_violations(
            tickers, w_cur, w_tgt, max_w, max_turnover, min_cash, move_cap, cons)
    else:
        proposal["target_reachable"] = True
        proposal["violations"] = []
    return proposal


def _projection_fallback(w_cur: np.ndarray, w_tgt: np.ndarray, max_w: float,
                         max_turnover: float, min_cash: float,
                         move_cap: np.ndarray) -> np.ndarray:
    """Deterministic: clip the DESIRED weights to caps, bound each move by
    liquidity, scale all moves together until the turnover budget holds —
    and never clip the result afterward: a final clip would teleport an
    over-cap current weight straight to the cap, silently violating the
    liquidity budget just enforced (adversarial-review finding). An over-cap
    position therefore unwinds toward the cap only as fast as the caps allow.
    """
    desired = np.clip(w_tgt, 0.0, max_w)
    total = desired.sum()
    if total > 1.0 - min_cash and total > 0:
        desired = desired * (1.0 - min_cash) / total
    move = desired - w_cur
    move = np.sign(move) * np.minimum(np.abs(move), np.where(np.isfinite(move_cap), move_cap, np.abs(move)))
    one_way = np.abs(move).sum() / 2.0
    if one_way > max_turnover and one_way > 0:
        move = move * (max_turnover / one_way)
    # w_cur >= 0 and |move| <= |desired - w_cur| with desired >= 0, so the
    # result cannot go negative; over-cap weights stay over-cap honestly.
    return w_cur + move


def _constraint_violations(tickers: list[str], w_cur: np.ndarray, w_tgt: np.ndarray,
                           max_w: float, max_turnover: float, min_cash: float,
                           move_cap: np.ndarray, cons: dict[str, float]) -> list[dict[str, Any]]:
    """Name each constraint that prevents reaching the target, with its shortfall."""
    violations: list[dict[str, Any]] = []
    over_cap = [(t, w) for t, w in zip(tickers, w_tgt) if w > max_w + 1e-9]
    for ticker, weight in over_cap:
        violations.append({
            "constraint": "max_single_position_pct",
            "detail": (f"{ticker} target weight {weight * 100:.1f}% exceeds the "
                       f"{max_w * 100:.1f}% cap by {(weight - max_w) * 100:.1f}pp."),
        })
    if w_tgt.sum() > 1.0 - min_cash + 1e-9:
        violations.append({
            "constraint": "min_cash_pct",
            "detail": (f"Target weights sum to {w_tgt.sum() * 100:.1f}% but the cash buffer "
                       f"requires ≤ {(1.0 - min_cash) * 100:.1f}% invested."),
        })
    needed_turnover = np.abs(np.clip(w_tgt, 0, max_w) - w_cur).sum() / 2.0
    if needed_turnover > max_turnover + 1e-9:
        violations.append({
            "constraint": "max_turnover_pct",
            "detail": (f"Reaching target needs {needed_turnover * 100:.1f}% one-way turnover; "
                       f"the cap is {max_turnover * 100:.1f}%. Raise the cap or rebalance in stages."),
        })
    finite = np.isfinite(move_cap)
    needed_move = np.abs(w_tgt - w_cur)
    for idx in np.where(finite & (needed_move > move_cap + 1e-9))[0]:
        violations.append({
            "constraint": "max_adv_participation_pct",
            "detail": (f"{tickers[idx]} needs a {needed_move[idx] * 100:.1f}pp move but liquidity "
                       f"allows {move_cap[idx] * 100:.1f}pp over {cons['trade_horizon_days']:.0f} "
                       f"day(s) at {cons['max_adv_participation_pct']:.0f}% ADV participation."),
        })
    return violations


def _build_proposal(account_id: str, model: Any, snap: dict[str, Any],
                    tickers: list[str], w_cur: np.ndarray, w_tgt: np.ndarray,
                    w_prop: np.ndarray, prices: dict[str, float],
                    price_dates: dict[str, str], adv: dict[str, float | None],
                    total_value: float, cash: float, cons: dict[str, float],
                    method: str, warnings: list[str],
                    solver_notes: list[str]) -> dict[str, Any]:
    min_trade = cons["min_trade_usd"]
    cost_rate = cons["cost_bps_per_side"] / 10000.0
    part = cons["max_adv_participation_pct"] / 100.0
    trades: list[dict[str, Any]] = []
    suppressed = 0
    for idx, ticker in enumerate(tickers):
        delta_usd = (w_prop[idx] - w_cur[idx]) * total_value
        if abs(delta_usd) < min_trade:
            if abs(delta_usd) > 1e-6:
                suppressed += 1
            continue
        px = prices[ticker]
        ticker_adv = adv.get(ticker)
        trades.append({
            "ticker": ticker,
            "side": "BUY" if delta_usd > 0 else "SELL",
            "current_weight_pct": round(w_cur[idx] * 100, 2),
            "target_weight_pct": round(w_tgt[idx] * 100, 2),
            "proposed_weight_pct": round(w_prop[idx] * 100, 2),
            "trade_usd": round(delta_usd, 0),
            "est_shares": round(delta_usd / px, 1),
            "price_used": px,
            "price_as_of": price_dates.get(ticker, ""),
            "est_cost_usd": round(abs(delta_usd) * cost_rate, 2),
            "est_days_to_trade": (round(abs(delta_usd) / (part * ticker_adv), 2)
                                  if ticker_adv else None),
        })
    trades.sort(key=lambda t: -abs(t["trade_usd"]))
    one_way_turnover = float(np.abs(w_prop - w_cur).sum() / 2.0)
    residual = float(np.abs(w_prop - w_tgt).sum() / 2.0)
    return {
        "status": "proposed",
        "account_id": account_id,
        "model_id": model.id,
        "model_name": model.name,
        "snapshot_as_of": snap["as_of"],
        "total_value_usd": round(total_value, 2),
        "cash_usd": round(cash, 2),
        "method": method,
        "trades": trades,
        "suppressed_dust_trades": suppressed,
        "summary": {
            "n_trades": len(trades),
            "one_way_turnover_pct": round(one_way_turnover * 100, 2),
            "residual_to_target_pct": round(residual * 100, 2),
            "est_total_cost_usd": round(sum(t["est_cost_usd"] for t in trades), 2),
            "proposed_cash_pct": round((1.0 - float(w_prop.sum())) * 100, 2),
        },
        "constraints": cons,
        "warnings": warnings,
        "solver_notes": solver_notes,
        "basis": ("Weights from the latest custodian snapshot; prices are last real closes "
                  "(dates shown per trade); ADV from observed 60-day dollar volume; costs at "
                  f"{cons['cost_bps_per_side']:.1f} bps/side are estimates, not quotes."),
        "disclaimer": _PROPOSAL_DISCLAIMER,
    }

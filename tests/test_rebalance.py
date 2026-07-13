"""Current-to-target rebalance proposals (deep-review implementation slice).

Constrained proposals with named infeasibility, honest price/liquidity
sourcing, dust suppression, and the no-cvxpy projection fallback.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import data, persistence, portfolio, rebalance
from tests.conftest import price_series


@pytest.fixture()
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    st = persistence.get_store()
    yield st
    persistence.reset_store_for_tests()


def _register_priced(symbol: str, close: float, adv_usd: float = 5_000_000.0):
    series = price_series(days=120) / price_series(days=120).iloc[-1] * close
    volume = pd.Series(adv_usd / close, index=series.index)
    frame = pd.DataFrame({"close": series, "volume": volume})
    data.register(data.Instrument(symbol, symbol, frame, "live", []))


def _snapshot(st, account_id: str, positions: list[dict], cash: float):
    total = cash + sum(p["market_value"] for p in positions)
    st.record_account_snapshot(
        {"account_id": account_id, "as_of": "2026-07-10", "cash": cash, "total_value": total},
        positions)
    return total


def test_feasible_proposal_reaches_target(store):
    for sym, px in (("AAA", 100.0), ("BBB", 50.0)):
        _register_priced(sym, px)
    portfolio.register(portfolio.Model(
        id="RBAL", name="Rebal Model", mandate_key="balanced", mandate_context="",
        holdings=[portfolio.Holding("AAA", 0.20), portfolio.Holding("BBB", 0.20)]))
    _snapshot(store, "ACC-R1",
              [{"ticker": "AAA", "shares": 400, "price": 100.0, "market_value": 40000.0}],
              cash=60000.0)
    out = rebalance.propose_rebalance("ACC-R1", "RBAL")
    assert out["status"] == "proposed"
    assert out["method"] == "cvxpy_qp"
    by_ticker = {t["ticker"]: t for t in out["trades"]}
    assert by_ticker["AAA"]["side"] == "SELL"           # 40% -> 20%
    assert by_ticker["BBB"]["side"] == "BUY"            # 0% -> 20%
    assert by_ticker["BBB"]["proposed_weight_pct"] == pytest.approx(20.0, abs=0.5)
    assert out["summary"]["residual_to_target_pct"] < 1.0
    assert out["summary"]["est_total_cost_usd"] > 0
    assert "never executes" in out["disclaimer"]


def test_infeasible_constraints_are_named_not_relaxed(store):
    _register_priced("CCC", 100.0)
    portfolio.register(portfolio.Model(
        id="RINF", name="Infeasible", mandate_key="balanced", mandate_context="",
        holdings=[portfolio.Holding("CCC", 0.80)]))
    _snapshot(store, "ACC-R2",
              [{"ticker": "CCC", "shares": 100, "price": 100.0, "market_value": 10000.0}],
              cash=90000.0)
    out = rebalance.propose_rebalance(
        "ACC-R2", "RINF", constraints={"max_single_position_pct": 25.0})
    # The QP lands ON the cap (best feasible) — but the unreachable target is
    # NAMED, never silently absorbed (review: block or resize, explicitly).
    assert out["status"] == "proposed"
    assert out["target_reachable"] is False
    kinds = {v["constraint"] for v in out["violations"]}
    assert "max_single_position_pct" in kinds
    assert any("80.0%" in v["detail"] for v in out["violations"])
    trade = next(t for t in out["trades"] if t["ticker"] == "CCC")
    assert trade["proposed_weight_pct"] <= 25.5


def test_liquidity_cap_limits_move_and_reports_days(store):
    # Tiny ADV: $10k/day at 10% participation over 5 days = $5k max move.
    _register_priced("DDD", 10.0, adv_usd=10_000.0)
    portfolio.register(portfolio.Model(
        id="RLIQ", name="Illiquid", mandate_key="balanced", mandate_context="",
        holdings=[portfolio.Holding("DDD", 0.20)]))
    _snapshot(store, "ACC-R3", [], cash=100000.0)
    out = rebalance.propose_rebalance("ACC-R3", "RLIQ")
    assert out["status"] == "proposed"
    trade = next(t for t in out["trades"] if t["ticker"] == "DDD")
    assert abs(trade["trade_usd"]) <= 5000 + 1        # liquidity-capped, not 20000
    assert trade["est_days_to_trade"] is not None
    assert out["summary"]["residual_to_target_pct"] > 5   # honest: target NOT reached
    assert out["target_reachable"] is False
    assert any(v["constraint"] == "max_adv_participation_pct" for v in out["violations"])


def test_unpriced_ticker_blocks_never_invents(store):
    portfolio.register(portfolio.Model(
        id="RNOPX", name="No Price", mandate_key="balanced", mandate_context="",
        holdings=[portfolio.Holding("ZZZQ", 1.0)]))
    _snapshot(store, "ACC-R4", [], cash=50000.0)
    out = rebalance.propose_rebalance("ACC-R4", "RNOPX")
    assert out["status"] == "blocked"
    assert "ZZZQ" in out["unpriced_tickers"]
    assert "invented" in out["reason"]


def test_missing_snapshot_blocks(store):
    portfolio.register(portfolio.Model(
        id="RNOSNAP", name="No Snap", mandate_key="balanced", mandate_context="",
        holdings=[portfolio.Holding("AAA", 1.0)]))
    out = rebalance.propose_rebalance("ACC-NONE", "RNOSNAP")
    assert out["status"] == "blocked"
    assert "snapshot" in out["reason"]


def test_dust_trades_suppressed(store):
    _register_priced("EEE", 100.0)
    portfolio.register(portfolio.Model(
        id="RDUST", name="Dust", mandate_key="balanced", mandate_context="",
        holdings=[portfolio.Holding("EEE", 0.20005)]))
    _snapshot(store, "ACC-R5",
              [{"ticker": "EEE", "shares": 200, "price": 100.0, "market_value": 20000.0}],
              cash=80000.0)
    out = rebalance.propose_rebalance("ACC-R5", "RDUST")
    assert out["status"] == "proposed"
    # The cost term creates a no-trade zone: a $5 improvement never pays the
    # ticket, so either the solver holds still or the dust filter eats it.
    assert out["trades"] == []
    assert out["target_reachable"] is True            # 0.005pp off target is noise
    assert out["summary"]["residual_to_target_pct"] < 0.25


def test_projection_fallback_without_cvxpy(store, monkeypatch):
    monkeypatch.setattr(rebalance, "HAS_CVXPY", False)
    for sym, px in (("FFF", 100.0), ("GGG", 50.0)):
        _register_priced(sym, px)
    portfolio.register(portfolio.Model(
        id="RFALL", name="Fallback", mandate_key="balanced", mandate_context="",
        holdings=[portfolio.Holding("FFF", 0.20), portfolio.Holding("GGG", 0.20)]))
    _snapshot(store, "ACC-R6", [], cash=100000.0)
    out = rebalance.propose_rebalance("ACC-R6", "RFALL")
    assert out["status"] == "proposed"
    assert out["method"] == "capped_projection_fallback"
    assert any("projection" in n for n in out["solver_notes"])
    by_ticker = {t["ticker"]: t for t in out["trades"]}
    assert by_ticker["FFF"]["proposed_weight_pct"] == pytest.approx(20.0, abs=0.5)


# --------------------------------------------------------------------------- #
# Adversarial-review locks (confirmed defects, fixed)
# --------------------------------------------------------------------------- #
def test_fallback_never_teleports_overcap_positions(store, monkeypatch):
    """An over-cap current weight must unwind only as fast as the liquidity
    budget allows — the old final clip jumped it straight to the cap."""
    monkeypatch.setattr(rebalance, "HAS_CVXPY", False)
    # Tiny ADV: $10k/day @ 10% x 5d = $5k budget = 5pp of a $100k account.
    _register_priced("OVW", 100.0, adv_usd=10_000.0)
    portfolio.register(portfolio.Model(
        id="ROVC", name="Overcap", mandate_key="balanced", mandate_context="",
        holdings=[portfolio.Holding("OVW", 0.20)]))
    _snapshot(store, "ACC-R7",
              [{"ticker": "OVW", "shares": 400, "price": 100.0, "market_value": 40000.0}],
              cash=60000.0)
    out = rebalance.propose_rebalance("ACC-R7", "ROVC", constraints={"max_single_position_pct": 25.0})
    assert out["status"] == "proposed"
    trade = next(t for t in out["trades"] if t["ticker"] == "OVW")
    # 40% -> at most 5pp of movement, NOT a 15pp teleport to the 25% cap.
    assert trade["proposed_weight_pct"] >= 34.5
    assert abs(trade["trade_usd"]) <= 5000 + 1
    assert out["target_reachable"] is False


def test_snapshot_and_positions_must_share_a_date(store):
    """A newer cash-only snapshot must not be mixed with older position rows —
    that combination proposed selling holdings the account no longer owns."""
    _register_priced("MIX", 100.0)
    portfolio.register(portfolio.Model(
        id="RMIX", name="Mixed", mandate_key="balanced", mandate_context="",
        holdings=[portfolio.Holding("MIX", 0.20)]))
    store.record_account_snapshot(
        {"account_id": "ACC-R8", "as_of": "2026-07-01", "cash": 10000.0, "total_value": 50000.0},
        [{"ticker": "MIX", "shares": 400, "price": 100.0, "market_value": 40000.0}])
    store.record_account_snapshot(
        {"account_id": "ACC-R8", "as_of": "2026-07-10", "cash": 51000.0, "total_value": 51000.0},
        [])
    out = rebalance.propose_rebalance("ACC-R8", "RMIX")
    assert out["status"] == "blocked"
    assert "2026-07-10" in out["reason"] and "no position rows" in out["reason"]

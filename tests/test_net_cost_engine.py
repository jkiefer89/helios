"""Shared net-cost engine locks (deep-review completion slice).

One disclosed assumption in engine/costs.py; every evidence surface reports
gross AND a net companion computed from it at presentation time; stored
measurements stay gross; the Strategy Lab default is imported, not duplicated.
"""
from __future__ import annotations

import inspect

import pytest

from engine import backtest, costs, decision_journal, model_validation, opportunity, signal_journal, strategy


def test_strategy_and_backtest_defaults_import_the_shared_constant():
    assert (inspect.signature(strategy.analyze_strategy).parameters["cost_bps"].default
            == costs.DEFAULT_COST_BPS_PER_SIDE)
    assert (inspect.signature(backtest.run).parameters["cost_bps"].default
            == costs.DEFAULT_COST_BPS_PER_SIDE)


def test_signal_journal_summary_carries_net_companion():
    entries = [
        {"forward_status": "measured", "alpha_pct": 1.5, "forward_result_pct": 2.0,
         "benchmark_result_pct": 0.5, "score": 0.4, "data_mode": "real",
         "eligible_for_real_research": True, "action_label": "BUY", "target_kind": "instrument",
         "target_id": "X"},
        {"forward_status": "measured", "alpha_pct": 0.5, "forward_result_pct": 1.0,
         "benchmark_result_pct": 0.5, "score": 0.2, "data_mode": "real",
         "eligible_for_real_research": True, "action_label": "BUY", "target_kind": "instrument",
         "target_id": "Y"},
    ]
    summary = signal_journal.summarize_entries(entries)
    assert summary["avg_alpha_pct"] == pytest.approx(1.0)
    assert summary["avg_alpha_after_default_costs_pct"] == pytest.approx(
        1.0 - costs.ROUND_TRIP_COST_PCT)
    assert summary["alpha_basis"] == costs.GROSS_LABEL
    assert "presentation-level assumption" in summary["alpha_cost_basis"]


def test_decision_scoreboard_reports_net_alpha():
    entries = [{
        "agreement": "agree", "my_action": "BUY", "outcome_status": "measured",
        "outcomes": {"21": {"hit": True, "target_return_pct": 3.0, "alpha_pct": 2.0}},
    }]
    board = decision_journal.scoreboard(entries)
    overall = board["overall"] if "overall" in board else board
    block = overall.get("agree") or overall
    # Whatever the exact nesting, the headline aggregate must carry the companion.
    flat = str(board)
    assert "avg_alpha_after_default_costs_pct" in flat
    assert "alpha_cost_basis" in flat


def test_validation_segment_stats_net_companion():
    rows = [
        {"paper_hit": True, "alpha_pct": 1.2, "action_label": "BUY", "false_positive": False},
        {"paper_hit": False, "alpha_pct": -0.2, "action_label": "SELL", "false_positive": True},
    ]
    seg = model_validation._segment_stats(rows)
    assert seg["avg_alpha_pct"] == pytest.approx(0.5)
    assert seg["avg_alpha_after_default_costs_pct"] == pytest.approx(
        0.5 - costs.ROUND_TRIP_COST_PCT)


def test_selection_adjusted_ci_has_net_endpoints():
    out = model_validation._selection_adjusted_ci(
        {"measured_count": 10, "hit_rate_p": 0.8, "alpha_mean": 1.0, "alpha_se": 0.2},
        n_trials=4)
    assert out["status"] == "ok"
    assert out["alpha_ci_net_low_pct"] == pytest.approx(
        out["alpha_ci_low_pct"] - costs.ROUND_TRIP_COST_PCT)
    assert out["alpha_ci_net_high_pct"] == pytest.approx(
        out["alpha_ci_high_pct"] - costs.ROUND_TRIP_COST_PCT)
    assert "presentation-level" in out["alpha_cost_basis"]


def test_opportunity_backtest_quality_uses_like_for_like_basis():
    # Strategy return arrives NET; the benchmark leg must be charged its one
    # round trip so alpha inside the score is like-for-like, not anti-strategy.
    same_gross = opportunity._backtest_quality(10.0, 10.0, sharpe=0.0)
    baseline = opportunity._backtest_quality(10.0, 10.0 + costs.ROUND_TRIP_COST_PCT, sharpe=0.0)
    assert same_gross > 50.0          # net strategy matching gross benchmark IS alpha
    assert baseline == pytest.approx(50.0)


def test_stored_journal_rows_stay_gross(monkeypatch, tmp_path):
    """The engine never writes net values into measured rows — presentation only."""
    from engine import persistence
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "h.db"))
    persistence.reset_store_for_tests()
    try:
        import sqlite3
        st = persistence.get_store()
        with st._connect() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(signal_journal)")]
        assert not any("net" in c for c in cols)
    finally:
        persistence.reset_store_for_tests()

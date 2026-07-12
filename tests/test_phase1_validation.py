"""Phase 1 honest-validation locks (IMPLEMENTATION_PLAN 1.1-1.3).

Champion winner's-curse guards, daily composite auto-record, and the
versioned assumptions registry.
"""
from __future__ import annotations

import pytest

from engine import assumptions, cma, data, fundamentals, model_validation, portfolio, risk_exposure
from tests.conftest import price_csv, price_series


# --------------------------------------------------------------------------- #
# 1.1 Champion selection guards
# --------------------------------------------------------------------------- #
def _windows(n: int, hit_rate: float, alpha: float, start_month: int = 1):
    rows = []
    for i in range(n):
        rows.append({
            "signal_date": f"2025-{(start_month + i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}",
            "paper_hit": i % 10 < hit_rate * 10,
            "alpha_pct": alpha,
            "action_label": "BUY",
            "false_positive": not (i % 10 < hit_rate * 10),
        })
    return rows


def test_segment_stats_and_holdout_threshold():
    stats = model_validation._segment_stats(_windows(20, 0.6, 1.5))
    assert stats["measured_count"] == 20
    assert stats["hit_rate_pct"] == pytest.approx(60.0)
    assert stats["avg_alpha_pct"] == pytest.approx(1.5)
    empty = model_validation._segment_stats([])
    assert empty["hit_rate_pct"] is None


def test_selection_adjusted_ci_widens_with_trials():
    inputs = {"measured_count": 50, "hit_rate_p": 0.62, "alpha_mean": 1.2, "alpha_se": 0.4}
    one = model_validation._selection_adjusted_ci(inputs, 1)
    five = model_validation._selection_adjusted_ci(inputs, 5)
    assert one["status"] == five["status"] == "ok"
    # More trials -> wider family-wise band (Bonferroni z grows).
    assert five["z"] > one["z"]
    assert five["hit_rate_ci_low_pct"] < one["hit_rate_ci_low_pct"]
    assert five["hit_rate_ci_high_pct"] > one["hit_rate_ci_high_pct"]
    assert five["alpha_ci_low_pct"] < one["alpha_ci_low_pct"]
    assert model_validation._selection_adjusted_ci({}, 3)["status"] == "insufficient_data"


def test_dashboard_carries_selection_block(monkeypatch, tmp_path):
    from engine import persistence
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    data.parse_csv(price_csv(days=400), "VALA", "Val A")
    data.parse_csv(price_csv(days=400), "VALB", "Val B")
    portfolio.register(portfolio.Model(id="VMOD", name="Val Model", mandate_key="balanced",
                                       mandate_context="",
                                       holdings=[portfolio.Holding("VALA", 0.6),
                                                 portfolio.Holding("VALB", 0.4)]))
    board = model_validation.dashboard(models=[portfolio.get("VMOD")])
    assert board["selection"]["n_trials"] == board["summary"]["eligible_count"]
    assert "upward-biased" in board["selection"]["basis"]
    champ = board["champion"]
    if champ is not None:
        assert "holdout_confirmation" in champ
        assert champ["holdout_confirmation"]["status"] in {"ok", "insufficient_windows"}
        assert "ranking_basis" in champ
        assert "selection-biased" not in champ["ranking_basis"]["basis"]  # sanity: text present
        assert board["selection"]["champion_adjusted"]["status"] in {"ok", "insufficient_data"}
    persistence.reset_store_for_tests()


# --------------------------------------------------------------------------- #
# 1.2 Daily composite auto-record
# --------------------------------------------------------------------------- #
def test_auto_record_daily_signals_is_idempotent_per_day(monkeypatch, tmp_path):
    from engine import persistence, signal_journal
    from helios_web import analysis as web_analysis
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    data.parse_csv(price_csv(days=200), "AUTOA", "Auto A")

    first = web_analysis.auto_record_daily_signals()
    assert first["recorded"] >= 1
    entries = [e for e in signal_journal.list_entries(limit=50)
               if (e.get("metadata") or {}).get("endpoint") == "auto_snapshot"]
    assert any(e["target_id"] == "AUTOA" for e in entries)
    # Composite evidence metadata rides along (tracks recorded at signal time).
    auto = next(e for e in entries if e["target_id"] == "AUTOA")
    assert auto["eligible_for_real_research"] is True

    second = web_analysis.auto_record_daily_signals()
    assert second["recorded"] == 0    # per-day guard: same-day rerun is a no-op
    persistence.reset_store_for_tests()


# --------------------------------------------------------------------------- #
# 1.3 Versioned assumptions surface in payloads
# --------------------------------------------------------------------------- #
def test_cma_labels_fair_pe_assumption():
    f = fundamentals.Fundamentals(ticker="LBL", dividend_yield=0.02, trailing_pe=20.0,
                                  earnings_growth=0.10, growth_basis="forward_consensus_cagr",
                                  sector="technology", source="fake")
    out = cma.instrument_forward("LBL", f)
    assert out["fair_pe_anchor"] == pytest.approx(24.0)       # NOT x100-scaled
    assert out["anchor_as_of"] == assumptions.SECTOR_ANCHORS["as_of"]
    assert out["assumptions_version"] == assumptions.SECTOR_ANCHORS["as_of"]
    assert "fair_pe_anchor" not in out["blocks_pct"]


def test_risk_methodology_labels_factor_basis():
    data.parse_csv(price_csv(days=300), "RSKA", "Rsk A")
    data.parse_csv(price_csv(days=300), "RSKB", "Rsk B")
    mdl = portfolio.Model(id="RSKM", name="Risk Model", mandate_key="balanced",
                          mandate_context="",
                          holdings=[portfolio.Holding("RSKA", 0.5), portfolio.Holding("RSKB", 0.5)])
    out = risk_exposure.analyze_model_risk(mdl)
    meth = out["methodology"]
    assert "not regression betas" in meth["factor_exposure_basis"]
    assert meth["assumptions_version"] == assumptions.ASSUMPTIONS_VERSION
    for item in out["liquidity_flags"]["items"]:
        if item["adv_source"] == "static_public_proxy":
            assert item["proxy_as_of"] == assumptions.LIQUIDITY_ADV_PROXIES["as_of"]


def test_registry_matches_consumers():
    from engine import macro
    assert macro.sector_anchor("technology")["fair_pe"] == \
        assumptions.SECTOR_ANCHORS["values"]["technology"]["fair_pe"]
    assert risk_exposure._FACTOR_MAP is assumptions.FACTOR_MAP["values"]


def test_wilson_band_never_collapses_at_extremes():
    # Wald collapsed to [100, 100] at p=1 (review finding); Wilson must not.
    perfect = model_validation._selection_adjusted_ci(
        {"measured_count": 12, "hit_rate_p": 1.0}, 3)
    assert perfect["status"] == "ok"
    assert perfect["hit_rate_ci_high_pct"] == 100.0
    assert perfect["hit_rate_ci_low_pct"] < 85.0     # honest width on n=12
    zero = model_validation._selection_adjusted_ci(
        {"measured_count": 12, "hit_rate_p": 0.0}, 3)
    assert zero["hit_rate_ci_high_pct"] > 15.0
    assert zero["hit_rate_ci_low_pct"] == 0.0


def test_below_chance_forecast_cannot_move_the_action():
    """Reproduced review finding: DA=42% on n=111 kept full forecast weight
    and flipped HOLD->BUY. The edge gate must zero it out."""
    import pandas as pd
    from engine import signals
    from tests.conftest import price_series
    close = price_series(days=300, daily=0.0005)
    fc_no_edge = {"expected_return_pct": 6.0, "horizon_days": 21,
                  "quality": {"directional_accuracy": 0.42, "n_test": 111}}
    fc_edge = {"expected_return_pct": 6.0, "horizon_days": 21,
               "quality": {"directional_accuracy": 0.62, "n_test": 111}}
    fc_unmeasured = {"expected_return_pct": 6.0, "horizon_days": 21,
                     "quality": {"directional_accuracy": 0.42, "n_test": 10}}
    sent = {"aggregate_score": 0.0, "count": 0}
    gated = signals.evaluate(close, fc_no_edge, sent, history_days=300)
    full = signals.evaluate(close, fc_edge, sent, history_days=300)
    unmeasured = signals.evaluate(close, fc_unmeasured, sent, history_days=300)

    def fc_contrib(sig):
        return next(c for c in sig["components"] if c["name"] == "forecast")["contribution"]

    assert fc_contrib(gated) == 0.0                      # below chance -> zero weight
    assert fc_contrib(full) > 0.0                        # measured edge -> full weight
    assert fc_contrib(unmeasured) > 0.0                  # unmeasured -> kept, caveated
    assert any("gated" in c for c in gated["caveats"])
    assert gated["score"] < full["score"]                # the gate actually moves the rating


def test_edge_multiplier_shape():
    from engine import signals
    m = signals._forecast_edge_multiplier
    assert m({"directional_accuracy": 0.50, "n_test": 100}) == 0.0
    assert m({"directional_accuracy": 0.55, "n_test": 100}) == 1.0
    assert m({"directional_accuracy": 0.525, "n_test": 100}) == pytest.approx(0.5)
    assert m({"directional_accuracy": 0.42, "n_test": 10}) is None   # unmeasured
    assert m(None) is None

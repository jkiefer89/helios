"""Locks for the 2026-07-12 economic-edge deep review response (batch 1).

Edge-gated forecasts, Wilson bands, overlap-adjusted CIs, span-aligned
evidence benchmarks, gross-of-costs labeling, exact-composite persistence,
and the unavailable (never fabricated) empty-state regime.
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine import costs, data, evidence_lab, regime
from tests.conftest import price_series


def test_empty_state_regime_is_unavailable_not_neutral_50():
    # Zero data used to render NEUTRAL 50/100 as numeric state (review finding).
    out = regime.market_regime([])
    assert out["status"] == "unavailable"
    assert out["score"] is None and out["label"] == "unavailable"
    assert out["drivers"] == []
    empty = data.Instrument("EMPTY", "Empty", pd.DataFrame({"close": pd.Series(dtype=float)}), "live", [])
    assert regime.market_regime([empty])["status"] == "unavailable"
    # Real data still classifies normally, with status present.
    live = data.Instrument("SPY", "SPY", price_series(days=300).to_frame("close"), "live", [])
    ok = regime.market_regime([live])
    assert ok["status"] == "ok" and isinstance(ok["score"], int)


def test_evidence_alpha_is_labeled_gross_with_net_companion():
    close = price_series(days=420, daily=0.0012)
    bench = price_series(days=420, daily=0.0005)
    out = evidence_lab.analyze_series(
        close, target={"kind": "instrument", "id": "T", "name": "T"},
        data_provenance={"data_mode": "real", "eligible_for_real_research": True,
                         "display_label": "", "reason": "", "required_action": ""},
        benchmark_symbol="SPY", benchmark_close=bench)
    assert "gross of trading costs" in out["methodology"]["alpha_basis"]
    summary = out["summary"]
    assert summary["avg_alpha_after_default_costs_pct"] == pytest.approx(
        summary["avg_alpha_pct"] - costs.ROUND_TRIP_COST_PCT, abs=1e-6)
    assert "presentation-level assumption" in summary["alpha_cost_basis"]


def test_confidence_bands_widen_with_window_overlap():
    close = price_series(days=900, daily=0.0012)
    bench = price_series(days=900, daily=0.0005)
    out = evidence_lab.analyze_series(
        close, target={"kind": "instrument", "id": "T", "name": "T"},
        data_provenance={"data_mode": "real", "eligible_for_real_research": True,
                         "display_label": "", "reason": "", "required_action": ""},
        benchmark_symbol="SPY", benchmark_close=bench)
    # 63-day decay windows at step 21 overlap 3x -> effective N is a third.
    decay63 = next(row for row in out["decay"] if row["horizon_days"] == 63)
    bands = decay63["confidence_bands"]
    assert bands["overlap_factor"] == pytest.approx(3.0)
    assert bands["n_eff"] == pytest.approx(bands["count"] / 3.0, rel=0.01)
    base = out["confidence_bands"]["alpha_pct"]
    assert base["overlap_factor"] == pytest.approx(1.0)   # horizon == step: adjacent


def test_evidence_benchmark_uses_target_calendar_span():
    # 7-day-calendar target vs weekday benchmark: the old first-bar-at/AFTER
    # end lookup measured the benchmark over a wider span (review finding).
    end = pd.Timestamp.today().normalize()
    t_idx = pd.date_range(end=end, periods=500, freq="D")
    close = pd.Series([100 * (1.001 ** i) for i in range(500)], index=t_idx)
    b_idx = pd.bdate_range(end=end, periods=380)
    bench = pd.Series([100 * (1.0006 ** i) for i in range(380)], index=b_idx)
    for start_date, fwd_date in [(t_idx[300], t_idx[321])]:
        got = evidence_lab._benchmark_return(bench, start_date, fwd_date)
        start_px = float(bench.loc[:start_date].iloc[-1])
        end_px = float(bench.loc[:fwd_date].iloc[-1])   # at/BEFORE, both ends
        assert got == pytest.approx((end_px / start_px - 1) * 100, abs=1e-9)
    # A window the benchmark doesn't cover stays unresolved.
    assert evidence_lab._benchmark_return(bench, t_idx[490], t_idx[499] + pd.Timedelta(days=30)) is None


def test_journal_metadata_carries_full_component_breakdown():
    from helios_web.analysis import _signal_evidence_metadata
    sig = {
        "components": [{"name": "trend", "raw": 0.5, "effective_weight": 0.3,
                        "contribution": 0.15, "clause": "long prose"}],
        "vol_penalty": 0.9, "mandate_fit": 1.0, "conviction_pct": 40.0,
        "tactical": {"action": "BUY", "score": 0.4},
        "strategic": {"usable": False, "reason": "no fundamentals"},
        "event_risk_damper": 0.95, "macro": {"gpr_index": 0.4},
    }
    meta = _signal_evidence_metadata(sig, endpoint="test")
    assert meta["components"][0]["contribution"] == 0.15
    assert "clause" not in meta["components"][0]         # compact, numeric-only
    assert meta["vol_penalty"] == 0.9
    assert meta["strategic_usable"] is False             # missing leg RECORDED
    assert meta["strategic_reason"] == "no fundamentals"

"""Phase 0 integrity locks for the 2026-07-11 external review response.

Future-dated data rejection, opt-in samples, regime-proxy honesty, the
series-basis label, and one-window-one-observation journal aggregation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import data, data_quality, opportunity, provenance, regime, signal_journal
from tests.conftest import load_sample_instruments, price_csv, price_series


# --------------------------------------------------------------------------- #
# 0.1 Future-dated data is rejected, flagged, and unmeasurable
# --------------------------------------------------------------------------- #
def _csv_from(series: pd.Series) -> bytes:
    df = pd.DataFrame({"Date": series.index.strftime("%Y-%m-%d"), "Close": series.values})
    return df.to_csv(index=False).encode("utf-8")


def test_parse_csv_rejects_future_dated_rows():
    future = price_series(days=60, future_days=20)   # ends ~20 business days ahead
    with pytest.raises(ValueError, match="future-dated"):
        data.parse_csv(_csv_from(future), "FUTCSV", "Future CSV")
    assert data.get("FUTCSV") is None                # nothing registered

    # A clean same-day export still parses (the +1 day tolerance is for
    # markets ahead of UTC, not a loophole).
    ok = data.parse_csv(price_csv(days=60), "OKCSV", "OK CSV")
    assert ok.df.index.max() <= data._future_cutoff()


def test_data_quality_flags_persisted_future_history(monkeypatch, tmp_path):
    from engine import persistence
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    # Simulate contamination that predates the ingestion guard.
    future = price_series(days=90, future_days=30)
    data.register(data.Instrument("FUTLIVE", "Future Live", future.to_frame("close"), "live", []))
    payload = data_quality.dashboard_payload()
    row = next(r for r in payload["symbols"] if r["symbol"] == "FUTLIVE")
    assert row["future_dated"] is True
    assert row["research_ready"] is False
    issue = next(i for i in payload["issues"] if i["category"] == "future_dated_history")
    assert issue["severity"] == "blocker" and "FUTLIVE" in issue["detail"]
    persistence.reset_store_for_tests()


def test_record_signal_refuses_future_ending_input(monkeypatch, tmp_path):
    from engine import persistence
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    contaminated = price_series(days=90, future_days=30)
    entry = signal_journal.record_signal(
        target_kind="instrument", target_id="FUTSIG", target_name="Future Sig",
        close=contaminated, input_close=contaminated,   # input window ends in the future
        signal={"action": "BUY", "score": 0.5}, horizon_days=10, benchmark="",
        source_counts={"upload": 1}, eligible_for_real_research=True, data_mode="real",
    )
    assert entry["forward_status"] == "not_measurable"
    assert "FUTURE" in entry["metadata"]["not_measurable_reason"]
    persistence.reset_store_for_tests()


# --------------------------------------------------------------------------- #
# 0.2 Samples are opt-in; a synthetic regime proxy never moves real scores
# --------------------------------------------------------------------------- #
def test_production_app_has_no_sample_loader(monkeypatch):
    data._STORE.clear()
    import helios_web
    helios_web.init_app()
    assert not any(i.source == "sample" for i in data.all_instruments())
    assert not hasattr(data, "load_samples")
    load_sample_instruments()
    assert any(i.source == "sample" for i in data.all_instruments())


def test_ineligible_regime_proxy_is_unavailable_and_never_penalizes_real_candidates():
    sample_spy = next(i for i in data.all_instruments()
                      if i.symbol == "SPY" and i.source == "sample")
    market = regime.market_regime([sample_spy])
    assert market["source"] == "sample"
    assert market["status"] == "unavailable"
    assert market["score"] is None
    assert any("not eligible market evidence" in w for w in market["warnings"])

    # A real candidate in a sample-derived risk-off regime keeps its score.
    base = {"symbol": "REAL", "name": "Real Co", "source": "live",
            "signal_score": 0.2, "expected_return_pct": 4.0, "expected_vol_pct": 15.0,
            "max_drawdown_pct": -10.0, "simulated_weight_pct": 0.0,
            "backtest": {"strategy_return_pct": 8.0, "benchmark_return_pct": 6.0, "sharpe": 0.8},
            "forecast_quality": {"directional_accuracy": 0.6}, "warnings": []}
    fake_regime_sample = {"label": "risk-off", "source": "sample"}
    fake_regime_real = {"label": "risk-off", "source": "live"}
    penalized = opportunity.score_candidate(dict(base), regime=fake_regime_real)
    unpenalized = opportunity.score_candidate(dict(base), regime=fake_regime_sample)
    assert unpenalized["opportunity_score"] > penalized["opportunity_score"]
    assert any("Risk-off regime" in w for w in penalized["warnings"])
    assert not any("Risk-off regime" in w for w in unpenalized["warnings"])


# --------------------------------------------------------------------------- #
# 0.3 The model series says what it is
# --------------------------------------------------------------------------- #
def test_series_basis_label_rides_provenance():
    from engine import portfolio
    data.parse_csv(price_csv(days=120), "SBA", "SB A")
    data.parse_csv(price_csv(days=120), "SBB", "SB B")
    mdl = portfolio.Model(id="SBM", name="SB Model", mandate_key="balanced",
                          mandate_context="",
                          holdings=[portfolio.Holding("SBA", 0.5), portfolio.Holding("SBB", 0.5)])
    ps = portfolio.build_series(mdl)
    assert ps.provenance["series_basis"] == "weight_rescaled_research_series"
    assert "not a performance track record" in ps.provenance["series_basis_note"]
    verdict = provenance.portfolio(ps.provenance)
    assert verdict["series_basis"] == "weight_rescaled_research_series"


# --------------------------------------------------------------------------- #
# 0.4 One forward window = one observation in evidence aggregation
# --------------------------------------------------------------------------- #
def test_dashboard_collapses_near_duplicate_forward_windows():
    def entry(score, created, measured=True, hit_ret=2.0):
        return {"target_kind": "instrument", "target_id": "DUP",
                "input_end_date": "2026-07-01", "horizon_days": 21,
                "action_label": "BUY", "score": score, "created_at": created,
                "forward_status": "measured" if measured else "pending",
                "forward_result_pct": hit_ret if measured else None,
                "alpha_pct": 1.0 if measured else None, "benchmark": "SPY",
                "benchmark_result_pct": 1.0 if measured else None,
                "eligible_for_real_research": True, "data_mode": "real",
                "source_counts": {"live": 1}, "metadata": {}}
    # Three intraday re-views of the SAME forward window + one distinct window.
    entries = [entry(0.501, "2026-07-01T10:00:00"),
               entry(0.502, "2026-07-01T12:00:00"),
               entry(0.503, "2026-07-01T15:00:00"),
               {**entry(0.4, "2026-06-01T10:00:00"), "input_end_date": "2026-06-01"}]
    payload = signal_journal.dashboard_payload(entries)
    assert payload["superseded_count"] == 2
    assert payload["summary"]["measured_count"] == 2   # not 4
    assert "once" in payload["dedupe_basis"]


def test_dedupe_key_normalizes_negative_zero():
    a = signal_journal._dedupe_key("instrument", "X", "2026-07-01", 21, "HOLD", -0.00004)
    b = signal_journal._dedupe_key("instrument", "X", "2026-07-01", 21, "HOLD", 0.00004)
    assert a == b

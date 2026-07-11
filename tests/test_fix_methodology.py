"""Regression tests for the methodology audit fixes.

Covers: pandas-3 read-only covariance patch (portfolio.build_series), NaN
guards in Opportunity Radar scoring, Strategy Lab trade statistics (episode
windows, net returns, round-trip cost labeling, risk-free disclosure, trade
counting), and Portfolio Clinic estimates/diagnostics plus insights hardening.
"""
import numpy as np
import pandas as pd
import pytest

from engine import data, indicators, insights, portfolio
from engine.portfolio import Model, PortfolioSeries
from engine.strategy import analyze_strategy, _trade_episodes, _trade_stats
from tests.conftest import price_series


def _register_upload(symbol: str, close: pd.Series) -> None:
    df = pd.DataFrame({"close": close}, index=close.index)
    data.register(data.Instrument(symbol, symbol, df, "upload", []))


def _pattern_series(start_date: str, periods: int, pattern, start: float = 100.0) -> pd.Series:
    idx = pd.bdate_range(start_date, periods=periods)
    rets = np.resize(np.asarray(pattern, dtype=float), periods)
    rets[0] = 0.0
    return pd.Series(start * np.cumprod(1.0 + rets), index=idx, name="close")


def _model(model_id: str, mandate_key: str, holdings: list[tuple[str, float]]) -> Model:
    return Model(id=model_id, name=model_id.title(), mandate_key=mandate_key,
                 mandate_context="test", holdings=[portfolio.Holding(t, w) for t, w in holdings])


def _reversal_series(up_days: int = 150, down_days: int = 150) -> pd.Series:
    # Trending WITH pullbacks: a perfectly monotonic leg pins the (correct)
    # Wilder RSI at 100/0 and the contrarian momentum component then rightly
    # suppresses entries — real trends wiggle, and these tests need trades,
    # not a pathological series.
    idx = pd.bdate_range("2023-01-02", periods=up_days + down_days)
    up = np.tile([0.009, -0.003, 0.007, -0.002], up_days // 4 + 1)[:up_days]
    down = np.tile([-0.010, 0.003, -0.008, 0.002], down_days // 4 + 1)[:down_days]
    rets = np.concatenate([up, down])
    return pd.Series(100.0 * np.cumprod(1.0 + rets), index=idx, name="close")


# --------------------------------------------------------------------------- #
# FIX 1: pandas 3 read-only covariance patch
# --------------------------------------------------------------------------- #

def test_build_series_survives_disjoint_histories_and_patches_covariance():
    a = _pattern_series("2022-01-03", 120, [0.012, -0.008, 0.005, -0.003])
    b = _pattern_series("2023-06-01", 120, [-0.006, 0.011, -0.002, 0.007])
    _register_upload("DISJA", a)
    _register_upload("DISJB", b)
    mdl = _model("DISJ", "balanced", [("DISJA", 0.6), ("DISJB", 0.4)])

    ps = portfolio.build_series(mdl)

    va = float(a.pct_change().var() * 252.0)
    vb = float(b.pct_change().var() * 252.0)
    da, db = np.sqrt(va), np.sqrt(vb)
    assert ps.n_days > 0
    assert ps.corr_mean == pytest.approx(0.4)
    assert ps.cov_matrix["DISJA"]["DISJA"] == pytest.approx(va, rel=1e-9)
    assert ps.cov_matrix["DISJB"]["DISJB"] == pytest.approx(vb, rel=1e-9)
    assert ps.cov_matrix["DISJA"]["DISJB"] == pytest.approx(0.4 * da * db, rel=1e-9)
    assert ps.cov_matrix["DISJB"]["DISJA"] == pytest.approx(0.4 * da * db, rel=1e-9)


# --------------------------------------------------------------------------- #
# FIX 2: NaN guards in Opportunity Radar scoring
# --------------------------------------------------------------------------- #

def _candidate(**overrides):
    base = {
        "id": "instrument:TEST",
        "kind": "instrument",
        "name": "Test Asset",
        "symbol": "TEST",
        "source": "live",
        "action": "BUY",
        "signal_score": 0.55,
        "expected_return_pct": 6.0,
        "expected_vol_pct": 18.0,
        "max_drawdown_pct": -9.0,
        "forecast_quality": {"directional_accuracy": 0.62, "n_test": 80},
        "backtest": {"strategy_return_pct": 18.0, "benchmark_return_pct": 12.0, "sharpe": 1.2},
        "warnings": [],
    }
    base.update(overrides)
    return base


def test_nan_inputs_yield_finite_score_with_data_integrity_warning():
    from engine.opportunity import score_candidate

    scored = score_candidate(_candidate(
        symbol="NANNY",
        expected_return_pct=float("nan"),
        expected_vol_pct=float("nan"),
    ))

    assert np.isfinite(scored["opportunity_score"])
    assert np.isfinite(scored["risk_score"])
    assert np.isfinite(scored["evidence_score"])
    assert any("data integrity" in w.lower() for w in scored["warnings"])


def test_nan_candidate_ranks_last_not_first():
    from engine.opportunity import rank_candidates

    nan_candidate = _candidate(symbol="NANNY", expected_return_pct=float("nan"),
                               expected_vol_pct=float("nan"))
    healthy = _candidate(symbol="GOOD")

    ranked = rank_candidates([nan_candidate, healthy], regime={"label": "neutral"})

    assert [item["symbol"] for item in ranked] == ["GOOD", "NANNY"]
    assert all(np.isfinite(item["opportunity_score"]) for item in ranked)


def test_non_numeric_inputs_are_coerced_not_crashed():
    from engine.opportunity import score_candidate

    scored = score_candidate(_candidate(
        expected_return_pct="not-a-number",
        backtest={"strategy_return_pct": float("inf"), "benchmark_return_pct": None, "sharpe": "x"},
    ))

    assert np.isfinite(scored["opportunity_score"])
    assert any("data integrity" in w.lower() for w in scored["warnings"])


# --------------------------------------------------------------------------- #
# FIX 3a/3b: episode windows and net-return trade statistics
# --------------------------------------------------------------------------- #

def test_trade_episode_excludes_post_exit_day_loss():
    idx = pd.bdate_range("2024-01-02", periods=5)
    position = pd.Series([0.0, 1.0, 1.0, 0.0, 0.0], index=idx)
    rets = pd.Series([0.0, 0.02, 0.01, -0.30, 0.0], index=idx)

    episodes, open_episode = _trade_episodes(position, rets)

    assert episodes == [pytest.approx(1.02 * 1.01 - 1.0)]
    assert open_episode is None


def test_episode_returns_reconcile_exactly_with_net_equity_curve():
    from engine import signals as sig_mod
    from engine.strategy import _position_from_scores

    px = _reversal_series()
    result = analyze_strategy(px, cost_bps=0, slippage_bps=0)
    assert result["trade_stats"]["completed_trades"] >= 1

    # Rebuild the same causal position/net-return series the engine uses; with
    # zero costs the compounded episode returns must equal the equity curve.
    position = _position_from_scores(sig_mod.historical_signals(px), 0.15, -0.05).shift(1).fillna(0.0)
    strategy_rets = position * px.pct_change().fillna(0.0)
    episodes, open_episode = _trade_episodes(position, strategy_rets)

    compounded = 1.0
    for r in episodes:
        compounded *= 1.0 + r
    if open_episode is not None:
        compounded *= 1.0 + open_episode
    assert compounded == pytest.approx(result["strategy_curve"][-1], rel=1e-6)


def test_trade_costs_reduce_completed_episode_returns():
    close = _reversal_series()

    free = analyze_strategy(close, cost_bps=0, slippage_bps=0)
    costly = analyze_strategy(close, cost_bps=100, slippage_bps=0)

    assert free["trade_stats"]["completed_trades"] >= 1
    assert costly["trade_stats"]["avg_win_pct"] < free["trade_stats"]["avg_win_pct"]


# --------------------------------------------------------------------------- #
# FIX 3c/3d: assumptions payload honesty
# --------------------------------------------------------------------------- #

def test_round_trip_cost_is_twice_the_per_side_charge():
    result = analyze_strategy(price_series(days=320, daily=0.001), cost_bps=5, slippage_bps=2)

    assert result["assumptions"]["per_side_cost_bps"] == 7.0
    assert result["assumptions"]["round_trip_cost_bps"] == 14.0


def test_risk_free_rate_is_disclosed_and_threaded(monkeypatch):
    close = price_series(days=320, daily=0.001)
    base = analyze_strategy(close)
    assert base["assumptions"]["risk_free_rate_pct"] == pytest.approx(2.0)

    monkeypatch.setattr("engine.mandate.RF", 0.10)
    high_rf = analyze_strategy(close)

    assert high_rf["assumptions"]["risk_free_rate_pct"] == pytest.approx(10.0)
    assert high_rf["strategy"]["sharpe"] < base["strategy"]["sharpe"]


def test_indicators_sharpe_uses_mandate_rf(monkeypatch):
    close = price_series(days=260, daily=0.002)
    base = indicators.sharpe(close)

    monkeypatch.setattr("engine.mandate.RF", 0.10)

    assert indicators.sharpe(close) < base
    assert indicators.sharpe(close, rf=0.02) == pytest.approx(base)


# --------------------------------------------------------------------------- #
# FIX 3e: trade counting semantics
# --------------------------------------------------------------------------- #

def test_n_trades_counts_round_trips_and_open_episode_is_separate():
    idx = pd.bdate_range("2024-01-02", periods=8)
    position = pd.Series([0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 1.0, 1.0], index=idx)
    rets = pd.Series([0.0, 0.01, 0.01, -0.20, 0.0, 0.02, 0.02, 0.02], index=idx)
    turnover = position.diff().abs().fillna(0.0)

    episodes, open_episode = _trade_episodes(position, rets)
    stats = _trade_stats(episodes, open_episode, position, turnover)

    assert stats["n_trades"] == 2
    assert stats["n_position_changes"] == 3
    assert stats["completed_trades"] == 1
    assert stats["open_position_episode_return_pct"] == pytest.approx((1.02 ** 3 - 1.0) * 100)
    assert stats["win_rate_pct"] == 100.0
    assert stats["current_position"] == "long"


def test_entries_equal_completed_plus_open_across_series():
    for close in (price_series(days=320, daily=0.001), _reversal_series()):
        result = analyze_strategy(close, cost_bps=0, slippage_bps=0)
        ts = result["trade_stats"]
        open_count = 1 if ts["open_position_episode_return_pct"] is not None else 0

        assert ts["n_trades"] >= 1
        assert ts["n_trades"] == ts["completed_trades"] + open_count
        assert ts["n_position_changes"] >= ts["n_trades"]


# --------------------------------------------------------------------------- #
# FIX 4a: clinic after-vol uses the covariance, not sqrt(HHI)
# --------------------------------------------------------------------------- #

def test_after_vol_stays_flat_for_perfectly_correlated_book():
    from engine.portfolio_clinic import analyze_clinic

    pattern = [0.012, -0.008, 0.005, -0.003, 0.009, -0.006]
    for symbol in ("CORA", "CORB", "CORC"):
        _register_upload(symbol, _pattern_series("2024-01-02", 320, pattern))
    mdl = _model("COR", "balanced", [("CORA", 0.70), ("CORB", 0.15), ("CORC", 0.15)])

    result = analyze_clinic(mdl)

    assert result["after"]["weights"]["CORA"] == pytest.approx(0.35, abs=1e-9)
    before_vol = result["before"]["estimates"]["annual_vol_pct"]
    after_vol = result["after"]["estimates"]["annual_vol_pct"]
    assert before_vol > 1.0
    assert after_vol == pytest.approx(before_vol, rel=1e-6)
    assert "heuristic" in result["after"]["estimate_basis"]["max_drawdown_pct"].lower()
    assert "covariance" in result["after"]["estimate_basis"]["annual_vol_pct"].lower()


# --------------------------------------------------------------------------- #
# FIX 4b: clinic concentration flag uses mandate-specific HHI thresholds
# --------------------------------------------------------------------------- #

def test_clinic_concentration_flag_uses_mandate_hhi_thresholds():
    from engine.portfolio_clinic import analyze_clinic

    pattern = [0.004, -0.003, 0.002, -0.001]
    symbols = ["HHA", "HHB", "HHC", "HHD", "HHE"]
    for symbol in symbols:
        _register_upload(symbol, _pattern_series("2024-01-02", 320, pattern))
    holdings = list(zip(symbols, [0.35, 0.20, 0.15, 0.15, 0.15]))

    cd = analyze_clinic(_model("HHI-CD", "cd_alternative", holdings))
    bal = analyze_clinic(_model("HHI-BAL", "balanced", holdings))

    assert cd["diagnostics"]["hhi"] == pytest.approx(0.23, abs=1e-6)
    assert cd["diagnostics"]["concentration_flag"] is True
    assert bal["diagnostics"]["concentration_flag"] is False


# --------------------------------------------------------------------------- #
# FIX 4c/4d: insights survive excluded holdings and never fabricate numbers
# --------------------------------------------------------------------------- #

def _make_ps(holdings: list[dict]) -> PortfolioSeries:
    return PortfolioSeries(close=pd.Series(dtype=float), holdings=holdings,
                           n_days=400, sources={}, warnings=[], hhi=0.5, n_eff=2.0,
                           corr_mean=0.30, binding_ticker="", provenance={})


def _run_insights(ps: PortfolioSeries, maxdd: float, fc_long_1y: dict | None) -> list[dict]:
    metrics = {"annual_vol_pct": 10.0, "max_drawdown_pct": maxdd}
    sig = {"vol_penalty": 1.0, "components": []}
    fc_short = {"quality": {"directional_accuracy": 0.60, "n_test": 60}}
    mdl = Model(id="T", name="T", mandate_key="balanced", mandate_context="", holdings=[])
    return insights.generate(mdl, ps, metrics, sig, fc_short, fc_long_1y)


def test_insights_survive_excluded_holding_with_none_mrc():
    holdings = [
        {"ticker": "BADX", "weight": 0.50, "mrc_pct": None, "excluded": True},
        {"ticker": "OKAY", "weight": 0.50, "mrc_pct": 100.0},
    ]

    out = _run_insights(_make_ps(holdings), maxdd=-10.0,
                        fc_long_1y={"prob_breach_maxdd": 0.0, "prob_positive": 0.9})

    flagged = [d for d in out if d["id"] == "conc_single_name"]
    assert len(flagged) == 2
    assert any("0% of portfolio volatility" in d["message"] for d in flagged)


def test_drawdown_breach_without_forecast_does_not_fabricate_path_numbers():
    holdings = [{"ticker": "OKAY", "weight": 0.30, "mrc_pct": 100.0}]

    out = _run_insights(_make_ps(holdings), maxdd=-25.0, fc_long_1y=None)

    ins = next(d for d in out if d["id"] == "drawdown_breaches_tolerance")
    assert "simulated paths breach" not in ins["message"]
    assert "chance of" not in ins["message"]
    assert "no 1-year simulation is available" in ins["message"]


def test_drawdown_breach_with_forecast_quotes_actual_simulated_numbers():
    holdings = [{"ticker": "OKAY", "weight": 0.30, "mrc_pct": 100.0}]

    out = _run_insights(_make_ps(holdings), maxdd=-10.0,
                        fc_long_1y={"prob_breach_maxdd": 0.25, "prob_positive": 0.60})

    ins = next(d for d in out if d["id"] == "drawdown_breaches_tolerance")
    assert "25% of 1-year simulated paths breach it" in ins["message"]
    assert "40% chance" in ins["message"]

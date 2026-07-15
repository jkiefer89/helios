"""Unit tests for engine.insights rule outputs.

Each rule in insights.generate has an explicit numeric trigger; these tests
exercise triggering and non-triggering inputs per rule using hand-built
model/analysis payloads so thresholds stay honest to the mandate config.
"""
import pandas as pd

from engine import insights
from engine.portfolio import Model, PortfolioSeries


def make_model(mandate_key: str = "balanced") -> Model:
    return Model(id="TEST", name="Test Model", mandate_key=mandate_key,
                 mandate_context="", holdings=[])


def make_ps(holdings: list | None = None, hhi: float = 0.20, n_eff: float = 5.0,
            corr_mean: float = 0.30, n_days: int = 400, binding_ticker: str = "",
            provenance: dict | None = None) -> PortfolioSeries:
    if holdings is None:
        holdings = [
            {"ticker": "AAA", "weight": 0.30, "mrc_pct": 35.0},
            {"ticker": "BBB", "weight": 0.30, "mrc_pct": 30.0},
            {"ticker": "CCC", "weight": 0.20, "mrc_pct": 20.0},
            {"ticker": "DDD", "weight": 0.20, "mrc_pct": 15.0},
        ]
    return PortfolioSeries(close=pd.Series(dtype=float), holdings=holdings,
                           n_days=n_days, sources={}, warnings=[], hhi=hhi,
                           n_eff=n_eff, corr_mean=corr_mean,
                           binding_ticker=binding_ticker,
                           provenance=provenance if provenance is not None else {})


def gen(mandate_key: str = "balanced", *, vol: float = 10.0, maxdd: float = -10.0,
        da: float | None = 0.60, n_test: int = 60, vol_penalty: float = 1.0,
        fc_raw: float = 0.0, fc_long_1y: dict | None = None,
        breach_1y: float = 0.0, prob_positive: float = 0.85,
        omit_fc_long: bool = False, **ps_kwargs) -> list[dict]:
    """Run insights.generate with benign defaults; overrides trigger one rule."""
    metrics = {"annual_vol_pct": vol, "max_drawdown_pct": maxdd}
    sig = {"vol_penalty": vol_penalty,
           "components": [{"name": "forecast", "raw": fc_raw}]}
    fc_short = {"quality": {"directional_accuracy": da, "n_test": n_test}}
    if fc_long_1y is None and not omit_fc_long:
        fc_long_1y = {"prob_breach_maxdd": breach_1y, "prob_positive": prob_positive}
    return insights.generate(make_model(mandate_key), make_ps(**ps_kwargs),
                             metrics, sig, fc_short, fc_long_1y)


def ids(out: list[dict]) -> list[str]:
    return [d["id"] for d in out]


def by_id(out: list[dict], insight_id: str) -> dict:
    return next(d for d in out if d["id"] == insight_id)


def test_clean_balanced_portfolio_yields_no_insights():
    assert gen() == []


# ---- rule 1: single-name concentration ------------------------------------ #

def test_single_name_cap_breach_flags_each_overweight_holding_largest_first():
    holdings = [
        {"ticker": "AAA", "weight": 0.45, "mrc_pct": 50.0},
        {"ticker": "BBB", "weight": 0.50, "mrc_pct": 40.0},
        {"ticker": "CCC", "weight": 0.05, "mrc_pct": 10.0},
    ]
    out = gen(holdings=holdings)

    flagged = [d for d in out if d["id"] == "conc_single_name"]
    assert len(flagged) == 2
    assert all(d["severity"] == "high" and d["category"] == "concentration" for d in flagged)
    assert "BBB is 50%" in flagged[0]["message"]  # sorted by weight, descending
    assert "AAA is 45%" in flagged[1]["message"]
    assert "40% of portfolio volatility" in flagged[0]["message"]


def test_single_name_at_exactly_the_cap_does_not_trigger():
    holdings = [{"ticker": "AAA", "weight": 0.35, "mrc_pct": 40.0},
                {"ticker": "BBB", "weight": 0.65 / 2, "mrc_pct": 30.0},
                {"ticker": "CCC", "weight": 0.65 / 2, "mrc_pct": 30.0}]
    assert "conc_single_name" not in ids(gen(holdings=holdings))


def test_low_risk_mandate_applies_tighter_single_name_cap():
    holdings = [{"ticker": "AAA", "weight": 0.20, "mrc_pct": 25.0},
                *({"ticker": f"H{i}", "weight": 0.10, "mrc_pct": 9.0} for i in range(8))]
    out = gen("capital_preservation", vol=5.0, maxdd=-5.0, holdings=holdings)

    assert "conc_single_name" in ids(out)  # 0.20 > the 0.15 preservation cap
    assert "conc_single_name" not in ids(gen(holdings=holdings))  # balanced cap 0.35


# ---- rule 2: HHI concentration --------------------------------------------- #

def test_hhi_above_high_threshold_triggers_medium_insight():
    out = gen(hhi=0.30, n_eff=3.3)

    ins = by_id(out, "conc_hhi")
    assert ins["severity"] == "medium"
    assert "3.3 independent holdings" in ins["message"]


def test_hhi_insight_suppressed_when_single_name_already_flagged():
    holdings = [{"ticker": "AAA", "weight": 0.50, "mrc_pct": 55.0},
                {"ticker": "BBB", "weight": 0.50, "mrc_pct": 45.0}]
    out = gen(hhi=0.50, n_eff=2.0, holdings=holdings)

    assert "conc_single_name" in ids(out)
    assert "conc_hhi" not in ids(out)


def test_hhi_threshold_is_tighter_for_preservation_mandates():
    holdings = [{"ticker": f"H{i}", "weight": 0.125, "mrc_pct": 12.5} for i in range(8)]
    kwargs = dict(hhi=0.22, n_eff=4.5, holdings=holdings, vol=5.0, maxdd=-5.0)

    assert "conc_hhi" in ids(gen("capital_preservation", **kwargs))  # high line 0.20
    assert "conc_hhi" not in ids(gen("balanced", **kwargs))          # high line 0.25


# ---- rule 3: volatility above mandate budget -------------------------------- #

def test_vol_more_than_15pct_over_target_triggers():
    out = gen(vol=14.0)  # balanced target 12%; band edge is 13.8%

    ins = by_id(out, "vol_above_mandate")
    assert ins["severity"] == "high"
    assert ins["category"] == "mandate-fit"
    assert "Balanced target of 12%" in ins["message"]


def test_vol_within_tolerance_band_does_not_trigger():
    assert "vol_above_mandate" not in ids(gen(vol=13.0))


# ---- rule 4: drawdown breach ------------------------------------------------ #

def test_historical_drawdown_beyond_tolerance_triggers():
    out = gen(maxdd=-25.0)  # balanced tolerance is 22%

    ins = by_id(out, "drawdown_breaches_tolerance")
    assert ins["severity"] == "high"
    assert "-25% vs the Balanced tolerance of -22%" in ins["message"]


def test_simulated_breach_probability_above_20pct_triggers_without_historical_breach():
    out = gen(maxdd=-10.0, breach_1y=0.25, prob_positive=0.60)

    ins = by_id(out, "drawdown_breaches_tolerance")
    assert "25% of 1-year simulated paths" in ins["message"]
    assert "40% chance" in ins["message"]


def test_no_breach_when_history_and_simulation_are_inside_tolerance():
    assert "drawdown_breaches_tolerance" not in ids(gen(maxdd=-10.0, breach_1y=0.10))


def test_historical_breach_still_fires_without_a_1y_forecast():
    out = gen(maxdd=-25.0, omit_fc_long=True)

    assert "drawdown_breaches_tolerance" in ids(out)


def test_cd_alternative_breach_is_called_a_hard_breach():
    out = gen("cd_alternative", vol=4.0, maxdd=-6.0)  # tolerance 4%

    ins = by_id(out, "drawdown_breaches_tolerance")
    assert "not fit for purpose" in ins["suggested_action"]
    assert "not fit for purpose" not in by_id(gen(maxdd=-25.0),
                                             "drawdown_breaches_tolerance")["suggested_action"]


# ---- rule 5: high pairwise correlation -------------------------------------- #

def test_high_mean_pairwise_correlation_triggers_medium_insight():
    out = gen(corr_mean=0.80)

    ins = by_id(out, "high_pairwise_correlation")
    assert ins["severity"] == "medium"
    assert "0.80" in ins["message"]


def test_moderate_correlation_or_single_holding_does_not_trigger():
    assert "high_pairwise_correlation" not in ids(gen(corr_mean=0.65))
    single = [{"ticker": "AAA", "weight": 1.0, "mrc_pct": 100.0}]
    assert "high_pairwise_correlation" not in ids(gen(corr_mean=0.99, holdings=single))


# ---- rule 6: no forecast edge ----------------------------------------------- #

def test_directional_accuracy_at_or_below_coin_flip_triggers():
    out = gen(da=0.48, n_test=80)

    ins = by_id(out, "no_forecast_edge")
    assert ins["severity"] == "medium"
    assert "48% on 80 days" in ins["message"]


def test_forecast_edge_ok_or_unmeasured_does_not_trigger():
    assert "no_forecast_edge" not in ids(gen(da=0.55))
    assert "no_forecast_edge" not in ids(gen(da=None))


def test_strong_forecast_lean_with_no_edge_is_called_out():
    strong = by_id(gen(da=0.50, fc_raw=0.9), "no_forecast_edge")
    weak = by_id(gen(da=0.50, fc_raw=0.3), "no_forecast_edge")

    assert "leaning strongly" in strong["message"]
    assert "leaning strongly" not in weak["message"]


def test_signal_without_forecast_component_is_treated_as_neutral_lean():
    sig = {"vol_penalty": 1.0, "components": [{"name": "trend", "raw": 0.9}]}
    fc_short = {"quality": {"directional_accuracy": 0.50, "n_test": 60}}
    out = insights.generate(make_model(), make_ps(),
                            {"annual_vol_pct": 10.0, "max_drawdown_pct": -10.0},
                            sig, fc_short, None)

    assert "leaning strongly" not in by_id(out, "no_forecast_edge")["message"]


# ---- rule 7: volatility penalty --------------------------------------------- #

def test_vol_penalty_below_085_triggers_info_insight():
    out = gen(vol_penalty=0.70)

    ins = by_id(out, "vol_penalty_active")
    assert ins["severity"] == "info"
    assert "reduced 30%" in ins["message"]
    assert "floor 0.60 at vol>=115%" in ins["message"]


def test_mild_vol_penalty_does_not_trigger():
    assert "vol_penalty_active" not in ids(gen(vol_penalty=0.90))


# ---- rule 8: income yield gap ----------------------------------------------- #

def test_income_mandate_without_yield_data_gets_info_insight():
    out = gen("income")

    ins = by_id(out, "income_yield_gap")
    assert ins["severity"] == "info"
    assert "3.5% floor" in ins["message"]


def test_income_gap_skipped_when_mandate_has_no_income_floor():
    # Balanced is 0.40 income-oriented but income_floor() gates it to None.
    assert "income_yield_gap" not in ids(gen("balanced"))
    assert "income_yield_gap" not in ids(gen("pure_growth", vol=20.0))


# ---- rule 9: growth mandate under-risked ------------------------------------ #

def test_growth_mandate_running_under_half_risk_budget_triggers_low():
    out = gen("pure_growth", vol=8.0)  # target 22%, half-budget line 11%

    ins = by_id(out, "growth_overdefensive")
    assert ins["severity"] == "low"


def test_growth_mandate_near_budget_or_non_growth_mandate_does_not_trigger():
    assert "growth_overdefensive" not in ids(gen("pure_growth", vol=15.0))
    assert "growth_overdefensive" not in ids(gen("balanced", vol=4.0))  # growth 0.6 < 0.8


# ---- rule 10: mandate label mismatch ---------------------------------------- #

def test_preservation_label_running_growth_risk_triggers():
    out = gen("cd_alternative", vol=10.0, maxdd=-2.0)  # 2.5x the 4% target

    ins = by_id(out, "mandate_label_mismatch")
    assert ins["severity"] == "high"
    assert "2.5×" in ins["message"]


def test_label_mismatch_only_applies_to_low_risk_mandates():
    assert "mandate_label_mismatch" not in ids(gen("balanced", vol=20.0))  # >1.5x target
    assert "mandate_label_mismatch" not in ids(gen("cd_alternative", vol=5.0, maxdd=-2.0))


# ---- rule 11: simulated data warning ----------------------------------------- #

def test_simulated_price_history_triggers_high_warning_naming_symbols():
    prov = {"simulated_weight_pct": 40.0, "n_simulated": 2, "n_holdings": 4,
            "simulated_symbols": ["XXX", "YYY"]}
    out = gen(provenance=prov)

    ins = by_id(out, "simulated_data_warning")
    assert ins["severity"] == "high"
    assert "40% of portfolio weight uses SIMULATED" in ins["message"]
    assert "XXX, YYY" in ins["suggested_action"]


def test_fully_real_data_does_not_trigger_simulated_warning():
    assert "simulated_data_warning" not in ids(
        gen(provenance={"simulated_weight_pct": 0.0}))


# ---- rule 12: short history --------------------------------------------------- #

def test_short_history_severity_scales_with_days():
    assert by_id(gen(n_days=200), "short_history_low_confidence")["severity"] == "info"
    assert by_id(gen(n_days=100), "short_history_low_confidence")["severity"] == "medium"
    assert "short_history_low_confidence" not in ids(gen(n_days=252))


def test_short_history_action_names_binding_ticker_when_known():
    named = by_id(gen(n_days=200, binding_ticker="AAA"), "short_history_low_confidence")
    anon = by_id(gen(n_days=200), "short_history_low_confidence")

    assert "AAA" in named["suggested_action"]
    assert "the shortest-history holding" in anon["suggested_action"]


# ---- cross-cutting behavior ---------------------------------------------------- #

def test_insights_are_sorted_most_severe_first():
    out = gen(maxdd=-25.0, corr_mean=0.80, vol_penalty=0.70)  # high + medium + info

    rank = {"high": 0, "medium": 1, "low": 2, "info": 3}
    severities = [rank[d["severity"]] for d in out]
    assert severities == sorted(severities)
    assert len(out) == 3


def test_every_insight_carries_the_full_advisor_payload():
    out = gen(maxdd=-25.0, corr_mean=0.80, vol_penalty=0.70, hhi=0.30)

    for ins in out:
        for key in ("id", "category", "severity", "message", "suggested_action", "rationale"):
            assert ins[key], f"{ins.get('id')} missing {key}"


def test_unknown_mandate_key_falls_back_to_balanced_thresholds():
    out = gen("no_such_mandate", vol=14.0)

    assert "Balanced target of 12%" in by_id(out, "vol_above_mandate")["message"]

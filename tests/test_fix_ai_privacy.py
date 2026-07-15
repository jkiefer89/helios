"""Regression tests for the AI Copilot privacy/validator audit fixes.

Covers: structural holdings gating, name redaction inside composed strings,
mixed-data-mode caveats, action-upgrade and numeric-claim validator hardening,
and data-mode detection scoped to keys literally named data_mode.
No provider is ever called; everything runs against pure engine functions.
"""
import json

import pytest

from engine import ai_copilot


def _config(**overrides):
    values = {
        "enabled": True,
        "provider": "anthropic",
        "local_backend": "ollama",
        "local_base_url": "http://127.0.0.1:11434",
        "local_model": "",
        "timeout_s": 1.0,
        "local_require_localhost": True,
        "anthropic_key_present": False,
        "anthropic_model": "test-claude",
        "openai_key_present": False,
        "openai_model": "test-openai",
        "redact_client_names": True,
        "send_holdings": False,
        "cache_ttl_s": 0,
    }
    values.update(overrides)
    return ai_copilot.AIConfig(**values)


def _clinic_payload():
    """Realistic Portfolio Clinic response shape (see engine/portfolio_clinic.py)."""
    return {
        "id": "model-1",
        "name": "Balanced Growth",
        "data_mode": "real",
        "display_label": "Real Research Mode",
        "eligible_for_real_research": True,
        "reason": "All analyzed model weight uses live or uploaded price history.",
        "data_provenance": {
            "data_mode": "real",
            "source_weight_pct": {"live": 100.0},
            "missing_tickers": [],
        },
        "mandate": {"key": "balanced_growth", "label": "Balanced Growth", "single_name_cap": 0.35},
        "constraints": {"long_only": True, "single_name_cap": 0.35, "no_short_weights": True},
        "diagnostics": {"hhi": 0.505, "effective_holdings": 1.98, "top_weight_pct": 55.0},
        "risk_contributions": [
            {"ticker": "AAPL", "weight": 0.55, "source": "live", "mrc_pct": 61.2, "window_return_pct": 12.5},
            {"ticker": "MSFT", "weight": 0.45, "source": "live", "mrc_pct": 38.8, "window_return_pct": 8.1},
        ],
        "suggestions": [
            {
                "type": "trim",
                "ticker": "AAPL",
                "current_weight": 0.55,
                "suggested_weight": 0.35,
                "rationale": "Position exceeds the single-name cap for this mandate.",
            },
        ],
        "before": {"weights": {"AAPL": 0.55, "MSFT": 0.45}, "estimates": {"annual_vol_pct": 14.2}},
        "after": {"weights": {"AAPL": 0.35, "MSFT": 0.65}, "estimates": {"annual_vol_pct": 13.1}},
        "warnings": [],
        "refusals": [],
    }


# ---------------------------------------------------------------- FIX 1


def test_clinic_composition_is_gated_when_holdings_disabled():
    sanitized = ai_copilot.sanitize_payload(_clinic_payload(), _config(send_holdings=False))
    text = json.dumps(sanitized)

    # No ticker->weight pairs may survive anywhere in the serialized payload.
    assert "AAPL" not in text
    assert "MSFT" not in text
    assert "0.55" not in text
    assert "0.45" not in text
    assert "0.65" not in text
    assert "mrc_pct" not in text
    assert sanitized["before"]["weights"]["omitted"] is True
    assert sanitized["after"]["weights"]["omitted"] is True
    assert sanitized["risk_contributions"]["omitted"] is True
    assert sanitized["suggestions"]["omitted"] is True
    assert sanitized["_sanitization"]["holdings_sent"] is False


def test_clinic_composition_is_kept_when_holdings_enabled():
    sanitized = ai_copilot.sanitize_payload(_clinic_payload(), _config(send_holdings=True))

    assert sanitized["before"]["weights"]["AAPL"] == 0.55
    assert sanitized["risk_contributions"][0]["ticker"] == "AAPL"
    assert sanitized["_sanitization"]["holdings_sent"] is True


def test_holdings_sent_is_false_when_no_composition_remains():
    # Even with the env gate open, the flag must reflect actual payload content.
    sanitized = ai_copilot.sanitize_payload(
        {"symbol": "AAPL", "score": 42, "data_mode": "real"},
        _config(send_holdings=True),
    )

    assert sanitized["_sanitization"]["holdings_sent"] is False


def test_scalar_weight_aggregates_are_not_gated():
    # top_weight_pct and similar diagnostics are aggregates, not composition.
    sanitized = ai_copilot.sanitize_payload(_clinic_payload(), _config(send_holdings=False))

    assert sanitized["diagnostics"]["top_weight_pct"] == 55.0
    assert sanitized["diagnostics"]["hhi"] == 0.505


# ---------------------------------------------------------------- FIX 2


def test_client_name_is_redacted_inside_composed_strings():
    payload = {
        "client_name": "Smith Family Trust",
        "headline": "Report for Smith Family Trust",
        "sections": [{"title_text": "Overview prepared for SMITH FAMILY TRUST."}],
        "score": 42,
    }

    sanitized = ai_copilot.sanitize_payload(payload, _config())
    text = json.dumps(sanitized)

    assert "smith family trust" not in text.lower()
    assert sanitized["client_name"] == "[redacted]"
    assert sanitized["headline"] == "Report for [redacted]"
    assert sanitized["sections"][0]["title_text"] == "Overview prepared for [redacted]."


def test_composed_strings_untouched_when_redaction_disabled():
    payload = {"client_name": "Smith Family Trust", "headline": "Report for Smith Family Trust"}

    sanitized = ai_copilot.sanitize_payload(payload, _config(redact_client_names=False))

    assert sanitized["headline"] == "Report for Smith Family Trust"


# ---------------------------------------------------------------- FIX 3


def test_mixed_data_mode_forces_proportionate_data_warning():
    result = ai_copilot.validate_ai_output(
        {"summary": "Review candidate.", "data_quality_statement": "Coverage summary."},
        {"data_mode": "mixed", "score": 42},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert result["data_mode"] == "mixed"
    assert "not verified real market data" in result["data_quality_statement"].lower()
    assert "compliance_caveats" not in result


def test_real_data_mode_gets_no_mixed_caveat():
    result = ai_copilot.validate_ai_output(
        {"summary": "Review candidate.", "data_quality_statement": "Real data."},
        {"data_mode": "real", "score": 42},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert "not verified real market data" not in result["data_quality_statement"].lower()


# ---------------------------------------------------------------- FIX 4a


@pytest.mark.parametrize(
    "phrase",
    [
        "You could accumulate on weakness.",
        "Consider adding exposure to this name.",
        "Increase the position gradually.",
        "Increasing the allocation may help.",
        "We would overweight this sector.",
        "Load up before the next review.",
    ],
)
def test_upgrade_phrasings_surface_dissent_when_action_is_hold(phrase):
    """Owner contract (2026-07-07): upgrade language against a HOLD is recorded
    as explicit AI dissent — surfaced, never censored, and not a review flag."""
    result = ai_copilot.validate_ai_output(
        {"summary": phrase},
        {"action": "HOLD", "data_mode": "real", "score": 42},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert result["ai_disagrees_with_action"] is True
    assert result["needs_review"] is False
    assert result["deterministic_action"] == "HOLD"


def test_upgrade_phrasings_surface_dissent_for_review_action():
    result = ai_copilot.validate_ai_output(
        {"summary": "Accumulate while the review completes."},
        {"action": "REVIEW", "data_mode": "real", "score": 42},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert result["ai_disagrees_with_action"] is True
    assert result["deterministic_action"] == "REVIEW"


def test_neutral_language_is_not_flagged_as_upgrade():
    result = ai_copilot.validate_ai_output(
        {"summary": "Maintain the current stance and revisit the weighting at the next review."},
        {"action": "HOLD", "data_mode": "real", "score": 42},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert result["ai_disagrees_with_action"] is False
    assert result["needs_review"] is False


# ---------------------------------------------------------------- FIX 4b


def test_hundred_percent_hit_rate_is_flagged_without_payload_support():
    result = ai_copilot.validate_ai_output(
        {"summary": "The signal shows a 100% hit rate."},
        {"score": 42, "data_mode": "real"},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert result["needs_review"] is True
    assert "100" in result["unsupported_numbers"]


def test_ten_is_no_longer_blanket_whitelisted():
    result = ai_copilot.validate_ai_output(
        {"summary": "Expect roughly 10% upside from here."},
        {"score": 42, "data_mode": "real"},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert result["needs_review"] is True
    assert "10" in result["unsupported_numbers"]


def test_payload_numbers_survive_including_round_ones():
    result = ai_copilot.validate_ai_output(
        {"summary": "Helios reports 10 usable holdings and a score of 42."},
        {"n_holdings": 10, "score": 42, "data_mode": "real"},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert "unsupported_numbers" not in result
    assert result["needs_review"] is False


def test_rounded_restatements_of_payload_numbers_pass():
    result = ai_copilot.validate_ai_output(
        {
            "summary": "Volatility is about 14.24%, roughly 14.2 annualized, near 14 overall.",
            "advisor_language": "Max drawdown was 12.35%, about 12.4 in round terms.",
        },
        {"annual_vol_pct": 14.238, "max_drawdown_pct": -12.352, "data_mode": "real"},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert "unsupported_numbers" not in result
    assert result["needs_review"] is False


def test_bare_small_counts_zero_to_three_are_allowed():
    result = ai_copilot.validate_ai_output(
        {"summary": "2 of the 3 risks stand out; 1 requires action and 0 were dismissed."},
        {"score": 42.5, "data_mode": "real"},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert "unsupported_numbers" not in result
    assert result["needs_review"] is False


# ---------------------------------------------------------------- FIX 5


def test_unrelated_mode_key_is_not_treated_as_data_mode():
    result = ai_copilot.validate_ai_output(
        {"summary": "Review candidate.", "data_quality_statement": "Provenance unknown."},
        {"chart": {"mode": "candlestick"}, "score": 42},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert result.get("data_mode") is None


def test_nested_data_provenance_data_mode_still_detected():
    result = ai_copilot.validate_ai_output(
        {"summary": "Review candidate.", "data_quality_statement": "Sample data."},
        {"data_provenance": {"data_mode": "demo"}, "score": 42},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert result["data_mode"] == "demo"
    assert "not real market evidence" in result["data_quality_statement"].lower()


# ---------------------------------------------------------------- PASS 3


def test_series_shaped_maps_and_variant_keys_are_blocked():
    dates = {f"2025-{m:02d}-{d:02d}": 100.0 + m + d for m in range(1, 13) for d in (3, 17)}
    payload = {"price_history_daily": dates, "close_series": [1.0] * 30,
               "ci90_high": 112.4, "history_days": 252}
    sanitized = ai_copilot.sanitize_payload(payload, _config())

    assert sanitized["price_history_daily"]["omitted"] is True   # token block
    assert sanitized["close_series"]["omitted"] is True
    assert sanitized["ci90_high"] == 112.4                       # scalar context survives
    assert sanitized["history_days"] == 252                      # exempted row count
    assert sanitized["_sanitization"]["full_price_history_sent"] is False


def test_strategy_evidence_keeps_derived_summaries_but_blocks_raw_curves_and_holdings():
    payload = {
        "dates": ["2026-01-02", "2026-01-05"],
        "strategy_curve": [1.0, 1.01],
        "drawdown_curve": [0.0, -1.0],
        "rolling_sharpe_curve": [None, 0.4],
        "current_signal": {
            "action_label": "MAINTAIN_LONG",
            "score": 0.22,
            "as_of_date": "2026-01-05",
        },
        "path_evidence": {
            "trade_summary": {"completed_count": 4, "best_trade": {"net_return_pct": 8.2}},
            "rolling_sharpe_summary": {"latest": 0.4, "negative_window_pct": 25.0},
        },
        "oos_evidence": {
            "status": "ok",
            "fold_count": 6,
            "primary": {"net_excess_return_pct": 1.3},
            "sensitivity": {"winner_selected": False, "variant_count": 9},
        },
        "research_context": {
            "configured": True,
            "thesis": "AI infrastructure demand remains durable relative to the governed benchmark.",
            "benchmark": "QQQ",
            "actor": "advisor@example.test",
            "change_note": "Prepared for a confidential client review.",
            "evidence": {"evidence_id": "internal-only"},
        },
        "freshness": {
            "status": "component_evidence_available",
            "component_count": 3,
            "binding_latest_bar_date": "2026-01-05",
            "component_details": [{"symbol": "NVDA"}, {"symbol": "AVGO"}],
        },
    }

    sanitized = ai_copilot.sanitize_payload(payload, _config())

    assert sanitized["dates"]["omitted"] is True
    assert sanitized["strategy_curve"]["omitted"] is True
    assert sanitized["drawdown_curve"]["omitted"] is True
    assert sanitized["rolling_sharpe_curve"]["omitted"] is True
    assert sanitized["current_signal"]["action_label"] == "MAINTAIN_LONG"
    assert sanitized["path_evidence"]["trade_summary"]["completed_count"] == 4
    assert sanitized["oos_evidence"]["primary"]["net_excess_return_pct"] == 1.3
    assert sanitized["research_context"]["benchmark"] == "QQQ"
    assert "actor" not in sanitized["research_context"]
    assert "change_note" not in sanitized["research_context"]
    assert "evidence" not in sanitized["research_context"]
    assert sanitized["freshness"]["component_count"] == 3
    assert sanitized["freshness"]["component_details"]["omitted"] is True
    assert sanitized["_sanitization"]["full_price_history_sent"] is False


def test_full_price_history_flag_is_observed_not_asserted():
    # A raw date->price map that (hypothetically) survived sanitization must
    # flip the audit flag — the old hardcoded False lied about what was sent.
    surviving = {"x": {f"2025-01-{d:02d}": float(d) for d in range(1, 20)}}
    assert ai_copilot._contains_series(surviving) is True
    assert ai_copilot._contains_series({"x": {"a": 1.0, "b": 2.0}}) is False


def test_anonymous_series_shaped_dict_is_structurally_capped():
    # No blocked key name at all: a >16-entry scalar-valued map is a series
    # regardless of what it is called.
    payload = {"levels": {f"k{i}": float(i) for i in range(40)}}
    sanitized = ai_copilot.sanitize_payload(payload, _config())
    assert sanitized["levels"] == {"omitted": True, "count": 40, "reason": "long map omitted"}


def test_mandate_context_free_text_is_blocked():
    payload = {"name": "Balanced Core 60/40",
               "context": "Sleeve for the Smith Family Trust household",
               "mandate_context": "Prepared for the Smith Family Trust"}
    sanitized = ai_copilot.sanitize_payload(payload, _config())
    assert "smith family trust" not in json.dumps(sanitized).lower()
    assert sanitized["context"]["omitted"] is True


def test_identity_bearing_keys_redacted_and_pattern_scrubbed():
    payload = {"client": "Smith Family Trust",
               "note": "Rebalance for Smith Family Trust next week"}
    sanitized = ai_copilot.sanitize_payload(payload, _config())
    text = json.dumps(sanitized).lower()
    assert "smith family trust" not in text
    assert sanitized["client"] == "[redacted]"
    assert "[redacted]" in sanitized["note"]


def test_redacted_map_keys_do_not_collide_and_lose_entries():
    payload = {"models": [{"model_name": "Alpha Fund"}, {"model_name": "Beta Fund"}],
               "sleeves": {"Alpha Fund": {"cagr_pct": 10.0}, "Beta Fund": {"cagr_pct": 2.0}}}
    sanitized = ai_copilot.sanitize_payload(payload, _config())
    sleeves = sanitized["sleeves"]
    assert len(sleeves) == 2                       # nothing silently overwritten
    cagrs = sorted(v["cagr_pct"] for v in sleeves.values())
    assert cagrs == [2.0, 10.0]
    assert "alpha fund" not in json.dumps(sanitized).lower()


def test_comma_grouped_numbers_validate_as_single_values():
    assert ai_copilot._number_values_in("$2,000,000") == {2000000.0}
    assert ai_copilot._number_values_in("grew to $4,750,000.50") == {4750000.5}
    assert ai_copilot._number_values_in("1e5 shares") == {100000.0}


def test_year_exemption_is_context_aware():
    # Plain year mentions still pass unvalidated...
    assert ai_copilot._number_values_in("in 2026 and FY2027 guidance") == set()
    # ...but money and unit-bearing quantities in the year range are claims.
    assert 1950.0 in ai_copilot._number_values_in("gold at $1950")
    assert 2050.0 in ai_copilot._number_values_in("spread of 2050 bps")


def test_fabricated_comma_dollar_figure_is_flagged():
    result = ai_copilot.validate_ai_output(
        {"summary": "The position would grow to $2,000,000 within a year."},
        {"score": 42.5, "data_mode": "real"},
        "fake", "fake-model", "opportunity_explain",
    )
    assert any("2000000" in n for n in result.get("unsupported_numbers", []))
    assert result["needs_review"] is True

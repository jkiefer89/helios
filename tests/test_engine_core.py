import hashlib
import math

import pandas as pd
import pytest

from engine import data, forecast, indicators, sentiment, signals
from tests.conftest import price_csv, price_series


def test_clean_symbol_and_name_sanitize_user_supplied_text():
    assert data.clean_symbol(" brk-b! <x> ") == "BRK-BX"
    assert data.clean_symbol("", fallback="CLIENT") == "CLIENT"
    assert data.clean_name(" Smith\x00\n IRA ", fallback="Model") == "Smith IRA"
    assert data.clean_name("\x00", fallback="Model") == "Model"


def test_parse_csv_accepts_valid_price_series():
    inst = data.parse_csv(price_csv(40), "client", "Client NAV")

    assert inst.symbol == "CLIENT"
    assert inst.name == "Client NAV"
    assert inst.source == "upload"
    assert len(inst.df) == 40
    assert "close" in inst.df.columns


def test_parse_csv_rejects_missing_date_column():
    raw = b"Close\n100\n101\n"

    with pytest.raises(ValueError, match="date column"):
        data.parse_csv(raw, "bad")


def test_parse_csv_rejects_missing_price_column():
    raw = b"Date,Volume\n2024-01-02,100\n2024-01-03,101\n"

    with pytest.raises(ValueError, match="price column"):
        data.parse_csv(raw, "bad")


def test_parse_csv_rejects_too_few_rows():
    with pytest.raises(ValueError, match="at least 30"):
        data.parse_csv(price_csv(10), "short")


def test_ticker_seed_is_sha256_based_and_stable_by_construction():
    expected = int.from_bytes(hashlib.sha256(b"helios:ZZZ").digest()[:8], "big") % (2**31)

    assert data._ticker_seed("ZZZ") == expected
    assert data._ticker_seed("ZZZ") == data._ticker_seed("ZZZ")


def test_indicators_metrics_are_finite_and_drawdown_is_negative_for_losses():
    close = pd.Series([100, 110, 90, 120], index=pd.bdate_range("2024-01-02", periods=4))
    metrics = indicators.metrics_summary(close)

    for key in ("annual_return_pct", "annual_vol_pct", "sharpe", "sortino", "max_drawdown_pct"):
        assert math.isfinite(metrics[key])
    assert indicators.max_drawdown(close) < 0
    assert metrics["max_drawdown_pct"] < 0


def test_sentiment_scores_positive_negative_and_neutral_headlines():
    assert sentiment.score_headlines(["Company beats estimates on strong growth"])["aggregate_label"] == "positive"
    assert sentiment.score_headlines(["Shares plunge after fraud warning"])["aggregate_label"] == "negative"
    assert sentiment.score_headlines(["Company reports quarterly update"])["aggregate_label"] == "neutral"


def test_signal_output_shape_and_mandate_weights_change_effective_weights():
    close = price_series(260)
    fc = {"expected_return_pct": 2.5, "horizon_days": 21, "prob_up": 0.58, "quality": {"n_test": 50}}
    sent = {"aggregate_score": 0.2, "aggregate_label": "positive", "count": 2}

    base = signals.evaluate(close, fc, sent)
    income = signals.evaluate(close, fc, sent, mandate_key="income")

    for key in ("action", "score", "conviction_pct", "components", "headline_rationale"):
        assert key in base
    assert len(base["components"]) == 4
    base_weights = {c["name"]: c["effective_weight"] for c in base["components"]}
    income_weights = {c["name"]: c["effective_weight"] for c in income["components"]}
    assert income_weights["sentiment"] != base_weights["sentiment"]


def test_signal_short_history_caveat_uses_analyzed_not_aligned_history():
    close = price_series(100)
    fc = {"expected_return_pct": 1.0, "horizon_days": 21, "prob_up": 0.55, "quality": {"n_test": 30}}
    sent = {"aggregate_score": 0.0, "aggregate_label": "neutral", "count": 0}

    sig = signals.evaluate(close, fc, sent, history_days=100)
    caveats = " ".join(sig["caveats"])

    assert "analyzed trading days" in caveats
    assert "aligned trading days" not in caveats


def test_short_forecast_returns_expected_shape():
    close = price_series(260)

    fc = forecast.forecast(close, horizon=10, n_paths=250)

    assert fc["horizon_days"] == 10
    assert len(fc["dates"]) == 10
    assert {"p05", "p25", "p50", "p75", "p95"} <= set(fc["bands"])
    assert "quality" in fc
    assert "expected_return_pct" in fc


def test_long_forecast_returns_mandate_fields_and_disclaimer():
    close = price_series(300)

    fc = forecast.forecast_long(close, 252, "balanced")

    assert fc["kind"] == "long"
    assert fc["label"] == "1Y"
    assert "cagr_pct" in fc
    assert "prob_breach_maxdd" in fc
    assert "mandate_target_pct" in fc
    assert "not a trading forecast" in fc["disclaimer"]

import pandas as pd
import pytest

from engine import data, portfolio
from tests.conftest import price_series


def parse_model(csv_text: str, name: str = "Client Model"):
    return portfolio.parse_model_file(csv_text.encode("utf-8"), "model.csv", name)


def weights(model):
    return {h.ticker: h.weight for h in model.holdings}


def close_series(values, start: str = "2024-01-02"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), name="close")


def test_parse_model_csv_with_percentage_weights():
    model = parse_model("Ticker,Weight\nAAPL,30\nMSFT,70\n")

    assert weights(model) == {"MSFT": 0.7, "AAPL": 0.3}


def test_parse_model_csv_with_fractional_weights():
    model = parse_model("Ticker,Weight\nAAPL,0.25\nMSFT,0.75\n")

    assert weights(model) == {"MSFT": 0.75, "AAPL": 0.25}


def test_parse_model_missing_weight_column_equal_weights():
    model = parse_model("Ticker\nAAPL\nMSFT\nNVDA\n")

    assert weights(model) == {"AAPL": 0.333333, "MSFT": 0.333333, "NVDA": 0.333333}


@pytest.mark.parametrize(
    "csv_text",
    [
        "Ticker,Weight\nAAPL,-25\nMSFT,125\n",
        "Ticker,Weight\nAAPL,0\nMSFT,100\n",
        "Ticker,Weight\nAAPL,-50\nMSFT,-50\n",
        "Ticker,Weight\nAAPL,50\nAAPL,-50\nMSFT,100\n",
    ],
)
def test_parse_model_rejects_nonpositive_or_signed_explicit_weights(csv_text):
    with pytest.raises(ValueError, match="strictly positive"):
        parse_model(csv_text)


def test_parse_model_rejects_unreadable_weight_column_instead_of_equal_weighting():
    with pytest.raises(ValueError, match="blank or unreadable"):
        parse_model("Ticker,Weight\nAAPL,not-a-weight\nMSFT,also-bad\n")


def test_parse_model_duplicate_tickers_merge_weights():
    model = parse_model("Ticker,Weight\nAAPL,30\nMSFT,40\nAAPL,30\n")

    assert weights(model) == {"AAPL": 0.6, "MSFT": 0.4}


def test_parse_model_missing_ticker_column_returns_user_facing_value_error():
    with pytest.raises(ValueError, match="ticker column"):
        parse_model("Name,Weight\nApple,50\nMicrosoft,50\n")


def test_build_series_uses_union_dates_and_rescales_available_weights(monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", False)
    first = price_series(days=260, start=100, daily=0.001)
    second = price_series(days=120, start=50, daily=0.002)
    second.index = pd.bdate_range(first.index[100], periods=120)
    data.register(data.Instrument("UNIONA", "Union A", pd.DataFrame({"close": first}), "upload"))
    data.register(data.Instrument("UNIONB", "Union B", pd.DataFrame({"close": second}), "upload"))
    model = portfolio.Model(
        id="UNION-TEST",
        name="Union Test",
        mandate_key="balanced",
        mandate_context="",
        holdings=[portfolio.Holding("UNIONA", 0.5), portfolio.Holding("UNIONB", 0.5)],
    )

    ps = portfolio.build_series(model, min_days=30)

    assert ps.n_days > 200
    assert any("weight-rescaled" in warning for warning in ps.warnings)
    assert ps.provenance["n_kept"] == 2


def test_build_series_rescales_weights_over_available_holding_returns(monkeypatch):
    resolved = {
        "EARLY": data.PriceSeries("EARLY", close_series([100, 110, 121, 133.1]), "upload"),
        "LATE": data.PriceSeries("LATE", close_series([100, 100, 200], start="2024-01-03"), "upload"),
    }
    monkeypatch.setattr(data, "resolve_series", lambda ticker, allow_live=True, allow_sample=True, allow_simulated=True: resolved[ticker])
    model = portfolio.Model(
        id="RESCALE-TEST",
        name="Rescale Test",
        mandate_key="balanced",
        mandate_context="",
        holdings=[portfolio.Holding("EARLY", 0.5), portfolio.Holding("LATE", 0.5)],
    )

    ps = portfolio.build_series(model, min_days=1)

    assert ps.close.tolist() == pytest.approx([110.0, 115.5, 179.025])
    assert ps.n_days == 3
    assert any("weight-rescaled" in warning for warning in ps.warnings)
    assert ps.provenance["n_kept"] == 2


def test_build_series_does_not_mutate_model_holdings(monkeypatch):
    resolved = {
        "AAA": data.PriceSeries("AAA", close_series([100, 101, 102, 103]), "upload"),
        "BBB": data.PriceSeries("BBB", close_series([200, 201, 202, 203]), "sample"),
    }
    monkeypatch.setattr(data, "resolve_series", lambda ticker, allow_live=True, allow_sample=True, allow_simulated=True: resolved[ticker])
    model = portfolio.Model(
        id="NO-MUTATION",
        name="No Mutation",
        mandate_key="balanced",
        mandate_context="",
        holdings=[portfolio.Holding("AAA", 0.6), portfolio.Holding("BBB", 0.4)],
    )

    portfolio.build_series(model, min_days=1)

    assert [h.source for h in model.holdings] == ["", ""]

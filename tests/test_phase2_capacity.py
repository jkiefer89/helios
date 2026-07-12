"""Phase 2 capacity-engine locks (IMPLEMENTATION_PLAN 2.1).

AUM-sized participation, days-to-liquidate, impact estimates, and the
honest aum_not_set / adv_unavailable degradations.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import data, portfolio, risk_exposure
from tests.conftest import price_series


def _register_with_volume(symbol: str, adv_usd: float, days: int = 300) -> None:
    close = price_series(days=days, start=100.0, daily=0.0008)
    volume = adv_usd / close   # exact $ADV every day regardless of drift
    df = pd.DataFrame({"close": close, "volume": volume})
    data.register(data.Instrument(symbol, f"{symbol} Co", df, "upload", []))


def _model(*pairs):
    return portfolio.Model(id="CAPM", name="Capacity Model", mandate_key="balanced",
                           mandate_context="",
                           holdings=[portfolio.Holding(t, w) for t, w in pairs])


def test_capacity_reports_aum_not_set_without_aum():
    _register_with_volume("CAPA", 50_000_000)
    out = risk_exposure.analyze_model_risk(_model(("CAPA", 1.0)))
    assert out["capacity"]["status"] == "aum_not_set"
    assert "never assumes" in out["capacity"]["note"]


def test_capacity_sizes_participation_days_and_impact():
    _register_with_volume("LIQ", 1_000_000_000)   # deep: $1B ADV
    _register_with_volume("THIN", 5_000_000)      # thin: $5M ADV
    out = risk_exposure.analyze_model_risk(_model(("LIQ", 0.9), ("THIN", 0.1)),
                                           aum_usd=1_000_000_000)
    cap = out["capacity"]
    rows = {r["ticker"]: r for r in cap["holdings"]}
    # LIQ: $900M position / $1B ADV -> 90% one-day participation, 9 days @10%.
    assert rows["LIQ"]["one_day_participation_pct"] == pytest.approx(90.0, rel=0.02)
    assert rows["LIQ"]["days_to_liquidate_10pct"] == pytest.approx(9.0, rel=0.02)
    # THIN: $100M / $5M ADV -> 200 days @10% -> the book is capacity-constrained.
    assert rows["THIN"]["days_to_liquidate_10pct"] == pytest.approx(200.0, rel=0.02)
    assert rows["THIN"]["impact_estimate_bps"] is not None
    assert cap["status"] == "capacity_constrained"
    assert "THIN" in cap["holdings_over_20d"]
    assert cap["max_days_to_liquidate_10pct"] == pytest.approx(200.0, rel=0.02)
    # ...and the risk narrative + posture carry it.
    pack = out["client_risk_pack"]
    assert pack["summary"]["risk_posture"] in {"watch", "elevated"}
    assert any(row["driver"] == "Implementation capacity" for row in pack["what_would_break_this_model"])
    assert pack["capacity"]["status"] == "capacity_constrained"


def test_capacity_within_limits_at_modest_aum():
    _register_with_volume("EASY", 2_000_000_000)
    out = risk_exposure.analyze_model_risk(_model(("EASY", 1.0)), aum_usd=50_000_000)
    cap = out["capacity"]
    assert cap["status"] == "within_capacity"
    assert cap["max_days_to_liquidate_10pct"] < 1.0


def test_capacity_flags_unsized_holdings_honestly():
    # No volume column at all -> no observed ADV; unknown ticker -> no proxy.
    close = price_series(days=300)
    data.register(data.Instrument("NOVOL", "No Volume", close.to_frame("close"), "upload", []))
    _register_with_volume("CAPB", 100_000_000)
    out = risk_exposure.analyze_model_risk(_model(("CAPB", 0.5), ("NOVOL", 0.5)),
                                           aum_usd=10_000_000)
    rows = {r["ticker"]: r for r in out["capacity"]["holdings"]}
    assert rows["NOVOL"]["status"] == "adv_unavailable"
    assert out["capacity"]["unsized_count"] == 1


def test_risk_route_parses_aum(monkeypatch):
    import app as helios
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    client = helios.app.test_client()
    _register_with_volume("RTEA", 500_000_000)
    portfolio.register(_model(("RTEA", 1.0)))
    body = client.get("/api/model/risk?id=CAPM&aum=250,000,000").get_json()
    assert body["capacity"]["aum_usd"] == pytest.approx(250_000_000)
    assert client.get("/api/model/risk?id=CAPM&aum=nonsense").status_code == 400
    # No aum and no env default -> honest aum_not_set.
    monkeypatch.delenv("HELIOS_MODEL_AUM_USD", raising=False)
    body2 = client.get("/api/model/risk?id=CAPM").get_json()
    assert body2["capacity"]["status"] == "aum_not_set"

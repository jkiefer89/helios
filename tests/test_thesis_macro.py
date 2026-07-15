"""Locks for FOMC odds, earnings breadth, the operator playbook, and thesis.

Non-technical parameters enter as OBSERVATIONS (market-implied odds, reported
beat/miss breadth) and conviction dampers — never as fabricated predictions.
The thesis layer makes evaluation judge models against the operator's stated
purpose, deterministically where numbers exist (income bucket) and via the
copilot elsewhere.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import ai_copilot, data, earnings_breadth, macro_events, portfolio, risk_exposure, signals
from tests.conftest import price_csv, price_series


# --------------------------------------------------------------------------- #
# FOMC odds: day-weighted futures decomposition (FedWatch arithmetic)
# --------------------------------------------------------------------------- #
def test_rate_odds_decomposes_futures_honestly(monkeypatch):
    # July 2026 meeting (28th); meeting month prices avg of pre/post rates.
    monkeypatch.setattr(macro_events._macro_odds_source(), "fed_funds_implied",
                        lambda y, m: {(2026, 7): 0.03630, (2026, 8): 0.03715}.get((y, m)),
                        raising=True) if False else None
    from engine import macro as _macro
    monkeypatch.setattr(_macro, "fed_funds_implied",
                        lambda y, m: {(2026, 7): 0.03630, (2026, 8): 0.03715}.get((y, m)))
    monkeypatch.setattr(_macro, "fed_funds_strip", lambda n=5: [])
    odds = macro_events.rate_odds()
    assert odds["available"] is True
    assert odds["direction"] == "hike"
    # pre = (avg - (2/31)*post) / (29/31); change ~ +9.1bps -> ~36% of a 25bp move
    assert odds["implied_change_bps"] == pytest.approx(9.1, abs=0.3)
    assert odds["prob_25bp_move_pct"] == pytest.approx(36.3, abs=1.5)
    assert "not a Helios forecast" in odds["basis"]


def test_rate_odds_degrades_without_futures(monkeypatch):
    from engine import macro as _macro
    monkeypatch.setattr(_macro, "fed_funds_implied", lambda y, m: None)
    odds = macro_events.rate_odds()
    assert odds["available"] is False


# --------------------------------------------------------------------------- #
# Earnings breadth: observation + damper
# --------------------------------------------------------------------------- #
def _fake_earnings(reports: dict[str, list[tuple[str, float, float]]]):
    def http(url):
        for symbol, rows in reports.items():
            if f"symbol={symbol}" in url:
                return [{"date": d, "epsActual": a, "epsEstimated": e} for d, a, e in rows]
        return []
    return http


def test_breadth_counts_beats_and_misses(monkeypatch):
    earnings_breadth.invalidate_cache()
    today = pd.Timestamp.today()
    recent = str((today - pd.Timedelta(days=20)).date())
    data.parse_csv(price_csv(days=90), "EBA", "EB A")
    data.parse_csv(price_csv(days=90), "EBB", "EB B")
    reports = {"EBA": [(recent, 2.0, 1.5)] * 1, "EBB": [(recent, 1.0, 1.4)]}
    # 12 reporting symbols to clear the sufficiency floor: 10 beats, 2 misses.
    for i in range(10):
        sym = f"EB{i:02d}"
        data.parse_csv(price_csv(days=90), sym, sym)
        reports[sym] = [(recent, 2.0, 1.0 if i < 9 else 3.0)]
    earnings_breadth.set_http(_fake_earnings(reports))
    try:
        snap = earnings_breadth.breadth_snapshot(force=True)
        assert snap["sufficient"] is True
        assert snap["beats"] + snap["misses"] == 12
        assert snap["breadth_pct"] == pytest.approx(100.0 * snap["beats"] / 12, abs=0.1)
        assert "not a forecast" in snap["basis"]
    finally:
        earnings_breadth.set_http(None)


def test_weak_breadth_damps_conviction_via_macro_context():
    ctx = {"earnings_breadth_deteriorating": True, "earnings_breadth_pct": 30.0,
           "earnings_reports": 15}
    damper, reasons = signals._event_risk_damper(ctx)
    assert damper == pytest.approx(signals._EARNINGS_BREADTH_DAMPER)
    assert any("Earnings breadth weak" in r for r in reasons)
    # Healthy breadth adds nothing.
    ok_damper, ok_reasons = signals._event_risk_damper(
        {"earnings_breadth_deteriorating": False, "earnings_breadth_pct": 90.0})
    assert ok_damper == 1.0 and ok_reasons == []


# --------------------------------------------------------------------------- #
# Operator playbook: loaded, bounded, conflict-flagging instruction attached
# --------------------------------------------------------------------------- #
def test_playbook_loads_into_prompts(monkeypatch, tmp_path):
    pb = tmp_path / "PLAYBOOK.md"
    pb.write_text("Ultra growth: missing upside is the greater risk.\n" * 3)
    monkeypatch.setenv("HELIOS_PLAYBOOK_PATH", str(pb))
    ai_copilot._PLAYBOOK_CACHE["mtime"] = None
    block = ai_copilot._playbook_block()
    assert "missing upside is the greater risk" in block
    assert "not infallible" in block          # pushback instruction rides along
    prompt = ai_copilot.build_prompt("opportunity_explain", {"score": 1})
    assert "missing upside is the greater risk" in prompt["system"]
    # Absent file -> empty block, no fabricated philosophy.
    monkeypatch.setenv("HELIOS_PLAYBOOK_PATH", str(tmp_path / "missing.md"))
    ai_copilot._PLAYBOOK_CACHE["mtime"] = None
    assert ai_copilot._playbook_block() == ""
    ai_copilot._PLAYBOOK_CACHE["mtime"] = None


# --------------------------------------------------------------------------- #
# Model thesis: sanitized, surfaced, and deterministically checked
# --------------------------------------------------------------------------- #
def test_thesis_roundtrip_and_income_bucket(monkeypatch, tmp_path):
    from engine import persistence
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    # SGOV-like low-vol sleeve + volatile growth sleeve.
    calm = price_series(days=300, start=100.0, daily=0.0001)
    data.register(data.Instrument("SGOV", "Bills", calm.to_frame("close"), "upload", []))
    # The growth sleeve must be genuinely volatile — a constant-return fixture
    # reads as ~0% vol and would (correctly!) classify as low-vol.
    wiggly = 100.0 * np.cumprod(1.0 + np.tile([0.02, -0.019], 150))
    grw = pd.Series(wiggly, index=calm.index)
    data.register(data.Instrument("GRWX", "Growth X", grw.to_frame("close"), "upload", []))
    mdl = portfolio.Model(id="INCM", name="Income Model", mandate_key="balanced",
                          mandate_context="",
                          holdings=[portfolio.Holding("SGOV", 0.30),
                                    portfolio.Holding("GRWX", 0.70)])
    portfolio.register(mdl)
    portfolio.set_thesis("INCM",
                         "Income bucket: 12-24 months of draw in cash/low-vol; "
                         "switch draws to the bucket in extended downturns.",
                         thesis_params={"income_monthly_draw_usd": 50_000})
    assert portfolio.get("INCM").thesis_params["income_monthly_draw_usd"] == 50_000.0

    out = risk_exposure.analyze_model_risk(portfolio.get("INCM"), aum_usd=3_000_000)
    bucket = out["income_bucket"]
    # 30% x $3M = $900k / $50k per month = 18 months -> inside the 12-24 band.
    assert bucket["months_of_coverage"] == pytest.approx(18.0, abs=0.3)
    assert bucket["status"] == "within_thesis_band"
    assert any(r["ticker"] == "SGOV" for r in bucket["bucket_holdings"])

    # Below the floor -> loud break-model language.
    portfolio.set_thesis("INCM", portfolio.get("INCM").thesis,
                         thesis_params={"income_monthly_draw_usd": 200_000})
    out2 = risk_exposure.analyze_model_risk(portfolio.get("INCM"), aum_usd=3_000_000)
    assert out2["income_bucket"]["status"] == "below_thesis_floor"
    assert any(row["driver"] == "Income bucket below thesis floor"
               for row in out2["client_risk_pack"]["what_would_break_this_model"])

    # No thesis knob -> no block (absence, never a fabricated verdict).
    mdl2 = portfolio.Model(id="NOTH", name="No Thesis", mandate_key="balanced",
                           mandate_context="", holdings=[portfolio.Holding("GRWX", 1.0)])
    portfolio.register(mdl2)
    assert risk_exposure.analyze_model_risk(mdl2, aum_usd=1_000_000)["income_bucket"] is None
    persistence.reset_store_for_tests()

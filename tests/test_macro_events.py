"""Macro intelligence layer tests — all offline via the injected HTTP seam."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from engine import forecast, macro_events, signals

_FED_RSS = """<?xml version="1.0" encoding="utf-8" ?>
<rss version="2.0"><channel><title>FRB</title>
<item><title>Statement: inflation remains elevated and persistent; the Committee will tighten further</title>
<link>https://fed.example/1</link><pubDate>Tue, 07 Jul 2026 10:00:00 GMT</pubDate>
<description>Restrictive policy stance; vigilant on upside inflation risks.</description></item>
<item><title>Speech: policy is restrictive and further hikes may be warranted</title>
<link>https://fed.example/2</link><pubDate>Mon, 06 Jul 2026 10:00:00 GMT</pubDate>
<description>Higher for longer; sticky inflation.</description></item>
</channel></rss>"""

_FED_DOVISH_RSS = """<?xml version="1.0" encoding="utf-8" ?>
<rss version="2.0"><channel><title>FRB</title>
<item><title>Statement: cuts are appropriate as disinflation continues</title>
<link>https://fed.example/3</link><pubDate>Tue, 07 Jul 2026 10:00:00 GMT</pubDate>
<description>Easing toward accommodative policy; cooling labor market, downside risks.</description></item>
</channel></rss>"""

_WH_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>WH</title>
<item><title>Executive Order imposing tariffs on semiconductor imports</title>
<link>https://wh.example/1</link><pubDate>Mon, 06 Jul 2026 12:00:00 GMT</pubDate>
<description>New tariffs and export controls on chips.</description></item>
<item><title>Presidential Memorandum on drug pricing</title>
<link>https://wh.example/2</link><pubDate>Sun, 05 Jul 2026 12:00:00 GMT</pubDate>
<description>Medicare negotiation expansion.</description></item>
</channel></rss>"""

_GDELT_HOT = {"articles": [{"title": f"War escalation: missile attacks and invasion fears rise {i}"}
                           for i in range(20)]}
_GDELT_CALM = {"articles": [{"title": f"Trade talks continue as negotiations progress {i}"}
                            for i in range(6)]}


def _http(fed=_FED_RSS, wh=_WH_RSS, gdelt=_GDELT_HOT):
    def fake(url):
        if "federalreserve" in url:
            return fed
        if "whitehouse" in url:
            return wh
        if "gdeltproject" in url:
            return gdelt
        raise AssertionError(url)
    return fake


@pytest.fixture(autouse=True)
def _reset():
    macro_events.invalidate_cache()
    yield
    macro_events.set_http(None)
    macro_events.invalidate_cache()


def test_fed_component_scores_hawkish_and_dovish():
    macro_events.set_http(_http())
    snap = macro_events.macro_snapshot(force=True)
    fed = snap["fed"]
    assert fed["available"] and fed["stance_label"] == "hawkish" and fed["stance_score"] > 0
    macro_events.set_http(_http(fed=_FED_DOVISH_RSS))
    snap = macro_events.macro_snapshot(force=True)
    assert snap["fed"]["stance_label"] == "dovish" and snap["fed"]["stance_score"] < 0


def test_policy_component_tags_themes_and_sectors():
    macro_events.set_http(_http())
    snap = macro_events.macro_snapshot(force=True)
    policy = snap["policy"]
    assert policy["available"] and policy["n_actions"] == 2
    assert "trade" in policy["themes"] and "healthcare" in policy["themes"]
    assert "technology" in policy["sector_pressure"]
    pressure = macro_events.sector_policy_pressure("technology", snap)
    assert pressure and pressure["n_actions"] >= 1


def test_geopolitics_index_hot_vs_calm():
    macro_events.set_http(_http(gdelt=_GDELT_HOT))
    hot = macro_events.macro_snapshot(force=True)["geopolitics"]
    macro_events.set_http(_http(gdelt=_GDELT_CALM))
    calm = macro_events.macro_snapshot(force=True)["geopolitics"]
    assert hot["available"] and calm["available"]
    assert hot["risk_index"] > calm["risk_index"]
    assert hot["risk_level"] in {"elevated", "moderate"}


def test_offline_sources_are_honestly_unavailable():
    def down(url):
        raise OSError("offline")
    macro_events.set_http(down)
    snap = macro_events.macro_snapshot(force=True)
    assert snap["fed"]["available"] is False
    assert snap["policy"]["available"] is False
    assert snap["geopolitics"]["available"] is False
    # Unknown geopolitics is None — never assumed calm OR risky.
    assert snap["event_risk"]["gpr_index"] is None
    assert snap["fomc"]["start"]  # static calendar still works offline


def test_next_fomc_proximity():
    on_meeting_eve = macro_events.next_fomc(date(2026, 7, 26))
    assert on_meeting_eve["start"] == "2026-07-28" and on_meeting_eve["imminent"] is True
    far = macro_events.next_fomc(date(2026, 8, 15))
    assert far["start"] == "2026-09-15" and far["imminent"] is False
    during = macro_events.next_fomc(date(2026, 7, 28))
    assert during["in_progress"] is True


# --------------------------------------------------------------------------- #
# signals: event-risk damper
# --------------------------------------------------------------------------- #
def _close(n=300, seed=7):
    idx = pd.bdate_range("2024-01-02", periods=n)
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.15 / 252, 0.01, n))), index=idx)


_SENT = {"aggregate_score": 0.0, "aggregate_label": "neutral", "count": 0}


def test_event_risk_damper_shrinks_conviction_never_flips():
    idx = pd.bdate_range("2024-01-02", periods=300)
    rng = np.random.default_rng(3)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.60 / 252, 0.008, 300))), index=idx)
    fc = forecast.forecast(close, horizon=21, n_paths=200)
    base = signals.evaluate(close, fc, _SENT)
    assert abs(base["score"]) > 0.05  # precondition: a real signal to damp
    risky = signals.evaluate(close, fc, _SENT, macro_context={
        "fomc_imminent": True, "fomc_days_until": 2, "gpr_index": 1.0})
    assert abs(risky["score"]) < abs(base["score"])
    # 0.90 (FOMC) x 0.75 (gpr=1.0) = 0.675, floored at the documented 0.70.
    assert risky["event_risk_damper"] == pytest.approx(signals._EVENT_DAMPER_FLOOR)
    # Direction preserved: same sign, shrunk magnitude.
    assert np.sign(risky["score"]) == np.sign(base["score"])
    assert any("FOMC" in c for c in risky["caveats"])
    assert any("Geopolitical" in c for c in risky["caveats"])


def test_no_macro_context_means_no_damper_and_no_macro_keys():
    close = _close()
    fc = forecast.forecast(close, horizon=21, n_paths=200)
    sig = signals.evaluate(close, fc, _SENT)
    assert "event_risk_damper" not in sig
    assert "macro" not in sig


def test_unavailable_gpr_is_not_treated_as_risk():
    close = _close()
    fc = forecast.forecast(close, horizon=21, n_paths=200)
    sig = signals.evaluate(close, fc, _SENT, macro_context={
        "fomc_imminent": False, "gpr_index": None})
    assert sig["event_risk_damper"] == pytest.approx(1.0)


def test_sector_policy_pressure_becomes_caveat():
    close = _close()
    fc = forecast.forecast(close, horizon=21, n_paths=200)
    sig = signals.evaluate(close, fc, _SENT, macro_context={
        "fomc_imminent": False, "gpr_index": 0.2,
        "sector_policy": {"sector": "technology", "n_actions": 2, "themes": ["trade"]}})
    assert any("policy activity touching technology" in c for c in sig["caveats"])


def test_fed_full_text_scoring_outweighs_neutral_titles():
    """Administrative titles score ~0; the stance must come from the fetched
    document body when available (weighted 3x)."""
    neutral_rss = """<?xml version="1.0" encoding="utf-8" ?>
<rss version="2.0"><channel><title>FRB</title>
<item><title>Minutes of the Federal Open Market Committee, June 2026</title>
<link>https://www.federalreserve.gov/doc1</link><pubDate>x</pubDate>
<description></description></item>
</channel></rss>"""
    hawkish_body = ("<html><body>" + "Participants judged inflation elevated and persistent; "
                    "further tightening and a restrictive stance may be warranted; "
                    "the Committee remains vigilant to upside risks. " * 20 + "</body></html>")

    def fake(url):
        if "federalreserve.gov/doc1" in url:
            return hawkish_body
        if "federalreserve" in url:
            return neutral_rss
        if "whitehouse" in url:
            return _WH_RSS
        if "gdeltproject" in url:
            return _GDELT_CALM
        raise AssertionError(url)

    macro_events.set_http(fake)
    snap = macro_events.macro_snapshot(force=True)
    fed = snap["fed"]
    assert fed["n_full_text"] >= 1
    assert fed["stance_label"] == "hawkish"
    assert any(d["scored"] == "full_text" for d in fed["documents"])


def test_fed_full_text_fetch_failure_falls_back_to_title():
    def fake(url):
        if url.endswith("press_monetary.xml") or url.endswith("speeches.xml"):
            return _FED_RSS.replace("https://fed.example", "https://www.federalreserve.gov/x")
        if "federalreserve.gov/x" in url:
            raise OSError("body fetch failed")
        if "whitehouse" in url:
            return _WH_RSS
        if "gdeltproject" in url:
            return _GDELT_CALM
        raise AssertionError(url)

    macro_events.set_http(fake)
    snap = macro_events.macro_snapshot(force=True)
    fed = snap["fed"]
    assert fed["available"] is True
    assert fed["n_full_text"] == 0
    assert all(d["scored"] == "title" for d in fed["documents"])
    assert fed["stance_label"] == "hawkish"   # title lexicon still works


def test_macro_reading_persists_and_changes_compute(monkeypatch, tmp_path):
    from engine import persistence
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    store = persistence.get_store()
    assert store.available
    store.record_macro_reading({"reading_date": "2026-06-30", "fed_stance": -0.2,
                                "fed_n_documents": 10, "gpr_index": 0.3,
                                "fomc_days_until": 20, "policy_themes": {"trade": 1}})
    store.record_macro_reading({"reading_date": "2026-07-08", "fed_stance": 0.1,
                                "fed_n_documents": 16, "gpr_index": 0.6,
                                "fomc_days_until": 19, "policy_themes": {"fiscal": 1}})
    # Upsert: same day overwrites, no duplicate rows.
    store.record_macro_reading({"reading_date": "2026-07-08", "fed_stance": 0.12,
                                "fed_n_documents": 16, "gpr_index": 0.61,
                                "fomc_days_until": 19, "policy_themes": {"fiscal": 1}})
    rows = store.macro_history()
    assert len(rows) == 2 and rows[0]["fed_stance"] == pytest.approx(0.12)
    changes = macro_events.history_and_changes()
    assert changes["fed_stance_change_7d"] == pytest.approx(0.32, abs=0.01)   # -0.2 -> +0.12
    assert changes["gpr_change_7d"] == pytest.approx(0.31, abs=0.01)
    persistence.reset_store_for_tests()


def test_track_evidence_reports_insufficient_below_minimum():
    from engine import signal_journal
    few = [{"metadata": {"strategic_gap_pp": 5.0}, "forward_result_pct": 2.0,
            "forward_status": "measured", "eligible_for_real_research": True}] * 3
    out = signal_journal.track_evidence(few)
    assert out["sufficient"] is False and "direction_agreement_pct" not in out


def test_track_evidence_scores_direction_agreement():
    from engine import signal_journal
    entries = []
    for i in range(12):
        gap = 5.0 if i % 2 == 0 else -5.0
        fwd = 3.0 if i % 2 == 0 else -2.0        # perfectly agreeing
        entries.append({"metadata": {"strategic_gap_pp": gap, "strategic_action": "BUY"},
                        "forward_result_pct": fwd, "forward_status": "measured",
                        "eligible_for_real_research": True})
    out = signal_journal.track_evidence(entries)
    assert out["sufficient"] is True
    assert out["direction_agreement_pct"] == pytest.approx(100.0)
    assert out["spread_pp"] == pytest.approx(5.0)

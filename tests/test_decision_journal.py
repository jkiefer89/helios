"""Decision journal + SEC event layer tests — all offline (fake EDGAR client,
uploaded synthetic prices, tmp-path encrypted store)."""
from __future__ import annotations

import pytest

from engine import data, decision_journal, persistence, sec_events
from tests.conftest import price_csv


@pytest.fixture()
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    st = persistence.get_store()
    assert st.available is True
    yield st
    persistence.reset_store_for_tests()


def _upload(symbol: str, days: int = 320):
    data.parse_csv(price_csv(days=days), symbol, f"{symbol} Test", source_filename=f"{symbol.lower()}.csv")


# --------------------------------------------------------------------------- #
# agreement + recording
# --------------------------------------------------------------------------- #
def test_agreement_buckets():
    assert decision_journal.classify_agreement("BUY", "ADD") == "agree"
    assert decision_journal.classify_agreement("HOLD", "HOLD") == "agree"
    assert decision_journal.classify_agreement("HOLD", "BUY") == "override"
    assert decision_journal.classify_agreement("SELL", "TRIM") == "agree"
    assert decision_journal.classify_agreement("BUY", "SELL") == "override"


def test_record_decision_persists_engine_snapshot(store):
    _upload("DECA")
    entry = decision_journal.record_decision(
        target_kind="instrument",
        target_id="DECA",
        my_action="BUY",
        rationale="Valuation gap + insider buying.",
        signal={"action": "HOLD", "score": 0.1,
                "tactical": {"action": "HOLD"}, "strategic": {"action": "BUY"}},
    )
    assert entry["decision_id"].startswith("dec-")
    assert entry["agreement"] == "override"          # my BUY vs engine HOLD
    assert entry["engine_action"] == "HOLD"
    assert entry["strategic_action"] == "BUY"
    assert entry["decision_price"] is not None
    listed = decision_journal.list_decisions(refresh_outcomes=False)
    assert listed and listed[0]["decision_id"] == entry["decision_id"]
    assert listed[0]["rationale"] == "Valuation gap + insider buying."


def test_record_rejects_bad_inputs(store):
    with pytest.raises(ValueError):
        decision_journal.record_decision(target_kind="instrument", target_id="NOPE-X", my_action="BUY")
    _upload("DECB")
    with pytest.raises(ValueError):
        decision_journal.record_decision(target_kind="instrument", target_id="DECB", my_action="YOLO")


# --------------------------------------------------------------------------- #
# outcome measurement
# --------------------------------------------------------------------------- #
def test_outcomes_measured_from_forward_prices(store):
    _upload("DECC", days=320)
    entry = decision_journal.record_decision(
        target_kind="instrument", target_id="DECC", my_action="BUY",
        signal={"action": "BUY", "score": 0.5},
    )
    # Backdate the decision so >252 trading days of "future" already exist.
    close = data.get("DECC").df["close"].dropna()
    backdated = str(close.index[10].date())
    store.update_decision_outcomes(entry["decision_id"], {}, "pending", "")
    raw = store.decision_journal(limit=10)[0]
    raw["decision_date"] = backdated
    # Keep record time coherent with the backdated decision — a decision
    # recorded long AFTER its price anchor is honestly not_measurable (the
    # staleness guard), which is not what this test exercises.
    raw["created_at"] = backdated + "T00:00:00+00:00"
    measured = decision_journal.evaluate_outcomes(raw)
    assert measured["outcomes"].get("21") is not None
    assert measured["outcomes"].get("252") is not None
    # Upward synthetic drift + BUY -> hits (alpha may be absent without benchmark data).
    assert measured["outcomes"]["21"]["target_return_pct"] > 0
    assert measured["outcomes"]["21"]["hit"] is True
    assert measured["outcomes"]["21"]["engine_hit"] is True
    assert measured["outcome_status"] in {"partial", "measured"}


def test_scoreboard_buckets_and_override_duel():
    entries = [
        {"agreement": "agree", "mandate": "balanced", "my_action": "BUY", "engine_action": "BUY",
         "outcome_status": "measured",
         "outcomes": {"63": {"target_return_pct": 5.0, "hit": True, "engine_hit": True}}},
        {"agreement": "override", "mandate": "balanced", "my_action": "SELL", "engine_action": "BUY",
         "outcome_status": "measured",
         "outcomes": {"63": {"target_return_pct": -4.0, "hit": True, "engine_hit": False}}},
        {"agreement": "override", "mandate": "income", "my_action": "BUY", "engine_action": "SELL",
         "outcome_status": "measured",
         "outcomes": {"63": {"target_return_pct": -2.0, "hit": False, "engine_hit": True}}},
        {"agreement": "agree", "mandate": "income", "my_action": "HOLD", "engine_action": "HOLD",
         "outcome_status": "pending", "outcomes": {}},
    ]
    board = decision_journal.scoreboard(entries)
    assert board["total"]["count"] == 4
    assert board["agree"]["count"] == 2
    assert board["override"]["count"] == 2
    assert board["override_vs_engine"]["override_won"] == 1
    assert board["override_vs_engine"]["engine_won"] == 1
    assert board["by_mandate"]["balanced"]["count"] == 2


def test_demo_targets_are_not_measurable(store):
    # Bundled sample instruments are demo data — decisions on them must never
    # be scored against synthetic prices.
    sample = next(iter(data.all_instruments()))
    entry = decision_journal.record_decision(
        target_kind="instrument", target_id=sample.symbol, my_action="HOLD")
    assert entry["outcome_status"] == "not_measurable"
    assert decision_journal.evaluate_outcomes(entry)["outcomes"] == {}


# --------------------------------------------------------------------------- #
# SEC events (fake EDGAR client)
# --------------------------------------------------------------------------- #
class _FakeEdgar:
    def __init__(self):
        from engine.edgar import Resolution
        self._res = Resolution(symbol="TEST", cik="12345", kind="stock", name="Test Co")

    def resolve(self, symbol):
        return self._res

    def get_submissions(self, cik):
        return {"filings": {"recent": {
            "form": ["8-K", "10-Q", "4", "4", "8-K"],
            "filingDate": ["2099-01-10", "2099-01-08", "2099-01-06", "2099-01-05", "1999-01-01"],
            "accessionNumber": ["a1", "a2", "a3", "a4", "a5"],
            "primaryDocument": ["ev.htm", "q.htm", "xslF345X05/f4a.xml", "xslF345X05/f4b.xml", "old.htm"],
            "items": ["2.02,5.02", "", "", "", "8.01"],
        }}}

    def get_text(self, url):
        if "f4a.xml" in url:
            return """<ownershipDocument><reportingOwner><reportingOwnerId>
              <rptOwnerName>Jane Insider</rptOwnerName></reportingOwnerId>
              <reportingOwnerRelationship><isOfficer>1</isOfficer></reportingOwnerRelationship>
              </reportingOwner><nonDerivativeTable><nonDerivativeTransaction>
              <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
              <transactionAmounts><transactionShares><value>1000</value></transactionShares></transactionAmounts>
              </nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""
        if "f4b.xml" in url:
            return """<ownershipDocument><nonDerivativeTable><nonDerivativeTransaction>
              <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
              </nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""
        raise AssertionError(f"unexpected fetch {url}")


def test_sec_events_parse_8ks_and_form4(monkeypatch):
    sec_events.invalidate_cache()
    # Freeze "now" far enough ahead that the 2099 filings sit inside the window.
    import engine.sec_events as se
    from datetime import datetime, timezone

    class _FrozenDt(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2099, 1, 15, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(se, "datetime", _FrozenDt)
    try:
        ev = sec_events.events_for("TEST", client=_FakeEdgar())
        assert ev["available"] is True
        assert len(ev["eight_ks"]) == 1                       # 1999 8-K is outside the window
        assert ev["eight_ks"][0]["notable"] is True            # 5.02 officer change
        assert "results of operations (earnings)" in ev["eight_ks"][0]["labels"]
        ins = ev["insider"]
        assert ins["open_market_purchases"] == 1
        assert ins["open_market_sales"] == 0
        assert ins["net_signal"] == "buying"
        # The option-exercise-only filing (code M) is excluded, not miscounted.
        assert len(ins["parsed"]) == 1 and ins["parsed"][0]["owner"] == "Jane Insider"
        # Cached-only accessor returns without a client.
        assert sec_events.events_cached("TEST")["available"] is True
    finally:
        sec_events.invalidate_cache()


def test_sec_events_offline_is_honest():
    sec_events.invalidate_cache()

    class _Down:
        def resolve(self, symbol):
            from engine.edgar import EdgarError
            raise EdgarError("EDGAR unreachable")

    ev = sec_events.events_for("TEST", client=_Down())
    assert ev["available"] is False
    assert "unreachable" in ev["reason"]
    assert sec_events.events_cached("TEST") is None


def test_stale_price_anchor_blocks_outcome_scoring(store):
    """A decision recorded against a price history >7 days stale must become
    not_measurable — scoring it against later-backfilled bars grants hindsight."""
    _upload("DECD", days=320)
    entry = decision_journal.record_decision(
        target_kind="instrument", target_id="DECD", my_action="BUY",
        signal={"action": "BUY", "score": 0.5})
    raw = dict(entry)
    raw["decision_date"] = "2020-01-02"      # ancient anchor, recorded today
    result = decision_journal.evaluate_outcomes(raw)
    assert result["outcome_status"] == "not_measurable"
    assert result["outcomes"] == {}


# --------------------------------------------------------------------------- #
# model-target decisions must be measurable when the model data is real
# --------------------------------------------------------------------------- #
def test_model_decision_with_real_holdings_is_measurable(store):
    from engine import portfolio
    _upload("MDA")
    _upload("MDB")
    portfolio.register(portfolio.Model(
        id="MDEC", name="Decision Model", mandate_key="balanced", mandate_context="",
        holdings=[portfolio.Holding("MDA", 0.6), portfolio.Holding("MDB", 0.4)]))
    entry = decision_journal.record_decision(
        target_kind="model", target_id="MDEC", my_action="HOLD")
    # Raw build_series provenance has no data_mode key; reading it directly
    # left every model decision permanently not_measurable (review finding).
    assert entry["data_mode"] == "real"
    assert entry["outcome_status"] == "pending"


def test_model_decision_with_sample_holdings_stays_not_measurable(store):
    from engine import portfolio
    sample = next(iter(data.all_instruments()))
    portfolio.register(portfolio.Model(
        id="MSAMP", name="Sample Model", mandate_key="balanced", mandate_context="",
        holdings=[portfolio.Holding(sample.symbol, 1.0)]))
    entry = decision_journal.record_decision(
        target_kind="model", target_id="MSAMP", my_action="HOLD")
    assert entry["outcome_status"] == "not_measurable"

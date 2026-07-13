"""Phase 3 ledger + PIT locks (IMPLEMENTATION_PLAN 3.1-3.2).

Custodian CSV import (idempotent, honest warnings), Modified-Dietz TWR,
actual-vs-paper, implementation shortfall, PIT fundamentals capture, and
cross-provider reconciliation warnings.
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine import data, fundamentals, ledger, persistence, portfolio
from tests.conftest import price_csv, price_series


@pytest.fixture()
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    st = persistence.get_store()
    assert st.available is True
    yield st
    persistence.reset_store_for_tests()


def _fills_csv(rows: list[str]) -> bytes:
    return ("Trade Date,Symbol,Action,Quantity,Price,Fees,Amount\n"
            + "\n".join(rows)).encode()


def _positions_csv(rows: list[str]) -> bytes:
    return ("Symbol,Quantity,Price,Market Value\n" + "\n".join(rows)).encode()


# --------------------------------------------------------------------------- #
# Import: idempotent, honest, future-date safe
# --------------------------------------------------------------------------- #
def test_fills_import_is_idempotent_and_honest(store):
    raw = _fills_csv([
        "2026-06-01,JPM,BUY,100,200.00,1.50,",
        "2026-06-02,JPM,SELL,50,205.00,1.50,",
        "2026-06-03,,DIVIDEND,,,,150.00",          # non-trade -> counted, skipped
        "2026-06-04,XYZ,FROB,10,10.00,,",           # unknown action -> warned
        "2026-06-05,,DEPOSIT,,,,10000.00",          # external flow
        "2099-01-01,JPM,BUY,1,1.00,,",              # future-dated -> rejected
    ])
    first = ledger.import_fills(raw, "ACC-1", "fills.csv")
    assert first["inserted"] == 4          # buy, sell, deposit + the dividend
    assert first["non_trade_recorded"] == 1   # RECORDED as typed activity, not dropped
    assert any("unrecognized action" in w for w in first["warnings"])
    assert any("future-dated" in w for w in first["warnings"])
    second = ledger.import_fills(raw, "ACC-1", "fills.csv")
    assert second["inserted"] == 0 and second["duplicates"] == 4   # re-import is a no-op
    flows = [f for f in store.fills("ACC-1") if f["side"] == "deposit"]
    assert flows and flows[0]["amount"] == pytest.approx(10000.0)
    dividends = [f for f in store.fills("ACC-1") if f["side"] == "non_trade"]
    assert dividends and dividends[0]["metadata"]["subtype"] == "dividend"
    assert dividends[0]["amount"] == pytest.approx(150.0)


def test_positions_import_builds_dated_snapshot(store):
    raw = _positions_csv([
        "JPM,100,210.00,21000.00",
        "MSFT,50,400.00,20000.00",
        "CASH,,,\"4,000.00\"",
    ])
    result = ledger.import_positions(raw, "ACC-1", as_of="2026-06-30")
    assert result["total_value"] == pytest.approx(45000.0)
    assert result["cash"] == pytest.approx(4000.0)
    assert result["n_positions"] == 2
    with pytest.raises(ValueError, match="future"):
        ledger.import_positions(raw, "ACC-1", as_of="2099-01-01")


# --------------------------------------------------------------------------- #
# Real TWR: Modified-Dietz with flows and fees
# --------------------------------------------------------------------------- #
def test_twr_chains_dietz_periods_with_flows_and_fees(store):
    # V0=100k; deposit 10k mid-period; V1=115k -> Dietz ~= (115-100-10)/(100+5) = 4.762%
    store.upsert_ledger_account("ACC-2")
    store.record_account_snapshot({"account_id": "ACC-2", "as_of": "2026-05-01",
                                   "cash": 5000.0, "total_value": 100000.0})
    store.record_account_snapshot({"account_id": "ACC-2", "as_of": "2026-05-31",
                                   "cash": 5000.0, "total_value": 115000.0})
    ledger.import_fills(_fills_csv([
        "2026-05-16,,DEPOSIT,,,,10000.00",
        "2026-05-10,JPM,BUY,100,200.00,25.00,",
    ]), "ACC-2")
    perf = ledger.account_performance("ACC-2")
    assert perf["status"] == "measured"
    assert perf["twr_net_pct"] == pytest.approx(4.762, abs=0.05)
    assert perf["twr_gross_pct"] > perf["twr_net_pct"]      # fees added back
    assert perf["fees_usd"] == pytest.approx(25.0)
    assert perf["external_flows_usd"] == pytest.approx(10000.0)
    assert perf["cash_drag_est_pct"] is not None


def test_single_snapshot_is_honestly_insufficient(store):
    store.record_account_snapshot({"account_id": "ACC-3", "as_of": "2026-06-30",
                                   "cash": 0.0, "total_value": 50000.0})
    perf = ledger.account_performance("ACC-3")
    assert perf["status"] == "insufficient_snapshots"
    assert "two dated" in perf["note"]


def test_actual_vs_paper_labels_both_sides(store):
    data.parse_csv(price_csv(days=200), "LPA", "LP A")
    data.parse_csv(price_csv(days=200), "LPB", "LP B")
    portfolio.register(portfolio.Model(id="LPM", name="Ledger Paper Model",
                                       mandate_key="balanced", mandate_context="",
                                       holdings=[portfolio.Holding("LPA", 0.5),
                                                 portfolio.Holding("LPB", 0.5)]))
    end = price_series(days=200).index
    start_date, end_date = str(end[-40].date()), str(end[-1].date())
    store.upsert_ledger_account("ACC-4", model_id="LPM")
    store.record_account_snapshot({"account_id": "ACC-4", "as_of": start_date,
                                   "cash": 1000.0, "total_value": 100000.0})
    store.record_account_snapshot({"account_id": "ACC-4", "as_of": end_date,
                                   "cash": 1000.0, "total_value": 104000.0})
    out = ledger.actual_vs_paper("ACC-4")
    assert out["actual"]["status"] == "measured"
    assert out["paper"]["status"] == "measured"
    assert "not a track record" in out["paper"]["label"]
    assert out["gap_pct"] == pytest.approx(
        out["actual"]["twr_net_pct"] - out["paper"]["return_pct"], abs=0.01)


def test_decision_shortfall_measures_linked_fills(store):
    from engine import decision_journal
    data.parse_csv(price_csv(days=200), "SFA", "SF A")
    entry = decision_journal.record_decision(
        target_kind="instrument", target_id="SFA", my_action="BUY",
        signal={"action": "BUY", "score": 0.4})
    close = data.get("SFA").df["close"].dropna()
    anchor = float(close[close.index <= pd.Timestamp(entry["decision_date"])].iloc[-1])
    fill_px = anchor * 1.002   # paid up 20bps
    raw = ("Trade Date,Symbol,Action,Quantity,Price,Fees,Decision Id\n"
           f"{entry['decision_date']},SFA,BUY,100,{fill_px:.4f},2.00,{entry['decision_id']}\n").encode()
    ledger.import_fills(raw, "ACC-5")
    rows = ledger.decision_shortfall("ACC-5")
    assert rows and rows[0]["status"] == "measured"
    assert rows[0]["shortfall_bps"] == pytest.approx(20.0, abs=1.0)
    assert rows[0]["fees_usd"] == pytest.approx(2.0)


# --------------------------------------------------------------------------- #
# PIT fundamentals + reconciliation
# --------------------------------------------------------------------------- #
def test_default_fetch_appends_pit_snapshot(store):
    fundamentals.invalidate_cache()
    fundamentals.set_default_provider(lambda t: {
        "trailing_pe": 15.0, "dividend_yield": 0.02, "sector": "financials", "source": "fake"})
    try:
        f = fundamentals.fetch("PITX")
        assert f.as_of  # stamped with the fetch date
        history = store.fundamentals_history("PITX")
        assert [row["slot"] for row in history] == ["first", "last"]
        assert all(row["trailing_pe"] == pytest.approx(15.0) for row in history)
        fundamentals.fetch("PITX")   # cached — no duplicate write
        assert len(store.fundamentals_history("PITX")) == 2
    finally:
        fundamentals.set_default_provider(None)
        fundamentals.invalidate_cache()


def test_same_day_refetch_preserves_first_observation(store):
    """The 'first' slot is immutable: an intraday revision (earnings-day flip)
    updates only 'last', so what-was-knowable-at-open survives verbatim."""
    store.record_fundamentals_snapshot({
        "symbol": "PITY", "as_of": "2026-07-12", "trailing_pe": 15.0, "source": "fmp",
        "reconciliation_warnings": ["trailing_pe: fmp=15.0 vs yfinance=26.0"]})
    store.record_fundamentals_snapshot({
        "symbol": "PITY", "as_of": "2026-07-12", "trailing_pe": 22.0, "source": "yfinance"})
    rows = store.fundamentals_history("PITY")
    assert [(r["slot"], r["trailing_pe"]) for r in rows] == [("first", 15.0), ("last", 22.0)]
    assert "yfinance=26.0" in rows[0]["recon_warnings"]  # warnings frozen with the first slot
    assert all(r["retrieved_at"] for r in rows)


def test_v8_snapshots_survive_slot_migration(tmp_path, monkeypatch):
    """v8 rows (no slot column) migrate as slot='last' — recorded evidence is
    never dropped, and new same-day writes still open a fresh 'first' slot."""
    import sqlite3

    db = tmp_path / "v8.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE fundamentals_snapshots (
        symbol TEXT NOT NULL, as_of TEXT NOT NULL, dividend_yield REAL, forward_pe REAL,
        trailing_pe REAL, earnings_growth REAL, growth_basis TEXT NOT NULL DEFAULT '',
        sector TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', roe REAL,
        profit_margin REAL, debt_to_equity REAL, revenue_growth REAL, current_price REAL,
        target_mean_price REAL, analyst_rating REAL, n_analysts INTEGER,
        next_earnings_date TEXT NOT NULL DEFAULT '', PRIMARY KEY (symbol, as_of))""")
    conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT)")
    conn.execute("INSERT INTO schema_version VALUES (8, 'migration-test')")
    conn.execute("INSERT INTO fundamentals_snapshots(symbol, as_of, trailing_pe, source) "
                 "VALUES ('AAPL', '2026-07-01', 30.0, 'fmp')")
    conn.commit()
    conn.close()

    monkeypatch.setenv("HELIOS_DB_PATH", str(db))
    persistence.reset_store_for_tests()
    try:
        st = persistence.get_store()
        migrated = st.fundamentals_history("AAPL")
        assert [(r["slot"], r["trailing_pe"], r["as_of"]) for r in migrated] == [
            ("last", 30.0, "2026-07-01")]
        st.record_fundamentals_snapshot(
            {"symbol": "AAPL", "as_of": "2026-07-12", "trailing_pe": 31.0, "source": "fmp"})
        assert len(st.fundamentals_history("AAPL")) == 3  # migrated row + new first/last pair
    finally:
        persistence.reset_store_for_tests()


def test_provider_disagreement_warns_never_blocks():
    warnings = fundamentals._reconcile([
        ("fmp", {"trailing_pe": 14.0, "dividend_yield": 0.021}),
        ("yfinance", {"trailing_pe": 26.0, "dividend_yield": 0.020}),
    ])
    assert len(warnings) == 1 and "trailing_pe" in warnings[0]
    # Agreeing providers -> no warning; single provider -> no warning.
    assert fundamentals._reconcile([
        ("fmp", {"trailing_pe": 14.0}), ("yfinance", {"trailing_pe": 14.5})]) == []
    assert fundamentals._reconcile([("fmp", {"trailing_pe": 14.0})]) == []


def test_delete_account_removes_all_ledger_rows(store):
    ledger.import_fills(_fills_csv(["2026-06-01,JPM,BUY,10,200.00,1.00,"]), "ACC-DEL")
    store.record_account_snapshot({"account_id": "ACC-DEL", "as_of": "2026-06-30",
                                   "cash": 0.0, "total_value": 2000.0})
    result = store.delete_ledger_account("ACC-DEL")
    assert result["deleted"] is True and result["fills_removed"] == 1
    assert store.fills("ACC-DEL") == []
    assert store.account_snapshots("ACC-DEL") == []
    assert all(a["account_id"] != "ACC-DEL" for a in store.ledger_accounts())


# --------------------------------------------------------------------------- #
# Ledger v2 (Deep Review 2, D8): reconciliation, frozen anchors, disclosure
# --------------------------------------------------------------------------- #
def test_cash_reconciliation_flags_unexplained_movement(store):
    store.upsert_ledger_account("ACC-R")
    store.record_account_snapshot({"account_id": "ACC-R", "as_of": "2026-05-01",
                                   "cash": 50000.0, "total_value": 100000.0})
    # Cash fell $30k but recorded activity explains only a $10k buy.
    store.record_account_snapshot({"account_id": "ACC-R", "as_of": "2026-05-31",
                                   "cash": 20000.0, "total_value": 101000.0})
    ledger.import_fills(_fills_csv(["2026-05-10,JPM,BUY,50,200.00,0.00,"]), "ACC-R")
    perf = ledger.account_performance("ACC-R")
    period = perf["periods"][0]
    assert period["reconciliation"]["status"] == "mismatch"
    assert period["reconciliation"]["diff_usd"] == pytest.approx(-20000.0)
    assert any("unexplained" in w for w in perf["warnings"])
    assert "Modified-Dietz estimate" in perf["method"]


def test_reconciliation_ok_when_activity_explains_cash(store):
    store.upsert_ledger_account("ACC-OK")
    store.record_account_snapshot({"account_id": "ACC-OK", "as_of": "2026-05-01",
                                   "cash": 50000.0, "total_value": 100000.0})
    # $10,050 buy (incl fees) + $150 dividend in -> cash 50k - 10,050 + 150 = 40,100.
    store.record_account_snapshot({"account_id": "ACC-OK", "as_of": "2026-05-31",
                                   "cash": 40100.0, "total_value": 102000.0})
    ledger.import_fills(_fills_csv([
        "2026-05-10,JPM,BUY,50,200.00,50.00,",
        "2026-05-15,JPM,DIVIDEND,,,,150.00",
    ]), "ACC-OK")
    perf = ledger.account_performance("ACC-OK")
    period = perf["periods"][0]
    assert period["reconciliation"]["status"] == "ok"
    assert period["income_usd"] == pytest.approx(150.0)
    assert perf["income_usd"] == pytest.approx(150.0)


def test_shortfall_prefers_frozen_decision_price(store):
    from engine import decision_journal
    data.parse_csv(price_csv(days=200), "FRZ", "Frozen Co")
    entry = decision_journal.record_decision(
        target_kind="instrument", target_id="FRZ", my_action="BUY",
        signal={"action": "BUY", "score": 0.4})
    frozen = float(entry["decision_price"])
    fill_px = frozen * 1.001
    raw = ("Trade Date,Symbol,Action,Quantity,Price,Fees,Decision Id\n"
           f"{entry['decision_date']},FRZ,BUY,100,{fill_px:.4f},1.00,{entry['decision_id']}\n").encode()
    ledger.import_fills(raw, "ACC-F")
    # Mutate the price history — the anchor must NOT drift with it.
    shifted = data.get("FRZ").df["close"] * 1.10
    data.register(data.Instrument("FRZ", "Frozen Co", shifted.to_frame("close"), "upload", []))
    rows = ledger.decision_shortfall("ACC-F")
    assert rows[0]["status"] == "measured"
    assert rows[0]["decision_close"] == pytest.approx(frozen)
    assert "frozen decision_price" in rows[0]["anchor_basis"]
    assert rows[0]["shortfall_bps"] == pytest.approx(10.0, abs=0.5)


def test_exec_id_column_preserves_distinct_identical_fills(store):
    raw = ("Trade Date,Symbol,Action,Quantity,Price,Fees,Exec Id\n"
           "2026-06-01,JPM,BUY,100,200.00,1.00,EX-1\n"
           "2026-06-01,JPM,BUY,100,200.00,1.00,EX-2\n").encode()
    result = ledger.import_fills(raw, "ACC-X", "fills.csv")
    assert result["inserted"] == 2      # distinct executions survive
    raw_no_id = ("Trade Date,Symbol,Action,Quantity,Price,Fees\n"
                 "2026-06-02,JPM,BUY,100,200.00,1.00\n"
                 "2026-06-02,JPM,BUY,100,200.00,1.00\n").encode()
    collapsed = ledger.import_fills(raw_no_id, "ACC-X", "fills.csv")
    assert collapsed["inserted"] == 1 and collapsed["duplicates"] == 1
    assert any("execution" in w.lower() for w in collapsed["warnings"])

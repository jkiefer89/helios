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
    assert first["inserted"] == 3          # buy, sell, deposit
    assert first["skipped_non_trade"] == 1
    assert any("unrecognized action" in w for w in first["warnings"])
    assert any("future-dated" in w for w in first["warnings"])
    second = ledger.import_fills(raw, "ACC-1", "fills.csv")
    assert second["inserted"] == 0 and second["duplicates"] == 3   # re-import is a no-op
    flows = [f for f in store.fills("ACC-1") if f["side"] == "deposit"]
    assert flows and flows[0]["amount"] == pytest.approx(10000.0)


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
        assert len(history) == 1
        assert history[0]["trailing_pe"] == pytest.approx(15.0)
        fundamentals.fetch("PITX")   # cached — no duplicate write
        assert len(store.fundamentals_history("PITX")) == 1
    finally:
        fundamentals.set_default_provider(None)
        fundamentals.invalidate_cache()


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

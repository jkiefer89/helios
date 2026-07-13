"""Schema v10 data-architecture spine (deep-review completion slice).

Security master, hash-deduped raw vendor vault, silent price-restatement
ledger (adjustment shifts excluded), corporate actions, and the tamper-evident
audit chain over journal writes.
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine import data, persistence
from tests.conftest import price_series


@pytest.fixture()
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    persistence.reset_store_for_tests()
    st = persistence.get_store()
    yield st
    persistence.reset_store_for_tests()


def test_security_master_upsert_keeps_known_fields(store):
    store.record_security("JPM", name="JPMorgan Chase", price_provider="fmp_eod_adjusted")
    store.record_security("JPM", figi="BBG000DMBXR2")   # later fact, empty name
    rows = store.securities()
    row = next(r for r in rows if r["symbol"] == "JPM")
    assert row["name"] == "JPMorgan Chase"               # not clobbered by ''
    assert row["figi"] == "BBG000DMBXR2"
    assert row["price_provider"] == "fmp_eod_adjusted"
    assert row["first_seen"] and row["last_verified"]


def test_vendor_vault_dedupes_identical_payloads(store):
    payload = '{"trailing_pe": 15.0, "source": "fmp"}'
    assert store.vault_payload("fmp", "fundamentals", "JPM", payload) is True
    assert store.vault_payload("fmp", "fundamentals", "JPM", payload) is False  # unchanged
    assert store.vault_payload("fmp", "fundamentals", "JPM", payload.replace("15", "16")) is True
    entries = store.vault_entries("JPM")
    assert len(entries) == 2
    assert store.vault_payload_text(entries[0]["id"]).startswith("{")


def test_price_revisions_ignore_uniform_adjustment_shifts(store):
    idx = pd.date_range("2026-01-01", periods=50, freq="B")
    base = pd.Series(100.0 + pd.RangeIndex(50).astype(float), index=idx)
    data.register(data.Instrument("REVX", "RevX", base.to_frame("close"), "live", []))
    # Dividend re-adjustment: EVERY bar shifts by the same ratio -> no revisions.
    data.register(data.Instrument("REVX", "RevX", (base * 0.99).to_frame("close"), "live", []))
    assert store.price_revisions("REVX") == []
    # True restatement: one historical bar moves against the common shift.
    edited = base * 0.99
    edited.iloc[10] = edited.iloc[10] * 1.03
    data.register(data.Instrument("REVX", "RevX", edited.to_frame("close"), "live", []))
    revs = store.price_revisions("REVX")
    assert len(revs) == 1
    assert revs[0]["bar_date"] == str(idx[10].date())
    assert revs[0]["change_pct"] == pytest.approx(3.0, abs=0.2)


def test_corporate_actions_idempotent(store):
    actions = [
        {"action_type": "dividend", "ex_date": "2026-06-05", "value": 1.15, "source": "yfinance"},
        {"action_type": "split", "ex_date": "2020-08-31", "value": 4.0, "source": "yfinance"},
    ]
    assert store.record_corporate_actions("AAPL", actions) == 2
    assert store.record_corporate_actions("AAPL", actions) == 0   # re-capture is a no-op
    rows = store.corporate_actions("AAPL")
    assert {r["action_type"] for r in rows} == {"dividend", "split"}


def test_audit_chain_links_and_detects_tampering(store):
    store.audit_append("decision.record", {"decision_id": "dec-1", "my_action": "BUY"})
    store.audit_append("decision.outcomes", {"decision_id": "dec-1", "status": "partial"})
    store.audit_append("ledger.fills", {"inserted": 3, "accounts": ["ACC-1"]})
    check = store.audit_verify()
    assert check["status"] == "intact" and check["entries"] == 3
    # Tamper with the middle link: verification names the first bad seq.
    with store._connect() as conn:
        conn.execute("UPDATE audit_chain SET payload_hash = 'forged' WHERE seq = 2")
    broken = store.audit_verify()
    assert broken["status"] == "broken"
    assert broken["first_bad_seq"] == 2


def test_journal_writes_append_audit_links(store):
    entry = {
        "decision_id": "dec-audit-1", "target_kind": "instrument", "target_id": "JPM",
        "target_name": "JPM", "my_action": "BUY", "decision_date": "2026-07-10",
        "decision_price": 300.0, "outcome_status": "pending", "outcomes": {},
    }
    store.record_decision(entry)
    store.update_decision_outcomes("dec-audit-1", {"21": {"hit": True}}, "partial", "2026-07-12T00:00:00Z")
    check = store.audit_verify()
    assert check["status"] == "intact"
    assert check["entries"] >= 2
    with store._connect() as conn:
        actions = [r["action"] for r in conn.execute("SELECT action FROM audit_chain ORDER BY seq")]
    assert "decision.record" in actions
    assert "decision.outcomes" in actions


# --------------------------------------------------------------------------- #
# Adversarial-review locks (confirmed defects, fixed)
# --------------------------------------------------------------------------- #
def test_vault_stores_full_payload_and_hash_verifies(store):
    """The 800-char redact_secrets trap must never touch vault payloads, and
    payload_hash must verify EXACTLY what was stored."""
    import hashlib
    import json as _json
    big = _json.dumps({"rows": [{"i": i, "close": 100.0 + i} for i in range(400)]})
    assert len(big) > 8000
    assert store.vault_payload("fmp", "fundamentals", "BIG", big) is True
    entry = store.vault_entries("BIG")[0]
    stored = store.vault_payload_text(entry["id"])
    assert len(stored) >= len(big) - 100          # full payload, not 800 chars
    assert _json.loads(stored)["rows"][399]["i"] == 399
    assert hashlib.sha256(stored.encode()).hexdigest() == entry["payload_hash"]


def test_audit_chain_survives_concurrent_writers(store):
    """Concurrent appends must serialize — a benign race must never read as
    tampering (the chain is trusted evidence)."""
    import threading

    def hammer(k):
        for i in range(15):
            store.audit_append("stress", {"writer": k, "i": i})

    threads = [threading.Thread(target=hammer, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check = store.audit_verify()
    assert check["status"] == "intact"
    assert check["entries"] == 120


def test_provider_flip_is_not_a_restatement(store):
    idx = pd.date_range("2026-01-01", periods=50, freq="B")
    base = pd.Series(100.0 + pd.RangeIndex(50).astype(float), index=idx)
    data.register(data.Instrument("FLIP", "Flip", base.to_frame("close"), "live", [],
                                  price_provider="fmp_eod_adjusted"))
    # Fallback flip to yfinance with systematically different adjusted closes:
    # a labeled sourcing event, NOT vendor restatements.
    jitter = base * (1.0 + pd.Series(pd.RangeIndex(50).astype(float) % 3, index=idx) * 0.002)
    data.register(data.Instrument("FLIP", "Flip", jitter.to_frame("close"), "live", [],
                                  price_provider="yfinance"))
    assert store.price_revisions("FLIP") == []

"""Immutable lineage contracts for every decision-bearing Helios artifact."""
from __future__ import annotations

import app as helios
import pandas as pd
import pytest
from engine import (
    data, decision_journal, evidence, model_governance, persistence, portfolio,
    signal_journal,
)
from tests.conftest import price_csv, price_series


def _store(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "lineage.db"))
    persistence.reset_store_for_tests()
    store = persistence.get_store()
    assert store.available
    return store


def _evidence_ref(payload: dict, container: str) -> dict:
    nested = payload.get(container) if isinstance(payload.get(container), dict) else {}
    ref = nested.get("evidence") if isinstance(nested.get("evidence"), dict) else {}
    assert ref.get("evidence_id")
    return ref


def test_signal_and_decision_outputs_are_replayable(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    close = price_series(days=180)
    data.parse_csv(price_csv(days=180), "LINEAGE", "Lineage", source_filename="lineage.csv")

    signal = signal_journal.record_signal(
        target_kind="instrument",
        target_id="LINEAGE",
        target_name="Lineage",
        close=close,
        input_close=close,
        signal={"action": "HOLD", "score": 0.1},
        horizon_days=21,
        benchmark="SPY",
        source_counts={"upload": 1},
        eligible_for_real_research=True,
        data_mode="real",
    )
    signal_ref = _evidence_ref(signal, "metadata")
    signal_replay = evidence.verify(signal_ref["evidence_id"])
    assert signal_replay["verified"] is True
    assert signal_replay["output"] == signal
    assert signal_replay["manifest"]["series_window"]["row_count"] == 180

    decision = decision_journal.record_decision(
        target_kind="instrument",
        target_id="LINEAGE",
        my_action="HOLD",
        rationale="Await benchmark-relative confirmation.",
        signal={"action": "HOLD", "score": 0.1},
    )
    decision_ref = _evidence_ref(decision, "context")
    decision_replay = evidence.verify(decision_ref["evidence_id"])
    assert decision_replay["verified"] is True
    assert decision_replay["output"] == decision


def test_signal_and_decision_rows_roll_back_when_evidence_capture_fails(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    close = price_series(days=180)
    data.parse_csv(price_csv(days=180), "FAILCLOSE", "Fail Close", source_filename="fail-close.csv")
    monkeypatch.setattr(evidence, "capture", lambda **_kwargs: {
        "recorded": False,
        "warning": "simulated evidence failure",
    })

    with pytest.raises(RuntimeError, match="simulated evidence failure"):
        signal_journal.record_signal(
            target_kind="instrument",
            target_id="FAILCLOSE",
            target_name="Fail Close",
            close=close,
            input_close=close,
            signal={"action": "HOLD", "score": 0.1},
            horizon_days=21,
            benchmark="SPY",
            source_counts={"upload": 1},
            eligible_for_real_research=True,
            data_mode="real",
        )
    assert store.signal_journal() == []

    with pytest.raises(RuntimeError, match="simulated evidence failure"):
        decision_journal.record_decision(
            target_kind="instrument",
            target_id="FAILCLOSE",
            my_action="HOLD",
            rationale="Do not retain a decision without evidence.",
            signal={"action": "HOLD", "score": 0.1},
        )
    assert store.decision_journal() == []


def test_governance_row_rolls_back_when_evidence_capture_fails(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    model = portfolio.Model(
        id="GOV-FAIL-CLOSE",
        name="Governance Fail Close",
        mandate_key="balanced",
        mandate_context="Atomic evidence test",
        holdings=[portfolio.Holding("AAPL", 1.0, "pending")],
    )
    portfolio.register(model)
    assert portfolio._persist_model(model, source_filename="governance-fail-close.csv")["persisted"] is True
    monkeypatch.setattr(evidence, "capture", lambda **_kwargs: {
        "recorded": False,
        "warning": "simulated governance evidence failure",
    })

    with pytest.raises(RuntimeError, match="simulated governance evidence failure"):
        model_governance.record_event(
            model,
            actor="validator",
            action="submitted_for_review",
            note="Submit only when immutable evidence can be sealed.",
        )

    assert store.model_governance_events(model.id) == []


def test_governance_event_and_report_snapshot_are_replayable(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    data.parse_csv(price_csv(days=260), "GOVX", "Governance X", source_filename="govx.csv")
    model = portfolio.Model(
        id="MODEL-LINEAGE",
        name="Lineage Model",
        mandate_key="balanced",
        mandate_context="Evidence lineage test",
        holdings=[portfolio.Holding("GOVX", 1.0, "upload")],
    )
    portfolio.register(model)
    assert portfolio._persist_model(model, source_filename="lineage-model.csv")["persisted"] is True
    event_result = model_governance.record_event(
        model,
        actor="test reviewer",
        action="submitted_for_review",
        note="Freeze the proposed model state for review.",
        approval_status="pending_review",
    )
    assert event_result["recorded"] is True
    event = event_result["event"]
    event_ref = _evidence_ref(event, "metadata")
    governance_replay = evidence.verify(event_ref["evidence_id"])
    assert governance_replay["verified"] is True
    assert governance_replay["output"] == event

    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    response = helios.app.test_client().post("/api/report/snapshots", json={
        "kind": "instrument",
        "id": "GOVX",
        "report_purpose": "advisor_review",
    })
    assert response.status_code == 200
    snapshot_id = response.get_json()["snapshot"]["id"]
    snapshot = store.get_report_snapshot(snapshot_id)
    assert snapshot is not None
    report_ref = _evidence_ref(snapshot, "metadata")
    report_replay = evidence.verify(report_ref["evidence_id"])
    assert report_replay["verified"] is True
    assert report_replay["output"]["id"] == snapshot_id
    assert report_replay["output_hash"] == evidence.sha256(report_replay["output"])

    api_replay = helios.app.test_client().get(report_ref["replay_url"])
    assert api_replay.status_code == 200
    assert api_replay.get_json()["status"] == "verified"


def test_signal_forward_result_is_sealed_before_journal_update(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    inst = data.parse_csv(price_csv(days=90), "FORWARD-EV", "Forward Evidence", source_filename="forward.csv")
    benchmark_inst = data.parse_csv(price_csv(days=90), "SPY", "Benchmark Evidence", source_filename="benchmark.csv")
    close = inst.df["close"].dropna()
    benchmark_close = benchmark_inst.df["close"].dropna()
    entry = signal_journal.record_signal(
        target_kind="instrument", target_id="FORWARD-EV", target_name="Forward Evidence",
        close=close, input_close=close, signal={"action": "BUY", "score": 0.7},
        horizon_days=21, benchmark="SPY", source_counts={"upload": 1},
        eligible_for_real_research=True, data_mode="real",
    )
    assert entry["forward_status"] == "pending"
    future_index = pd.bdate_range(start=close.index.max() + pd.offsets.BDay(1), periods=25)
    future = pd.Series(
        [float(close.iloc[-1]) * (1.002 ** i) for i in range(1, 26)],
        index=future_index,
    )
    extended = pd.concat([close, future])
    data.register(data.Instrument("FORWARD-EV", "Forward Evidence", extended.to_frame("close"), "upload", []))
    benchmark_future = pd.Series(
        [float(benchmark_close.iloc[-1]) * (1.001 ** i) for i in range(1, 26)],
        index=future_index,
    )
    data.register(data.Instrument(
        "SPY", "Benchmark Evidence", pd.concat([benchmark_close, benchmark_future]).to_frame("close"), "upload", [],
    ))

    signal_journal.refresh_forward_results()

    measured = next(row for row in store.signal_journal() if row["dedupe_key"] == entry["dedupe_key"])
    assert measured["forward_status"] == "measured"
    with store._connect() as conn:
        evidence_id = conn.execute(
            "SELECT evidence_id FROM evidence_snapshots WHERE artifact_kind = 'signal_forward_result'"
        ).fetchone()["evidence_id"]
    replay = evidence.replay(evidence_id)
    assert replay["verified"] is True
    assert replay["replay_verified"] is True
    assert replay["output"]["forward_status"] == "measured"
    assert replay["output"]["benchmark_result_pct"] is not None
    assert replay["output"]["alpha_pct"] is not None
    assert replay["input"]["benchmark_series"]["row_count"] == 115
    assert replay["manifest"]["parent_evidence_id"] == entry["metadata"]["evidence"]["evidence_id"]


def test_decision_forward_outcome_is_sealed_before_status_update(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    inst = data.parse_csv(price_csv(days=90), "DECISION-EV", "Decision Evidence", source_filename="decision.csv")
    benchmark_inst = data.parse_csv(price_csv(days=90), "SPY", "Decision Benchmark", source_filename="decision-benchmark.csv")
    close = inst.df["close"].dropna()
    benchmark_close = benchmark_inst.df["close"].dropna()
    entry = decision_journal.record_decision(
        target_kind="instrument", target_id="DECISION-EV", my_action="BUY",
        rationale="Prospective evidence test.", signal={"action": "BUY", "score": 0.6},
    )
    assert entry["outcome_status"] == "pending"
    future_index = pd.bdate_range(start=close.index.max() + pd.offsets.BDay(1), periods=25)
    future = pd.Series(
        [float(close.iloc[-1]) * (1.001 ** i) for i in range(1, 26)],
        index=future_index,
    )
    extended = pd.concat([close, future])
    data.register(data.Instrument("DECISION-EV", "Decision Evidence", extended.to_frame("close"), "upload", []))
    benchmark_future = pd.Series(
        [float(benchmark_close.iloc[-1]) * (1.0005 ** i) for i in range(1, 26)],
        index=future_index,
    )
    data.register(data.Instrument(
        "SPY", "Decision Benchmark", pd.concat([benchmark_close, benchmark_future]).to_frame("close"), "upload", [],
    ))

    assert decision_journal.refresh_pending_outcomes() == 1

    updated = next(row for row in store.decision_journal() if row["decision_id"] == entry["decision_id"])
    assert updated["outcome_status"] == "partial"
    with store._connect() as conn:
        evidence_id = conn.execute(
            "SELECT evidence_id FROM evidence_snapshots WHERE artifact_kind = 'decision_forward_outcome'"
        ).fetchone()["evidence_id"]
    replay = evidence.replay(evidence_id)
    assert replay["verified"] is True
    assert replay["replay_verified"] is True
    assert replay["output"]["outcome_status"] == "partial"
    assert replay["output"]["outcomes"]["21"]["benchmark_return_pct"] is not None
    assert replay["input"]["benchmark_series"]["row_count"] == 115
    assert replay["manifest"]["parent_evidence_id"] == entry["context"]["evidence"]["evidence_id"]

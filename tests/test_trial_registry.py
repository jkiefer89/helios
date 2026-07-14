"""Preregistered trial identity, linkage, assessment, and API contracts."""
from __future__ import annotations

import app as helios
import pandas as pd
import pytest

from engine import costs, data, evidence, persistence, portfolio, signal_journal, trials
from tests.conftest import price_csv, price_series


def _store(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "trials.db"))
    persistence.reset_store_for_tests()
    store = persistence.get_store()
    assert store.available
    return store


def _protocol() -> dict:
    return {
        "hypothesis": "Positive directional alpha persists after implementation costs.",
        "primary_metric": "net_directional_alpha_pct",
        "horizon_days": 21,
        "step_days": 21,
        "benchmark": "SPY",
        "cost_assumptions": {
            "commission_bps_per_side": 1,
            "spread_bps_per_side": 2,
            "slippage_bps_per_side": 3,
            "market_impact_bps_per_side": 4,
            "tax_drag_bps": 5,
            "idle_cash_pct": 2,
        },
        "success_thresholds": {
            "min_observations": 5,
            "min_hit_rate_pct": 50,
            "min_net_alpha_pct": 0,
            "max_false_positive_rate_pct": 50,
            "confidence_level_pct": 90,
        },
        "regimes": ["risk_on", "neutral", "risk_off"],
        "owner": "Research Committee",
        "allowed_sources": ["live", "upload"],
        "freshness_days": 3,
        "expected_aum_usd": 1_000_000_000,
        "max_position_pct": 10,
        "max_adv_participation_pct": 10,
        "planned_variants": ["registered composite"],
        "deleted_variants": ["unregistered threshold sweep"],
    }


def _upload(symbol="TRIALX", days=260):
    return data.parse_csv(price_csv(days=days), symbol, symbol, source_filename=f"{symbol}.csv")


def test_trial_protocol_is_explicit_immutable_and_one_open_per_target(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    _upload()
    result = trials.register(
        target_kind="instrument", target_id="TRIALX", protocol=_protocol(), actor="Owner",
    )
    assert result["created"] is True
    trial = result["trial"]
    assert trial["status"] == "open"
    assert trial["protocol_hash"]
    assert trial["registration_snapshot_hash"]
    assert trial["protocol"]["expected_aum_usd"] == 1_000_000_000

    duplicate = trials.register(
        target_kind="instrument", target_id="TRIALX", protocol={**_protocol(), "hypothesis": "Other"}, actor="Owner",
    )
    assert duplicate["created"] is False
    assert duplicate["status_code"] == 409

    result["trial"]["protocol"]["hypothesis"] = "mutated client copy"
    assert trials.get(trial["trial_id"])["protocol"]["hypothesis"] != "mutated client copy"


def test_trial_rejects_implicit_or_incomplete_protocol(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    _upload()
    with pytest.raises(ValueError, match="missing"):
        trials.register(
            target_kind="instrument", target_id="TRIALX",
            protocol={"hypothesis": "incomplete"}, actor="Owner",
        )


def test_only_scheduled_post_registration_signals_link_to_trial(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    inst = _upload()
    trial = trials.register(
        target_kind="instrument", target_id="TRIALX", protocol=_protocol(), actor="Owner",
    )["trial"]
    close = inst.df["close"].dropna()
    with persistence.get_store()._connect() as conn:
        conn.execute(
            "UPDATE prospective_trials SET starts_on = ? WHERE trial_id = ?",
            ((close.index.max() - pd.offsets.BDay(1)).isoformat(), trial["trial_id"]),
        )
    exploratory = signal_journal.record_signal(
        target_kind="instrument", target_id="TRIALX", target_name="TRIALX",
        close=close, input_close=close, signal={"action": "BUY", "score": 0.5},
        horizon_days=21, benchmark="SPY", source_counts={"upload": 1},
        eligible_for_real_research=True, data_mode="real",
        metadata={"endpoint": "/api/analyze"},
    )
    scheduled = signal_journal.record_signal(
        target_kind="instrument", target_id="TRIALX", target_name="TRIALX",
        close=close, input_close=close, signal={"action": "BUY", "score": 0.6},
        horizon_days=21, benchmark="SPY", source_counts={"upload": 1},
        eligible_for_real_research=True, data_mode="real",
        metadata={"endpoint": "auto_snapshot"},
    )
    assert exploratory["trial_id"] == ""
    assert exploratory["recording_source"] == "exploratory"
    assert scheduled["trial_id"] == trial["trial_id"]
    assert scheduled["recording_source"] == "scheduled"
    assert trials.get(trial["trial_id"])["assessment"]["observations"]["scheduled_count"] == 1


def test_same_day_pre_registration_input_cannot_link_to_trial(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    inst = _upload()
    trial = trials.register(
        target_kind="instrument", target_id="TRIALX", protocol=_protocol(), actor="Owner",
    )["trial"]
    close = inst.df["close"].dropna()
    same_day = str(close.index.max().date())
    with persistence.get_store()._connect() as conn:
        conn.execute(
            "UPDATE prospective_trials SET starts_on = ? WHERE trial_id = ?",
            (same_day, trial["trial_id"]),
        )

    recorded = signal_journal.record_signal(
        target_kind="instrument", target_id="TRIALX", target_name="TRIALX",
        close=close, input_close=close, signal={"action": "BUY", "score": 0.6},
        horizon_days=21, benchmark="SPY", source_counts={"upload": 1},
        eligible_for_real_research=True, data_mode="real",
        metadata={"endpoint": "auto_snapshot"},
    )

    assert recorded["trial_id"] == ""
    assert recorded["recording_source"] == "scheduled"
    assert "must end after" in recorded["metadata"]["trial_link_warning"].lower()


def test_completed_trial_requires_passing_replay_verified_assessment(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    _upload()
    trial = trials.register(
        target_kind="instrument", target_id="TRIALX", protocol=_protocol(), actor="Owner",
    )["trial"]
    monkeypatch.setattr(trials, "assess", lambda _trial: {"passed": True})
    monkeypatch.setattr(trials, "verified_assessment_evidence", lambda _trial_id: None)

    with pytest.raises(ValueError, match="replay-verify"):
        trials.close(
            trial["trial_id"], status="completed",
            note="Attempt completion without sealed evidence.", actor="Owner",
        )


def test_instrument_trial_allows_new_bars_but_detects_restatement(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    inst = _upload(days=100)
    trial = trials.register(
        target_kind="instrument", target_id="TRIALX", protocol=_protocol(), actor="Owner",
    )["trial"]
    original = inst.df[["close"]].copy()
    future_index = pd.bdate_range(
        start=original.index.max() + pd.offsets.BDay(1), periods=10,
    )
    future = pd.DataFrame(
        {"close": [float(original["close"].iloc[-1]) * (1.001 ** i) for i in range(1, 11)]},
        index=future_index,
    )
    extended = pd.concat([original, future])
    data.register(data.Instrument("TRIALX", "TRIALX", extended, "upload", []))
    assert trials.get(trial["trial_id"])["assessment"]["checks"]["target_version_unchanged"] is True

    revised = extended.copy()
    revised.iloc[0, 0] *= 1.2
    data.register(data.Instrument("TRIALX", "TRIALX", revised, "upload", []))
    assert trials.get(trial["trial_id"])["assessment"]["checks"]["target_version_unchanged"] is False
    rejected = signal_journal.record_signal(
        target_kind="instrument", target_id="TRIALX", target_name="TRIALX",
        close=revised["close"], input_close=revised["close"],
        signal={"action": "BUY", "score": 0.71}, horizon_days=21,
        benchmark="SPY", source_counts={"upload": 1},
        eligible_for_real_research=True, data_mode="real",
        metadata={"endpoint": "auto_snapshot"},
    )
    assert rejected["trial_id"] == ""


def test_model_trial_freezes_each_holding_history_prefix(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    first = _upload("TRIALA", days=120)
    _upload("TRIALB", days=120)
    model = portfolio.Model(
        id="MODEL-TRIAL", name="Model Trial", mandate_key="balanced", mandate_context="",
        holdings=[portfolio.Holding("TRIALA", 0.5), portfolio.Holding("TRIALB", 0.5)],
    )
    portfolio.register(model)
    trial = trials.register(
        target_kind="model", target_id=model.id, protocol=_protocol(), actor="Owner",
    )["trial"]
    snapshot = trial["registration_snapshot"]["target"]
    assert {row["ticker"] for row in snapshot["holdings"]} == {"TRIALA", "TRIALB"}
    assert all(row["history"]["series_hash"] for row in snapshot["holdings"])

    revised = first.df.copy()
    revised.iloc[0, revised.columns.get_loc("close")] *= 1.15
    data.register(data.Instrument("TRIALA", "TRIALA", revised, "upload", []))
    assessment = trials.get(trial["trial_id"])["assessment"]
    assert assessment["checks"]["target_version_unchanged"] is False


def test_trial_enforces_allowed_sources_and_freshness(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    _upload("SOURCE-X", days=120)
    live_only = {**_protocol(), "allowed_sources": ["live"]}
    with pytest.raises(ValueError, match="not allowed"):
        trials.register(
            target_kind="instrument", target_id="SOURCE-X", protocol=live_only, actor="Owner",
        )

    index = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=10), periods=120)
    frame = pd.DataFrame({"close": range(100, 220)}, index=index)
    data.register(data.Instrument("STALE-TRIAL", "Stale Trial", frame, "upload", []))
    with pytest.raises(ValueError, match="calendar day"):
        trials.register(
            target_kind="instrument", target_id="STALE-TRIAL", protocol=_protocol(), actor="Owner",
        )


def test_full_implementation_cost_stack_and_sell_direction():
    assumptions = _protocol()["cost_assumptions"]
    breakdown = costs.implementation_costs(assumptions, benchmark_return_pct=5)
    assert breakdown["round_trip_trading_bps"] == 20
    assert breakdown["tax_drag_pct"] == 0.05
    assert breakdown["idle_cash_drag_pct"] == 0.1
    assert costs.net_directional_alpha("BUY", 1.0, assumptions, benchmark_return_pct=5) == 0.65
    assert costs.net_directional_alpha("SELL", -1.0, assumptions, benchmark_return_pct=5) == 0.65
    assert costs.net_directional_alpha("HOLD", 1.0, assumptions) is None


def test_trial_enforces_declared_multiplicity_and_regime_coverage(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    _upload()
    trial = trials.register(
        target_kind="instrument", target_id="TRIALX", protocol=_protocol(), actor="Owner",
    )["trial"]
    assessment = trial["assessment"]
    assert assessment["multiplicity"]["declared_variant_count"] == 2
    assert assessment["multiplicity"]["adjusted_confidence_pct"] == 95.0
    assert assessment["multiplicity"]["enforced_in_confidence_check"] is True
    assert assessment["checks"]["regime_coverage"] is False
    implementation = assessment["implementation_evidence"]
    assert set(implementation) >= {"paper", "proposed", "actual"}
    assert implementation["paper"]["status"] == "collecting"
    assert implementation["proposed"]["status"] == "not_started"
    assert implementation["actual"]["status"] == "not_applicable"
    assert implementation["analysis_only"] is True
    assert implementation["no_execution"] is True


def test_model_trial_uses_explicitly_mapped_custodian_actuals_and_seals_assessment(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    _upload("ACTA", days=200)
    _upload("ACTB", days=200)
    model = portfolio.Model(
        id="ACTUAL-MODEL", name="Actual Model", mandate_key="balanced", mandate_context="",
        holdings=[portfolio.Holding("ACTA", 0.5), portfolio.Holding("ACTB", 0.5)],
    )
    portfolio.register(model)
    trial = trials.register(
        target_kind="model", target_id=model.id, protocol=_protocol(), actor="Owner",
    )["trial"]
    dates = data.get("ACTA").df.index
    with store._connect() as conn:
        conn.execute(
            "UPDATE prospective_trials SET starts_on = ? WHERE trial_id = ?",
            (pd.Timestamp(dates[-40]).isoformat(), trial["trial_id"]),
        )
    store.upsert_ledger_account("ACTUAL-ACCOUNT", model_id=model.id)
    store.record_account_snapshot({
        "account_id": "ACTUAL-ACCOUNT", "as_of": str(dates[-40].date()),
        "cash": 1_000.0, "total_value": 100_000.0,
    })
    store.record_account_snapshot({
        "account_id": "ACTUAL-ACCOUNT", "as_of": str(dates[-1].date()),
        "cash": 1_000.0, "total_value": 104_000.0,
    })

    assessment = trials.get(trial["trial_id"])["assessment"]
    actual = assessment["implementation_evidence"]["actual"]
    assert actual["status"] == "measured"
    assert actual["mapped_account_count"] == 1
    assert actual["accounts"][0]["account_id"] == "ACTUAL-ACCOUNT"
    assert actual["accounts"][0]["actual_twr_net_pct"] is not None

    recorded = trials.record_assessment(trial["trial_id"], actor="Validation Committee")
    replay = evidence.replay(recorded["evidence"]["evidence_id"])
    assert replay["verified"] is True
    assert replay["replay_verified"] is True
    assert replay["artifact_kind"] == "trial_assessment"
    assert replay["output"]["implementation_evidence"]["actual"]["mapped_account_count"] == 1


def test_trial_routes_are_read_pure_and_close_is_explicit_post(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    _upload()
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    client = helios.app.test_client()
    created = client.post("/api/trials", json={
        "target_kind": "instrument", "target_id": "TRIALX",
        "protocol": _protocol(), "actor": "Owner",
    })
    assert created.status_code == 200
    created_trial = created.get_json()["trial"]
    trial_id = created_trial["trial_id"]
    assert created_trial["actor"] == "local-operator"
    with store._connect() as conn:
        before = int(conn.execute("SELECT COUNT(*) FROM audit_chain").fetchone()[0])
    assert client.get("/api/trials").status_code == 200
    assert client.get(f"/api/trials/{trial_id}").status_code == 200
    with store._connect() as conn:
        after = int(conn.execute("SELECT COUNT(*) FROM audit_chain").fetchone()[0])
    assert after == before
    assert client.patch(f"/api/trials/{trial_id}", json={}).status_code == 405
    closed = client.post(f"/api/trials/{trial_id}/close", json={
        "status": "withdrawn", "note": "Protocol withdrawn before efficacy review.", "actor": "Owner",
    })
    assert closed.status_code == 200
    assert closed.get_json()["trial"]["status"] == "withdrawn"
    with store._connect() as conn:
        close_actor = conn.execute(
            "SELECT actor FROM audit_chain WHERE action = 'trial.close' ORDER BY seq DESC LIMIT 1"
        ).fetchone()["actor"]
    assert close_actor == "local-operator"


def test_trial_assessment_route_uses_authenticated_actor(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    helios.app.config.update(TESTING=True)
    captured = {}
    monkeypatch.setattr(trials, "record_assessment", lambda trial_id, *, actor: {
        "trial_id": trial_id,
        "assessment": {"state": "pending"},
        "evidence": {"recorded": True},
        **(captured.update(actor=actor) or {}),
    })

    response = helios.app.test_client().post("/api/trials/trial-route/assess")

    assert response.status_code == 200
    assert response.get_json()["assessment"]["state"] == "pending"
    assert captured["actor"] == "local-operator"

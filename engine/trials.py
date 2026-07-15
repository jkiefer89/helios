"""Preregistered prospective research trials and deployment evidence."""
from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timezone
from statistics import NormalDist
from typing import Any

from . import (
    costs, data, evidence, persistence, portfolio, provenance, risk_exposure,
    signal_journal,
)

REQUIRED_PROTOCOL_FIELDS = (
    "hypothesis", "primary_metric", "horizon_days", "step_days", "benchmark",
    "cost_assumptions", "success_thresholds", "regimes", "owner",
    "allowed_sources", "freshness_days", "expected_aum_usd",
    "max_position_pct", "max_adv_participation_pct", "planned_variants",
    "deleted_variants",
)
REQUIRED_THRESHOLDS = (
    "min_observations", "min_hit_rate_pct", "min_net_alpha_pct",
    "max_false_positive_rate_pct", "confidence_level_pct",
)

TRIAL_THRESHOLD_POLICY_VERSION = "institutional-v1"
TRIAL_THRESHOLD_FLOORS: dict[str, dict[str, float | int]] = {
    "instrument": {
        "min_observations": 30,
        "min_hit_rate_pct": 52.0,
        "min_net_alpha_pct": 0.10,
        "max_false_positive_rate_pct": 48.0,
        "confidence_level_pct": 95.0,
    },
    "pure_growth": {
        "min_observations": 40,
        "min_hit_rate_pct": 53.0,
        "min_net_alpha_pct": 0.15,
        "max_false_positive_rate_pct": 47.0,
        "confidence_level_pct": 95.0,
    },
    "balanced": {
        "min_observations": 50,
        "min_hit_rate_pct": 54.0,
        "min_net_alpha_pct": 0.10,
        "max_false_positive_rate_pct": 46.0,
        "confidence_level_pct": 95.0,
    },
    "income": {
        "min_observations": 50,
        "min_hit_rate_pct": 55.0,
        "min_net_alpha_pct": 0.08,
        "max_false_positive_rate_pct": 45.0,
        "confidence_level_pct": 95.0,
    },
    "capital_preservation": {
        "min_observations": 60,
        "min_hit_rate_pct": 56.0,
        "min_net_alpha_pct": 0.05,
        "max_false_positive_rate_pct": 44.0,
        "confidence_level_pct": 95.0,
    },
    "cd_alternative": {
        "min_observations": 60,
        "min_hit_rate_pct": 57.0,
        "min_net_alpha_pct": 0.03,
        "max_false_positive_rate_pct": 43.0,
        "confidence_level_pct": 95.0,
    },
}

# Historical evidence is a different decision surface from a preregistered
# deployment trial.  These floors are deliberately explicit and mandate-aware
# so "enough rows to calculate" is never confused with "edge supported".
HISTORICAL_THRESHOLD_POLICY_VERSION = "historical-economic-v1"
HISTORICAL_VALIDATION_FLOORS: dict[str, dict[str, float | int]] = {
    "instrument": {
        "min_observations": 12,
        "min_hit_rate_pct": 51.0,
        "min_net_alpha_pct": 0.05,
        "max_false_positive_rate_pct": 49.0,
        "min_benchmark_coverage_pct": 80.0,
        "min_regime_count": 1,
    },
    "pure_growth": {
        "min_observations": 12,
        "min_hit_rate_pct": 52.0,
        "min_net_alpha_pct": 0.10,
        "max_false_positive_rate_pct": 48.0,
        "min_benchmark_coverage_pct": 80.0,
        "min_regime_count": 1,
    },
    "balanced": {
        "min_observations": 16,
        "min_hit_rate_pct": 53.0,
        "min_net_alpha_pct": 0.08,
        "max_false_positive_rate_pct": 47.0,
        "min_benchmark_coverage_pct": 80.0,
        "min_regime_count": 2,
    },
    "income": {
        "min_observations": 16,
        "min_hit_rate_pct": 54.0,
        "min_net_alpha_pct": 0.05,
        "max_false_positive_rate_pct": 46.0,
        "min_benchmark_coverage_pct": 80.0,
        "min_regime_count": 2,
    },
    "capital_preservation": {
        "min_observations": 20,
        "min_hit_rate_pct": 55.0,
        "min_net_alpha_pct": 0.03,
        "max_false_positive_rate_pct": 45.0,
        "min_benchmark_coverage_pct": 80.0,
        "min_regime_count": 2,
    },
    "cd_alternative": {
        "min_observations": 20,
        "min_hit_rate_pct": 56.0,
        "min_net_alpha_pct": 0.02,
        "max_false_positive_rate_pct": 44.0,
        "min_benchmark_coverage_pct": 80.0,
        "min_regime_count": 2,
    },
}


def register(
    *,
    target_kind: str,
    target_id: str,
    protocol: dict[str, Any],
    actor: str,
    supersedes_trial_id: str = "",
) -> dict[str, Any]:
    kind = str(target_kind or "").strip().lower()
    target = str(target_id or "").strip()
    if kind not in {"instrument", "model"} or not target:
        raise ValueError("Trial target must be an instrument or model.")
    frozen = _validate_protocol(protocol, target_kind=kind, target_id=target)
    target_snapshot, model_version = target_snapshot_for(kind, target, frozen)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    trial_id = f"trial-{uuid.uuid4().hex[:20]}"
    protocol_hash = evidence.sha256({
        "target_kind": kind, "target_id": target, "protocol": frozen,
    })
    registration_snapshot = {
        "registered_at": now,
        "target": target_snapshot,
        "protocol_hash": protocol_hash,
        "analysis_only": True,
        "no_execution": True,
        "no_return_guarantee": True,
    }
    result = persistence.get_store().create_prospective_trial({
        "trial_id": trial_id,
        "created_at": now,
        "actor": str(actor or "Local research owner")[:120],
        "target_kind": kind,
        "target_id": target.upper() if kind == "instrument" else target,
        "model_version": model_version,
        "target_snapshot_hash": _target_integrity_hash(target_snapshot),
        "starts_on": now,
        "supersedes_trial_id": supersedes_trial_id,
        "protocol": frozen,
        "protocol_hash": protocol_hash,
        "registration_snapshot": registration_snapshot,
        "registration_snapshot_hash": evidence.sha256(registration_snapshot),
    })
    if not result.get("created"):
        return result
    return {**result, "trial": enrich(result["trial"])}


def list_trials(*, limit: int = 200, target_kind: str = "", target_id: str = "") -> list[dict[str, Any]]:
    return [
        enrich(row)
        for row in persistence.get_store().prospective_trials(
            limit=limit, target_kind=target_kind, target_id=target_id,
        )
    ]


def get(trial_id: str) -> dict[str, Any] | None:
    row = persistence.get_store().prospective_trial(trial_id)
    return enrich(row) if row else None


def close(trial_id: str, *, status: str, note: str, actor: str) -> dict[str, Any]:
    clean_status = str(status or "").strip().lower()
    if clean_status not in {"completed", "withdrawn", "invalidated"}:
        raise ValueError("Trial status must be completed, withdrawn, or invalidated.")
    if len(str(note or "").strip()) < 5:
        raise ValueError("A close note is required.")
    if clean_status == "completed":
        trial = persistence.get_store().prospective_trial(trial_id)
        if not trial:
            raise ValueError("Unknown trial.")
        current = assess(trial)
        if not current.get("passed"):
            raise ValueError("A trial can be completed only after every preregistered success check passes.")
        policy = deployment_policy_status(
            trial.get("protocol") or {},
            target_kind=str(trial.get("target_kind") or ""),
            target_id=str(trial.get("target_id") or ""),
        )
        if not policy["passed"]:
            raise ValueError(
                "A trial can be completed only when its frozen thresholds meet the current "
                "institutional deployment policy."
            )
        if not verified_assessment_evidence(trial_id):
            raise ValueError("Record and replay-verify the passing trial assessment before completion.")
    result = persistence.get_store().close_prospective_trial(
        trial_id, clean_status, str(note).strip(), str(actor or "Local research owner")[:120],
    )
    if result.get("trial"):
        result["trial"] = enrich(result["trial"])
    return result


def record_assessment(trial_id: str, *, actor: str) -> dict[str, Any]:
    """Persist the current prospective assessment as an immutable evidence envelope."""
    trial = persistence.get_store().prospective_trial(trial_id)
    if not trial:
        raise ValueError("Unknown trial.")
    assessment = assess(trial)
    evidence_id = evidence.new_id("trial-assessment")
    reference = evidence.capture(
        evidence_id=evidence_id,
        artifact_kind="trial_assessment",
        target_kind=str(trial.get("target_kind") or ""),
        target_id=str(trial.get("target_id") or ""),
        input_payload={
            "trial_id": trial_id,
            "protocol_hash": trial.get("protocol_hash"),
            "registration_snapshot_hash": trial.get("registration_snapshot_hash"),
            "recorded_by": persistence.redact_secrets(actor)[:120],
            "assessment_basis": assessment_replay_basis(trial, assessment),
        },
        output_payload=assessment,
        evidence_manifest=evidence.manifest(
            extra={
                "trial_id": trial_id,
                "prospective_only": True,
                "actual_basis": "custodian_account_snapshots",
            },
        ),
    )
    if not reference.get("recorded"):
        raise RuntimeError("Trial assessment evidence could not be persisted.")
    return {"trial_id": trial_id, "assessment": assessment, "evidence": reference}


def active_link(target_kind: str, target_id: str, metadata: dict[str, Any]) -> tuple[str, str]:
    source = "scheduled" if str(metadata.get("endpoint") or "") == "auto_snapshot" else "exploratory"
    if source != "scheduled":
        return "", source
    trial = active_trial(target_kind, target_id)
    if not trial:
        return "", source
    input_end = str(metadata.get("input_end_date") or "")[:10]
    starts_on = str(trial.get("starts_on") or "")[:10]
    if not input_end or input_end <= starts_on:
        metadata["trial_link_warning"] = (
            "Scheduled signal input must end after the preregistration date; trial linkage withheld."
        )
        return "", source
    return str(trial["trial_id"]), source


def active_trial(target_kind: str, target_id: str) -> dict[str, Any] | None:
    """Return the open frozen trial only while its registered target is intact."""
    rows = persistence.get_store().prospective_trials(
        limit=1, target_kind=target_kind, target_id=target_id,
    )
    trial = next((row for row in rows if row.get("status") == "open"), None)
    if not trial:
        return None
    try:
        current, _ = target_snapshot_for(target_kind, target_id, trial.get("protocol") or {})
    except ValueError:
        return None
    if (_target_integrity_hash(current) != trial.get("target_snapshot_hash")
            or not _registered_history_unchanged(trial, current)):
        return None
    return trial


def scheduled_horizon(target_kind: str, target_id: str, default: int = 21) -> int:
    trial = active_trial(target_kind, target_id)
    if not trial:
        return int(default)
    return max(1, int((trial.get("protocol") or {}).get("horizon_days") or default))


def validate_decision_link(trial_id: str, target_kind: str, target_id: str) -> str:
    if not trial_id:
        return ""
    trial = persistence.get_store().prospective_trial(trial_id)
    if not trial or trial.get("status") != "open":
        raise ValueError("Decision trial link must reference an open trial.")
    if trial.get("target_kind") != target_kind or str(trial.get("target_id")) != str(target_id):
        raise ValueError("Decision target does not match the linked trial.")
    return trial_id


def enrich(trial: dict[str, Any]) -> dict[str, Any]:
    if not trial:
        return {}
    assessment = assess(trial)
    return {**trial, "assessment": assessment}


def assess(trial: dict[str, Any]) -> dict[str, Any]:
    protocol = trial.get("protocol") or {}
    protocol_horizon = int(protocol.get("horizon_days") or 21)
    entries = [
        row for row in signal_journal.list_entries(limit=2000)
        if row.get("trial_id") == trial.get("trial_id")
        and row.get("recording_source") == "scheduled"
        and str(row.get("created_at") or "") >= str(trial.get("starts_on") or "")
        and str(row.get("input_end_date") or "") > str(trial.get("starts_on") or "")[:10]
        and int(row.get("horizon_days") or 0) == protocol_horizon
    ]
    measured = [
        row for row in entries
        if row.get("forward_status") == "measured"
        and row.get("action_label") in {"BUY", "SELL"}
        and row.get("alpha_pct") is not None
    ]
    net_values = [
        costs.net_directional_alpha(
            row["action_label"], row.get("alpha_pct"), protocol["cost_assumptions"],
            benchmark_return_pct=row.get("benchmark_result_pct"),
        )
        for row in measured
    ]
    net_values = [float(value) for value in net_values if value is not None]
    hits = [value > 0 for value in net_values]
    false_positives = sum(1 for value in net_values if value <= 0)
    count = len(net_values)
    hit_rate = 100.0 * sum(hits) / count if count else None
    avg_net = sum(net_values) / count if count else None
    fp_rate = 100.0 * false_positives / count if count else None
    declared_variant_count = max(
        1, len(protocol["planned_variants"]) + len(protocol["deleted_variants"]),
    )
    familywise_alpha = max(
        0.0001,
        1.0 - float(protocol["success_thresholds"]["confidence_level_pct"]) / 100.0,
    )
    per_variant_alpha = familywise_alpha / declared_variant_count
    adjusted_confidence_pct = (1.0 - per_variant_alpha) * 100.0
    confidence = _wilson_interval(sum(hits), count, adjusted_confidence_pct)
    current_hash = ""
    target_matches = False
    try:
        current_snapshot, _ = target_snapshot_for(
            trial["target_kind"], trial["target_id"], protocol,
        )
        current_hash = _target_integrity_hash(current_snapshot)
        target_matches = (
            current_hash == trial["target_snapshot_hash"]
            and _registered_history_unchanged(trial, current_snapshot)
        )
    except ValueError:
        pass
    capacity = _capacity_check(trial, protocol)
    regime_robustness = _regime_robustness(trial, measured, net_values, protocol)
    implementation = _implementation_evidence(trial, protocol, entries, measured, net_values)
    thresholds = protocol["success_thresholds"]
    checks = {
        "target_version_unchanged": target_matches,
        "minimum_observations": count >= int(thresholds["min_observations"]),
        "hit_rate": hit_rate is not None and hit_rate >= float(thresholds["min_hit_rate_pct"]),
        "net_alpha": avg_net is not None and avg_net >= float(thresholds["min_net_alpha_pct"]),
        "false_positive_rate": fp_rate is not None and fp_rate <= float(thresholds["max_false_positive_rate_pct"]),
        "confidence_lower_bound": confidence[0] is not None and confidence[0] >= float(thresholds["min_hit_rate_pct"]),
        "capacity": capacity["passed"],
        "regime_coverage": regime_robustness["coverage_passed"],
    }
    complete = count >= int(thresholds["min_observations"])
    passed = complete and all(checks.values())
    return {
        "state": "passed" if passed else "failed" if complete else "collecting",
        "passed": passed,
        "checks": checks,
        "observations": {
            "scheduled_count": len(entries),
            "measured_directional_count": count,
            "pending_count": sum(1 for row in entries if row.get("forward_status") != "measured"),
            "hold_count": sum(1 for row in entries if row.get("action_label") == "HOLD"),
        },
        "metrics": {
            "directional_hit_rate_pct": round(hit_rate, 4) if hit_rate is not None else None,
            "avg_net_directional_alpha_pct": round(avg_net, 6) if avg_net is not None else None,
            "false_positive_rate_pct": round(fp_rate, 4) if fp_rate is not None else None,
            "hit_rate_confidence_interval_pct": {
                "low": confidence[0], "high": confidence[1],
            },
        },
        "multiplicity": {
            "declared_variant_count": declared_variant_count,
            "familywise_alpha": round(familywise_alpha, 6),
            "bonferroni_alpha": round(per_variant_alpha, 6),
            "adjusted_confidence_pct": round(adjusted_confidence_pct, 4),
            "enforced_in_confidence_check": True,
            "deleted_variants": protocol["deleted_variants"],
        },
        "regime_robustness": regime_robustness,
        "capacity": capacity,
        "implementation_evidence": implementation,
        "cost_basis": costs.implementation_costs(protocol["cost_assumptions"]),
        "current_target_snapshot_hash": current_hash,
        "methodology": {
            "prospective_only": True,
            "scheduled_observations_only": True,
            "benchmark_alpha_required": True,
            "hold_is_abstention": True,
            "analysis_only": True,
            "no_execution": True,
        },
    }


def assessment_replay_basis(trial: dict[str, Any], assessment: dict[str, Any]) -> dict[str, Any]:
    protocol = trial.get("protocol") or {}
    horizon = int(protocol.get("horizon_days") or 21)
    entries = [
        row for row in signal_journal.list_entries(limit=2000)
        if row.get("trial_id") == trial.get("trial_id")
        and row.get("recording_source") == "scheduled"
        and str(row.get("created_at") or "") >= str(trial.get("starts_on") or "")
        and str(row.get("input_end_date") or "") > str(trial.get("starts_on") or "")[:10]
        and int(row.get("horizon_days") or 0) == horizon
    ]
    return {
        "protocol": protocol,
        "entries": [
            {
                "action_label": row.get("action_label"),
                "forward_status": row.get("forward_status"),
                "alpha_pct": row.get("alpha_pct"),
                "benchmark_result_pct": row.get("benchmark_result_pct"),
            }
            for row in entries
        ],
        "fixed_checks": {
            key: bool((assessment.get("checks") or {}).get(key))
            for key in ("target_version_unchanged", "capacity", "regime_coverage")
        },
    }


def replay_assessment_basis(basis: dict[str, Any]) -> dict[str, Any]:
    protocol = basis.get("protocol") or {}
    thresholds = protocol.get("success_thresholds") or {}
    entries = basis.get("entries") or []
    measured = [
        row for row in entries
        if row.get("forward_status") == "measured"
        and row.get("action_label") in {"BUY", "SELL"}
        and row.get("alpha_pct") is not None
    ]
    net_values = [
        costs.net_directional_alpha(
            row["action_label"], row.get("alpha_pct"), protocol.get("cost_assumptions") or {},
            benchmark_return_pct=row.get("benchmark_result_pct"),
        )
        for row in measured
    ]
    net_values = [float(value) for value in net_values if value is not None]
    count = len(net_values)
    hit_count = sum(1 for value in net_values if value > 0)
    hit_rate = 100.0 * hit_count / count if count else None
    avg_net = sum(net_values) / count if count else None
    fp_rate = 100.0 * (count - hit_count) / count if count else None
    declared = max(1, len(protocol.get("planned_variants") or []) + len(protocol.get("deleted_variants") or []))
    familywise_alpha = max(0.0001, 1.0 - float(thresholds.get("confidence_level_pct") or 0.0) / 100.0)
    adjusted_confidence = (1.0 - familywise_alpha / declared) * 100.0
    confidence = _wilson_interval(hit_count, count, adjusted_confidence)
    checks = {
        **(basis.get("fixed_checks") or {}),
        "minimum_observations": count >= int(thresholds.get("min_observations") or 0),
        "hit_rate": hit_rate is not None and hit_rate >= float(thresholds.get("min_hit_rate_pct") or 0.0),
        "net_alpha": avg_net is not None and avg_net >= float(thresholds.get("min_net_alpha_pct") or 0.0),
        "false_positive_rate": fp_rate is not None and fp_rate <= float(thresholds.get("max_false_positive_rate_pct") or 100.0),
        "confidence_lower_bound": confidence[0] is not None and confidence[0] >= float(thresholds.get("min_hit_rate_pct") or 0.0),
    }
    complete = count >= int(thresholds.get("min_observations") or 0)
    passed = complete and all(bool(value) for value in checks.values())
    return {
        "state": "passed" if passed else "failed" if complete else "collecting",
        "passed": passed,
        "checks": checks,
        "observations": {
            "scheduled_count": len(entries),
            "measured_directional_count": count,
            "pending_count": sum(1 for row in entries if row.get("forward_status") != "measured"),
            "hold_count": sum(1 for row in entries if row.get("action_label") == "HOLD"),
        },
        "metrics": {
            "directional_hit_rate_pct": round(hit_rate, 4) if hit_rate is not None else None,
            "avg_net_directional_alpha_pct": round(avg_net, 6) if avg_net is not None else None,
            "false_positive_rate_pct": round(fp_rate, 4) if fp_rate is not None else None,
            "hit_rate_confidence_interval_pct": {"low": confidence[0], "high": confidence[1]},
        },
    }


def verified_assessment_evidence(trial_id: str) -> dict[str, Any] | None:
    trial = persistence.get_store().prospective_trial(trial_id)
    if not trial:
        return None
    snapshots = persistence.get_store().evidence_snapshots(
        artifact_kind="trial_assessment",
        target_kind=str(trial.get("target_kind") or ""),
        target_id=str(trial.get("target_id") or ""),
        limit=100,
    )
    for snapshot in snapshots:
        if str((snapshot.get("input") or {}).get("trial_id") or "") != trial_id:
            continue
        replay = evidence.replay(str(snapshot.get("evidence_id") or ""))
        if replay.get("replay_verified"):
            return replay
    return None


def target_snapshot_for(
    kind: str,
    target_id: str,
    protocol: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    rules = protocol or {}
    if kind == "instrument":
        inst = data.get(target_id)
        if inst is None:
            raise ValueError("Trial registration requires eligible real instrument history.")
        return ({"kind": "instrument", **_history_snapshot(inst, rules)}, 0)
    model = portfolio.get(target_id)
    if model is None:
        raise ValueError("Unknown model trial target.")
    events = persistence.get_store().model_governance_events(model.id, limit=1)
    version = int(events[0].get("version") or 0) if events else 0
    holding_snapshots = []
    for holding in sorted(model.holdings, key=lambda row: row.ticker):
        inst = data.get(holding.ticker)
        if inst is None:
            raise ValueError(f"Trial registration requires history for {holding.ticker}.")
        holding_snapshots.append({
            "ticker": holding.ticker,
            "weight": round(float(holding.weight), 12),
            "declared_source": holding.source,
            "history": _history_snapshot(inst, rules),
        })
    return ({
        "kind": "model", "id": model.id, "name": model.name,
        "mandate_key": model.mandate_key, "mandate_context": model.mandate_context,
        "thesis": model.thesis, "thesis_params": model.thesis_params,
        "holdings": holding_snapshots,
        "governance_version": version,
    }, version)


def _target_integrity_hash(snapshot: dict[str, Any]) -> str:
    if snapshot.get("kind") == "instrument":
        return evidence.sha256({
            "kind": "instrument",
            "symbol": snapshot.get("symbol"),
            "source": snapshot.get("source"),
            "provider": snapshot.get("provider"),
        })
    return evidence.sha256({
        "kind": "model",
        "id": snapshot.get("id"),
        "mandate_key": snapshot.get("mandate_key"),
        "mandate_context": snapshot.get("mandate_context"),
        "thesis": snapshot.get("thesis"),
        "thesis_params": snapshot.get("thesis_params"),
        "governance_version": snapshot.get("governance_version"),
        "holdings": [
            {
                "ticker": row.get("ticker"),
                "weight": row.get("weight"),
                "declared_source": row.get("declared_source"),
            }
            for row in snapshot.get("holdings") or []
        ],
    })


def _registered_history_unchanged(trial: dict[str, Any], current: dict[str, Any]) -> bool:
    registered = (trial.get("registration_snapshot") or {}).get("target") or {}
    registered_histories = (
        [registered]
        if registered.get("kind") == "instrument"
        else [row.get("history") or {} for row in registered.get("holdings") or []]
    )
    for history in registered_histories:
        inst = data.get(str(history.get("symbol") or ""))
        if inst is None:
            return False
        cutoff = str(history.get("last_date") or "")
        close = inst.df["close"].dropna()
        prefix = close[close.index <= cutoff] if cutoff else close
        if (
            len(prefix) != int(history.get("row_count") or 0)
            or evidence.sha256(evidence.series_payload(prefix)) != history.get("series_hash")
        ):
            return False
    return True


def _history_snapshot(inst: data.Instrument, protocol: dict[str, Any]) -> dict[str, Any]:
    close = inst.df["close"].dropna() if "close" in inst.df else None
    if close is None or close.empty:
        raise ValueError(f"Trial registration requires retained history for {inst.symbol}.")
    allowed_sources = set(protocol.get("allowed_sources") or {"live", "upload"})
    if inst.source not in allowed_sources:
        raise ValueError(f"{inst.symbol} source '{inst.source}' is not allowed by the trial protocol.")
    p = provenance.instrument(
        inst.source,
        len(close),
        price_provider=getattr(inst, "price_provider", ""),
    )
    if not p["eligible_for_real_research"]:
        raise ValueError(p["reason"] or f"{inst.symbol} is not eligible for a prospective trial.")
    last = close.index.max().date()
    freshness_days = int(protocol.get("freshness_days") or 3)
    age = max(0, (date.today() - last).days)
    if age > freshness_days:
        raise ValueError(
            f"{inst.symbol} history is {age} calendar day(s) old; trial limit is {freshness_days}."
        )
    return {
        "symbol": inst.symbol,
        "name": inst.name,
        "source": inst.source,
        "provider": inst.price_provider,
        "retrieved_at": inst.retrieved_at,
        "series_hash": evidence.sha256(evidence.series_payload(close)),
        "row_count": len(close),
        "first_date": str(close.index.min().date()),
        "last_date": str(last),
        "age_days_at_snapshot": age,
    }


def threshold_policy(target_kind: str, target_id: str) -> dict[str, Any]:
    kind = str(target_kind or "").strip().lower()
    target = str(target_id or "").strip()
    if kind == "instrument":
        profile = "instrument"
        mandate_key = ""
    elif kind == "model":
        model = portfolio.get(target)
        if model is None:
            raise ValueError("Unknown model trial target.")
        mandate_key = str(model.mandate_key or "balanced").strip().lower()
        profile = mandate_key if mandate_key in TRIAL_THRESHOLD_FLOORS else "balanced"
    else:
        raise ValueError("Trial target must be an instrument or model.")
    return {
        "version": TRIAL_THRESHOLD_POLICY_VERSION,
        "profile": profile,
        "mandate_key": mandate_key,
        "floors": dict(TRIAL_THRESHOLD_FLOORS[profile]),
        "non_overridable": True,
    }


def historical_threshold_policy(*, target_kind: str, mandate_key: str = "") -> dict[str, Any]:
    """Return non-overridable historical economic-evidence floors.

    This helper accepts a mandate directly because validation may run against
    an in-memory model before it has been persisted in the library.
    """
    kind = str(target_kind or "").strip().lower()
    mandate = str(mandate_key or "balanced").strip().lower()
    if kind == "instrument":
        profile = "instrument"
        mandate = ""
    elif kind == "model":
        profile = mandate if mandate in HISTORICAL_VALIDATION_FLOORS else "balanced"
    else:
        raise ValueError("Historical validation target must be an instrument or model.")
    return {
        "version": HISTORICAL_THRESHOLD_POLICY_VERSION,
        "profile": profile,
        "mandate_key": mandate,
        "floors": dict(HISTORICAL_VALIDATION_FLOORS[profile]),
        "non_overridable": True,
        "basis": (
            "Historical ranking evidence must separately pass data, method, and "
            "mandate-specific after-cost economic-quality checks."
        ),
    }


def deployment_policy_status(
    protocol: dict[str, Any], *, target_kind: str, target_id: str,
) -> dict[str, Any]:
    policy = threshold_policy(target_kind, target_id)
    thresholds = protocol.get("success_thresholds") if isinstance(protocol, dict) else None
    if not isinstance(thresholds, dict):
        return {**policy, "passed": False, "blockers": ["success_thresholds missing"]}
    floors = policy["floors"]
    blockers: list[str] = []
    try:
        if int(thresholds.get("min_observations")) < int(floors["min_observations"]):
            blockers.append("min_observations")
        if float(thresholds.get("min_hit_rate_pct")) < float(floors["min_hit_rate_pct"]):
            blockers.append("min_hit_rate_pct")
        if float(thresholds.get("min_net_alpha_pct")) < float(floors["min_net_alpha_pct"]):
            blockers.append("min_net_alpha_pct")
        if float(thresholds.get("max_false_positive_rate_pct")) > float(floors["max_false_positive_rate_pct"]):
            blockers.append("max_false_positive_rate_pct")
        if float(thresholds.get("confidence_level_pct")) < float(floors["confidence_level_pct"]):
            blockers.append("confidence_level_pct")
    except (TypeError, ValueError, OverflowError):
        blockers.append("invalid_threshold_values")
    return {**policy, "passed": not blockers, "blockers": blockers}


def _validate_protocol(
    protocol: dict[str, Any], *, target_kind: str, target_id: str,
) -> dict[str, Any]:
    if not isinstance(protocol, dict):
        raise ValueError("Trial protocol must be a JSON object.")
    missing = [field for field in REQUIRED_PROTOCOL_FIELDS if field not in protocol]
    if missing:
        raise ValueError(f"Trial protocol is missing: {', '.join(missing)}.")
    out = evidence.normalize(protocol)
    for field in ("hypothesis", "primary_metric", "benchmark", "owner"):
        if not str(out.get(field) or "").strip():
            raise ValueError(f"Trial protocol field '{field}' is required.")
    for field in ("horizon_days", "step_days", "freshness_days"):
        value = int(out[field])
        if value <= 0 or value > 2520:
            raise ValueError(f"Trial protocol field '{field}' is out of range.")
        out[field] = value
    for field in ("expected_aum_usd", "max_position_pct", "max_adv_participation_pct"):
        value = float(out[field])
        if value <= 0:
            raise ValueError(f"Trial protocol field '{field}' must be positive.")
        out[field] = value
    if not isinstance(out["allowed_sources"], list) or not out["allowed_sources"]:
        raise ValueError("Trial protocol must declare allowed_sources.")
    if not set(out["allowed_sources"]) <= {"live", "upload"}:
        raise ValueError("Trial allowed_sources may contain only live or upload.")
    for field in ("regimes", "planned_variants", "deleted_variants"):
        if not isinstance(out[field], list):
            raise ValueError(f"Trial protocol field '{field}' must be a list.")
    costs_map = out.get("cost_assumptions")
    if not isinstance(costs_map, dict) or any(field not in costs_map for field in costs.IMPLEMENTATION_COST_FIELDS):
        raise ValueError("Trial cost_assumptions must declare the full implementation-cost stack.")
    for field in costs.IMPLEMENTATION_COST_FIELDS:
        value = float(costs_map[field])
        if value < 0 or value > 10_000:
            raise ValueError(f"Cost assumption '{field}' is out of range.")
        costs_map[field] = value
    thresholds = out.get("success_thresholds")
    if not isinstance(thresholds, dict) or any(field not in thresholds for field in REQUIRED_THRESHOLDS):
        raise ValueError("Trial success_thresholds are incomplete.")
    thresholds["min_observations"] = int(thresholds["min_observations"])
    for field in REQUIRED_THRESHOLDS[1:]:
        thresholds[field] = float(thresholds[field])
        if not math.isfinite(thresholds[field]):
            raise ValueError(f"Trial threshold '{field}' must be finite.")
    if not 0 <= thresholds["min_hit_rate_pct"] <= 100:
        raise ValueError("Trial min_hit_rate_pct must be between 0 and 100.")
    if not 0 <= thresholds["max_false_positive_rate_pct"] <= 100:
        raise ValueError("Trial max_false_positive_rate_pct must be between 0 and 100.")
    if not 0 < thresholds["confidence_level_pct"] < 100:
        raise ValueError("Trial confidence_level_pct must be between 0 and 100.")
    policy = deployment_policy_status(out, target_kind=target_kind, target_id=target_id)
    if not policy["passed"]:
        required = ", ".join(
            f"{field}={policy['floors'][field]}" for field in policy["blockers"]
            if field in policy["floors"]
        )
        raise ValueError(
            "Trial success thresholds are weaker than the non-overridable "
            f"{policy['profile']} deployment floor ({required})."
        )
    out["threshold_policy"] = {
        key: value for key, value in policy.items() if key not in {"passed", "blockers"}
    }
    return out


def _capacity_check(trial: dict[str, Any], protocol: dict[str, Any]) -> dict[str, Any]:
    if trial.get("target_kind") != "model":
        return {"passed": True, "status": "not_applicable", "expected_aum_usd": protocol["expected_aum_usd"]}
    model = portfolio.get(trial.get("target_id"))
    if model is None:
        return {"passed": False, "status": "model_missing"}
    top_weight = max((float(holding.weight) * 100 for holding in model.holdings), default=0.0)
    try:
        pack = risk_exposure.analyze_model_risk(model, aum_usd=float(protocol["expected_aum_usd"]))
        capacity = pack.get("capacity") or {}
    except Exception as exc:
        return {"passed": False, "status": "unavailable", "message": str(exc)}
    passed = (
        top_weight <= float(protocol["max_position_pct"])
        and capacity.get("status") in {"within_capacity", "watch"}
        and int(capacity.get("unsized_count") or 0) == 0
        and all(
            float(row.get("one_day_participation_pct") or 0.0) <= float(protocol["max_adv_participation_pct"])
            for row in capacity.get("holdings") or []
            if row.get("one_day_participation_pct") is not None
        )
    )
    return {
        **capacity,
        "passed": passed,
        "top_position_pct": round(top_weight, 4),
        "max_position_pct": protocol["max_position_pct"],
        "max_adv_participation_pct": protocol["max_adv_participation_pct"],
    }


def _implementation_evidence(
    trial: dict[str, Any],
    protocol: dict[str, Any],
    signal_entries: list[dict[str, Any]],
    measured_signals: list[dict[str, Any]],
    signal_net_values: list[float],
) -> dict[str, Any]:
    store = persistence.get_store()
    trial_id = str(trial.get("trial_id") or "")
    decisions = [
        row for row in store.decision_journal(limit=1000)
        if str(row.get("trial_id") or "") == trial_id
    ]
    horizon_key = str(int(protocol.get("horizon_days") or 21))
    proposed_values: list[float] = []
    for decision in decisions:
        outcome = (decision.get("outcomes") or {}).get(horizon_key) or {}
        alpha = outcome.get("alpha_pct")
        action = _directional_action(decision.get("my_action"))
        if action and alpha is not None:
            value = costs.net_directional_alpha(
                action, alpha, protocol["cost_assumptions"],
                benchmark_return_pct=outcome.get("benchmark_return_pct"),
            )
            if value is not None:
                proposed_values.append(float(value))

    decision_ids = {str(row.get("decision_id") or "") for row in decisions}
    fills = [row for row in store.fills(limit=20_000) if str(row.get("decision_id") or "") in decision_ids]
    notional = sum(abs(float(row.get("shares") or 0.0) * float(row.get("price") or 0.0)) for row in fills)
    observed_fees = sum(max(0.0, float(row.get("fees") or 0.0)) for row in fills)
    observed_fee_bps = (observed_fees / notional * 10_000.0) if notional > 0 else None
    try:
        from . import ledger
        shortfall_rows = [
            row for row in ledger.decision_shortfall("")
            if str(row.get("decision_id") or "") in decision_ids and row.get("shortfall_bps") is not None
        ]
    except Exception:
        shortfall_rows = []
    weighted_shortfall_bps = _weighted_shortfall(shortfall_rows)

    actual_accounts = []
    if trial.get("target_kind") == "model":
        try:
            from . import ledger

            for account in store.ledger_accounts():
                if str(account.get("model_id") or "") != str(trial.get("target_id") or ""):
                    continue
                assessment_end = max(
                    (str(row.get("forward_end_date") or "") for row in measured_signals),
                    default="",
                ) or str(trial.get("closed_at") or "")[:10] or date.today().isoformat()
                comparison = ledger.actual_vs_paper(
                    str(account.get("account_id") or ""),
                    start=str(trial.get("starts_on") or "")[:10],
                    end=assessment_end,
                )
                actual = comparison.get("actual") or {}
                actual_accounts.append({
                    "account_id": account.get("account_id"),
                    "status": actual.get("status"),
                    "period": actual.get("period"),
                    "actual_twr_net_pct": actual.get("twr_net_pct"),
                    "paper_return_pct": (comparison.get("paper") or {}).get("return_pct"),
                    "actual_minus_paper_pct": comparison.get("gap_pct"),
                    "fees_usd": actual.get("fees_usd"),
                    "cash_drag_est_pct": actual.get("cash_drag_est_pct"),
                    "basis": actual.get("basis") or actual.get("note"),
                })
        except Exception:
            actual_accounts = []
    measured_actual = [row for row in actual_accounts if row.get("status") == "measured"]
    actual_gaps = [
        float(row["actual_minus_paper_pct"])
        for row in measured_actual
        if row.get("actual_minus_paper_pct") is not None
    ]

    return {
        "paper": {
            "status": "measured" if signal_net_values else "collecting",
            "scheduled_count": len(signal_entries),
            "measured_count": len(signal_net_values),
            "avg_net_directional_alpha_pct": _mean(signal_net_values),
            "cost_basis": "full preregistered modeled stack",
        },
        "proposed": {
            "status": "measured" if proposed_values else "collecting" if decisions else "not_started",
            "decision_count": len(decisions),
            "measured_count": len(proposed_values),
            "avg_net_directional_alpha_pct": _mean(proposed_values),
            "cost_basis": "full preregistered modeled stack; no execution inferred",
        },
        "actual": {
            "status": (
                "measured" if measured_actual else
                "awaiting_snapshots" if actual_accounts else
                "unmapped" if trial.get("target_kind") == "model" else
                "not_applicable"
            ),
            "basis": "custodian account snapshots and fills mapped explicitly to the trial model",
            "assessment_window": {
                "start": str(trial.get("starts_on") or "")[:10],
                "end": max(
                    (str(row.get("forward_end_date") or "") for row in measured_signals),
                    default="",
                ) or str(trial.get("closed_at") or "")[:10] or date.today().isoformat(),
            },
            "accounts": actual_accounts,
            "mapped_account_count": len(actual_accounts),
            "measured_account_count": len(measured_actual),
            "linked_fill_count": len(fills),
            "linked_decision_count": len({row.get("decision_id") for row in fills}),
            "observed_notional_usd": round(notional, 2),
            "observed_fees_usd": round(observed_fees, 2),
            "observed_fee_bps": round(observed_fee_bps, 4) if observed_fee_bps is not None else None,
            "observed_shortfall_bps": weighted_shortfall_bps,
            "measured_count": len(measured_actual),
            "avg_actual_minus_paper_pct": _mean(actual_gaps),
            "observed_components": [
                "custodian_snapshots", "custodian_fees", "cash_weight",
                "external_flows", "decision_price_shortfall",
            ],
            "unobserved_components": (
                [] if shortfall_rows else ["spread_slippage_market_impact_attribution"]
            ),
        },
        "analysis_only": True,
        "no_execution": True,
    }


def _directional_action(action: Any) -> str:
    value = str(action or "").upper()
    if value in {"BUY", "ADD"}:
        return "BUY"
    if value in {"SELL", "TRIM"}:
        return "SELL"
    return ""


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _weighted_shortfall(rows: list[dict[str, Any]]) -> float | None:
    weighted = []
    for row in rows:
        value = row.get("shortfall_bps")
        if value is None:
            continue
        weight = abs(float(row.get("fill_notional") or row.get("notional") or 1.0))
        weighted.append((float(value), max(weight, 1.0)))
    if not weighted:
        return None
    total_weight = sum(weight for _, weight in weighted)
    return round(sum(value * weight for value, weight in weighted) / total_weight, 4)


def _regime_robustness(
    trial: dict[str, Any],
    measured: list[dict[str, Any]],
    net_values: list[float],
    protocol: dict[str, Any],
) -> dict[str, Any]:
    declared = [str(value).strip().lower().replace("_", "-") for value in protocol.get("regimes") or []]
    buckets: dict[str, list[float]] = {name: [] for name in declared}
    for row, net_value in zip(measured, net_values):
        label = _trial_regime_at(trial, row, protocol)
        if label in buckets:
            buckets[label].append(float(net_value))
    minimum_total = int((protocol.get("success_thresholds") or {}).get("min_observations") or 5)
    minimum_per_regime = max(1, minimum_total // max(1, len(declared) * 2))
    min_net = float((protocol.get("success_thresholds") or {}).get("min_net_alpha_pct") or 0.0)
    max_false_positive = float(
        (protocol.get("success_thresholds") or {}).get("max_false_positive_rate_pct") or 50.0,
    )
    rows = []
    for label in declared:
        values = buckets[label]
        average = sum(values) / len(values) if values else None
        fp_rate = 100.0 * sum(1 for value in values if value <= 0) / len(values) if values else None
        rows.append({
            "regime": label,
            "count": len(values),
            "avg_net_directional_alpha_pct": round(average, 6) if average is not None else None,
            "false_positive_rate_pct": round(fp_rate, 4) if fp_rate is not None else None,
            "coverage_passed": len(values) >= minimum_per_regime,
            "performance_passed": (
                average is not None and average >= min_net
                and fp_rate is not None and fp_rate <= max_false_positive
            ),
        })
    coverage_passed = bool(rows) and all(row["coverage_passed"] for row in rows)
    performance_passed = coverage_passed and all(row["performance_passed"] for row in rows)
    return {
        "status": "passed" if performance_passed else "failed" if coverage_passed else "collecting",
        "coverage_passed": coverage_passed,
        "performance_passed": performance_passed,
        "minimum_observations_per_regime": minimum_per_regime,
        "rows": rows,
        "basis": "Regimes are classified from price history available at each scheduled signal date; every preregistered regime must meet coverage and net-evidence thresholds.",
    }


def _trial_regime_at(
    trial: dict[str, Any], row: dict[str, Any], protocol: dict[str, Any],
) -> str:
    from . import regime

    symbol = str(protocol.get("benchmark") or "SPY").upper()
    inst = data.get(symbol)
    close = None
    source = ""
    if inst is not None and inst.source in {"live", "upload"} and "close" in inst.df:
        close = inst.df["close"].dropna()
        source = inst.source
    elif trial.get("target_kind") == "instrument":
        inst = data.get(str(trial.get("target_id") or ""))
        if inst is not None and inst.source in {"live", "upload"} and "close" in inst.df:
            close = inst.df["close"].dropna()
            symbol = inst.symbol
            source = inst.source
    else:
        model = portfolio.get(str(trial.get("target_id") or ""))
        if model is not None:
            try:
                series = portfolio.build_series(model, allow_sample=False, allow_simulated=False)
                close = series.close.dropna()
                symbol = model.id
                source = "model-real"
            except ValueError:
                close = None
    if close is None or close.empty:
        return "unclassified"
    cutoff = str(row.get("input_end_date") or "")
    frozen = close.loc[:cutoff] if cutoff else close
    if len(frozen) < 60:
        return "unclassified"
    return str(regime.classify_regime(frozen, symbol=symbol, source=source).get("label") or "unclassified")


def _wilson_interval(hits: int, count: int, confidence_pct: float) -> tuple[float | None, float | None]:
    if count <= 0:
        return None, None
    confidence = min(0.9999, max(0.5, float(confidence_pct) / 100.0))
    z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    p = hits / count
    denom = 1.0 + z * z / count
    center = (p + z * z / (2 * count)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * count)) / count) / denom
    return round(max(0.0, center - margin) * 100, 4), round(min(1.0, center + margin) * 100, 4)

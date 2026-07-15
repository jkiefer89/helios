"""Model validation dashboard.

This module combines existing deterministic governance, walk-forward evidence,
and Signal Journal facts into one review payload. It does not change Helios
analytics, rebalance models, execute trades, or bypass provenance gates.
"""
from __future__ import annotations

from statistics import NormalDist, stdev
from typing import Any

import numpy as np

from . import costs, evidence_lab, model_governance, portfolio, trials
from ._common import avg as _avg

# Ranking uses the FIRST ~80% of walk-forward windows (chronological); the
# last ~20% stays untouched by selection and confirms (or refutes) the
# champion. Below this many measured holdout windows we report
# "insufficient_windows" honestly instead of a noise statistic.
_RANKING_SPLIT = 0.8
_HOLDOUT_MIN_MEASURED = 8
_RANKING_MIN_MEASURED = 8
_MIN_BENCHMARK_COVERAGE_PCT = 80.0


def dashboard(
    *,
    models: list[portfolio.Model] | None = None,
    horizon_days: int = 21,
    train_window: int = 252,
    step: int = 21,
) -> dict[str, Any]:
    current_models = models if models is not None else portfolio.all_models()
    governance = model_governance.payload(current_models)
    governance_by_id = {row["id"]: row for row in governance.get("models", [])}
    rows = [
        _validation_row(
            model,
            governance_by_id.get(model.id, {}),
            horizon_days=horizon_days,
            train_window=train_window,
            step=step,
        )
        for model in current_models
    ]
    eligible = [row for row in rows if row["validation_state"] == "edge_supported"]
    eligible.sort(key=lambda row: (row["validation_score"], row["walk_forward"]["window_count"], row["model_name"]), reverse=True)
    blocked = [row for row in rows if row["validation_state"] != "edge_supported"]
    ordered = []
    for index, row in enumerate(eligible):
        role = "champion" if index == 0 else "challenger"
        ordered.append({**row, "role": role})
    ordered.extend({**row, "role": "blocked"} for row in blocked)
    champion = ordered[0] if ordered and ordered[0]["role"] == "champion" else None
    challengers = [row for row in ordered if row["role"] == "challenger"]
    alerts = _dashboard_alerts(ordered)
    selection: dict[str, Any] = {
        "n_trials": len(eligible),
        "basis": ("Champion is the max ranking score over "
                  f"{len(eligible)} model(s) evaluated on shared walk-forward history; "
                  "the winning score is upward-biased by selection. Ranking uses the "
                  "chronological first ~80% of windows; holdout_confirmation reports "
                  "the untouched last ~20%."),
        # n_trials undercounts real selection pressure by construction: it
        # cannot see horizon/train-window/step sweeps, the decay horizons that
        # feed each score, or models tried and deleted (review finding).
        "n_trials_not_counted": (
            "alternative horizon/train_window/step parameterizations, the decay "
            "horizons inside each score, and previously deleted models — treat "
            "the adjusted band as a LOWER bound on selection uncertainty"),
    }
    if champion:
        selection["champion_adjusted"] = _selection_adjusted_ci(
            champion.get("ci_inputs") or {}, max(1, len(eligible)))
        overlap = max(1.0, float(horizon_days) / max(1, int(step)))
        selection["deflated_sharpe"] = _deflated_sharpe(
            [a for _, a in champion.get("_alpha_series") or []],
            [_sharpe([a for _, a in row.get("_alpha_series") or []]) for row in eligible],
            overlap_factor=overlap)
        selection["pbo"] = _pbo_cscv(
            {row["model_id"]: dict(row.get("_alpha_series") or []) for row in eligible},
            overlap_factor=overlap)
        prospective = champion.get("prospective_validation") or {}
        selection["prospective_confirmation"] = {
            "measured_count": prospective.get("measured_count"),
            "hit_rate_pct": prospective.get("hit_rate_pct"),
            "avg_alpha_pct": prospective.get("avg_alpha_pct"),
            "avg_alpha_after_default_costs_pct": costs.net_of_default_costs(
                prospective.get("avg_alpha_pct")),
            "basis": "Out-of-sample Signal Journal record for the champion — "
                     "untouched by ranking by construction; alpha gross, net companion "
                     "at the disclosed default round trip.",
        }
    for row in ordered:
        row.pop("_alpha_series", None)
    return {
        "selection": selection,
        "models": ordered,
        "champion": champion,
        "challengers": challengers,
        "alerts": alerts,
        "summary": {
            "model_count": len(rows),
            "eligible_count": len(eligible),
            "blocked_count": len(blocked),
            "champion_model_id": champion["model_id"] if champion else None,
            "challenger_count": len(challengers),
            "alert_count": len(alerts),
            "governance_available": bool((governance.get("summary") or {}).get("available")),
        },
        "parameters": {"horizon_days": int(horizon_days), "train_window": int(train_window), "step": int(step)},
        "methodology": {
            "analysis_only": True,
            "paper_tracking_only": True,
            "no_execution": True,
            "does_not_change_analytics": True,
            "ranking_basis": ("Review ranking combines walk-forward hit rate, alpha, "
                              "false-positive rate, signal decay, and governance state — "
                              "computed on the chronological first ~80% of windows so the "
                              "last ~20% stays untouched for holdout confirmation."),
            "selection_bias": ("The champion is a max-of-N choice and its raw score is "
                               "upward-biased; see the selection block for the "
                               "family-wise-adjusted interval and holdout stats."),
        },
        "disclaimer": "Model validation is analysis-only. It does not execute trades, guarantee outcomes, or override Helios provenance gates.",
    }


def _validation_row(
    model: portfolio.Model,
    governance: dict[str, Any],
    *,
    horizon_days: int,
    train_window: int,
    step: int,
) -> dict[str, Any]:
    evidence = evidence_lab.analyze_model(
        model,
        horizon_days=horizon_days,
        train_window=train_window,
        step=step,
    )
    if evidence.get("evidence_unavailable") or not evidence.get("eligible_for_real_research"):
        verdicts = historical_verdicts(model, evidence)
        return {
            "model_id": model.id,
            "model_name": model.name,
            "mandate": model.mandate_key,
            "role": "blocked",
            "validation_state": "blocked",
            "validation_score": None,
            "validation_grade": "Blocked",
            "evidence_unavailable": True,
            "reason": evidence.get("reason") or "",
            "required_action": evidence.get("required_action") or "Provide eligible real price history.",
            "walk_forward": evidence.get("summary") or {},
            "false_positives": evidence.get("false_positives") or {},
            "regime_sensitivity": evidence.get("regime_sensitivity") or [],
            "decay": evidence.get("decay") or [],
            "confidence_bands": evidence.get("confidence_bands") or {},
            "prospective_validation": evidence.get("prospective_validation") or {},
            "governance": _governance_summary(governance),
            "validation_verdicts": verdicts,
            "drift_alerts": [{
                "severity": "blocked",
                "title": "Validation blocked",
                "detail": evidence.get("required_action") or evidence.get("reason") or "Eligible real history is required.",
            }],
            "methodology": _row_methodology(),
            "disclaimer": evidence.get("disclaimer") or "",
        }
    walk = evidence.get("summary") or {}
    false_positives = evidence.get("false_positives") or {}
    decay = evidence.get("decay") or []
    # Winner's-curse guard (review finding): the ranking statistic and the
    # champion's displayed evidence were the SAME number on the SAME sample —
    # max-of-N noisy estimates is upward-biased. Rank on the chronological
    # first ~80% of windows; the untouched last ~20% independently confirms.
    windows = sorted(evidence.get("windows") or [], key=lambda r: str(r.get("signal_date") or ""))
    split = int(len(windows) * _RANKING_SPLIT)
    ranking_stats = _segment_stats(windows[:split])
    holdout_stats = _segment_stats(windows[split:])
    verdicts = historical_verdicts(model, evidence, ranking_stats=ranking_stats)
    if not verdicts["method_valid"]["passed"]:
        return {
            "model_id": model.id,
            "model_name": model.name,
            "mandate": model.mandate_key,
            "role": "blocked",
            "validation_state": "method_invalid" if verdicts["data_valid"]["passed"] else "data_invalid",
            "validation_score": None,
            "validation_grade": "Insufficient evidence",
            "evidence_unavailable": True,
            "reason": verdicts["method_valid"]["detail"],
            "required_action": verdicts["method_valid"]["required_action"],
            "ranking_basis": {
                **ranking_stats,
                "min_measured": _RANKING_MIN_MEASURED,
                "min_benchmark_coverage_pct": _MIN_BENCHMARK_COVERAGE_PCT,
            },
            "walk_forward": walk,
            "false_positives": false_positives,
            "regime_sensitivity": evidence.get("regime_sensitivity") or [],
            "decay": decay,
            "confidence_bands": evidence.get("confidence_bands") or {},
            "prospective_validation": evidence.get("prospective_validation") or {},
            "governance": _governance_summary(governance),
            "validation_verdicts": verdicts,
            "drift_alerts": [{
                "severity": "blocked",
                "title": "Validation evidence insufficient",
                "detail": "Directional alpha evidence does not meet the ranking floor.",
            }],
            "methodology": _row_methodology(),
            "disclaimer": evidence.get("disclaimer") or "",
        }
    rank_walk = {
        "hit_rate_pct": ranking_stats["hit_rate_pct"] if ranking_stats["hit_rate_pct"] is not None
        else walk.get("hit_rate_pct"),
        "avg_alpha_pct": ranking_stats["avg_alpha_pct"] if ranking_stats["avg_alpha_pct"] is not None
        else walk.get("avg_alpha_pct"),
    }
    rank_fp = {
        "rate_pct": ranking_stats["false_positive_rate_pct"]
        if ranking_stats["false_positive_rate_pct"] is not None
        else false_positives.get("rate_pct"),
    }
    score = _validation_score(rank_walk, rank_fp, decay, governance)
    if holdout_stats["measured_count"] >= _HOLDOUT_MIN_MEASURED:
        holdout_confirmation = {**holdout_stats, "status": "ok"}
    else:
        holdout_confirmation = {"status": "insufficient_windows",
                                "measured_count": holdout_stats["measured_count"],
                                "min_required": _HOLDOUT_MIN_MEASURED}
    alerts = _drift_alerts(walk, false_positives, decay, evidence.get("prospective_validation") or {}, governance)
    edge_supported = bool(verdicts["edge_supported"]["passed"])
    if not edge_supported:
        alerts.insert(0, {
            "severity": "blocked",
            "title": "Economic edge not supported",
            "detail": verdicts["edge_supported"]["detail"],
        })
    return {
        "ranking_basis": {
            "basis": ("Score is computed on the chronological first "
                      f"~{int(_RANKING_SPLIT * 100)}% of walk-forward windows; the last "
                      f"~{int((1 - _RANKING_SPLIT) * 100)}% is untouched by ranking."),
            **ranking_stats,
        },
        "holdout_confirmation": holdout_confirmation,
        "ci_inputs": _ci_inputs(windows),
        "_alpha_series": [
            (str(r.get("signal_date") or ""), float(value))
            for r in windows
            for value in [costs.directional_alpha(r.get("action_label"), r.get("alpha_pct"))]
            if value is not None
        ],
        "model_id": model.id,
        "model_name": model.name,
        "mandate": model.mandate_key,
        "role": "candidate",
        "validation_state": "edge_supported" if edge_supported else "edge_not_supported",
        "validation_score": score if edge_supported else None,
        "validation_grade": _grade(score) if edge_supported else "Not supported",
        "evidence_unavailable": False,
        "reason": evidence.get("reason") or "",
        "required_action": evidence.get("required_action") or "",
        "walk_forward": walk,
        "false_positives": false_positives,
        "regime_sensitivity": evidence.get("regime_sensitivity") or [],
        "decay": decay,
        "confidence_bands": evidence.get("confidence_bands") or {},
        "prospective_validation": evidence.get("prospective_validation") or {},
        "governance": _governance_summary(governance),
        "validation_verdicts": verdicts,
        "drift_alerts": alerts,
        "methodology": _row_methodology(),
        "disclaimer": evidence.get("disclaimer") or "",
    }


def historical_verdicts(
    model: portfolio.Model,
    evidence: dict[str, Any],
    *,
    ranking_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Separate calculability, method integrity, and economic edge.

    ``edge_supported`` is intentionally fail-closed. Missing metrics fail the
    relevant check; no neutral default can turn unavailable evidence into a
    champion or pass a research-readiness gate.
    """
    policy = trials.historical_threshold_policy(
        target_kind="model", mandate_key=model.mandate_key,
    )
    floors = policy["floors"]
    windows = sorted(
        evidence.get("windows") or [], key=lambda row: str(row.get("signal_date") or ""),
    )
    if ranking_stats is None:
        split = int(len(windows) * _RANKING_SPLIT)
        ranking_stats = _segment_stats(windows[:split])
    benchmark_status = str((evidence.get("benchmark") or {}).get("status") or "")
    coverage = _number(ranking_stats.get("benchmark_coverage_pct"), default=0.0) or 0.0
    data_checks = [
        _verdict_check(
            "eligible_real_data",
            bool(evidence.get("eligible_for_real_research")) and not evidence.get("evidence_unavailable"),
            evidence.get("data_mode"),
            "eligible live or uploaded history",
        ),
        _verdict_check("benchmark_available", benchmark_status == "available", benchmark_status, "available"),
        _verdict_check(
            "benchmark_coverage_pct",
            coverage >= float(floors["min_benchmark_coverage_pct"]),
            coverage,
            floors["min_benchmark_coverage_pct"],
        ),
    ]
    data_passed = all(check["passed"] for check in data_checks)
    methods = evidence.get("validation_methods") or {}
    methodology = evidence.get("methodology") or {}
    measured = int(ranking_stats.get("measured_count") or 0)
    method_checks = [
        _verdict_check("no_lookahead", methodology.get("no_lookahead") is True, methodology.get("no_lookahead"), True),
        _verdict_check("rolling_method", bool((methods.get("rolling") or {}).get("summary")), bool(methods.get("rolling")), True),
        _verdict_check("anchored_method", bool((methods.get("anchored") or {}).get("summary")), bool(methods.get("anchored")), True),
        _verdict_check("measured_observations", measured >= int(floors["min_observations"]), measured, floors["min_observations"]),
    ]
    method_passed = data_passed and all(check["passed"] for check in method_checks)
    hit_rate = _number(ranking_stats.get("hit_rate_pct"), default=None)
    net_alpha = _number(ranking_stats.get("avg_alpha_after_default_costs_pct"), default=None)
    false_positive = _number(ranking_stats.get("false_positive_rate_pct"), default=None)
    covered_regimes = sum(
        1 for row in evidence.get("regime_sensitivity") or []
        if int(row.get("count") or row.get("measured_count") or 0) >= 3
    )
    regime_robustness = evidence.get("regime_robustness") or {}
    edge_checks = [
        _verdict_check("hit_rate_pct", hit_rate is not None and hit_rate >= float(floors["min_hit_rate_pct"]), hit_rate, floors["min_hit_rate_pct"]),
        _verdict_check("net_alpha_pct", net_alpha is not None and net_alpha >= float(floors["min_net_alpha_pct"]), net_alpha, floors["min_net_alpha_pct"]),
        _verdict_check("false_positive_rate_pct", false_positive is not None and false_positive <= float(floors["max_false_positive_rate_pct"]), false_positive, floors["max_false_positive_rate_pct"], operator="max"),
        _verdict_check("covered_regimes", covered_regimes >= int(floors["min_regime_count"]), covered_regimes, floors["min_regime_count"]),
        _verdict_check(
            "regime_robustness",
            regime_robustness.get("passed") is True,
            regime_robustness.get("status") or "unavailable",
            "passed",
        ),
    ]
    edge_passed = method_passed and all(check["passed"] for check in edge_checks)
    return {
        "policy": policy,
        "data_valid": _verdict_block(
            data_passed, data_checks,
            "Eligible real data and benchmark coverage pass." if data_passed else "Data or benchmark coverage is not valid for economic validation.",
            "Provide eligible real histories and benchmark coverage before validation.",
        ),
        "method_valid": _verdict_block(
            method_passed, method_checks,
            "Rolling and anchored no-lookahead methods meet the observation floor." if method_passed else "The historical method or observation floor is incomplete.",
            f"Accumulate at least {int(floors['min_observations'])} benchmark-measured ranking windows using both validation methods.",
        ),
        "edge_supported": _verdict_block(
            edge_passed, edge_checks,
            "Mandate-specific after-cost economic floors pass." if edge_passed else "One or more mandate-specific economic-quality floors did not pass.",
            "Review failed economic checks; do not promote this model from historical availability alone.",
        ),
    }


def _verdict_check(
    name: str,
    passed: bool,
    actual: Any,
    required: Any,
    *,
    operator: str = "min",
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "actual": actual,
        "required": required,
        "operator": operator,
    }


def _verdict_block(
    passed: bool,
    checks: list[dict[str, Any]],
    detail: str,
    required_action: str,
) -> dict[str, Any]:
    return {
        "passed": bool(passed),
        "detail": detail,
        "required_action": "" if passed else required_action,
        "checks": checks,
        "failed_checks": [check["name"] for check in checks if not check["passed"]],
    }


def _segment_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Hit rate / alpha / false-positive rate over one window segment."""
    hit_flags = [row["paper_hit"] for row in rows if row.get("paper_hit") is not None]
    alphas = [
        value for row in rows
        for value in [costs.directional_alpha(row.get("action_label"), row.get("alpha_pct"))]
        if value is not None
    ]
    actionable = [row for row in rows if row.get("action_label") in {"BUY", "SELL"}]
    measured_directional = [row for row in actionable if row.get("paper_hit") is not None]
    alpha_count = sum(1 for row in actionable if isinstance(row.get("alpha_pct"), (int, float)))
    fps = sum(1 for row in measured_directional if row.get("false_positive"))
    return {
        "window_count": len(rows),
        "measured_count": len(hit_flags),
        "directional_signal_count": len(actionable),
        "measured_directional_signal_count": len(measured_directional),
        "benchmark_coverage_pct": (
            round(100.0 * alpha_count / len(actionable), 2) if actionable else None
        ),
        "hit_rate_pct": (round(100.0 * sum(1 for f in hit_flags if f) / len(hit_flags), 2)
                         if hit_flags else None),
        "avg_alpha_pct": round(sum(alphas) / len(alphas), 4) if alphas else None,
        "avg_alpha_after_default_costs_pct": (
            costs.net_of_default_costs(round(sum(alphas) / len(alphas), 4)) if alphas else None),
        "false_positive_rate_pct": (round(100.0 * fps / len(measured_directional), 2)
                                    if measured_directional else None),
    }


def _ci_inputs(windows: list[dict[str, Any]]) -> dict[str, Any]:
    """Raw inputs for the selection-adjusted champion interval (computed at
    the dashboard level, where the trial count is known)."""
    hit_flags = [row["paper_hit"] for row in windows if row.get("paper_hit") is not None]
    alphas = [
        float(value) for row in windows
        for value in [costs.directional_alpha(row.get("action_label"), row.get("alpha_pct"))]
        if value is not None
    ]
    out: dict[str, Any] = {"measured_count": len(hit_flags)}
    if hit_flags:
        out["hit_rate_p"] = sum(1 for f in hit_flags if f) / len(hit_flags)
    if len(alphas) > 1:
        out["alpha_mean"] = sum(alphas) / len(alphas)
        out["alpha_se"] = stdev(alphas) / (len(alphas) ** 0.5)
    return out


def _selection_adjusted_ci(inputs: dict[str, Any], n_trials: int) -> dict[str, Any]:
    """Bonferroni-widened 90% band for the CHAMPION's stats.

    The champion is the max over n_trials models scored on shared history, so
    its naive per-model interval understates uncertainty. A family-wise band
    (alpha/N) is the honest, proportionate analogue of deflated-Sharpe
    machinery at this trial count (review finding)."""
    n = int(inputs.get("measured_count") or 0)
    p = inputs.get("hit_rate_p")
    if not n or p is None:
        return {"status": "insufficient_data"}
    z = NormalDist().inv_cdf(1 - (0.10 / max(1, n_trials)) / 2)
    # Wilson score interval at the family-wise z: the Wald form collapsed to
    # zero width at p=0 or p=1 (review finding — the live champion showed a
    # 100–100 band on 12 windows; Wilson gives ~[73, 100]).
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z / denom) * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    out = {
        "status": "ok",
        "n_trials": n_trials,
        "z": round(z, 3),
        "hit_rate_ci_low_pct": round(max(0.0, center - half) * 100, 2),
        "hit_rate_ci_high_pct": round(min(1.0, center + half) * 100, 2),
        "basis": f"90% family-wise (Bonferroni) Wilson band across {n_trials} ranked models.",
    }
    if inputs.get("alpha_mean") is not None and inputs.get("alpha_se") is not None:
        out["alpha_ci_low_pct"] = round(inputs["alpha_mean"] - z * inputs["alpha_se"], 4)
        out["alpha_ci_high_pct"] = round(inputs["alpha_mean"] + z * inputs["alpha_se"], 4)
        out["alpha_ci_net_low_pct"] = costs.net_of_default_costs(out["alpha_ci_low_pct"])
        out["alpha_ci_net_high_pct"] = costs.net_of_default_costs(out["alpha_ci_high_pct"])
        out["alpha_cost_basis"] = costs.NET_ASSUMPTION_LABEL
    return out


def _validation_score(
    walk: dict[str, Any],
    false_positives: dict[str, Any],
    decay: list[dict[str, Any]],
    governance: dict[str, Any],
) -> float:
    hit_rate = _number(walk.get("hit_rate_pct"), default=0.0)
    alpha = _number(walk.get("avg_alpha_pct"), default=0.0)
    alpha_component = _clamp(50.0 + alpha * 10.0, 0.0, 100.0)
    fp_rate = _number(false_positives.get("rate_pct"), default=50.0)
    false_positive_component = _clamp(100.0 - fp_rate, 0.0, 100.0)
    decay_component = _decay_component(decay)
    governance_component = 75.0
    if governance.get("risk_limit_state") == "breach":
        governance_component -= 25.0
    if governance.get("approval_status") == "approved":
        governance_component += 10.0
    score = (
        hit_rate * 0.35
        + alpha_component * 0.25
        + false_positive_component * 0.20
        + decay_component * 0.15
        + _clamp(governance_component, 0.0, 100.0) * 0.05
    )
    return round(_clamp(score, 0.0, 100.0), 2)


def _decay_component(decay: list[dict[str, Any]]) -> float:
    if not decay:
        return 50.0
    alphas = [_number(row.get("avg_alpha_pct"), default=None) for row in decay]
    alphas = [value for value in alphas if value is not None]
    hit_rates = [_number(row.get("hit_rate_pct"), default=None) for row in decay]
    hit_rates = [value for value in hit_rates if value is not None]
    if not alphas and not hit_rates:
        return 50.0
    alpha_component = _clamp(50.0 + (_avg(alphas) or 0.0) * 10.0, 0.0, 100.0)
    hit_component = _avg(hit_rates) if hit_rates else 50.0
    return round((alpha_component + hit_component) / 2.0, 2)


def _drift_alerts(
    walk: dict[str, Any],
    false_positives: dict[str, Any],
    decay: list[dict[str, Any]],
    prospective: dict[str, Any],
    governance: dict[str, Any],
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    if governance.get("risk_limit_state") == "breach":
        alerts.append({"severity": "high", "title": "Governance breach", "detail": "Risk-limit state is breached before validation approval."})
    avg_alpha = _number(walk.get("avg_alpha_pct"), default=None)
    if avg_alpha is not None and avg_alpha < 0:
        alerts.append({"severity": "high", "title": "Negative walk-forward alpha", "detail": f"Average alpha vs benchmark is {avg_alpha:.2f}%."})
    fp_rate = _number(false_positives.get("rate_pct"), default=None)
    if fp_rate is not None and fp_rate >= 35:
        alerts.append({"severity": "medium", "title": "False positives elevated", "detail": f"False-positive rate is {fp_rate:.1f}%."})
    long_decay = decay[-1] if decay else {}
    long_alpha = _number(long_decay.get("avg_alpha_pct"), default=None)
    if long_alpha is not None and long_alpha < 0:
        alerts.append({"severity": "medium", "title": "Signal decay warning", "detail": f"Longer-horizon decay alpha is {long_alpha:.2f}%."})
    prospective_alpha = _number(prospective.get("avg_alpha_pct"), default=None)
    if prospective_alpha is not None and prospective_alpha < 0:
        alerts.append({"severity": "medium", "title": "Prospective drift", "detail": f"Signal Journal alpha is {prospective_alpha:.2f}%."})
    if not alerts:
        alerts.append({"severity": "info", "title": "No validation drift alert", "detail": "Current walk-forward and governance checks did not trigger a material alert."})
    return alerts


def _dashboard_alerts(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    for row in rows:
        for alert in row.get("drift_alerts") or []:
            severity = alert.get("severity") or "info"
            if severity in {"blocked", "high", "medium"}:
                out.append({
                    "severity": severity,
                    "model_id": row["model_id"],
                    "model_name": row["model_name"],
                    "title": alert.get("title") or "Validation alert",
                    "detail": alert.get("detail") or "",
                })
    return out


def _governance_summary(governance: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": int(governance.get("version") or 1),
        "approval_status": governance.get("approval_status") or "draft",
        "risk_limit_state": governance.get("risk_limit_state") or "unknown",
        "risk_limit_violations": governance.get("risk_limit_violations") or [],
        "rebalance_status": governance.get("rebalance_status") or "not_recorded",
        "snapshot_count": int(governance.get("snapshot_count") or 0),
        "latest_change_note": governance.get("latest_change_note") or "",
        "updated_by": governance.get("updated_by") or "",
    }


def _row_methodology() -> dict[str, bool]:
    return {
        "analysis_only": True,
        "paper_tracking_only": True,
        "no_execution": True,
        "does_not_change_analytics": True,
    }


def _grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def _number(value: Any, *, default: float | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# --------------------------------------------------------------------------- #
# Selection-adjusted evidence v2: DSR + PBO (review priority fix — "correct
# for all trials"; both are validation OUTPUTS, not footnotes)
# --------------------------------------------------------------------------- #
_EULER_GAMMA = 0.5772156649015329


def _sharpe(values: list[float]) -> float | None:
    """Per-window Sharpe of an alpha series (not annualized — used only to
    compare trials measured on the same window grid)."""
    if len(values) < 2:
        return None
    arr = np.asarray(values, dtype=float)
    sd = float(arr.std(ddof=1))
    if sd <= 1e-12:
        return None
    return float(arr.mean() / sd)


def _deflated_sharpe(champion_alphas: list[float], trial_sharpes: list,
                     overlap_factor: float = 1.0) -> dict[str, Any]:
    """Bailey & Lopez de Prado's Deflated Sharpe Ratio: the probability the
    champion's Sharpe exceeds what the LUCKIEST of N trials would show by
    chance, adjusted for the alpha series' skew/kurtosis. Degrades honestly
    below the data floor instead of reporting noise."""
    from statistics import NormalDist

    trials = [s for s in trial_sharpes if s is not None]
    n = len(champion_alphas)
    # Overlapping windows (step < horizon) share forward days and are NOT
    # independent; treating them as such overstates DSR in the dangerous
    # direction. Deflate to the effective count, same convention as the
    # evidence-lab confidence bands (adversarial-review finding).
    n_eff = int(n / max(1.0, overlap_factor))
    n_trials = len(trials)
    if n_eff < 10 or n_trials < 2:
        return {"status": "insufficient_data", "n_observations": n,
                "n_effective": n_eff, "overlap_factor": round(overlap_factor, 2),
                "n_trials": n_trials,
                "note": ("Needs >=10 EFFECTIVE (overlap-adjusted) champion windows and "
                         ">=2 ranked trials — accumulates as walk-forward history grows.")}
    arr = np.asarray(champion_alphas, dtype=float)
    sd = float(arr.std(ddof=1))
    if sd <= 1e-12:
        return {"status": "insufficient_data", "n_observations": n, "n_trials": n_trials,
                "note": "Champion alpha series has no variance."}
    sr = float(arr.mean() / sd)
    centered = arr - arr.mean()
    skew = float(np.mean(centered ** 3) / sd ** 3)
    kurt = float(np.mean(centered ** 4) / sd ** 4)          # non-excess
    var_sr = float(np.var(np.asarray(trials, dtype=float), ddof=1)) if n_trials > 1 else 0.0
    nd = NormalDist()
    if var_sr > 1e-12:
        sr_star = (var_sr ** 0.5) * (
            (1.0 - _EULER_GAMMA) * nd.inv_cdf(1.0 - 1.0 / n_trials)
            + _EULER_GAMMA * nd.inv_cdf(1.0 - 1.0 / (n_trials * 2.718281828459045)))
    else:
        sr_star = 0.0   # identical trials: no selection lift to deflate
    denom_sq = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if denom_sq <= 1e-12:
        return {"status": "insufficient_data", "n_observations": n, "n_trials": n_trials,
                "note": "Higher-moment adjustment degenerate for this series."}
    dsr = nd.cdf((sr - sr_star) * ((n_eff - 1) ** 0.5) / (denom_sq ** 0.5))
    return {
        "status": "ok",
        "sharpe_per_window": round(sr, 4),
        "expected_max_sharpe_under_trials": round(sr_star, 4),
        "n_observations": n,
        "n_effective": n_eff,
        "overlap_factor": round(overlap_factor, 2),
        "n_trials": n_trials,
        "deflated_sharpe_probability": round(float(dsr), 4),
        "basis": ("P(champion Sharpe > the luckiest of N trials by chance), skew/kurtosis "
                  "adjusted (Bailey & Lopez de Prado). Confidence scales with the "
                  "EFFECTIVE window count (raw / overlap factor, matching the evidence-lab "
                  "bands); n_trials is a LOWER bound on real selection pressure "
                  "(see n_trials_not_counted)."),
    }


def _pbo_cscv(series_by_model: dict[str, dict[str, float]],
              overlap_factor: float = 1.0) -> dict[str, Any]:
    """Probability of Backtest Overfitting via CSCV: across symmetric
    train/test block splits of the aligned window grid, how often does the
    in-sample winner rank in the bottom half out-of-sample?"""
    from itertools import combinations

    if len(series_by_model) < 3:
        return {"status": "insufficient_data", "n_trials": len(series_by_model),
                "note": "CSCV needs >=3 ranked models — add models or wait for history."}
    common_dates = sorted(set.intersection(*(set(s) for s in series_by_model.values())))
    # Overlapping windows leak in-sample information across CSCV block
    # boundaries and bias PBO downward (looks less overfit). Decimate to a
    # non-overlapping subgrid first — honesty over sample size.
    stride = max(1, int(round(overlap_factor)))
    decimated = common_dates[::stride]
    T, N = len(decimated), len(series_by_model)
    if T < 16:
        return {"status": "insufficient_data", "n_trials": N,
                "n_aligned_windows": len(common_dates),
                "n_after_overlap_decimation": T,
                "note": ("CSCV needs >=16 NON-OVERLAPPING walk-forward windows shared by "
                         "every model (overlap-decimated at stride "
                         f"{stride}).")}
    model_ids = sorted(series_by_model)
    matrix = np.array([[series_by_model[m][d] for m in model_ids] for d in decimated])
    S = 8 if T >= 32 else 6 if T >= 24 else 4
    blocks = np.array_split(np.arange(T), S)
    logits: list[float] = []
    for train_ids in combinations(range(S), S // 2):
        train_rows = np.concatenate([blocks[i] for i in train_ids])
        test_rows = np.concatenate([blocks[i] for i in range(S) if i not in train_ids])
        best = int(np.argmax(matrix[train_rows].mean(axis=0)))
        oos = matrix[test_rows].mean(axis=0)
        rank = (float((oos < oos[best]).sum()) + 0.5 * float((oos == oos[best]).sum() - 1) + 1.0) / (N + 1)
        rank = min(max(rank, 1e-6), 1 - 1e-6)
        logits.append(float(np.log(rank / (1.0 - rank))))
    pbo = float(np.mean(np.asarray(logits) <= 0.0))
    return {
        "status": "ok",
        "pbo": round(pbo, 4),
        "n_splits": len(logits),
        "n_trials": N,
        "n_aligned_windows": len(common_dates),
        "n_after_overlap_decimation": T,
        "overlap_stride": stride,
        "block_count": S,
        "basis": ("CSCV (Bailey et al.): fraction of symmetric block splits where the "
                  "in-sample champion falls in the bottom half out-of-sample. Alpha "
                  "windows aligned by signal date across all ranked models, DECIMATED "
                  "to a non-overlapping subgrid when step < horizon. "
                  "PBO near 0 = selection is robust; near 0.5+ = the ranking is noise."),
    }

"""Shared institutional readiness gates for approvals and client exports.

The gate is deliberately deterministic. It composes existing provenance,
freshness, coverage, and walk-forward evidence without changing any analytics.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from . import (
    data, data_quality, evidence_lab, independent_validation, model_validation, portfolio,
    operations, persistence, provenance, provider_registry, trials,
)

MIN_VALIDATION_MEASURED = 8
MIN_BENCHMARK_COVERAGE_PCT = 80.0


def model_readiness(model: portfolio.Model) -> dict[str, Any]:
    coverage = data_quality.model_coverage(model)
    coverage_ok = coverage.get("coverage_state") == "real"
    freshness = _model_freshness(model)
    provenance_ok, provenance_detail = _model_provenance(model)
    validation = _model_validation(model) if coverage_ok and provenance_ok else {
        "passed": False,
        "measured_count": 0,
        "benchmark_coverage_pct": None,
        "reason": "Validation cannot run until provenance and holding coverage pass.",
    }
    provider_control = _model_provider_control(model)
    independent_review = independent_validation.status(model.id)
    operational = operations.institutional_readiness()
    prospective = _prospective_trial_control(model)
    checks = [
        _check(
            "provenance",
            provenance_ok,
            provenance_detail,
            "All holdings must use eligible live or uploaded histories.",
        ),
        _check(
            "coverage",
            coverage_ok,
            f"{coverage.get('real_coverage_count', 0)} of {len(model.holdings)} holdings covered.",
            "Resolve every missing or ineligible holding history.",
        ),
        _check(
            "freshness",
            freshness["passed"],
            freshness["detail"],
            "Refresh stale holding histories before approval or client export.",
        ),
        _check(
            "validation",
            validation["passed"],
            validation["reason"],
            (
                f"Accumulate at least {MIN_VALIDATION_MEASURED} benchmark-measured "
                f"BUY/SELL windows with at least {MIN_BENCHMARK_COVERAGE_PCT:.0f}% coverage."
            ),
        ),
        _check(
            "prospective_trial",
            prospective["passed"],
            prospective["detail"],
            prospective["required_action"],
        ),
        _check(
            "provider_control",
            provider_control["passed"],
            provider_control["detail"],
            provider_control["required_action"],
        ),
        _check(
            "independent_validation",
            independent_review["passed"],
            independent_review["detail"],
            independent_review["required_action"],
        ),
        _check(
            "operational_readiness",
            operational["passed"],
            operational["detail"],
            operational["required_action"],
        ),
    ]
    blockers = [check for check in checks if not check["passed"]]
    return {
        "passed": not blockers,
        "state": "pass" if not blockers else "blocked",
        "checks": checks,
        "blockers": blockers,
        "coverage": coverage,
        "freshness": freshness,
        "validation": validation,
        "provider_control": provider_control,
        "prospective_trial": prospective,
        "independent_validation": independent_review,
        "operational_readiness": operational,
        "blocked_reason": " ".join(check["required_action"] for check in blockers),
    }


def _prospective_trial_control(model: portfolio.Model) -> dict[str, Any]:
    if not provider_registry.controls_required():
        return {
            "passed": True,
            "state": "not_required",
            "detail": "Prospective deployment gating is disabled for this local/test process.",
            "required_action": "",
            "trial_id": "",
        }
    rows = persistence.get_store().prospective_trials(
        limit=100, target_kind="model", target_id=model.id,
    )
    for row in rows:
        if row.get("status") != "completed":
            continue
        policy = trials.deployment_policy_status(
            row.get("protocol") or {}, target_kind="model", target_id=model.id,
        )
        if not policy.get("passed"):
            continue
        assessment = trials.assess(row)
        verified = trials.verified_assessment_evidence(str(row.get("trial_id") or ""))
        if assessment.get("passed") and verified:
            return {
                "passed": True,
                "state": "pass",
                "detail": (
                    "A completed preregistered trial passed after-cost alpha, hit-rate, "
                    "false-positive, regime, confidence, and capacity checks."
                ),
                "required_action": "",
                "trial_id": row.get("trial_id") or "",
                "evidence_id": verified.get("evidence_id") or "",
            }
    return {
        "passed": False,
        "state": "blocked",
        "detail": "No completed replay-verified prospective trial has passed the deployment protocol.",
        "required_action": (
            "Preregister a model trial, collect scheduled out-of-sample outcomes, pass after-cost "
            "economic and capacity thresholds, record the assessment, then close it as completed."
        ),
        "trial_id": "",
    }


def _model_provider_control(model: portfolio.Model) -> dict[str, Any]:
    live_symbols = []
    usages = []
    for holding in model.holdings:
        instrument = data.get(holding.ticker)
        if instrument is not None and instrument.source == "live":
            live_symbols.append(holding.ticker)
            usages.append({
                "symbol": holding.ticker,
                "provider": provider_registry.provider_key(instrument.price_provider),
                "status": provider_registry.provider_usage_readiness(instrument.price_provider, "prices"),
            })
    if not live_symbols:
        return {
            "passed": True,
            "state": "not_applicable",
            "detail": "No live-provider histories are used by this model.",
            "required_action": "",
            "live_symbols": [],
        }
    domain_status = provider_registry.domain_readiness("prices")
    invalid = [usage for usage in usages if not usage["status"].get("passed")]
    if invalid:
        providers = ", ".join(sorted({usage["provider"] or "unknown" for usage in invalid}))
        return {
            **domain_status,
            "passed": False,
            "state": "blocked",
            "detail": f"Model uses live histories from providers outside the approved cutover: {providers}.",
            "required_action": "Refresh every live holding through the approved primary or backup price provider.",
            "live_symbols": live_symbols,
            "provider_usages": usages,
        }
    return {**domain_status, "live_symbols": live_symbols, "provider_usages": usages}


def instrument_readiness(instrument: data.Instrument) -> dict[str, Any]:
    close = instrument.df["close"].dropna() if "close" in instrument.df else pd.Series(dtype=float)
    p = provenance.instrument(
        instrument.source,
        len(close),
        price_provider=getattr(instrument, "price_provider", ""),
    )
    freshness = _series_freshness(close, instrument.symbol)
    provider_control = (
        provider_registry.provider_usage_readiness(instrument.price_provider, "prices")
        if instrument.source == "live"
        else {
            "passed": True,
            "state": "not_applicable",
            "detail": "This instrument does not use a live-provider history.",
            "required_action": "",
        }
    )
    checks = [
        _check(
            "provenance",
            bool(p.get("eligible_for_real_research")),
            str(p.get("reason") or p.get("display_label") or ""),
            str(p.get("required_action") or "Provide eligible live or uploaded history."),
        ),
        _check(
            "freshness",
            freshness["passed"],
            freshness["detail"],
            "Refresh the instrument history before client export.",
        ),
        _check(
            "provider_control",
            provider_control["passed"],
            provider_control["detail"],
            provider_control["required_action"],
        ),
    ]
    operational = operations.institutional_readiness()
    checks.append(_check(
        "operational_readiness",
        operational["passed"],
        operational["detail"],
        operational["required_action"],
    ))
    blockers = [check for check in checks if not check["passed"]]
    return {
        "passed": not blockers,
        "state": "pass" if not blockers else "blocked",
        "checks": checks,
        "blockers": blockers,
        "freshness": freshness,
        "provenance": p,
        "provider_control": provider_control,
        "operational_readiness": operational,
        "blocked_reason": " ".join(check["required_action"] for check in blockers),
    }


def _model_provenance(model: portfolio.Model) -> tuple[bool, str]:
    try:
        series = portfolio.build_series(model, allow_sample=False, allow_simulated=False)
    except ValueError as exc:
        return False, str(exc)
    p = provenance.portfolio(series.provenance)
    return bool(p.get("eligible_for_real_research")), str(
        p.get("reason") or p.get("display_label") or "Eligible model provenance."
    )


def _model_validation(model: portfolio.Model) -> dict[str, Any]:
    evidence = evidence_lab.analyze_model(model)
    verdicts = model_validation.historical_verdicts(model, evidence)
    ranking = verdicts["edge_supported"]
    summary = evidence.get("summary") if isinstance(evidence.get("summary"), dict) else {}
    measured = int(summary.get("measured_count") or 0)
    coverage = summary.get("benchmark_coverage_pct")
    passed = bool(ranking["passed"])
    if passed:
        reason = "Historical data, method, and mandate-specific economic edge checks pass."
    else:
        failed = []
        for key in ("data_valid", "method_valid", "edge_supported"):
            failed.extend(verdicts[key]["failed_checks"])
        reason = "Historical validation failed: " + ", ".join(dict.fromkeys(failed)) + "."
    return {
        "passed": passed,
        "measured_count": measured,
        "benchmark_coverage_pct": coverage if coverage is not None else None,
        "benchmark": evidence.get("benchmark") or {},
        "parameters": evidence.get("parameters") or {},
        "reason": reason,
        "verdicts": verdicts,
    }


def _model_freshness(model: portfolio.Model) -> dict[str, Any]:
    rows = []
    for holding in model.holdings:
        inst = data.get(holding.ticker)
        close = inst.df["close"].dropna() if inst is not None and "close" in inst.df else pd.Series(dtype=float)
        rows.append(_series_freshness(close, holding.ticker))
    stale = [row for row in rows if not row["passed"]]
    return {
        "passed": bool(rows) and not stale,
        "detail": (
            f"All {len(rows)} holding histories are within the configured freshness window."
            if rows and not stale
            else f"{len(stale)} of {len(rows)} holding histories are missing or stale."
        ),
        "stale_symbols": [row["symbol"] for row in stale],
        "symbols": rows,
        "threshold_days": data_quality.thresholds()["stale_days"],
    }


def _series_freshness(close: pd.Series, symbol: str) -> dict[str, Any]:
    threshold = data_quality.thresholds()["stale_days"]
    if close.empty:
        return {
            "symbol": symbol,
            "passed": False,
            "last_date": None,
            "age_calendar_days": None,
            "detail": "No price history is available.",
        }
    last = pd.Timestamp(close.index.max())
    if last.tzinfo is not None:
        last = last.tz_convert("UTC").tz_localize(None)
    today = pd.Timestamp(datetime.now(timezone.utc).date())
    age = max(0, int((today.normalize() - last.normalize()).days))
    passed = age <= threshold
    return {
        "symbol": symbol,
        "passed": passed,
        "last_date": str(last.date()),
        "age_calendar_days": age,
        "detail": (
            f"Latest bar {last.date()} is {age} calendar day(s) old "
            f"(limit {threshold})."
        ),
    }


def _check(key: str, passed: bool, detail: str, required_action: str) -> dict[str, Any]:
    return {
        "key": key,
        "passed": bool(passed),
        "state": "pass" if passed else "blocked",
        "detail": detail,
        "required_action": "" if passed else required_action,
    }

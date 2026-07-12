"""Data-quality dashboard: freshness, coverage, and research-readiness evidence.

Builds the deterministic payload behind /api/data-quality: per-symbol freshness
and history-depth rows, per-model real-coverage rows, categorised issues, and
the persisted alert inputs the store tracks across runs. Thresholds come from
environment variables with defensive range guards.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd

from . import data, macro, persistence, portfolio, provenance
from ._common import env_int as _env_int
from .reporting import DISCLAIMER

DEFAULT_STALE_DAYS = 7
DEFAULT_MIN_RESEARCH_ROWS = 60
DEFAULT_INSTITUTIONAL_ROWS = 252


def model_coverage(mdl: portfolio.Model) -> dict:
    """Per-model real-research coverage: which holdings have eligible history."""
    missing = []
    real_count = 0
    sources: dict[str, int] = {}
    for holding in mdl.holdings:
        inst = data.get(holding.ticker)
        source = inst.source if inst is not None else "missing"
        sources[source] = sources.get(source, 0) + 1
        history_days = len(inst.df["close"].dropna()) if inst is not None else 0
        if provenance.is_real_source(source) and history_days >= 60:
            real_count += 1
            continue
        missing.append(holding.ticker)
    if not mdl.holdings:
        state = "empty"
    elif real_count == len(mdl.holdings):
        state = "real"
    elif real_count:
        state = "mixed"
    else:
        state = "blocked"
    return {
        "real_coverage_count": real_count,
        "missing_tickers": missing,
        "source_counts": sources,
        "coverage_state": state,
    }


def thresholds() -> dict[str, int]:
    min_research_rows = _env_int(
        "HELIOS_DATA_QUALITY_MIN_RESEARCH_ROWS",
        DEFAULT_MIN_RESEARCH_ROWS,
        20,
        10_000,
    )
    institutional_rows = _env_int(
        "HELIOS_DATA_QUALITY_INSTITUTIONAL_ROWS",
        DEFAULT_INSTITUTIONAL_ROWS,
        min_research_rows,
        20_000,
    )
    return {
        "stale_days": _env_int("HELIOS_DATA_QUALITY_STALE_DAYS", DEFAULT_STALE_DAYS, 1, 3_650),
        "min_research_rows": min_research_rows,
        "institutional_history_rows": institutional_rows,
    }


def threshold_config() -> dict:
    env_names = {
        "stale_days": "HELIOS_DATA_QUALITY_STALE_DAYS",
        "min_research_rows": "HELIOS_DATA_QUALITY_MIN_RESEARCH_ROWS",
        "institutional_history_rows": "HELIOS_DATA_QUALITY_INSTITUTIONAL_ROWS",
    }
    return {
        "source": "environment" if any(os.environ.get(name) for name in env_names.values()) else "defaults",
        "env": env_names,
        "range_guards": {
            "stale_days": "1-3650",
            "min_research_rows": "20-10000",
            "institutional_history_rows": "at least min_research_rows, max 20000",
        },
    }


def dashboard_payload() -> dict:
    """Full data-quality dashboard payload, including synced persistent alerts."""
    active_thresholds = thresholds()
    instruments = data.all_instruments()
    models = portfolio.all_models()
    refresh_logs = persistence.get_store().refresh_log(limit=250)
    refresh_by_symbol = {}
    for row in refresh_logs:
        refresh_by_symbol.setdefault(row["symbol"], row)
    symbols = [symbol_row(inst, refresh_by_symbol.get(inst.symbol), active_thresholds) for inst in instruments]
    model_rows = [model_row(mdl) for mdl in models]
    issues = []
    for row in symbols:
        if row["is_stale"]:
            issues.append(quality_issue(
                "stale_symbols",
                "warning",
                row["symbol"],
                f"{row['symbol']} latest data is {row['days_stale']} calendar days old.",
                "Refresh live data or verify the uploaded date range before current research use.",
            ))
        if row.get("future_dated"):
            issues.append(quality_issue(
                "future_dated_history",
                "blocker",
                row["symbol"],
                f"{row['symbol']} price history extends {-row['days_stale']} day(s) into the "
                "future — fabricated bars contaminate backtests and journal measurements.",
                "Delete and re-import this history; check the source file's date column/format.",
            ))
        if row["source"] != "sample" and row["row_count"] < active_thresholds["institutional_history_rows"]:
            severity = "blocker" if row["row_count"] < active_thresholds["min_research_rows"] else "warning"
            issues.append(quality_issue(
                "short_histories",
                severity,
                row["symbol"],
                f"{row['symbol']} has {row['row_count']} rows; institutional review target is {active_thresholds['institutional_history_rows']}.",
                "Fetch/upload a longer adjusted history before relying on model evidence.",
            ))
        if row["refresh_evidence"]["requires_refresh_log"] and not row["refresh_evidence"]["has_refresh_log"]:
            issues.append(quality_issue(
                "refresh_observability_gaps",
                "warning",
                row["symbol"],
                f"{row['symbol']} is marked live but has no persisted provider refresh attempt in the local log.",
                "Refresh the symbol or enable automatic live polling so freshness evidence is auditable.",
            ))
    for row in refresh_logs:
        if str(row.get("status") or "").lower() == "error":
            issues.append(quality_issue(
                "refresh_failures",
                "warning",
                row["symbol"],
                f"{row['symbol']} refresh failed: {row.get('message') or 'provider error'}",
                "Retry refresh and keep the prior stored history until the provider succeeds.",
            ))
    # Rate-assumption honesty: mandate anchors and CD-alternative benchmarks are
    # keyed to HELIOS_RF; alert when it drifts materially from the live 3M bill
    # (offline -> no live yield -> no alert, never a fabricated comparison).
    rf = macro.rf_drift()
    if rf.get("stale"):
        issues.append(quality_issue(
            "rate_assumptions",
            "warning",
            "HELIOS_RF",
            (f"Configured risk-free rate {rf['configured_rf_pct']:.2f}% is {rf['drift_pp']:.1f}pp away "
             f"from the live 3M T-bill ({rf['live_3m_pct']:.2f}%) — mandate anchors and CD-alternative "
             "benchmarks are being judged against a stale rate."),
            "Update HELIOS_RF in .env to the current 3M bill yield so every anchor moves together.",
        ))
    missing_rows = []
    coverage_gaps = []
    source_conflicts = []
    for model_row_item in model_rows:
        missing_rows.extend({"symbol": symbol, "model_id": model_row_item["id"], "model_name": model_row_item["name"]} for symbol in model_row_item["missing_tickers"])
        if model_row_item["coverage_state"] != "real":
            coverage_gaps.append({
                "model_id": model_row_item["id"],
                "model_name": model_row_item["name"],
                "coverage_state": model_row_item["coverage_state"],
                "real_coverage_count": model_row_item["real_coverage_count"],
                "n_holdings": model_row_item["n_holdings"],
                "missing_tickers": model_row_item["missing_tickers"],
            })
            issues.append(quality_issue(
                "coverage_gaps",
                "blocker" if model_row_item["real_coverage_count"] == 0 else "warning",
                model_row_item["name"],
                f"{model_row_item['name']} has {model_row_item['real_coverage_count']}/{model_row_item['n_holdings']} holdings with real research coverage.",
                "Fetch live histories for each uncovered holding before treating the model as research-ready.",
            ))
        nonzero_sources = {k: v for k, v in model_row_item["source_counts"].items() if v}
        if len(nonzero_sources) > 1:
            conflict = {
                "model_id": model_row_item["id"],
                "model_name": model_row_item["name"],
                "source_counts": nonzero_sources,
                "coverage_state": model_row_item["coverage_state"],
            }
            source_conflicts.append(conflict)
            issues.append(quality_issue(
                "source_conflicts",
                "warning",
                model_row_item["name"],
                f"{model_row_item['name']} mixes sources: " + ", ".join(f"{source}={count}" for source, count in sorted(nonzero_sources.items())),
                "Resolve mixed/missing/sample coverage before presenting a unified model evidence pack.",
            ))
    for row in missing_rows:
        issues.append(quality_issue(
            "missing_data",
            "blocker",
            row["symbol"],
            f"{row['symbol']} is required by {row['model_name']} but has no eligible real history.",
            "Fetch live data or upload an eligible adjusted price history.",
        ))
    blockers = sum(1 for issue in issues if issue["severity"] == "blocker")
    warnings = sum(1 for issue in issues if issue["severity"] == "warning")
    research_ready_symbols = [row for row in symbols if row["research_ready"]]
    research_ready_models = [row for row in model_rows if row["research_ready"]]
    refresh_observability_gaps = [
        row for row in symbols
        if row["refresh_evidence"]["requires_refresh_log"] and not row["refresh_evidence"]["has_refresh_log"]
    ]
    refresh_observed = [
        row for row in symbols
        if row["refresh_evidence"]["requires_refresh_log"] and row["refresh_evidence"]["has_refresh_log"]
    ]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "research_ready": blockers == 0 and bool(research_ready_symbols or research_ready_models),
        "thresholds": active_thresholds,
        "threshold_config": threshold_config(),
        "refresh_observability": {
            "requires_log_for_sources": ["live"],
            "observed_count": len(refresh_observed),
            "gap_count": len(refresh_observability_gaps),
            "refresh_log_window": 250,
            "basis": "provider refresh log plus price-history date range",
        },
        "summary": {
            "symbol_count": len(symbols),
            "model_count": len(model_rows),
            "issue_count": len(issues),
            "blocker_count": blockers,
            "warning_count": warnings,
            "research_ready_count": len(research_ready_symbols) + len(research_ready_models),
            "stale_symbol_count": sum(1 for row in symbols if row["is_stale"]),
            "missing_symbol_count": len({row["symbol"] for row in missing_rows}),
            "refresh_failure_count": sum(1 for row in refresh_logs if str(row.get("status") or "").lower() == "error"),
            "refresh_observability_gap_count": len(refresh_observability_gaps),
            "coverage_gap_count": len(coverage_gaps),
        },
        "symbols": symbols,
        "models": model_rows,
        "issues": issues,
        "stale_symbols": [row for row in symbols if row["is_stale"]],
        "short_histories": [row for row in symbols if row["source"] != "sample" and row["row_count"] < active_thresholds["institutional_history_rows"]],
        "missing_data": missing_rows,
        "refresh_failures": [row for row in refresh_logs if str(row.get("status") or "").lower() == "error"],
        "refresh_observability_gaps": refresh_observability_gaps,
        "coverage_gaps": coverage_gaps,
        "source_conflicts": source_conflicts,
        "disclaimer": DISCLAIMER,
    }
    payload["alerts"] = persistence.get_store().sync_data_quality_alerts(
        alert_inputs(payload),
        generated_at=payload["generated_at"],
    )
    return payload


def symbol_row(inst: data.Instrument, last_refresh: dict | None, active_thresholds: dict[str, int]) -> dict:
    close = inst.df["close"].dropna() if "close" in inst.df else pd.Series(dtype=float)
    dates = pd.to_datetime(close.index, errors="coerce")
    dates = dates[~pd.isna(dates)]
    first_date = str(dates.min().date()) if len(dates) else None
    last_date = str(dates.max().date()) if len(dates) else None
    today = datetime.now(timezone.utc).date()
    days_stale = (today - dates.max().date()).days if len(dates) else None
    is_real_source = provenance.is_real_source(inst.source)
    requires_refresh_log = inst.source == "live"
    return {
        "symbol": inst.symbol,
        "name": inst.name,
        "source": inst.source,
        "row_count": int(len(close)),
        "first_date": first_date,
        "last_date": last_date,
        "days_stale": days_stale,
        "freshness_basis": "price_history_last_date" if last_date else "missing_price_history",
        "is_stale": bool(is_real_source and days_stale is not None and days_stale > active_thresholds["stale_days"]),
        # Negative age = bars dated in the future. Ingestion now rejects/clamps
        # these, but anything persisted BEFORE the guard (or arriving by another
        # path) must read as contaminated, not maximally fresh (review finding:
        # a future-dated upload passed every check and settled journal entries
        # against fabricated bars). >1 day past today allows non-UTC same-day bars.
        "future_dated": bool(days_stale is not None and days_stale < -1),
        "is_short": bool(is_real_source and len(close) < active_thresholds["institutional_history_rows"]),
        "research_ready": bool(is_real_source and len(close) >= active_thresholds["min_research_rows"]
                               and not (days_stale is not None and days_stale < -1)),
        "last_refresh": last_refresh,
        "refresh_evidence": {
            "requires_refresh_log": requires_refresh_log,
            "has_refresh_log": bool(last_refresh),
            "last_attempted_at": last_refresh.get("attempted_at") if last_refresh else None,
            "status": last_refresh.get("status") if last_refresh else None,
            "rows_added": last_refresh.get("rows_added") if last_refresh else None,
            "source": last_refresh.get("source") if last_refresh else None,
        },
        "next_step": symbol_next_step(inst.source, len(close), days_stale, active_thresholds, bool(last_refresh)),
    }


def model_row(mdl: portfolio.Model) -> dict:
    coverage = model_coverage(mdl)
    return {
        "id": mdl.id,
        "name": mdl.name,
        "mandate": mdl.mandate_key,
        "n_holdings": len(mdl.holdings),
        "research_ready": coverage["coverage_state"] == "real",
        **coverage,
    }


def symbol_next_step(source: str, row_count: int, days_stale: int | None, active_thresholds: dict[str, int], has_refresh_log: bool) -> str:
    if not provenance.is_real_source(source):
        return "Sample data is demo-only; fetch live or upload real history."
    if row_count < active_thresholds["min_research_rows"]:
        return f"Fetch/upload at least {active_thresholds['min_research_rows']} valid rows before real research."
    if row_count < active_thresholds["institutional_history_rows"]:
        return "Extend history toward a one-year institutional review window."
    if source == "live" and not has_refresh_log:
        return "Refresh once so provider freshness evidence is logged."
    if days_stale is not None and days_stale > active_thresholds["stale_days"]:
        return "Refresh or verify the latest available provider date."
    return "Research-ready, subject to analysis-only caveats."


def quality_issue(category: str, severity: str, target: str, detail: str, next_step: str) -> dict:
    return {
        "category": category,
        "severity": severity,
        "target": target,
        "detail": detail,
        "next_step": next_step,
    }


def alert_inputs(payload: dict) -> list[dict]:
    alerts = []
    for issue in payload.get("issues") or []:
        alerts.append({
            "id": _alert_id(issue.get("category"), issue.get("target")),
            "category": issue.get("category") or "data_quality",
            "severity": issue.get("severity") or "info",
            "target": issue.get("target") or "Workspace",
            "detail": issue.get("detail") or "",
            "next_step": issue.get("next_step") or "",
            "metadata": {
                "source": "data_quality_dashboard",
                "research_ready": bool(payload.get("research_ready")),
            },
        })
    summary = payload.get("summary") or {}
    if not payload.get("research_ready"):
        blockers = int(summary.get("blocker_count") or 0)
        warnings = int(summary.get("warning_count") or 0)
        alerts.append({
            "id": "data_quality:research_readiness:workspace",
            "category": "research_readiness",
            "severity": "blocker" if blockers else "warning",
            "target": "Workspace",
            "detail": f"Workspace is not research-ready: {blockers} blockers and {warnings} warnings are active.",
            "next_step": "Resolve blocker alerts before using Helios outputs as real research evidence.",
            "metadata": {
                "source": "data_quality_dashboard",
                "blocker_count": blockers,
                "warning_count": warnings,
            },
        })
    return alerts


def _alert_id(category: str | None, target: str | None) -> str:
    return f"data_quality:{_alert_id_part(category or 'data_quality')}:{_alert_id_part(target or 'workspace')}"


def _alert_id_part(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value))
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned[:80] or "item"

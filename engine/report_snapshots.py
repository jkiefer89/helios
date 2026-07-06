"""Report-snapshot orchestration: compose, version, and enrich saved reports.

Coordinates the deterministic report payload, target-scoped signal-journal
evidence, optional AI narrative generation (fails closed), and the storage
status block. Rendering and persistence stay in report_exports/persistence.
"""
from __future__ import annotations

from typing import Any

from . import ai_copilot, data, portfolio, report_exports, reporting, signal_journal
from .reporting import DISCLAIMER


def report_for_snapshot(kind: str, target_id: str) -> dict:
    """Build the fresh deterministic report a snapshot is taken from.

    Raises ValueError with a user-facing message on any invalid target.
    """
    if kind == "instrument":
        symbol = data.clean_symbol(target_id, fallback="")
        if not symbol:
            raise ValueError("Provide a ticker symbol.")
        inst = data.get(symbol)
        if inst is None:
            raise ValueError(f"Unknown ticker '{symbol}'.")
        return reporting.instrument_report(inst)
    if kind == "model":
        mdl = portfolio.get(target_id)
        if mdl is None:
            raise ValueError("Unknown model.")
        return reporting.model_report(mdl)
    raise ValueError("Report snapshot kind must be 'instrument' or 'model'.")


def next_version(store, kind: str, target_id: str) -> int:
    """Next monotonically increasing snapshot version for one target."""
    try:
        versions = [
            int(row.get("version") or row.get("metadata", {}).get("version") or 1)
            for row in store.report_snapshots(limit=200)
            if row.get("target_kind") == kind and row.get("target_id") == target_id
        ]
    except Exception:
        versions = []
    return (max(versions) if versions else 0) + 1


def signal_journal_evidence(kind: str, target_id: str) -> dict:
    """Target-scoped paper-signal evidence embedded in a saved snapshot."""
    journal_target_id = data.clean_symbol(target_id, fallback="") if kind == "instrument" else target_id
    try:
        entries = signal_journal.list_entries(limit=250)
    except Exception:
        entries = []
    target_entries = [
        entry for entry in entries
        if entry.get("target_kind") == kind and str(entry.get("target_id") or "") == journal_target_id
    ]
    dashboard = signal_journal.dashboard_payload(target_entries)
    return {
        "scope": "target",
        "target_kind": kind,
        "target_id": journal_target_id,
        "summary": dashboard["summary"],
        "benchmark_comparison": dashboard["benchmark_comparison"],
        "model_credibility": dashboard["model_evidence"],
        "drift": dashboard["drift"][-24:],
        "target_history": [_compact_entry(entry) for entry in target_entries[:24]],
        "methodology": {
            "analysis_only": True,
            "paper_tracking_only": True,
            "raw_price_history_stored": False,
            "forward_results": "Pending results resolve only after later live or persisted price history covers the original signal horizon.",
            "hit_rate_basis": "Measured paper signals are scored against action intent and benchmark-relative alpha when available.",
        },
        "disclaimer": DISCLAIMER,
    }


def _compact_entry(entry: dict) -> dict:
    return {
        "id": entry.get("id"),
        "created_at": entry.get("created_at"),
        "target_kind": entry.get("target_kind"),
        "target_id": entry.get("target_id"),
        "target_name": entry.get("target_name"),
        "action_label": entry.get("action_label"),
        "score": entry.get("score"),
        "benchmark": entry.get("benchmark"),
        "input_start_date": entry.get("input_start_date"),
        "input_end_date": entry.get("input_end_date"),
        "input_rows": entry.get("input_rows"),
        "horizon_days": entry.get("horizon_days"),
        "data_mode": entry.get("data_mode"),
        "eligible_for_real_research": bool(entry.get("eligible_for_real_research")),
        "source_counts": entry.get("source_counts") or {},
        "forward_status": entry.get("forward_status"),
        "forward_start_date": entry.get("forward_start_date"),
        "forward_end_date": entry.get("forward_end_date"),
        "forward_result_pct": entry.get("forward_result_pct"),
        "benchmark_result_pct": entry.get("benchmark_result_pct"),
        "alpha_pct": entry.get("alpha_pct"),
        "evaluated_at": entry.get("evaluated_at"),
    }


def ai_narrative(
    report: dict,
    *,
    provided_narrative: str,
    include_ai_narrative: bool,
) -> tuple[str, dict]:
    """Legacy narrative resolver for callers that collapse an absent
    include-AI-narrative toggle to False (a provided narrative always wins).

    New callers should use resolve_narrative with a tri-state toggle so an
    explicit False excludes even a provided narrative.
    """
    if provided_narrative:
        return provided_narrative, {
            "status": "provided",
            "provider": {"source": "current_session", "needs_review": True},
        }
    return resolve_narrative(
        report,
        provided_narrative="",
        include_ai_narrative=bool(include_ai_narrative),
    )


def resolve_narrative(
    report: dict,
    *,
    provided_narrative: str,
    include_ai_narrative: bool | None,
) -> tuple[str, dict]:
    """Resolve the snapshot narrative: provided text, generated text, or none.

    ``include_ai_narrative`` is tri-state: ``False`` explicitly excludes any
    narrative (even one provided/generated earlier in-session), ``True``
    includes a provided narrative or generates one, and ``None`` means the
    caller surfaced no toggle, so a provided narrative is kept.

    Never raises on provider failure — the snapshot saves without a narrative
    and records the provider status instead.
    """
    if include_ai_narrative is False:
        return "", {"status": "not_requested", "provider": {}}
    if provided_narrative:
        return provided_narrative, {
            "status": "provided",
            "provider": {"source": "current_session", "needs_review": True},
        }
    if not include_ai_narrative:
        return "", {"status": "not_requested", "provider": {}}
    provider = ai_copilot.get_provider()
    status = provider.status()
    provider_meta = {
        "provider": status.get("provider") or "",
        "model": status.get("model") or "",
        "available": bool(status.get("available")),
        "needs_review": True,
    }
    if not status.get("available"):
        return "", {"status": "provider_unavailable", "provider": provider_meta}
    try:
        result = provider.write_advisor_report({
            "report": report,
            "title": report.get("title"),
            "preview_locked": not bool(report.get("eligible_for_real_research")),
            "analysis_only_disclaimer": DISCLAIMER,
        }, regenerate=True)
    except ai_copilot.AIError as exc:
        safe_status = exc.status or status
        return "", {
            "status": "provider_error",
            "provider": {
                "provider": safe_status.get("provider") or provider_meta["provider"],
                "model": safe_status.get("model") or provider_meta["model"],
                "available": bool(safe_status.get("available")),
                "needs_review": True,
            },
        }
    provider_meta.update({
        "provider": result.get("provider") or provider_meta["provider"],
        "model": result.get("model") or provider_meta["model"],
        "needs_review": bool(result.get("needs_review", True)),
    })
    return report_exports.ai_result_to_narrative(result), {
        "status": "generated",
        "provider": provider_meta,
    }


def storage_status(store) -> dict:
    """Public description of where and how snapshots are stored."""
    status: dict[str, Any] = store.status()
    encryption = status.get("encryption") or {}
    return {
        "backend": "sqlite",
        "scope": "local",
        "durable": bool(status.get("available")),
        "configured": bool(status.get("configured")),
        "encrypted_at_rest": bool(encryption.get("enabled")),
        "at_rest_format": encryption.get("at_rest_format") or "sqlite",
        "warning": status.get("warning") or "",
    }

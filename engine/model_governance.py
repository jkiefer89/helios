"""Model governance audit trail and mandate/risk-limit checks.

The governance layer records advisor workflow facts around a model. It does not
alter Helios analytics, trade labels, forecasts, scores, or provenance gates.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from collections import Counter
from html import escape
from io import BytesIO
from typing import Any

from . import data, mandate, model_library, persistence, portfolio

APPROVAL_STATUSES = {"draft", "pending_review", "approved", "rejected", "archived"}
REBALANCE_ACTIONS = {"rebalance_recorded", "rebalance_reviewed"}
DEFAULT_ACTOR = "Advisor Console"


def payload(models: list[portfolio.Model] | None = None) -> dict[str, Any]:
    """Return the current governance workspace for loaded models."""
    store = persistence.get_store()
    if not store.available:
        warning = store.warning or "SQLite persistence is required for model governance."
        return {
            "models": [],
            "snapshots": [],
            "change_log": [],
            "rebalance_history": [],
            "summary": {
                "available": False,
                "model_count": 0,
                "approved_count": 0,
                "pending_count": 0,
                "draft_count": 0,
                "archived_count": 0,
                "breach_count": 0,
                "snapshot_count": 0,
                "change_count": 0,
            },
            "warning": warning,
            "disclaimer": _disclaimer(),
        }

    current_models = models if models is not None else portfolio.all_models()
    events = store.model_governance_events(limit=1000)
    events_by_model: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        events_by_model.setdefault(event["model_id"], []).append(event)

    rows = [_model_row(model, events_by_model.get(model.id, [])) for model in current_models]
    rows.sort(key=lambda item: item["name"])
    change_log = [event for event in events if event["model_id"] in {row["id"] for row in rows}]
    snapshots = [
        _snapshot_summary(event)
        for event in change_log
        if isinstance(event.get("snapshot"), dict) and event["snapshot"]
    ]
    rebalance_history = [
        _event_summary(event)
        for event in change_log
        if event.get("action") in REBALANCE_ACTIONS
    ]
    counts = Counter(row["approval_status"] for row in rows)
    summary = {
        "available": True,
        "model_count": len(rows),
        "approved_count": counts.get("approved", 0),
        "pending_count": counts.get("pending_review", 0),
        "draft_count": counts.get("draft", 0),
        "archived_count": counts.get("archived", 0),
        "breach_count": sum(1 for row in rows if row["risk_limit_state"] == "breach"),
        "snapshot_count": len(snapshots),
        "change_count": len(change_log),
    }
    return {
        "models": rows,
        "snapshots": snapshots,
        "change_log": [_event_summary(event) for event in change_log],
        "rebalance_history": rebalance_history,
        "summary": summary,
        "warning": "",
        "disclaimer": _disclaimer(),
    }


def record_event(
    model: portfolio.Model,
    *,
    actor: str = DEFAULT_ACTOR,
    action: str = "",
    note: str = "",
    approval_status: str = "",
    previous_model: portfolio.Model | None = None,
    committee_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Archive a governance event for the current model state."""
    store = persistence.get_store()
    if not store.available:
        return {"recorded": False, "warning": store.warning or "SQLite persistence is required for model governance."}

    current_events = store.model_governance_events(model.id, limit=1000)
    current = _model_row(model, current_events)
    status = _normalise_status(approval_status) or current["approval_status"]
    safe_action = _normalise_action(action, bool(approval_status))
    safe_note = str(note or "").strip()
    if status in {"approved", "rejected"} and len(safe_note) < 5:
        return {
            "recorded": False,
            "status_code": 400,
            "warning": "Committee notes are required before approving or rejecting a model.",
        }
    if status == "approved" and not current["can_approve"]:
        return {
            "recorded": False,
            "status_code": 400,
            "warning": current["approval_blocked_reason"] or "Cannot approve model while risk-limit breaches are active.",
        }
    identity, identity_error = _committee_identity(committee_identity, actor=safe_actor(actor), approval_status=status)
    if identity_error:
        return {
            "recorded": False,
            "status_code": 400,
            "warning": identity_error,
        }
    version = store.next_model_governance_version(model.id)
    snapshot = _snapshot(
        model,
        version=version,
        approval_status=status,
        previous_model=previous_model,
        action=safe_action,
        note=safe_note,
        committee_identity=identity,
    )
    event = store.record_model_governance_event(
        model_id=model.id,
        version=version,
        actor=safe_actor(actor),
        action=safe_action,
        note=safe_note,
        approval_status=status,
        snapshot=snapshot,
        metadata={
            "committee_note": safe_note,
            "committee_identity": identity,
            "version_diff": snapshot.get("version_diff") or {},
            "risk_gate": snapshot.get("risk_gate") or {},
        },
    )
    return {"recorded": event is not None, "event": event, "warning": "" if event else store.warning}


def preview_edit(
    model: portfolio.Model,
    *,
    holdings: list[dict[str, Any]],
    rebalance_to_target: bool = False,
) -> dict[str, Any]:
    """Return a save preview for proposed holdings without mutating the model."""
    draft = _draft_model(model, holdings, rebalance_to_target=rebalance_to_target)
    return _editor_payload(model, draft, rebalance_to_target=rebalance_to_target)


def save_edit(
    model: portfolio.Model,
    *,
    holdings: list[dict[str, Any]],
    change_note: str,
    actor: str = DEFAULT_ACTOR,
    rebalance_to_target: bool = False,
) -> dict[str, Any]:
    """Persist edited holdings and record a governance snapshot."""
    note = str(change_note or "").strip()
    if len(note) < 5:
        return {"saved": False, "warning": "A change note is required before saving model edits."}
    draft = _draft_model(model, holdings, rebalance_to_target=rebalance_to_target)
    source_by_ticker = {holding.ticker: holding.source for holding in model.holdings}
    edited = portfolio.Model(
        id=model.id,
        name=model.name,
        mandate_key=model.mandate_key,
        mandate_context=model.mandate_context,
        holdings=[
            portfolio.Holding(holding.ticker, holding.weight, source_by_ticker.get(holding.ticker, "pending"))
            for holding in draft.holdings
        ],
    )
    portfolio.register(edited)
    persisted = portfolio._persist_model(edited, source_filename="model-editor")
    if not persisted.get("persisted"):
        return {"saved": False, "warning": persisted.get("warning") or "Could not persist model edits."}
    event_result = record_event(
        edited,
        actor=actor or DEFAULT_ACTOR,
        action="model_edit",
        note=note,
        approval_status="pending_review",
        previous_model=model,
    )
    if not event_result.get("recorded"):
        return {"saved": False, "warning": event_result.get("warning") or "Could not record model edit governance event."}
    return {
        **_editor_payload(model, edited, rebalance_to_target=rebalance_to_target),
        "saved": True,
        "event": event_result["event"],
    }


def approval_packet(model: portfolio.Model) -> dict[str, Any]:
    """Build an exportable local approval packet for committee review."""
    store = persistence.get_store()
    if not store.available:
        return {
            "available": False,
            "warning": store.warning or "SQLite persistence is required for model approval packets.",
            "disclaimer": _disclaimer(),
        }
    events = store.model_governance_events(model.id, limit=1000)
    row = _model_row(model, events)
    event_summaries = [_event_summary(event) for event in events]
    snapshot_summaries = [
        _snapshot_summary(event)
        for event in events
        if isinstance(event.get("snapshot"), dict) and event["snapshot"]
    ]
    latest_snapshot = next(
        (event.get("snapshot") for event in events if isinstance(event.get("snapshot"), dict) and event.get("snapshot")),
        {},
    )
    committee_identity = _latest_committee_identity(events, latest_snapshot)
    before_snapshot = latest_snapshot.get("before_snapshot") or {}
    after_snapshot = latest_snapshot.get("after_snapshot") or _compact_model_snapshot(model, row["version"])
    version_diff = latest_snapshot.get("version_diff") or _empty_version_diff()
    packet = {
        "available": True,
        "packet_type": "model_approval_packet",
        "model": row,
        "approval": {
            "status": row["approval_status"],
            "approved_by": row["approved_by"],
            "approval_updated_at": row["approval_updated_at"],
            "can_approve": row["can_approve"],
            "blocked_reason": row["approval_blocked_reason"],
            "committee_identity": committee_identity,
        },
        "committee_identity": committee_identity,
        "risk_gate": _risk_gate(model),
        "risk_limits": row["risk_limits"],
        "version": row["version"],
        "version_diff": version_diff,
        "before_snapshot": before_snapshot,
        "after_snapshot": after_snapshot,
        "committee_notes": [
            {
                "event_id": event["id"],
                "created_at": event["created_at"],
                "actor": event["actor"],
                "action": event["action"],
                "note": event["note"],
                "committee_identity": _event_committee_identity(event),
            }
            for event in events
            if event.get("note")
        ],
        "snapshots": snapshot_summaries,
        "audit_trail": event_summaries,
        "export": {
            "formats": ["json", "html", "pdf"],
            "json_url": f"/api/model-governance/{model.id}/approval-packet",
            "html_url": f"/api/model-governance/{model.id}/approval-packet.html",
            "pdf_url": f"/api/model-governance/{model.id}/approval-packet.pdf",
        },
        "disclaimer": _disclaimer(),
    }
    return packet


def render_approval_packet_html(packet: dict[str, Any]) -> str:
    model = packet.get("model") if isinstance(packet.get("model"), dict) else {}
    risk_gate = packet.get("risk_gate") if isinstance(packet.get("risk_gate"), dict) else {}
    version_diff = packet.get("version_diff") if isinstance(packet.get("version_diff"), dict) else {}
    notes = packet.get("committee_notes") if isinstance(packet.get("committee_notes"), list) else []
    audit = packet.get("audit_trail") if isinstance(packet.get("audit_trail"), list) else []
    identity = packet.get("committee_identity") if isinstance(packet.get("committee_identity"), dict) else {}
    holdings = (packet.get("after_snapshot") or {}).get("holdings") if isinstance(packet.get("after_snapshot"), dict) else []
    changed = version_diff.get("changed_weights") if isinstance(version_diff.get("changed_weights"), list) else []
    added = version_diff.get("added") if isinstance(version_diff.get("added"), list) else []
    removed = version_diff.get("removed") if isinstance(version_diff.get("removed"), list) else []
    return "\n".join([
        "<!doctype html>",
        "<html><head><meta charset=\"utf-8\"><title>Model Approval Packet</title>",
        "<style>body{font-family:Arial,sans-serif;background:#08111d;color:#e6eef8;margin:32px;line-height:1.45}section{border:1px solid #26384d;border-radius:8px;padding:18px;margin:16px 0;background:#101b2a}h1,h2{margin:0 0 10px}table{width:100%;border-collapse:collapse}td,th{border-bottom:1px solid #26384d;padding:8px;text-align:left}.muted{color:#9fb1c7}.warn{color:#ffd24a}.ok{color:#47d66f}</style>",
        "</head><body>",
        f"<h1>Model Approval Packet: {escape(str(model.get('name') or 'Model'))}</h1>",
        f"<p class=\"muted\">Version {escape(str(packet.get('version') or ''))} / Status {escape(str((packet.get('approval') or {}).get('status') or ''))}</p>",
        "<section><h2>Approval Gate</h2>",
        f"<p class=\"{'ok' if risk_gate.get('can_approve') else 'warn'}\">{escape('Can approve' if risk_gate.get('can_approve') else risk_gate.get('blocked_reason') or 'Approval blocked')}</p>",
        "</section>",
        "<section><h2>Local Committee Identity</h2>",
        _html_table(
            ["Signer", "Role", "Committee", "Verification", "Scope"],
            [[
                identity.get("signer_name") or "Not recorded",
                identity.get("signer_role") or "Not recorded",
                identity.get("committee") or "Not recorded",
                "Verified local PIN" if identity.get("verified") else identity.get("verification_method") or "Local attestation",
                identity.get("scope") or "local_workspace",
            ]],
        ),
        "</section>",
        "<section><h2>Version Diff</h2>",
        f"<p>{escape(str(version_diff.get('summary') or 'No version diff recorded.'))}</p>",
        _html_table(["Ticker", "From", "To"], [[row.get("ticker"), row.get("from_weight_pct"), row.get("to_weight_pct")] for row in changed]),
        _html_table(["Added", "Weight"], [[row.get("ticker"), row.get("weight_pct")] for row in added]),
        _html_table(["Removed", "Weight"], [[row.get("ticker"), row.get("weight_pct")] for row in removed]),
        "</section>",
        "<section><h2>Current Holdings</h2>",
        _html_table(["Ticker", "Weight"], [[row.get("ticker"), row.get("weight_pct")] for row in holdings if isinstance(row, dict)]),
        "</section>",
        "<section><h2>Committee Notes</h2>",
        "".join(_html_committee_note(row) for row in notes if isinstance(row, dict)) or "<p class=\"muted\">No committee notes recorded.</p>",
        "</section>",
        "<section><h2>Audit Trail</h2>",
        _html_table(["Version", "Actor", "Action", "Status"], [[row.get("version"), row.get("actor"), row.get("action"), row.get("approval_status")] for row in audit if isinstance(row, dict)]),
        "</section>",
        f"<p class=\"muted\">{escape(str(packet.get('disclaimer') or _disclaimer()))}</p>",
        "</body></html>",
    ])


def render_approval_packet_pdf(packet: dict[str, Any]) -> bytes:
    """Render a local committee approval packet PDF.

    The packet is evidence for governance review only; it does not change model
    calculations, approvals, or Helios provenance gates.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover - dependency is verified in CI.
        raise RuntimeError("ReportLab is required for model approval PDF exports.") from exc

    model = packet.get("model") if isinstance(packet.get("model"), dict) else {}
    approval = packet.get("approval") if isinstance(packet.get("approval"), dict) else {}
    risk_gate = packet.get("risk_gate") if isinstance(packet.get("risk_gate"), dict) else {}
    version_diff = packet.get("version_diff") if isinstance(packet.get("version_diff"), dict) else {}
    identity = packet.get("committee_identity") if isinstance(packet.get("committee_identity"), dict) else {}
    notes = packet.get("committee_notes") if isinstance(packet.get("committee_notes"), list) else []
    audit = packet.get("audit_trail") if isinstance(packet.get("audit_trail"), list) else []
    holdings = (packet.get("after_snapshot") or {}).get("holdings") if isinstance(packet.get("after_snapshot"), dict) else []

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter, pageCompression=0)
    width, height = letter

    def background(section: str, page_no: int, total: int) -> None:
        pdf.setFillColor(colors.HexColor("#071019"))
        pdf.rect(0, 0, width, height, stroke=0, fill=1)
        pdf.setFillColor(colors.HexColor("#0d1622"))
        pdf.rect(0, height - 84, width, 84, stroke=0, fill=1)
        pdf.setStrokeColor(colors.HexColor("#263548"))
        pdf.line(38, height - 84, width - 38, height - 84)
        _pdf_text(pdf, "HELIOS PRO", 42, height - 42, 18, colors.HexColor("#e6edf7"), bold=True)
        _pdf_text(pdf, "MODEL APPROVAL PACKET", 42, height - 60, 9, colors.HexColor("#55a7ff"), bold=True)
        _pdf_text(pdf, section, width - 252, height - 44, 10, colors.HexColor("#ffd24a"), bold=True)
        pdf.setStrokeColor(colors.HexColor("#263548"))
        pdf.line(42, 62, width - 42, 62)
        _pdf_text(pdf, "Analysis-only governance evidence. Not investment advice, order execution, or a return guarantee.", 42, 44, 7.5, colors.HexColor("#9fb2c8"))
        _pdf_text(pdf, f"Page {page_no} of {total}", width - 104, 44, 8, colors.HexColor("#9fb2c8"))

    total_pages = 3
    background("COMMITTEE COVER", 1, total_pages)
    y = 662
    y = _pdf_wrapped(pdf, str(model.get("name") or "Model"), 42, y, 520, 22, colors.HexColor("#f8fafc"), bold=True, max_lines=2)
    _pdf_text(pdf, f"Version {packet.get('version') or ''} / Status {approval.get('status') or ''}", 42, y - 12, 10, colors.HexColor("#9fb2c8"))
    cards = [
        ("Approval Gate", "Pass" if risk_gate.get("can_approve") else "Blocked"),
        ("Risk State", risk_gate.get("state") or "unknown"),
        ("Turnover", f"{version_diff.get('turnover_pct') or 0}%"),
        ("Committee Notes", len(notes)),
        ("Signer", identity.get("signer_name") or "Not recorded"),
        ("Verification", "Verified local PIN" if identity.get("verified") else identity.get("verification_method") or "Local attestation"),
    ]
    _pdf_card_grid(pdf, cards, 42, y - 98, 252, 56, colors)
    _pdf_panel(
        pdf,
        42,
        130,
        width - 84,
        112,
        "ADVISOR REVIEW REQUIRED",
        str(packet.get("disclaimer") or _disclaimer()),
        colors,
        accent="#ffd24a",
    )
    pdf.showPage()

    background("LOCAL COMMITTEE IDENTITY", 2, total_pages)
    y = 660
    _pdf_text(pdf, "LOCAL COMMITTEE IDENTITY", 42, y, 16, colors.HexColor("#f8fafc"), bold=True)
    y -= 34
    identity_rows = [
        ("Signer", identity.get("signer_name") or "Not recorded"),
        ("Role", identity.get("signer_role") or "Not recorded"),
        ("Committee", identity.get("committee") or "Not recorded"),
        ("Verification", "Verified local PIN" if identity.get("verified") else identity.get("verification_method") or "Local attestation"),
        ("Scope", identity.get("scope") or "local_workspace"),
    ]
    for label, value in identity_rows:
        _pdf_text(pdf, label.upper(), 42, y, 8, colors.HexColor("#9fb2c8"), bold=True)
        y = _pdf_wrapped(pdf, str(value), 160, y, 380, 10, colors.HexColor("#e6edf7"), max_lines=2) - 14
    _pdf_text(pdf, "VERSION DIFF", 42, y - 4, 13, colors.HexColor("#ffd24a"), bold=True)
    y -= 28
    y = _pdf_wrapped(pdf, str(version_diff.get("summary") or "No version diff recorded."), 42, y, 500, 9, colors.HexColor("#c8d3df"), max_lines=4) - 10
    _pdf_text(pdf, "COMMITTEE NOTES", 42, y, 13, colors.HexColor("#ffd24a"), bold=True)
    y -= 22
    for row in notes[:5]:
        if not isinstance(row, dict):
            continue
        note_identity = row.get("committee_identity") if isinstance(row.get("committee_identity"), dict) else {}
        text = (
            f"{row.get('actor') or 'Unknown'} / {row.get('action') or ''}: {row.get('note') or ''} "
            f"Signer: {note_identity.get('signer_name') or identity.get('signer_name') or 'not recorded'}"
        )
        y = _pdf_wrapped(pdf, text, 58, y, 482, 8.5, colors.HexColor("#c8d3df"), max_lines=3) - 10
    if not notes:
        _pdf_text(pdf, "No committee notes recorded.", 58, y, 9, colors.HexColor("#9fb2c8"))
    pdf.showPage()

    background("HOLDINGS AND AUDIT TRAIL", 3, total_pages)
    y = 660
    _pdf_text(pdf, "CURRENT HOLDINGS", 42, y, 14, colors.HexColor("#ffd24a"), bold=True)
    y -= 24
    for row in holdings[:12]:
        if not isinstance(row, dict):
            continue
        _pdf_text(pdf, f"{row.get('ticker') or ''}", 58, y, 9, colors.HexColor("#e6edf7"), bold=True)
        _pdf_text(pdf, f"{row.get('weight_pct') or 0}%", 170, y, 9, colors.HexColor("#9fb2c8"))
        y -= 16
    y -= 12
    _pdf_text(pdf, "AUDIT TRAIL", 42, y, 14, colors.HexColor("#ffd24a"), bold=True)
    y -= 24
    for row in audit[:10]:
        if not isinstance(row, dict):
            continue
        text = f"v{row.get('version')} / {row.get('actor')} / {row.get('action')} / {row.get('approval_status')}"
        y = _pdf_wrapped(pdf, text, 58, y, 482, 8.5, colors.HexColor("#c8d3df"), max_lines=2) - 8
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _model_row(model: portfolio.Model, events: list[dict[str, Any]]) -> dict[str, Any]:
    latest_event = events[0] if events else None
    latest_status_event = next((event for event in events if event.get("approval_status")), None)
    status = _normalise_status(latest_status_event.get("approval_status") if latest_status_event else "") or "draft"
    version = int(latest_event.get("version") or 1) if latest_event else 1
    limits = _risk_limits(model)
    rebalance = _rebalance_rules(model)
    violations = _risk_limit_violations(model, limits)
    risk_gate = _risk_gate(model)
    latest_rebalance = next((event for event in events if event.get("action") in REBALANCE_ACTIONS), None)
    latest_note_event = next((event for event in events if event.get("note")), None)
    approved_by = ""
    approval_updated_at = None
    if latest_status_event:
        approved_by = latest_status_event.get("actor") or ""
        approval_updated_at = latest_status_event.get("created_at")
    return {
        "id": model.id,
        "name": model.name,
        "mandate": model.mandate_key,
        "mandate_label": mandate.get(model.mandate_key)["label"],
        "mandate_context": model.mandate_context,
        "version": version,
        "approval_status": status,
        "approved_by": approved_by if status == "approved" else "",
        "approval_updated_at": approval_updated_at,
        "risk_limits": limits,
        "risk_limit_state": "breach" if violations else "within_limits",
        "risk_limit_violations": violations,
        "can_approve": risk_gate["can_approve"],
        "approval_blocked_reason": risk_gate["blocked_reason"],
        "rebalance_rules": rebalance,
        "rebalance_status": "recorded" if latest_rebalance else "not_recorded",
        "last_rebalance_at": latest_rebalance.get("created_at") if latest_rebalance else None,
        "version_count": max(version, 1),
        "snapshot_count": sum(1 for event in events if event.get("snapshot")),
        "change_note_count": sum(1 for event in events if event.get("note")),
        "latest_change_note": latest_note_event.get("note") if latest_note_event else "",
        "updated_by": latest_event.get("actor") if latest_event else "",
        "updated_at": latest_event.get("created_at") if latest_event else None,
        "holdings_count": len(model.holdings),
        "top_holding": model.holdings[0].ticker if model.holdings else "",
        "top_weight_pct": round(float(model.holdings[0].weight) * 100, 2) if model.holdings else 0.0,
        "holdings": _holdings_payload(model.holdings),
        "source": _template_for_model(model).get("provenance", {}).get("source_type", "client_model"),
        "provenance": _template_for_model(model).get("provenance", {
            "source_type": "client_model",
            "version": "",
            "basis": "uploaded or locally created client model",
            "caveat": "Governance metadata is local workflow evidence, not investment advice.",
        }),
    }


def _editor_payload(
    current_model: portfolio.Model,
    draft_model: portfolio.Model,
    *,
    rebalance_to_target: bool,
) -> dict[str, Any]:
    limits = _risk_limits(current_model)
    violations = _risk_limit_violations(draft_model, limits)
    return {
        "model": {
            "id": draft_model.id,
            "name": draft_model.name,
            "mandate": draft_model.mandate_key,
            "mandate_label": mandate.get(draft_model.mandate_key)["label"],
            "holdings": _holdings_payload(draft_model.holdings),
        },
        "current_holdings": _holdings_payload(current_model.holdings),
        "proposed_holdings": _holdings_payload(draft_model.holdings),
        "rebalance_to_target": bool(rebalance_to_target),
        "risk_limits": limits,
        "risk_limit_state": "breach" if violations else "within_limits",
        "risk_limit_violations": violations,
        "can_save": True,
        "requires_change_note": True,
        "disclaimer": _disclaimer(),
    }


def _draft_model(
    model: portfolio.Model,
    holdings: list[dict[str, Any]],
    *,
    rebalance_to_target: bool,
) -> portfolio.Model:
    proposed = _parse_editor_holdings(holdings, rebalance_to_target=rebalance_to_target)
    return portfolio.Model(
        id=model.id,
        name=model.name,
        mandate_key=model.mandate_key,
        mandate_context=model.mandate_context,
        holdings=proposed,
    )


def _parse_editor_holdings(raw_holdings: list[dict[str, Any]], *, rebalance_to_target: bool) -> list[portfolio.Holding]:
    if not isinstance(raw_holdings, list) or not raw_holdings:
        raise ValueError("Add at least one holding before previewing or saving model edits.")
    merged: dict[str, float] = {}
    order: list[str] = []
    for raw in raw_holdings:
        if not isinstance(raw, dict):
            continue
        ticker = data.clean_symbol(str(raw.get("ticker") or raw.get("symbol") or ""), fallback="")
        if not ticker:
            continue
        weight_pct = _editor_weight_pct(raw)
        if weight_pct <= 0:
            continue
        if ticker not in merged:
            order.append(ticker)
        merged[ticker] = merged.get(ticker, 0.0) + weight_pct
    if not merged:
        raise ValueError("Add at least one valid ticker with a positive target weight.")
    if len(merged) > portfolio.MAX_HOLDINGS:
        raise ValueError(f"Model editor supports up to {portfolio.MAX_HOLDINGS} holdings.")
    total_pct = sum(merged.values())
    if total_pct <= 0:
        raise ValueError("Target weights must be positive.")
    divisor = total_pct if rebalance_to_target else 100.0
    holdings = [portfolio.Holding(ticker, round(merged[ticker] / divisor, 6)) for ticker in order]
    return holdings


def _editor_weight_pct(raw: dict[str, Any]) -> float:
    value = raw.get("weight_pct")
    if value is None:
        value = raw.get("target_weight_pct")
    if value is None:
        value = raw.get("weight")
        if isinstance(value, (int, float)) and 0 < float(value) <= 1:
            return float(value) * 100
    try:
        return max(0.0, float(str(value).replace("%", "").strip()))
    except (TypeError, ValueError):
        return 0.0


def _holdings_payload(holdings: list[portfolio.Holding]) -> list[dict[str, Any]]:
    return [
        {
            "ticker": holding.ticker,
            "weight": round(float(holding.weight), 6),
            "weight_pct": round(float(holding.weight) * 100, 2),
            "source": holding.source or "pending",
        }
        for holding in holdings
    ]


def _snapshot(
    model: portfolio.Model,
    *,
    version: int,
    approval_status: str,
    previous_model: portfolio.Model | None = None,
    action: str = "",
    note: str = "",
    committee_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    before_snapshot = _compact_model_snapshot(previous_model, max(1, version - 1)) if previous_model is not None else {}
    after_snapshot = _compact_model_snapshot(model, version)
    return {
        "model": {
            "id": model.id,
            "name": model.name,
            "mandate": model.mandate_key,
            "mandate_label": mandate.get(model.mandate_key)["label"],
            "mandate_context": model.mandate_context,
        },
        "version": version,
        "approval_status": approval_status,
        "holdings": [
            {"ticker": holding.ticker, "weight": round(float(holding.weight), 6), "weight_pct": round(float(holding.weight) * 100, 2)}
            for holding in model.holdings
        ],
        "risk_limits": _risk_limits(model),
        "risk_limit_violations": _risk_limit_violations(model, _risk_limits(model)),
        "risk_gate": _risk_gate(model),
        "rebalance_rules": _rebalance_rules(model),
        "before_snapshot": before_snapshot,
        "after_snapshot": after_snapshot,
        "version_diff": _version_diff(previous_model, model) if previous_model is not None else _empty_version_diff(),
        "event_action": action,
        "committee_note": note,
        "committee_identity": committee_identity or {},
        "provenance": _template_for_model(model).get("provenance", {
            "source_type": "client_model",
            "version": "",
            "basis": "uploaded or locally created client model",
            "caveat": "Governance snapshot is local workflow evidence, not investment advice.",
        }),
        "disclaimer": _disclaimer(),
    }


def _compact_model_snapshot(model: portfolio.Model, version: int) -> dict[str, Any]:
    limits = _risk_limits(model)
    return {
        "model": {
            "id": model.id,
            "name": model.name,
            "mandate": model.mandate_key,
            "mandate_label": mandate.get(model.mandate_key)["label"],
        },
        "version": int(version),
        "holdings": _holdings_payload(model.holdings),
        "risk_limits": limits,
        "risk_limit_violations": _risk_limit_violations(model, limits),
        "risk_gate": _risk_gate(model),
    }


def _version_diff(before: portfolio.Model | None, after: portfolio.Model) -> dict[str, Any]:
    if before is None:
        return _empty_version_diff()
    before_weights = {holding.ticker: round(float(holding.weight) * 100, 2) for holding in before.holdings}
    after_weights = {holding.ticker: round(float(holding.weight) * 100, 2) for holding in after.holdings}
    added = [
        {"ticker": ticker, "weight_pct": after_weights[ticker]}
        for ticker in after_weights
        if ticker not in before_weights
    ]
    removed = [
        {"ticker": ticker, "weight_pct": before_weights[ticker]}
        for ticker in before_weights
        if ticker not in after_weights
    ]
    changed = [
        {
            "ticker": ticker,
            "from_weight_pct": before_weights[ticker],
            "to_weight_pct": after_weights[ticker],
            "change_pct": round(after_weights[ticker] - before_weights[ticker], 2),
        }
        for ticker in after_weights
        if ticker in before_weights and abs(after_weights[ticker] - before_weights[ticker]) >= 0.01
    ]
    all_tickers = set(before_weights) | set(after_weights)
    turnover_pct = round(sum(abs(after_weights.get(ticker, 0.0) - before_weights.get(ticker, 0.0)) for ticker in all_tickers) / 2.0, 2)
    summary = f"{len(added)} added, {len(removed)} removed, {len(changed)} changed weights; estimated one-way turnover {turnover_pct:.2f}%."
    return {
        "added": added,
        "removed": removed,
        "changed_weights": changed,
        "turnover_pct": turnover_pct,
        "summary": summary,
    }


def safe_actor(actor: Any) -> str:
    value = str(actor or "").strip()
    return value[:120] if value else DEFAULT_ACTOR


def _committee_identity(
    raw: dict[str, Any] | None,
    *,
    actor: str,
    approval_status: str,
) -> tuple[dict[str, Any], str]:
    raw_identity = raw if isinstance(raw, dict) else {}
    is_decision = approval_status in {"approved", "rejected"}
    pin_configured = _governance_pin_configured()
    if not is_decision and not raw_identity:
        return {}, ""

    signer_name = str(raw_identity.get("signer_name") or "").strip()
    signer_role = str(raw_identity.get("signer_role") or "").strip()
    committee = str(raw_identity.get("committee") or "").strip()
    secret = str(raw_identity.get("secret") or raw_identity.get("pin") or "").strip()

    if pin_configured:
        if not signer_name or not signer_role or not committee or not secret:
            return {}, "Committee identity and local approval PIN verification are required before approving or rejecting a model."
        if not _verify_governance_secret(secret):
            return {}, "Local committee identity verification failed."
        verified = True
        method = "local_pin"
    else:
        signer_name = signer_name or safe_actor(actor)
        signer_role = signer_role or "Local workspace reviewer"
        committee = committee or "Local Investment Committee"
        verified = False
        method = "local_attestation"

    return {
        "signer_name": signer_name[:120],
        "signer_role": signer_role[:120],
        "committee": committee[:160],
        "verification_method": method,
        "verified": verified,
        "scope": "local_workspace",
    }, ""


def _governance_pin_configured() -> bool:
    return bool(os.environ.get("HELIOS_GOVERNANCE_APPROVER_PIN") or os.environ.get("HELIOS_GOVERNANCE_APPROVER_PIN_HASH"))


def _verify_governance_secret(secret: str) -> bool:
    supplied_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    configured_hash = str(os.environ.get("HELIOS_GOVERNANCE_APPROVER_PIN_HASH") or "").strip().lower()
    if configured_hash:
        return hmac.compare_digest(supplied_hash, configured_hash)
    configured_pin = str(os.environ.get("HELIOS_GOVERNANCE_APPROVER_PIN") or "")
    if not configured_pin:
        return False
    expected_hash = hashlib.sha256(configured_pin.encode("utf-8")).hexdigest()
    return hmac.compare_digest(supplied_hash, expected_hash)


def _event_committee_identity(event: dict[str, Any]) -> dict[str, Any]:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    identity = metadata.get("committee_identity") if isinstance(metadata.get("committee_identity"), dict) else {}
    if identity:
        return identity
    snapshot = event.get("snapshot") if isinstance(event.get("snapshot"), dict) else {}
    identity = snapshot.get("committee_identity") if isinstance(snapshot.get("committee_identity"), dict) else {}
    return identity if isinstance(identity, dict) else {}


def _latest_committee_identity(events: list[dict[str, Any]], latest_snapshot: dict[str, Any]) -> dict[str, Any]:
    snapshot_identity = latest_snapshot.get("committee_identity") if isinstance(latest_snapshot, dict) else {}
    if isinstance(snapshot_identity, dict) and snapshot_identity:
        return snapshot_identity
    for event in events:
        identity = _event_committee_identity(event)
        if identity:
            return identity
    return {}


def _empty_version_diff() -> dict[str, Any]:
    return {
        "added": [],
        "removed": [],
        "changed_weights": [],
        "turnover_pct": 0.0,
        "summary": "No holding-level version changes recorded.",
    }


def _risk_gate(model: portfolio.Model) -> dict[str, Any]:
    limits = _risk_limits(model)
    violations = _risk_limit_violations(model, limits)
    return {
        "can_approve": not violations,
        "state": "blocked" if violations else "pass",
        "blocked_reason": "Risk-limit breaches must be resolved before approval." if violations else "",
        "violations": violations,
    }


def _html_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "<p class=\"muted\">None.</p>"
    head = "".join(f"<th>{escape(str(header))}</th>" for header in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(value if value is not None else ''))}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _html_committee_note(row: dict[str, Any]) -> str:
    identity = row.get("committee_identity") if isinstance(row.get("committee_identity"), dict) else {}
    signer = identity.get("signer_name") if isinstance(identity, dict) else ""
    verified = "verified local PIN" if identity.get("verified") else identity.get("verification_method") or "local attestation"
    identity_copy = f"<small>{escape(str(signer or 'Signer not recorded'))} / {escape(str(verified))}</small>"
    return (
        f"<article><strong>{escape(str(row.get('actor') or ''))}</strong>"
        f"{identity_copy}<p>{escape(str(row.get('note') or ''))}</p></article>"
    )


def _pdf_text(pdf: Any, text: str, x: float, y: float, size: float, color: Any, *, bold: bool = False) -> None:
    pdf.setFillColor(color)
    pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    pdf.drawString(x, y, str(text))


def _pdf_wrapped(
    pdf: Any,
    text: str,
    x: float,
    y: float,
    width: float,
    size: float,
    color: Any,
    *,
    bold: bool = False,
    max_lines: int = 4,
) -> float:
    words = str(text or "").split()
    if not words:
        return y
    line = ""
    lines: list[str] = []
    max_chars = max(18, int(width / max(size * 0.52, 1)))
    for word in words:
        candidate = f"{line} {word}".strip()
        if len(candidate) > max_chars and line:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    for idx, line_text in enumerate(lines[:max_lines]):
        _pdf_text(pdf, line_text, x, y - idx * (size + 4), size, color, bold=bold)
    return y - min(len(lines), max_lines) * (size + 4)


def _pdf_card_grid(pdf: Any, cards: list[tuple[str, Any]], x: float, y: float, card_w: float, card_h: float, colors: Any) -> None:
    for index, (label, value) in enumerate(cards):
        col = index % 2
        row = index // 2
        cx = x + col * (card_w + 14)
        cy = y - row * (card_h + 12)
        pdf.setFillColor(colors.HexColor("#0b1522"))
        pdf.setStrokeColor(colors.HexColor("#263548"))
        pdf.roundRect(cx, cy, card_w, card_h, 6, stroke=1, fill=1)
        _pdf_text(pdf, str(label).upper(), cx + 12, cy + card_h - 18, 7.5, colors.HexColor("#9fb2c8"), bold=True)
        _pdf_wrapped(pdf, str(value), cx + 12, cy + card_h - 36, card_w - 24, 10, colors.HexColor("#e6edf7"), bold=True, max_lines=2)


def _pdf_panel(
    pdf: Any,
    x: float,
    y: float,
    width: float,
    height: float,
    title: str,
    body: str,
    colors: Any,
    *,
    accent: str,
) -> None:
    pdf.setFillColor(colors.HexColor("#101b2a"))
    pdf.setStrokeColor(colors.HexColor("#263548"))
    pdf.roundRect(x, y, width, height, 8, stroke=1, fill=1)
    _pdf_text(pdf, title, x + 16, y + height - 24, 10, colors.HexColor(accent), bold=True)
    _pdf_wrapped(pdf, body, x + 16, y + height - 46, width - 32, 9, colors.HexColor("#c8d3df"), max_lines=4)


def _snapshot_summary(event: dict[str, Any]) -> dict[str, Any]:
    snapshot = event.get("snapshot") or {}
    model = snapshot.get("model") or {}
    return {
        "event_id": event["id"],
        "model_id": event["model_id"],
        "model_name": model.get("name") or event["model_id"],
        "version": event["version"],
        "created_at": event["created_at"],
        "actor": event["actor"],
        "action": event["action"],
        "approval_status": event["approval_status"],
        "holding_count": len(snapshot.get("holdings") or []),
        "risk_limit_state": "breach" if snapshot.get("risk_limit_violations") else "within_limits",
        "version_diff": snapshot.get("version_diff") or _empty_version_diff(),
        "committee_identity": _event_committee_identity(event),
    }


def _event_summary(event: dict[str, Any]) -> dict[str, Any]:
    snapshot = event.get("snapshot") if isinstance(event.get("snapshot"), dict) else {}
    return {
        "id": event["id"],
        "model_id": event["model_id"],
        "created_at": event["created_at"],
        "version": event["version"],
        "actor": event["actor"],
        "action": event["action"],
        "note": event["note"],
        "approval_status": event["approval_status"],
        "committee_note": snapshot.get("committee_note") or event.get("note") or "",
        "committee_identity": _event_committee_identity(event),
        "version_diff": snapshot.get("version_diff") or _empty_version_diff(),
        "risk_gate": snapshot.get("risk_gate") or {},
    }


def _risk_limits(model: portfolio.Model) -> dict[str, float | int]:
    template = _template_for_model(model)
    if template:
        return dict(template["risk_limits"])
    m = mandate.get(model.mandate_key)
    return {
        "max_single_position_pct": round(float(m.get("single_name_cap", 0.35)) * 100, 2),
        "max_theme_position_pct": 100.0,
        "max_etf_position_pct": round(float(m.get("single_name_cap", 0.35)) * 100, 2),
        "min_holdings": 5,
    }


def _rebalance_rules(model: portfolio.Model) -> dict[str, Any]:
    template = _template_for_model(model)
    if template:
        return dict(template["rebalance_rules"])
    return {
        "frequency": "quarterly",
        "drift_band_pct": 5.0,
        "review_trigger": "review after material drift, mandate change, or risk-limit breach",
    }


def _risk_limit_violations(model: portfolio.Model, limits: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    max_single = float(limits.get("max_single_position_pct") or 0.0)
    for holding in model.holdings:
        weight_pct = round(float(holding.weight) * 100, 2)
        if max_single and weight_pct > max_single:
            out.append({
                "field": "max_single_position_pct",
                "ticker": holding.ticker,
                "limit": max_single,
                "actual": weight_pct,
                "message": f"{holding.ticker} is {weight_pct:.1f}% versus {max_single:.1f}% max single-position limit.",
            })
    min_holdings = int(limits.get("min_holdings") or 0)
    if min_holdings and len(model.holdings) < min_holdings:
        out.append({
            "field": "min_holdings",
            "limit": min_holdings,
            "actual": len(model.holdings),
            "message": f"Model has {len(model.holdings)} holdings versus {min_holdings} required by governance rules.",
        })
    total_pct = round(sum(float(holding.weight) for holding in model.holdings) * 100, 2)
    if model.holdings and abs(total_pct - 100.0) > 0.5:
        out.append({
            "field": "total_weight_pct",
            "limit": 100.0,
            "actual": total_pct,
            "message": f"Model weights sum to {total_pct:.1f}%; governance expects normalized 100% allocation.",
        })
    return out


def _template_for_model(model: portfolio.Model) -> dict[str, Any]:
    for template in model_library.public_list():
        if template["model_id"] == model.id:
            return template
    return {}


def _normalise_status(value: Any) -> str:
    status = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return status if status in APPROVAL_STATUSES else ""


def _normalise_action(value: Any, has_status: bool) -> str:
    action = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    allowed = {"approval_update", "archive_snapshot", "change_note", "model_edit", *REBALANCE_ACTIONS}
    if action in allowed:
        return action
    return "approval_update" if has_status else "change_note"


def _disclaimer() -> str:
    return (
        "Model governance records local approval, mandate, risk-limit, rebalance, "
        "and snapshot evidence only. It is analysis-only and does not execute "
        "trades, guarantee outcomes, or override Helios data provenance gates."
    )

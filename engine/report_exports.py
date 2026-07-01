"""Saved report snapshots and export renderers.

Exports are intentionally built from deterministic Helios report payloads. The
client may provide an already-generated AI narrative, but this module never
calls an AI provider or recalculates analytics.
"""
from __future__ import annotations

import html
import secrets
import textwrap
from datetime import datetime, timezone
from typing import Any

from .reporting import DISCLAIMER


def build_snapshot(
    *,
    target_kind: str,
    target_id: str,
    report: dict[str, Any],
    ai_narrative: str = "",
    ai_narrative_status: str = "",
    ai_provider: dict[str, Any] | None = None,
    version: int = 1,
    prepared_for: str = "",
    prepared_by: str = "",
    reviewer: str = "",
    report_purpose: str = "",
) -> dict[str, Any]:
    data_quality = report.get("data_provenance") or {}
    sections = report.get("sections") or {}
    provenance = sections.get("provenance") if isinstance(sections.get("provenance"), dict) else {}
    source = str(data_quality.get("source") or provenance.get("source") or report.get("source") or "")
    row_count = _int(data_quality.get("row_count") or provenance.get("row_count") or data_quality.get("history_days") or 0)
    first_date = _text(data_quality.get("first_date") or provenance.get("first_date"))
    last_date = _text(data_quality.get("last_date") or provenance.get("last_date"))
    source_counts = _dict(data_quality.get("source_counts") or provenance.get("source_counts"))
    if not source_counts and source:
        source_counts = {source: 1}
    target_name = _text(report.get("name") or report.get("symbol") or report.get("id") or target_id)
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    safe_version = max(1, _int(version))
    version_label = f"v{safe_version}"
    prepared_for = _text(prepared_for)[:160]
    prepared_by = _text(prepared_by)[:160]
    reviewer = _text(reviewer)[:160]
    report_purpose = _text(report_purpose)[:80] or "advisor_review"
    output_formats = ["html", "pdf", "print"]
    disclosure_blocks = _disclosure_blocks(report)
    audit_trail = _audit_trail(
        report=report,
        created_at=created_at,
        version_label=version_label,
        prepared_by=prepared_by,
    )
    snapshot = {
        "id": secrets.token_urlsafe(12),
        "created_at": created_at,
        "report_package": "institutional_advisor_report",
        "version": safe_version,
        "version_label": version_label,
        "target_kind": target_kind,
        "target_id": target_id,
        "target_name": target_name,
        "prepared_for": prepared_for,
        "prepared_by": prepared_by,
        "reviewer": reviewer,
        "report_purpose": report_purpose,
        "title": _text(report.get("title") or target_name),
        "data_mode": _text(report.get("data_mode") or data_quality.get("data_mode")),
        "display_label": _text(report.get("display_label") or data_quality.get("display_label")),
        "eligible_for_real_research": bool(report.get("eligible_for_real_research")),
        "source": source,
        "row_count": row_count,
        "first_date": first_date,
        "last_date": last_date,
        "source_counts": source_counts,
        "model_metadata": _model_metadata(report) if target_kind == "model" else {},
        "warnings": _list(report.get("warnings")),
        "report": report,
        "ai_narrative": _text(ai_narrative)[:6000],
        "ai_narrative_included": bool(_text(ai_narrative)),
        "ai_narrative_status": ai_narrative_status or ("provided" if _text(ai_narrative) else "not_requested"),
        "ai_provider": ai_provider or {},
        "audit_trail": audit_trail,
        "disclosure_blocks": disclosure_blocks,
        "output_formats": output_formats,
        "metadata": {
            "snapshot_version": 2,
            "institutional_report": True,
            "report_package": "institutional_advisor_report",
            "version": safe_version,
            "version_label": version_label,
            "prepared_for": prepared_for,
            "prepared_by": prepared_by,
            "reviewer": reviewer,
            "report_purpose": report_purpose,
            "audit_trail": audit_trail,
            "disclosure_blocks": disclosure_blocks,
            "output_formats": output_formats,
            "analysis_only": True,
            "no_execution": True,
            "no_return_guarantee": True,
            "report_timestamp": _text(report.get("timestamp")),
            "ai_narrative_status": ai_narrative_status or ("provided" if _text(ai_narrative) else "not_requested"),
            "ai_provider": ai_provider or {},
        },
    }
    snapshot["html"] = render_html(snapshot)
    return snapshot


def public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    out = {key: value for key, value in snapshot.items() if key not in {"report", "html", "ai_narrative"}}
    snapshot_id = str(snapshot.get("id") or "")
    out["html_url"] = f"/api/report/snapshots/{snapshot_id}.html"
    out["pdf_url"] = f"/api/report/snapshots/{snapshot_id}.pdf"
    out["ai_narrative_included"] = bool(snapshot.get("ai_narrative"))
    out["ai_narrative_status"] = snapshot.get("ai_narrative_status") or snapshot.get("metadata", {}).get("ai_narrative_status") or ("included" if snapshot.get("ai_narrative") else "not_included")
    out["ai_provider"] = snapshot.get("ai_provider") or snapshot.get("metadata", {}).get("ai_provider") or {}
    return out


def ai_result_to_narrative(result: dict[str, Any]) -> str:
    parts = [
        _text(result.get("summary")),
        _text(result.get("advisor_language")),
        _text(result.get("data_quality_statement")),
        _bullet_block("Key points", result.get("key_points")),
        _bullet_block("Risks", result.get("risks")),
        _bullet_block("Compliance caveats", result.get("compliance_caveats")),
    ]
    return "\n\n".join(part for part in parts if part)


def render_html(snapshot: dict[str, Any]) -> str:
    rows = {
        "Report Version": snapshot.get("version_label") or "v1",
        "Prepared For": snapshot.get("prepared_for") or "Advisor review",
        "Prepared By": snapshot.get("prepared_by") or "Helios local workspace",
        "Reviewer": snapshot.get("reviewer") or "Advisor review required",
        "Report Purpose": snapshot.get("report_purpose") or "advisor_review",
        "Source": snapshot.get("source") or "Unknown",
        "Row Count": snapshot.get("row_count") or 0,
        "First Date": snapshot.get("first_date") or "Unknown",
        "Last Date": snapshot.get("last_date") or "Unknown",
        "Data Mode": snapshot.get("data_mode") or "Unknown",
        "Eligible For Real Research": "Yes" if snapshot.get("eligible_for_real_research") else "No",
        "Source Counts": snapshot.get("source_counts") or {},
    }
    report = snapshot.get("report") or {}
    sections = report.get("sections") if isinstance(report.get("sections"), dict) else {}
    return "\n".join([
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_esc(snapshot.get('title') or 'Helios Report Snapshot')}</title>",
        "<style>",
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#071019;color:#e6edf7;margin:0;padding:32px;line-height:1.45}",
        "main{max-width:1120px;margin:0 auto;background:#0d1622;border:1px solid #263548;border-radius:8px;overflow:hidden}",
        ".cover{padding:30px 34px;background:linear-gradient(135deg,#101d2c,#08111b 70%);border-bottom:1px solid #263548}",
        ".eyebrow{margin:0 0 8px;color:#55a7ff;font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.1em}",
        "h1,h2,h3{margin:0 0 12px} h1{font-size:32px;letter-spacing:-.01em} h2{font-size:16px;text-transform:uppercase;letter-spacing:.08em;color:#9fb2c8} h3{font-size:14px}",
        ".body{padding:26px 34px} section{border-top:1px solid #263548;margin-top:22px;padding-top:18px} dl{display:grid;grid-template-columns:230px 1fr;gap:8px 18px}",
        "dt{color:#9fb2c8;font-weight:700} dd{margin:0} .warn{color:#ffd24a}.muted{color:#9fb2c8}.caveat{border:1px solid #775f18;background:#17180f;padding:14px;border-radius:6px}",
        ".disclosures{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.disclosure{border:1px solid #263548;background:#0a111c;border-radius:6px;padding:14px}.audit{display:grid;gap:10px}.audit article{border:1px solid #263548;border-radius:6px;background:#09111b;padding:12px}",
        "pre{white-space:pre-wrap;font-family:inherit}.footer{font-size:13px;color:#9fb2c8}@media print{body{background:#fff;color:#111827;padding:0}main{border:0}section{break-inside:avoid}.cover{background:#f8fafc;color:#0f172a}.disclosures{grid-template-columns:1fr}}",
        "</style>",
        "</head>",
        "<body>",
        "<main>",
        "<header class=\"cover\">",
        "<p class=\"eyebrow\">Institutional Advisor Report / Helios Report Snapshot</p>",
        f"<h1>{_esc(snapshot.get('title'))}</h1>",
        f"<p class=\"muted\">{_esc(snapshot.get('version_label') or 'v1')} / Saved {_esc(snapshot.get('created_at'))} / {_esc(snapshot.get('target_kind'))}:{_esc(snapshot.get('target_id'))}</p>",
        "</header>",
        '<div class="body">',
        '<section class="caveat">',
        "<h2>Analysis-Only Caveat</h2>",
        f"<p>{_esc(report.get('disclaimer') or DISCLAIMER)}</p>",
        "</section>",
        '<section class="caveat">',
        "<h2>Advisor Review Required</h2>",
        "<p>Reports and any AI narrative are evidence summaries for advisor review. Helios does not execute orders, provide investment advice, or guarantee returns.</p>",
        "</section>",
        "<section>",
        "<h2>Source And Provenance</h2>",
        _render_dict(rows),
        "</section>",
        _audit_html(snapshot),
        _disclosures_html(snapshot),
        _model_metadata_html(snapshot),
        _warnings_html(snapshot),
        _ai_html(snapshot),
        "<section>",
        "<h2>Report Sections</h2>",
        _render_value(sections),
        "</section>",
        f"<p class=\"footer\">{_esc(DISCLAIMER)}</p>",
        "</div>",
        "</main>",
        "</body>",
        "</html>",
    ])


def render_pdf(snapshot: dict[str, Any]) -> bytes:
    page_streams = _pdf_page_streams(snapshot)
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    }
    page_refs = []
    obj_id = 5
    for stream_text in page_streams:
        page_obj = obj_id
        content_obj = obj_id + 1
        obj_id += 2
        page_refs.append(f"{page_obj} 0 R")
        stream = stream_text.encode("latin-1", "replace")
        objects[page_obj] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_obj} 0 R >>"
        ).encode("ascii")
        objects[content_obj] = b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(page_refs)}] /Count {len(page_refs)} >>".encode("ascii")
    out = bytearray(b"%PDF-1.4\n")
    offsets = {0: 0}
    for index in sorted(objects):
        obj = objects[index]
        offsets[index] = len(out)
        out.extend(f"{index} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref = len(out)
    max_obj = max(objects)
    out.extend(f"xref\n0 {max_obj + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for index in range(1, max_obj + 1):
        offset = offsets.get(index, 0)
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer << /Size {max_obj + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)


def _pdf_page_streams(snapshot: dict[str, Any]) -> list[str]:
    return [_pdf_cover_page(snapshot), _pdf_evidence_page(snapshot)]


def _pdf_cover_page(snapshot: dict[str, Any]) -> str:
    cmds = [_pdf_rect(0, 0, 612, 792, (0.025, 0.055, 0.085))]
    cmds.append(_pdf_rect(0, 724, 612, 68, (0.04, 0.075, 0.12)))
    cmds.append(_pdf_text("HELIOS PRO", 42, 758, 18, bold=True))
    cmds.append(_pdf_text("Advisor-Grade Research Terminal", 42, 740, 9, color=(0.70, 0.78, 0.88)))
    cmds.append(_pdf_text("INSTITUTIONAL ADVISOR REPORT", 340, 754, 11, bold=True, color=(0.32, 0.65, 1.0)))
    y = _pdf_wrapped(cmds, str(snapshot.get("title") or "Helios Report Snapshot"), 42, 682, 520, size=21, bold=True, max_lines=3)
    y -= 18
    cmds.append(_pdf_text("REPORT VERSION", 42, y, 11, bold=True, color=(0.32, 0.65, 1.0)))
    cmds.append(_pdf_text(str(snapshot.get("version_label") or "v1"), 174, y, 11, bold=True))
    y -= 24
    cmds.append(_pdf_text("SOURCE AND PROVENANCE", 42, y, 11, bold=True, color=(1.0, 0.82, 0.24)))
    y -= 18
    cards = [
        ("Prepared For", snapshot.get("prepared_for") or "Advisor review"),
        ("Prepared By", snapshot.get("prepared_by") or "Helios local workspace"),
        ("Source", snapshot.get("source") or "Unknown"),
        ("Rows", snapshot.get("row_count") or 0),
        ("First Date", snapshot.get("first_date") or "Unknown"),
        ("Last Date", snapshot.get("last_date") or "Unknown"),
    ]
    for index, (label, value) in enumerate(cards):
        x = 42 + (index % 2) * 266
        box_y = y - 66 - (index // 2) * 82
        cmds.append(_pdf_rect(x, box_y, 246, 58, (0.055, 0.095, 0.145), stroke=(0.16, 0.25, 0.35)))
        cmds.append(_pdf_text(label, x + 14, box_y + 36, 8, bold=True, color=(0.62, 0.70, 0.80)))
        cmds.append(_pdf_text(str(value), x + 14, box_y + 16, 13, bold=True))
    cmds.append(_pdf_rect(42, 198, 528, 76, (0.11, 0.10, 0.05), stroke=(0.56, 0.43, 0.09)))
    cmds.append(_pdf_text("ADVISOR REVIEW REQUIRED", 58, 244, 12, bold=True, color=(1.0, 0.82, 0.24)))
    _pdf_wrapped(
        cmds,
        "Analysis only. Helios provides evidence summaries and does not provide investment advice, order execution, or return guarantees.",
        58,
        224,
        492,
        size=9,
        color=(0.86, 0.90, 0.96),
        max_lines=3,
    )
    cmds.append(_pdf_text("Helios Report Snapshot", 42, 42, 9, color=(0.62, 0.70, 0.80)))
    cmds.append(_pdf_text(str(snapshot.get("created_at") or ""), 430, 42, 9, color=(0.62, 0.70, 0.80)))
    return "".join(cmds)


def _pdf_evidence_page(snapshot: dict[str, Any]) -> str:
    cmds = [_pdf_rect(0, 0, 612, 792, (0.025, 0.055, 0.085))]
    cmds.append(_pdf_text("HELIOS PRO", 42, 760, 13, bold=True))
    cmds.append(_pdf_text("Institutional Report Details", 402, 760, 10, bold=True, color=(0.32, 0.65, 1.0)))
    y = 718
    y = _pdf_section(cmds, "AUDIT TRAIL", _audit_rows(snapshot), 42, y)
    y = _pdf_section(cmds, "DISCLOSURE BLOCKS", _disclosure_rows(snapshot), 42, y)
    y = _pdf_section(cmds, "MODEL METADATA", snapshot.get("model_metadata") or {}, 42, y)
    y = _pdf_section(cmds, "SOURCE COUNTS", snapshot.get("source_counts") or {}, 42, y)
    warnings = snapshot.get("warnings") or []
    if warnings:
        y = _pdf_section(cmds, "CAVEATS", {f"Caveat {i + 1}": warning for i, warning in enumerate(warnings[:6])}, 42, y)
    if snapshot.get("ai_narrative"):
        cmds.append(_pdf_text("AI NARRATIVE", 42, y, 11, bold=True, color=(1.0, 0.82, 0.24)))
        y -= 18
        y = _pdf_wrapped(cmds, str(snapshot["ai_narrative"]), 42, y, 520, size=9, max_lines=12)
        y -= 14
    cmds.append(_pdf_rect(42, max(y - 86, 64), 528, 72, (0.055, 0.095, 0.145), stroke=(0.16, 0.25, 0.35)))
    cmds.append(_pdf_text("EXPORT CONTROLS", 58, max(y - 42, 108), 10, bold=True, color=(0.32, 0.65, 1.0)))
    _pdf_wrapped(
        cmds,
        "This PDF is a frozen local snapshot of deterministic Helios report facts. Review the matching HTML snapshot for expanded sections and caveats.",
        58,
        max(y - 60, 90),
        492,
        size=8,
        color=(0.70, 0.78, 0.88),
        max_lines=3,
    )
    cmds.append(_pdf_text("ADVISOR REVIEW REQUIRED", 42, 42, 9, bold=True, color=(1.0, 0.82, 0.24)))
    return "".join(cmds)


def _pdf_section(cmds: list[str], title: str, rows: dict[str, Any], x: int, y: int) -> int:
    if not rows:
        return y
    cmds.append(_pdf_text(title, x, y, 11, bold=True, color=(1.0, 0.82, 0.24)))
    y -= 18
    for key, value in list(rows.items())[:8]:
        cmds.append(_pdf_text(_label(key), x, y, 8, bold=True, color=(0.62, 0.70, 0.80)))
        y = _pdf_wrapped(cmds, str(value), x + 145, y, 375, size=8, max_lines=2)
        y -= 6
    return y - 12


def _pdf_rect(x: int, y: int, width: int, height: int, fill: tuple[float, float, float], stroke: tuple[float, float, float] | None = None) -> str:
    command = f"{fill[0]} {fill[1]} {fill[2]} rg {x} {y} {width} {height} re f\n"
    if stroke:
        command += f"{stroke[0]} {stroke[1]} {stroke[2]} RG {x} {y} {width} {height} re S\n"
    return command


def _pdf_text(text: str, x: int, y: int, size: int, *, bold: bool = False, color: tuple[float, float, float] = (0.90, 0.94, 0.98)) -> str:
    font = "F2" if bold else "F1"
    return f"BT /{font} {size} Tf {color[0]} {color[1]} {color[2]} rg {x} {y} Td ({_pdf_escape(str(text))}) Tj ET\n"


def _pdf_wrapped(
    cmds: list[str],
    text: str,
    x: int,
    y: int,
    width: int,
    *,
    size: int,
    bold: bool = False,
    color: tuple[float, float, float] = (0.90, 0.94, 0.98),
    max_lines: int,
) -> int:
    chars = max(24, int(width / max(size * 0.52, 1)))
    for line in textwrap.wrap(str(text), width=chars)[:max_lines]:
        cmds.append(_pdf_text(line, x, y, size, bold=bold, color=color))
        y -= int(size * 1.45)
    return y


def _pdf_lines(snapshot: dict[str, Any]) -> list[str]:
    report = snapshot.get("report") or {}
    lines = [
        "Helios Report Snapshot",
        str(snapshot.get("title") or ""),
        f"Saved: {snapshot.get('created_at') or ''}",
        f"Target: {snapshot.get('target_kind') or ''}:{snapshot.get('target_id') or ''}",
        f"Source: {snapshot.get('source') or 'Unknown'}",
        f"Date Range: {snapshot.get('first_date') or 'Unknown'} to {snapshot.get('last_date') or 'Unknown'}",
        f"Row Count: {snapshot.get('row_count') or 0}",
        f"Source Counts: {snapshot.get('source_counts') or {}}",
        f"Data Mode: {snapshot.get('data_mode') or ''}",
        "Analysis only: no investment advice, no order execution, no return guarantee.",
    ]
    model_metadata = snapshot.get("model_metadata") or {}
    if model_metadata:
        lines.append(f"Model Metadata: {model_metadata}")
    warnings = snapshot.get("warnings") or []
    if warnings:
        lines.append("Caveats:")
        lines.extend(f"- {warning}" for warning in warnings[:8])
    if snapshot.get("ai_narrative"):
        lines.append("AI Narrative:")
        lines.extend(textwrap.wrap(str(snapshot["ai_narrative"]), width=92)[:8])
    sections = report.get("sections") if isinstance(report.get("sections"), dict) else {}
    summary = sections.get("executive_summary") if isinstance(sections.get("executive_summary"), dict) else {}
    if summary:
        lines.append("Executive Summary:")
        for value in summary.values():
            lines.extend(textwrap.wrap(str(value), width=92)[:4])
    return [part for line in lines for part in (textwrap.wrap(str(line), width=96) or [""])]


def _model_metadata(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _text(report.get("id")),
        "name": _text(report.get("name")),
        "mandate": _text(report.get("mandate")),
        "source": _text(report.get("source")),
    }


def _audit_trail(*, report: dict[str, Any], created_at: str, version_label: str, prepared_by: str) -> list[dict[str, str]]:
    report_timestamp = _text(report.get("timestamp")) or created_at
    return [
        {
            "event": "report_built",
            "at": report_timestamp,
            "actor": "Helios deterministic analytics engine",
            "summary": "Report facts were composed from current Helios analytics, provenance, and caveats.",
        },
        {
            "event": "snapshot_saved",
            "at": created_at,
            "actor": prepared_by or "Helios local workspace",
            "summary": f"Institutional report snapshot {version_label} was frozen for history, print, and PDF export.",
        },
    ]


def _disclosure_blocks(report: dict[str, Any]) -> list[dict[str, str]]:
    data_provenance = report.get("data_provenance") if isinstance(report.get("data_provenance"), dict) else {}
    data_mode = _text(report.get("data_mode") or data_provenance.get("data_mode"))
    return [
        {
            "title": "Analysis-Only Use",
            "body": DISCLAIMER,
        },
        {
            "title": "No Trade Execution",
            "body": "This report is an evidence summary. Helios does not route orders, rebalance accounts, or execute brokerage instructions.",
        },
        {
            "title": "No Return Guarantee",
            "body": "Forecasts, scores, strategy evidence, and AI narratives are estimates or explanations, not promises of return or risk reduction.",
        },
        {
            "title": "Data Provenance Required",
            "body": f"Real research depends on eligible live or uploaded histories. Current data mode: {data_mode or 'unknown'}.",
        },
    ]


def _audit_html(snapshot: dict[str, Any]) -> str:
    rows = snapshot.get("audit_trail") or []
    if not rows:
        return ""
    articles = []
    for row in rows:
        articles.append(
            "<article>"
            f"<h3>{_esc(_label(row.get('event')))}</h3>"
            f"<p class=\"muted\">{_esc(row.get('at'))} / {_esc(row.get('actor'))}</p>"
            f"<p>{_esc(row.get('summary'))}</p>"
            "</article>"
        )
    return "<section><h2>Audit Trail</h2><div class=\"audit\">" + "".join(articles) + "</div></section>"


def _disclosures_html(snapshot: dict[str, Any]) -> str:
    rows = snapshot.get("disclosure_blocks") or []
    if not rows:
        return ""
    cards = []
    for row in rows:
        cards.append(
            "<article class=\"disclosure\">"
            f"<h3>{_esc(row.get('title'))}</h3>"
            f"<p>{_esc(row.get('body'))}</p>"
            "</article>"
        )
    return "<section><h2>Disclosure Blocks</h2><div class=\"disclosures\">" + "".join(cards) + "</div></section>"


def _audit_rows(snapshot: dict[str, Any]) -> dict[str, str]:
    rows = snapshot.get("audit_trail") or []
    out = {}
    for index, row in enumerate(rows[:4], start=1):
        out[f"{index}. {_label(row.get('event'))}"] = f"{row.get('at') or ''} / {row.get('summary') or ''}"
    return out


def _disclosure_rows(snapshot: dict[str, Any]) -> dict[str, str]:
    rows = snapshot.get("disclosure_blocks") or []
    return {str(row.get("title") or f"Disclosure {index + 1}"): str(row.get("body") or "") for index, row in enumerate(rows[:4])}


def _model_metadata_html(snapshot: dict[str, Any]) -> str:
    metadata = snapshot.get("model_metadata") or {}
    if not metadata:
        return ""
    return "<section><h2>Model Metadata</h2>" + _render_dict(metadata) + "</section>"


def _warnings_html(snapshot: dict[str, Any]) -> str:
    warnings = snapshot.get("warnings") or []
    if not warnings:
        return ""
    items = "".join(f"<li class=\"warn\">{_esc(item)}</li>" for item in warnings)
    return f"<section><h2>Caveats</h2><ul>{items}</ul></section>"


def _ai_html(snapshot: dict[str, Any]) -> str:
    narrative = snapshot.get("ai_narrative")
    if not narrative:
        return ""
    return (
        "<section><h2>AI Narrative</h2>"
        "<p class=\"muted\">AI-generated narrative saved with this snapshot; calculations are from the Helios engine. Advisor Review Required.</p>"
        f"<pre>{_esc(narrative)}</pre></section>"
    )


def _render_value(value: Any) -> str:
    if isinstance(value, dict):
        return _render_dict(value)
    if isinstance(value, list):
        if not value:
            return "<span class=\"muted\">None</span>"
        return "<ul>" + "".join(f"<li>{_render_value(item)}</li>" for item in value) + "</ul>"
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    if value is None or value == "":
        return '<span class="muted">Unknown</span>'
    return _esc(value)


def _render_dict(value: dict[str, Any]) -> str:
    rows = []
    for key, item in value.items():
        rows.append(f"<dt>{_esc(_label(key))}</dt><dd>{_render_value(item)}</dd>")
    return "<dl>" + "".join(rows) + "</dl>"


def _label(value: Any) -> str:
    return str(value).replace("_", " ").replace("-", " ").title()


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _bullet_block(title: str, rows: Any) -> str:
    values = rows if isinstance(rows, list) else []
    if not values:
        return ""
    return f"{title}:\n" + "\n".join(f"- {row}" for row in values)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

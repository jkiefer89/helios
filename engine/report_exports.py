"""Saved report snapshots and export renderers.

Exports are intentionally built from deterministic Helios report payloads. The
client may provide an already-generated AI narrative, but this module never
calls an AI provider or recalculates analytics.
"""
from __future__ import annotations

import html
import secrets
import textwrap
from io import BytesIO
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
            "pdf_engine": "reportlab",
            "pdf_layout": "designer_grade_institutional",
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
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover - dependency is verified in CI.
        raise RuntimeError("ReportLab is required for institutional PDF exports.") from exc

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter, pageCompression=0)
    width, height = letter
    pages = [
        ("CLIENT-READY PDF PACKAGE", _rl_cover_page),
        ("EXECUTIVE SUMMARY", _rl_summary_page),
        ("PROVENANCE DASHBOARD", _rl_provenance_page),
        ("EVIDENCE DETAIL", _rl_evidence_page),
    ]
    total = len(pages)
    for page_no, (section, renderer) in enumerate(pages, start=1):
        _rl_background(pdf, width, height, colors)
        renderer(pdf, snapshot, width, height, page_no, total, section, colors)
        _rl_footer(pdf, snapshot, width, page_no, total, section, colors)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _rl_background(pdf, width: float, height: float, colors) -> None:
    pdf.setFillColor(colors.HexColor("#071019"))
    pdf.rect(0, 0, width, height, stroke=0, fill=1)
    pdf.setFillColor(colors.HexColor("#0d1622"))
    pdf.rect(0, height - 82, width, 82, stroke=0, fill=1)
    pdf.setStrokeColor(colors.HexColor("#263548"))
    pdf.setLineWidth(1)
    pdf.line(36, height - 82, width - 36, height - 82)


def _rl_header(pdf, width: float, height: float, section: str, colors) -> None:
    _rl_text(pdf, "HELIOS PRO", 42, height - 42, 18, colors.HexColor("#e6edf7"), bold=True)
    _rl_text(pdf, "Advisor-Grade Research Terminal", 42, height - 58, 8.5, colors.HexColor("#9fb2c8"), bold=True)
    _rl_text(pdf, section, width - 260, height - 42, 10, colors.HexColor("#55a7ff"), bold=True)


def _rl_cover_page(pdf, snapshot: dict[str, Any], width: float, height: float, page_no: int, total: int, section: str, colors) -> None:
    _rl_header(pdf, width, height, section, colors)
    _rl_text(pdf, "CLIENT-READY PDF PACKAGE", 42, 650, 10, colors.HexColor("#55a7ff"), bold=True)
    _rl_text(pdf, "INSTITUTIONAL ADVISOR REPORT", 42, 632, 10, colors.HexColor("#ffd24a"), bold=True)
    _rl_wrapped(pdf, str(snapshot.get("title") or "Helios Report Snapshot"), 42, 602, 520, 26, colors.HexColor("#f8fafc"), bold=True, max_lines=3)
    _rl_text(pdf, f"REPORT VERSION {snapshot.get('version_label') or 'v1'}", 42, 528, 12, colors.HexColor("#ffd24a"), bold=True)
    _rl_text(pdf, "Helios Report Snapshot", 42, 508, 10, colors.HexColor("#9fb2c8"))

    cards = [
        ("Prepared For", snapshot.get("prepared_for") or "Advisor review"),
        ("Prepared By", snapshot.get("prepared_by") or "Helios local workspace"),
        ("Reviewer", snapshot.get("reviewer") or "Advisor review required"),
        ("Source", snapshot.get("source") or "Unknown"),
        ("Input Range", f"{snapshot.get('first_date') or 'Unknown'} to {snapshot.get('last_date') or 'Unknown'}"),
        ("Rows", snapshot.get("row_count") or 0),
    ]
    _rl_card_grid(pdf, cards, 42, 438, 252, 62, colors)
    _rl_panel(
        pdf,
        42,
        120,
        width - 84,
        92,
        "ADVISOR REVIEW REQUIRED",
        "Analysis only. Helios provides evidence summaries and does not provide investment advice, order execution, brokerage services, or return guarantees.",
        colors,
        accent="#ffd24a",
    )


def _rl_summary_page(pdf, snapshot: dict[str, Any], width: float, height: float, page_no: int, total: int, section: str, colors) -> None:
    _rl_header(pdf, width, height, section, colors)
    report = snapshot.get("report") if isinstance(snapshot.get("report"), dict) else {}
    sections = report.get("sections") if isinstance(report.get("sections"), dict) else {}
    summary = sections.get("executive_summary") if isinstance(sections.get("executive_summary"), dict) else {}
    action = sections.get("action") if isinstance(sections.get("action"), dict) else {}
    evidence = sections.get("evidence") if isinstance(sections.get("evidence"), dict) else {}
    risk = sections.get("risk") if isinstance(sections.get("risk"), dict) else {}
    forecast = sections.get("forecast") if isinstance(sections.get("forecast"), dict) else {}

    _rl_text(pdf, "EXECUTIVE SUMMARY", 42, 674, 18, colors.HexColor("#f8fafc"), bold=True)
    y = _rl_wrapped(
        pdf,
        str(summary.get("summary") or summary.get("headline") or "Report summary is generated from deterministic Helios sections."),
        42,
        646,
        522,
        10.5,
        colors.HexColor("#c8d3df"),
        max_lines=7,
    )
    y -= 18
    cards = [
        ("Action", action.get("action") or "Review"),
        ("Conviction", _fmt_pdf_value(action.get("conviction_pct"), suffix="%")),
        ("Opportunity", _fmt_pdf_value(evidence.get("opportunity_score"))),
        ("Risk", _fmt_pdf_value(evidence.get("risk_score") or risk.get("risk_score"))),
        ("Expected Return", _fmt_pdf_value(forecast.get("expected_return_pct"), suffix="%")),
        ("Expected Vol", _fmt_pdf_value(forecast.get("expected_vol_pct"), suffix="%")),
    ]
    _rl_card_grid(pdf, cards, 42, min(y - 34, 500), 252, 58, colors)
    _rl_panel(
        pdf,
        42,
        118,
        width - 84,
        100,
        "REPORT GOVERNANCE",
        "This page is a frozen advisor/client-ready summary. Calculations remain deterministic Helios outputs; any AI narrative is explanatory only and requires advisor review.",
        colors,
        accent="#55a7ff",
    )


def _rl_provenance_page(pdf, snapshot: dict[str, Any], width: float, height: float, page_no: int, total: int, section: str, colors) -> None:
    _rl_header(pdf, width, height, section, colors)
    _rl_text(pdf, "PROVENANCE DASHBOARD", 42, 674, 18, colors.HexColor("#f8fafc"), bold=True)
    _rl_text(pdf, "SOURCE AND PROVENANCE", 42, 648, 10, colors.HexColor("#ffd24a"), bold=True)
    cards = [
        ("Source", snapshot.get("source") or "Unknown"),
        ("Source Counts", snapshot.get("source_counts") or {}),
        ("First Date", snapshot.get("first_date") or "Unknown"),
        ("Last Date", snapshot.get("last_date") or "Unknown"),
        ("Row Count", snapshot.get("row_count") or 0),
        ("Research Eligible", "Yes" if snapshot.get("eligible_for_real_research") else "No"),
    ]
    _rl_card_grid(pdf, cards, 42, 564, 252, 58, colors)

    audit = snapshot.get("audit_trail") if isinstance(snapshot.get("audit_trail"), list) else []
    y = 330
    _rl_text(pdf, "AUDIT TRAIL", 42, y, 14, colors.HexColor("#f8fafc"), bold=True)
    y -= 24
    for row in audit[:4]:
        text = f"{_label(row.get('event'))}: {row.get('at') or ''} - {row.get('summary') or ''}"
        y = _rl_wrapped(pdf, text, 58, y, 490, 9, colors.HexColor("#c8d3df"), max_lines=3)
        y -= 10
    metadata = snapshot.get("model_metadata") if isinstance(snapshot.get("model_metadata"), dict) else {}
    if metadata:
        _rl_panel(pdf, 42, 118, width - 84, 88, "MODEL METADATA", str(metadata), colors, accent="#55a7ff")


def _rl_evidence_page(pdf, snapshot: dict[str, Any], width: float, height: float, page_no: int, total: int, section: str, colors) -> None:
    _rl_header(pdf, width, height, section, colors)
    _rl_text(pdf, "EVIDENCE DETAIL", 42, 674, 18, colors.HexColor("#f8fafc"), bold=True)
    warnings = snapshot.get("warnings") if isinstance(snapshot.get("warnings"), list) else []
    y = 640
    if warnings:
        _rl_text(pdf, "CAVEATS", 42, y, 12, colors.HexColor("#ffd24a"), bold=True)
        y -= 20
        for warning in warnings[:5]:
            y = _rl_wrapped(pdf, f"- {warning}", 58, y, 490, 8.8, colors.HexColor("#c8d3df"), max_lines=2)
            y -= 7
    else:
        y = _rl_wrapped(pdf, "No additional caveats beyond the required disclosure blocks.", 42, y, 500, 9, colors.HexColor("#c8d3df"), max_lines=2) - 12
    if snapshot.get("ai_narrative"):
        _rl_text(pdf, "AI NARRATIVE", 42, y, 12, colors.HexColor("#ffd24a"), bold=True)
        y = _rl_wrapped(pdf, str(snapshot.get("ai_narrative")), 58, y - 20, 490, 8.5, colors.HexColor("#c8d3df"), max_lines=8) - 12
    _rl_text(pdf, "DISCLOSURE BLOCKS", 42, max(y, 310), 14, colors.HexColor("#f8fafc"), bold=True)
    y = max(y, 310) - 26
    for block in (snapshot.get("disclosure_blocks") or [])[:4]:
        _rl_panel(pdf, 42, y - 78, width - 84, 72, str(block.get("title") or "Disclosure"), str(block.get("body") or ""), colors, accent="#ffd24a")
        y -= 86


def _rl_footer(pdf, snapshot: dict[str, Any], width: float, page_no: int, total: int, section: str, colors) -> None:
    pdf.setStrokeColor(colors.HexColor("#263548"))
    pdf.line(42, 66, width - 42, 66)
    _rl_text(pdf, "Analysis only - not investment advice, order execution, or a return guarantee.", 42, 46, 7.5, colors.HexColor("#9fb2c8"))
    _rl_text(pdf, f"Page {page_no} of {total}", width - 104, 46, 8, colors.HexColor("#c8d3df"), bold=True)
    _rl_text(pdf, str(snapshot.get("version_label") or "v1"), width - 104, 34, 7, colors.HexColor("#9fb2c8"))


def _rl_card_grid(pdf, cards: list[tuple[str, Any]], x: float, y: float, card_width: float, card_height: float, colors) -> None:
    for index, (label, value) in enumerate(cards):
        col = index % 2
        row = index // 2
        _rl_card(pdf, x + col * (card_width + 24), y - row * (card_height + 20), card_width, card_height, str(label), str(value), colors)


def _rl_card(pdf, x: float, y: float, width: float, height: float, label: str, value: str, colors) -> None:
    pdf.setFillColor(colors.HexColor("#0d1622"))
    pdf.setStrokeColor(colors.HexColor("#263548"))
    pdf.roundRect(x, y, width, height, 8, stroke=1, fill=1)
    _rl_text(pdf, label.upper(), x + 14, y + height - 20, 7.5, colors.HexColor("#9fb2c8"), bold=True)
    _rl_wrapped(pdf, value, x + 14, y + height - 38, width - 28, 10.5, colors.HexColor("#f8fafc"), bold=True, max_lines=2)


def _rl_panel(pdf, x: float, y: float, width: float, height: float, title: str, body: str, colors, *, accent: str) -> None:
    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.setStrokeColor(colors.HexColor("#263548"))
    pdf.roundRect(x, y, width, height, 8, stroke=1, fill=1)
    pdf.setFillColor(colors.HexColor(accent))
    pdf.rect(x, y, 4, height, stroke=0, fill=1)
    _rl_text(pdf, title, x + 16, y + height - 22, 10.5, colors.HexColor(accent), bold=True)
    max_lines = max(1, int((height - 42) / 12))
    _rl_wrapped(pdf, body, x + 16, y + height - 42, width - 32, 8.8, colors.HexColor("#c8d3df"), max_lines=max_lines)


def _rl_text(pdf, text: Any, x: float, y: float, size: float, color, *, bold: bool = False) -> None:
    pdf.setFillColor(color)
    pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    pdf.drawString(x, y, _pdf_clean(text))


def _rl_wrapped(pdf, text: Any, x: float, y: float, width: float, size: float, color, *, bold: bool = False, max_lines: int) -> float:
    chars = max(24, int(width / max(size * 0.50, 1)))
    lines = textwrap.wrap(_pdf_clean(text), width=chars)[:max_lines]
    if not lines:
        lines = [""]
    leading = size * 1.38
    for line in lines:
        _rl_text(pdf, line, x, y, size, color, bold=bold)
        y -= leading
    return y


def _fmt_pdf_value(value: Any, *, suffix: str = "") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "Research locked" if value is None else str(value)
    return f"{number:.1f}{suffix}"


def _pdf_clean(value: Any) -> str:
    return str(value or "").replace("\u2014", "-").replace("\u2013", "-").replace("\u2011", "-")


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

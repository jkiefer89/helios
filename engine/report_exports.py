"""Saved report snapshots and export renderers.

Exports are intentionally built from deterministic Helios report payloads. The
client may provide an already-generated AI narrative, but this module never
calls an AI provider or recalculates analytics.
"""
from __future__ import annotations

import html
import secrets
from io import BytesIO
from datetime import datetime, timezone
from typing import Any

from .pdf_layout import (
    card_grid as _rl_card_grid,
    draw_text as _rl_text,
    draw_wrapped as _rl_wrapped,
    fmt_optional_pct as _fmt_optional_pdf_pct,
    fmt_value as _fmt_pdf_value,
    header as _rl_header,
    page_background as _rl_background,
    panel as _rl_panel,
)
from . import costs, pdf_layout
from .reporting import DISCLAIMER


def build_snapshot(
    *,
    target_kind: str,
    target_id: str,
    report: dict[str, Any],
    signal_journal_evidence: dict[str, Any] | None = None,
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
    external_ready = bool(
        report.get("eligible_for_real_research")
        and report_purpose in {"client_review", "investment_committee"}
    )
    report_package = "institutional_advisor_report" if external_ready else "internal_research_evidence"
    output_formats = ["html", "pdf", "print"]
    disclosure_blocks = _disclosure_blocks(report)
    signal_journal_payload = _dict(signal_journal_evidence)
    client_risk_pack = _dict((sections.get("client_risk_pack") if isinstance(sections, dict) else {}) or {})
    capital_context = _dict((sections.get("capital_context") if isinstance(sections, dict) else {}) or {})
    audit_trail = _audit_trail(
        report=report,
        created_at=created_at,
        version_label=version_label,
        prepared_by=prepared_by,
    )
    snapshot = {
        "id": secrets.token_urlsafe(12),
        "created_at": created_at,
        "report_package": report_package,
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
        "signal_journal": signal_journal_payload,
        "client_risk_pack": client_risk_pack,
        "capital_context": capital_context,
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
            "institutional_report": external_ready,
            "report_package": report_package,
            "version": safe_version,
            "version_label": version_label,
            "pdf_engine": "reportlab",
            "pdf_layout": "designer_grade_institutional" if external_ready else "internal_evidence",
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
            "signal_journal": signal_journal_payload,
            "client_risk_pack": client_risk_pack,
            "capital_context": capital_context,
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
    capital = snapshot.get("capital_context") if isinstance(snapshot.get("capital_context"), dict) else {}
    if snapshot.get("target_kind") == "model":
        rows.update({
            "AUM As Of": capital.get("aum_as_of") or "Not supplied",
            "AUM USD": capital.get("aum_usd") or "Not supplied",
            "Capacity Status": capital.get("capacity_status") or "Unavailable",
        })
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
        "table{width:100%;border-collapse:collapse;margin:12px 0 0}th,td{border-bottom:1px solid #263548;padding:8px 10px;text-align:left;vertical-align:top}th{color:#9fb2c8;text-transform:uppercase;font-size:12px}",
        "pre{white-space:pre-wrap;font-family:inherit}.footer{font-size:13px;color:#9fb2c8}@media print{body{background:#fff;color:#111827;padding:0}main{border:0}section{break-inside:avoid}.cover{background:#f8fafc;color:#0f172a}.disclosures{grid-template-columns:1fr}}",
        "</style>",
        "</head>",
        "<body>",
        "<main>",
        "<header class=\"cover\">",
        f"<p class=\"eyebrow\">{_esc(_package_label(snapshot))} / Helios Report Snapshot</p>",
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
        _client_risk_pack_html(snapshot),
        _signal_journal_html(snapshot),
        _ai_html(snapshot),
        "<section>",
        "<h2>Report Sections</h2>",
        _render_value(_report_sections_for_render(sections)),
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
    risk_section = "CLIENT-GRADE RISK PACK" if _is_external_package(snapshot) else "RISK EVIDENCE"
    pages = [
        (_package_label(snapshot).upper(), _rl_cover_page),
        ("EXECUTIVE SUMMARY", _rl_summary_page),
        ("PROVENANCE DASHBOARD", _rl_provenance_page),
        (risk_section, _rl_risk_pack_page),
        ("SIGNAL JOURNAL EVIDENCE", _rl_signal_journal_page),
        ("EVIDENCE DETAIL", _rl_evidence_page),
    ]
    if _evidence_layout(snapshot)["overflow_blocks"]:
        pages.append(("EVIDENCE DETAIL", _rl_evidence_overflow_page))
    total = len(pages)
    for page_no, (section, renderer) in enumerate(pages, start=1):
        _rl_background(pdf, width, height, colors)
        renderer(pdf, snapshot, width, height, page_no, total, section, colors)
        _rl_footer(pdf, snapshot, width, page_no, total, section, colors)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _rl_cover_page(pdf, snapshot: dict[str, Any], width: float, height: float, page_no: int, total: int, section: str, colors) -> None:
    _rl_header(pdf, width, height, section, colors)
    _rl_text(pdf, _package_label(snapshot).upper(), 42, 650, 10, colors.HexColor("#55a7ff"), bold=True)
    _rl_text(
        pdf,
        "APPROVED EXTERNAL REVIEW PACK" if _is_external_package(snapshot) else "INTERNAL ANALYSIS-ONLY EVIDENCE",
        42,
        632,
        10,
        colors.HexColor("#ffd24a"),
        bold=True,
    )
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
    missing_score = _missing_score_label(report)
    risk_score = evidence.get("risk_score")
    if risk_score is None:
        risk_score = risk.get("risk_score")
    cards = [
        ("Action", action.get("action") or "Review"),
        ("Conviction", _fmt_pdf_value(action.get("conviction_pct"), suffix="%")),
        ("Opportunity", _fmt_pdf_value(evidence.get("opportunity_score"), none_label=missing_score)),
        ("Risk", _fmt_pdf_value(risk_score, none_label=missing_score)),
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
        (
            "This page is an approved external-review summary. Calculations remain deterministic Helios outputs; any AI narrative is explanatory only and requires advisor review."
            if _is_external_package(snapshot)
            else "This page is an internal analysis-only evidence draft and is not approved for client or investment-committee distribution."
        ),
        colors,
        accent="#55a7ff",
    )


def _is_external_package(snapshot: dict[str, Any]) -> bool:
    return snapshot.get("report_package") == "institutional_advisor_report"


def _package_label(snapshot: dict[str, Any]) -> str:
    return "Institutional Advisor Report" if _is_external_package(snapshot) else "Internal Research Evidence Draft"


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

    capital = snapshot.get("capital_context") if isinstance(snapshot.get("capital_context"), dict) else {}
    if snapshot.get("target_kind") == "model":
        _rl_text(
            pdf,
            (
                f"AUM {capital.get('aum_usd') or 'not supplied'} as of "
                f"{capital.get('aum_as_of') or 'not supplied'} | "
                f"Capacity {_label(capital.get('capacity_status') or 'unavailable')}"
            ),
            42,
            356,
            9,
            colors.HexColor("#9fb2c8"),
        )

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


def _rl_signal_journal_page(pdf, snapshot: dict[str, Any], width: float, height: float, page_no: int, total: int, section: str, colors) -> None:
    _rl_header(pdf, width, height, section, colors)
    _rl_text(pdf, "SIGNAL JOURNAL EVIDENCE", 42, 674, 18, colors.HexColor("#f8fafc"), bold=True)
    _rl_wrapped(
        pdf,
        "Paper-performance evidence record only. Forward results are pending until later persisted market data covers the signal horizon.",
        42,
        648,
        520,
        9.5,
        colors.HexColor("#c8d3df"),
        max_lines=2,
    )
    journal = snapshot.get("signal_journal") if isinstance(snapshot.get("signal_journal"), dict) else {}
    summary = journal.get("summary") if isinstance(journal.get("summary"), dict) else {}
    history = journal.get("target_history") if isinstance(journal.get("target_history"), list) else []
    credibility = journal.get("model_credibility") if isinstance(journal.get("model_credibility"), list) else []
    cards = [
        ("Signal Count", summary.get("total_count") or 0),
        ("Forward Results", f"{summary.get('measured_count') or 0} measured / {summary.get('pending_count') or 0} pending"),
        ("Hit Rate", _fmt_optional_pdf_pct(summary.get("hit_rate_pct"))),
        ("Alpha Vs Benchmark (Gross)", _fmt_optional_pdf_pct(summary.get("avg_alpha_pct"))),
        ("Alpha Net Of Default Costs", _fmt_optional_pdf_pct(
            summary.get("avg_alpha_after_default_costs_pct")
            if summary.get("avg_alpha_after_default_costs_pct") is not None
            else costs.net_of_default_costs(summary.get("avg_alpha_pct")))),
    ]
    _rl_card_grid(pdf, cards, 42, 568, 252, 58, colors)

    y = 392
    _rl_text(pdf, "FORWARD RESULTS", 42, y, 12, colors.HexColor("#ffd24a"), bold=True)
    y -= 22
    if history:
        for row in history[:6]:
            text = (
                f"{row.get('input_end_date') or ''} / {row.get('target_name') or row.get('target_id') or ''} / "
                f"{row.get('action_label') or 'Review'} / score {row.get('score') or 0} / "
                f"{row.get('forward_status') or 'pending'} / forward {_fmt_optional_pdf_pct(row.get('forward_result_pct'))} / "
                f"alpha vs benchmark (gross) {_fmt_optional_pdf_pct(row.get('alpha_pct'))}"
            )
            y = _rl_wrapped(pdf, text, 58, y, 490, 8.4, colors.HexColor("#c8d3df"), max_lines=2)
            y -= 7
    else:
        _rl_panel(
            pdf,
            42,
            y - 76,
            width - 84,
            68,
            "NO SIGNAL JOURNAL ENTRIES",
            "No deterministic Helios signal has been logged for this report target yet.",
            colors,
            accent="#ffd24a",
        )
        y -= 96

    y = min(y, 206)
    _rl_text(pdf, "MODEL-BY-MODEL CREDIBILITY", 42, y, 12, colors.HexColor("#ffd24a"), bold=True)
    y -= 22
    if credibility:
        for row in credibility[:3]:
            text = (
                f"{row.get('target_name') or row.get('target_id') or ''}: "
                f"{row.get('signal_count') or 0} signals, {row.get('measured_count') or 0} measured, "
                f"hit rate {_fmt_optional_pdf_pct(row.get('hit_rate_pct'))}, "
                f"alpha vs benchmark (gross) {_fmt_optional_pdf_pct(row.get('avg_alpha_pct'))}, "
                f"latest {row.get('latest_action_label') or 'Review'} vs {row.get('benchmark') or 'benchmark'}."
            )
            y = _rl_wrapped(pdf, text, 58, y, 490, 8.4, colors.HexColor("#c8d3df"), max_lines=2)
            y -= 8
    else:
        _rl_wrapped(
            pdf,
            "No model-level journal evidence is available for this report target.",
            58,
            y,
            490,
            8.4,
            colors.HexColor("#c8d3df"),
            max_lines=2,
        )


def _rl_risk_pack_page(pdf, snapshot: dict[str, Any], width: float, height: float, page_no: int, total: int, section: str, colors) -> None:
    _rl_header(pdf, width, height, section, colors)
    external = _is_external_package(snapshot)
    heading = "CLIENT-GRADE RISK PACK" if external else "RISK EVIDENCE"
    _rl_text(pdf, heading, 42, 674, 18, colors.HexColor("#f8fafc"), bold=True)
    _rl_wrapped(
        pdf,
        (
            "Stress scenarios, benchmark-relative drawdown, concentration warnings, liquidity flags, correlation clusters, and breakpoints are analysis-only evidence for advisor review."
            if external
            else "Internal analysis-only risk evidence. This draft is not approved for client or investment-committee distribution."
        ),
        42,
        648,
        520,
        9.5,
        colors.HexColor("#c8d3df"),
        max_lines=3,
    )
    pack = _risk_pack(snapshot)
    if not pack or not pack.get("available"):
        _rl_panel(
            pdf,
            42,
            540,
            width - 84,
            78,
            "RISK PACK LOCKED",
            str((pack or {}).get("required_action") or "Eligible real model history is required before rendering risk evidence."),
            colors,
            accent="#ffd24a",
        )
        return
    summary = pack.get("summary") if isinstance(pack.get("summary"), dict) else {}
    drawdown = pack.get("benchmark_relative_drawdown") if isinstance(pack.get("benchmark_relative_drawdown"), dict) else {}
    cards = [
        ("Risk Posture", summary.get("risk_posture") or "review"),
        ("Benchmark", summary.get("benchmark_symbol") or drawdown.get("benchmark_symbol") or "benchmark"),
        ("Relative Drawdown", _fmt_optional_pdf_pct(drawdown.get("relative_drawdown_pct"))),
        # This page only renders for eligible real models, so a missing beta
        # means insufficient benchmark overlap, not locked research.
        ("Beta", _fmt_pdf_value(drawdown.get("beta"), none_label="Pending")),
    ]
    _rl_card_grid(pdf, cards, 42, 566, 252, 58, colors)

    y = 382
    _rl_text(pdf, "STRESS SCENARIOS", 42, y, 12, colors.HexColor("#ffd24a"), bold=True)
    y -= 22
    for row in (pack.get("stress_scenarios") or [])[:3]:
        text = f"{row.get('scenario')}: {row.get('portfolio_impact_pct')}% / {row.get('severity')} / {row.get('what_it_tests')}"
        y = _rl_wrapped(pdf, text, 58, y, 490, 8.5, colors.HexColor("#c8d3df"), max_lines=2)
        y -= 7
    y = min(y, 288)
    _rl_text(pdf, "HISTORICAL STRESS REPLAY", 42, y, 12, colors.HexColor("#ffd24a"), bold=True)
    y -= 22
    for row in (pack.get("historical_stress_replay") or [])[:3]:
        text = f"{row.get('scenario')}: {row.get('portfolio_impact_pct')}% / {row.get('start_date')} to {row.get('end_date')} / Observed Model History"
        y = _rl_wrapped(pdf, text, 58, y, 490, 8.5, colors.HexColor("#c8d3df"), max_lines=2)
        y -= 7
    y = min(y, 230)
    _rl_text(pdf, "WHAT WOULD BREAK THIS MODEL", 42, y, 12, colors.HexColor("#ffd24a"), bold=True)
    y -= 22
    for row in (pack.get("what_would_break_this_model") or [])[:5]:
        text = f"{row.get('driver')}: {row.get('trigger')} / {row.get('evidence')} / {row.get('language')}"
        y = _rl_wrapped(pdf, text, 58, y, 490, 8.3, colors.HexColor("#c8d3df"), max_lines=2)
        y -= 7


# Evidence-page disclosure layout: header drop, then per-panel cursor offsets.
_EVIDENCE_TOP_Y = 640.0
_DISCLOSURE_HEADER_DROP = 26.0
_DISCLOSURE_PANEL_HEIGHT = 72.0
_DISCLOSURE_PANEL_OFFSET = 78.0  # panel bottom edge sits this far below the cursor
_DISCLOSURE_STEP = 86.0


def _evidence_layout(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Deterministic evidence-page layout plan.

    Mirrors the drawing arithmetic in _rl_evidence_page so render_pdf can
    decide upfront how many disclosure blocks fit above the footer margin
    (pdf_layout.CONTENT_BOTTOM) and paginate the rest instead of drawing
    over the footer or off the page.
    """
    y = _EVIDENCE_TOP_Y
    warnings = snapshot.get("warnings") if isinstance(snapshot.get("warnings"), list) else []
    if warnings:
        y -= 20
        for warning in warnings[:5]:
            y -= pdf_layout.wrapped_drop(f"- {warning}", 490, 8.8, max_lines=2) + 7
    else:
        y -= pdf_layout.wrapped_drop("No additional caveats beyond the required disclosure blocks.", 500, 9, max_lines=2) + 12
    if snapshot.get("ai_narrative"):
        y -= 20 + pdf_layout.wrapped_drop(str(snapshot.get("ai_narrative")), 490, 8.5, max_lines=8) + 12
    blocks = [block for block in (snapshot.get("disclosure_blocks") or [])[:4] if isinstance(block, dict)]
    fit = 0
    for index in range(len(blocks)):
        bottom = y - _DISCLOSURE_HEADER_DROP - _DISCLOSURE_STEP * index - _DISCLOSURE_PANEL_OFFSET
        if bottom < pdf_layout.CONTENT_BOTTOM:
            break
        fit = index + 1
    return {
        "disclosure_header_y": y,
        "first_page_blocks": blocks[:fit],
        "overflow_blocks": blocks[fit:],
    }


def _rl_disclosure_panels(pdf, blocks: list[dict[str, Any]], y: float, width: float, colors) -> None:
    for block in blocks:
        _rl_panel(
            pdf,
            42,
            y - _DISCLOSURE_PANEL_OFFSET,
            width - 84,
            _DISCLOSURE_PANEL_HEIGHT,
            str(block.get("title") or "Disclosure"),
            str(block.get("body") or ""),
            colors,
            accent="#ffd24a",
        )
        y -= _DISCLOSURE_STEP


def _rl_evidence_page(pdf, snapshot: dict[str, Any], width: float, height: float, page_no: int, total: int, section: str, colors) -> None:
    _rl_header(pdf, width, height, section, colors)
    _rl_text(pdf, "EVIDENCE DETAIL", 42, 674, 18, colors.HexColor("#f8fafc"), bold=True)
    warnings = snapshot.get("warnings") if isinstance(snapshot.get("warnings"), list) else []
    y = _EVIDENCE_TOP_Y
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
    layout = _evidence_layout(snapshot)
    blocks = layout["first_page_blocks"]
    if blocks or not layout["overflow_blocks"]:
        _rl_text(pdf, "DISCLOSURE BLOCKS", 42, y, 14, colors.HexColor("#f8fafc"), bold=True)
        _rl_disclosure_panels(pdf, blocks, y - _DISCLOSURE_HEADER_DROP, width, colors)


def _rl_evidence_overflow_page(pdf, snapshot: dict[str, Any], width: float, height: float, page_no: int, total: int, section: str, colors) -> None:
    _rl_header(pdf, width, height, section, colors)
    layout = _evidence_layout(snapshot)
    title = "DISCLOSURE BLOCKS (CONTINUED)" if layout["first_page_blocks"] else "DISCLOSURE BLOCKS"
    _rl_text(pdf, title, 42, 674, 14, colors.HexColor("#f8fafc"), bold=True)
    _rl_disclosure_panels(pdf, layout["overflow_blocks"], 674 - _DISCLOSURE_HEADER_DROP, width, colors)


def _rl_footer(pdf, snapshot: dict[str, Any], width: float, page_no: int, total: int, section: str, colors) -> None:
    pdf_layout.footer(pdf, width, page_no, total, colors, version_label=str(snapshot.get("version_label") or "v1"))


def _missing_score_label(report: dict[str, Any]) -> str:
    """Honest placeholder for a score the report simply does not carry.

    'Research locked' is reserved for genuinely blocked/ineligible data modes;
    real eligible model reports are not scored on the instrument scale.
    """
    data_provenance = report.get("data_provenance") if isinstance(report.get("data_provenance"), dict) else {}
    eligible = bool(report.get("eligible_for_real_research") or data_provenance.get("eligible_for_real_research"))
    if not eligible:
        return "Research locked"
    return "Not scored for models" if _text(report.get("kind")) == "model" else "Not scored"


def _fmt_html_pct(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "Pending"
    return f"{number:.2f}%"


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


def _client_risk_pack_html(snapshot: dict[str, Any]) -> str:
    pack = _risk_pack(snapshot)
    if not pack:
        return ""
    external = _is_external_package(snapshot)
    heading = "Client Risk Pack" if external else "Risk Evidence"
    if not pack.get("available"):
        return (
            f"<section><h2>{heading}</h2>"
            f"<p class=\"warn\">{_esc(pack.get('required_action') or 'Eligible real model history is required before rendering risk-pack evidence.')}</p>"
            "</section>"
        )
    summary = pack.get("summary") if isinstance(pack.get("summary"), dict) else {}
    drawdown = pack.get("benchmark_relative_drawdown") if isinstance(pack.get("benchmark_relative_drawdown"), dict) else {}
    liquidity = pack.get("liquidity_flags") if isinstance(pack.get("liquidity_flags"), dict) else {}
    html_parts = [
        "<section>",
        f"<h2>{heading}</h2>",
        (
            "<p class=\"muted\">Stress scenarios, benchmark-relative drawdown, concentration warnings, liquidity flags, correlation clusters, and breakpoints are analysis-only evidence for advisor review.</p>"
            if external
            else "<p class=\"muted\">Internal analysis-only risk evidence. This draft is not approved for client or investment-committee distribution.</p>"
        ),
        _render_dict({
            "Risk Posture": summary.get("risk_posture"),
            "Benchmark Relative Drawdown": _fmt_html_pct(drawdown.get("relative_drawdown_pct")),
            "Benchmark": drawdown.get("benchmark_symbol"),
            "Beta": drawdown.get("beta"),
            "Tracking Error": _fmt_html_pct(drawdown.get("tracking_error_pct")),
        }),
        "<h3>Stress Scenarios</h3>",
        _risk_pack_table(pack.get("stress_scenarios") or [], ("scenario", "portfolio_impact_pct", "severity", "what_it_tests")),
        "<h3>Historical Stress Replay</h3>",
        _risk_pack_table(pack.get("historical_stress_replay") or [], ("scenario", "portfolio_impact_pct", "window_days", "basis", "start_date", "end_date")),
        "<h3>Concentration Warnings</h3>",
        _risk_pack_table(pack.get("concentration_warnings") or [], ("title", "severity", "detail")),
        "<h3>Liquidity Watchlist</h3>",
        _risk_pack_table(liquidity.get("items") or [], ("ticker", "weight_pct", "flag", "observed_adv_usd", "adv_source", "language")),
        "<h3>Correlation Clusters</h3>",
        _risk_pack_table(pack.get("correlation_clusters") or [], ("name", "type", "language")),
        "<h3>What Would Break This Model</h3>",
        _risk_pack_table(pack.get("what_would_break_this_model") or [], ("driver", "severity", "trigger", "evidence", "language")),
        "</section>",
    ]
    return "".join(html_parts)


def _risk_pack_table(rows: list[Any], columns: tuple[str, ...]) -> str:
    if not rows:
        return '<p class="muted">No rows available.</p>'
    body = []
    for row in rows[:12]:
        if not isinstance(row, dict):
            continue
        body.append("<tr>" + "".join(f"<td>{_esc(_format_risk_cell(row.get(column)))}</td>" for column in columns) + "</tr>")
    if not body:
        return '<p class="muted">No rows available.</p>'
    return (
        "<table><thead><tr>"
        + "".join(f"<th>{_esc(_label(column))}</th>" for column in columns)
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _format_risk_cell(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, list):
        return ", ".join(_format_risk_cell(item) for item in value[:4])
    if isinstance(value, dict):
        return ", ".join(f"{_label(key)}: {_format_risk_cell(item)}" for key, item in list(value.items())[:4])
    if isinstance(value, str) and "_" in value:
        return _label(value)
    if value is None or value is False or value == "":
        return ""
    return str(value)


def _risk_pack(snapshot: dict[str, Any]) -> dict[str, Any]:
    public_pack = snapshot.get("client_risk_pack")
    if isinstance(public_pack, dict) and public_pack:
        return public_pack
    report = snapshot.get("report") if isinstance(snapshot.get("report"), dict) else {}
    sections = report.get("sections") if isinstance(report.get("sections"), dict) else {}
    pack = sections.get("client_risk_pack")
    return pack if isinstance(pack, dict) else {}


def _report_sections_for_render(sections: dict[str, Any]) -> dict[str, Any]:
    """Avoid duplicating sections that have governed, purpose-aware renderers."""
    return {
        key: value for key, value in sections.items()
        if key not in {"client_risk_pack"}
    }


def _signal_journal_html(snapshot: dict[str, Any]) -> str:
    journal = snapshot.get("signal_journal") if isinstance(snapshot.get("signal_journal"), dict) else {}
    summary = journal.get("summary") if isinstance(journal.get("summary"), dict) else {}
    history = journal.get("target_history") if isinstance(journal.get("target_history"), list) else []
    benchmark_rows = journal.get("benchmark_comparison") if isinstance(journal.get("benchmark_comparison"), list) else []
    credibility = journal.get("model_credibility") if isinstance(journal.get("model_credibility"), list) else []
    summary_rows = {
        "Signal Count": summary.get("total_count") or 0,
        "Measured Forward Results": summary.get("measured_count") or 0,
        "Pending Forward Results": summary.get("pending_count") or 0,
        "Hit Rate": _fmt_html_pct(summary.get("hit_rate_pct")),
        "Alpha Vs Benchmark (Gross)": _fmt_html_pct(summary.get("avg_alpha_pct")),
        "Alpha Net Of Default Costs": _fmt_html_pct(
            summary.get("avg_alpha_after_default_costs_pct")
            if summary.get("avg_alpha_after_default_costs_pct") is not None
            else costs.net_of_default_costs(summary.get("avg_alpha_pct"))),
    }
    html_parts = [
        "<section>",
        "<h2>Signal Journal Evidence</h2>",
        "<p class=\"muted\">Paper performance tracking only. Forward results remain pending until later persisted market data covers the original signal horizon.</p>",
        _render_dict(summary_rows),
        "<h3>Forward Results</h3>",
        _signal_history_table(history),
        "<h3>Benchmark Comparison</h3>",
        _benchmark_table(benchmark_rows),
        "<h3>Model-By-Model Credibility</h3>",
        _model_credibility_table(credibility),
        "</section>",
    ]
    return "".join(html_parts)


def _signal_history_table(rows: list[Any]) -> str:
    if not rows:
        return '<p class="muted">No signal journal entries are available for this report target.</p>'
    body = []
    for row in rows[:12]:
        if not isinstance(row, dict):
            continue
        body.append(
            "<tr>"
            f"<td>{_esc(row.get('input_end_date'))}</td>"
            f"<td>{_esc(row.get('target_name') or row.get('target_id'))}<br><span class=\"muted\">{_esc(row.get('target_kind'))}:{_esc(row.get('target_id'))}</span></td>"
            f"<td>{_esc(row.get('action_label'))}<br><span class=\"muted\">Score {_esc(row.get('score'))}</span></td>"
            f"<td>{_esc(row.get('benchmark') or 'Benchmark')}</td>"
            f"<td>{_esc(row.get('forward_status') or 'pending')}<br><span class=\"muted\">{_esc(_fmt_html_pct(row.get('forward_result_pct')))}</span></td>"
            f"<td>{_esc(_fmt_html_pct(row.get('alpha_pct')))}</td>"
            "</tr>"
        )
    if not body:
        return '<p class="muted">No signal journal entries are available for this report target.</p>'
    return (
        "<table><thead><tr><th>Signal Date</th><th>Target</th><th>Action</th><th>Benchmark</th>"
        "<th>Forward Results</th><th>Alpha Vs Benchmark (Gross)</th></tr></thead><tbody>"
        + "".join(body) + "</tbody></table>"
    )


def _benchmark_table(rows: list[Any]) -> str:
    if not rows:
        return '<p class="muted">No measured benchmark comparison is available yet.</p>'
    body = []
    for row in rows[:6]:
        if not isinstance(row, dict):
            continue
        body.append(
            "<tr>"
            f"<td>{_esc(row.get('benchmark'))}</td>"
            f"<td>{_esc(row.get('measured_count') or 0)}</td>"
            f"<td>{_esc(_fmt_html_pct(row.get('avg_forward_result_pct')))}</td>"
            f"<td>{_esc(_fmt_html_pct(row.get('avg_benchmark_result_pct')))}</td>"
            f"<td>{_esc(_fmt_html_pct(row.get('avg_alpha_pct')))}</td>"
            f"<td>{_esc(_fmt_html_pct(row.get('hit_rate_pct')))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Benchmark</th><th>Measured</th><th>Forward</th><th>Benchmark</th>"
        "<th>Alpha Vs Benchmark (Gross)</th><th>Hit Rate</th></tr></thead><tbody>"
        + "".join(body) + "</tbody></table>"
    )


def _model_credibility_table(rows: list[Any]) -> str:
    if not rows:
        return '<p class="muted">No model-by-model credibility record is available for this report target.</p>'
    body = []
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        body.append(
            "<tr>"
            f"<td>{_esc(row.get('target_name') or row.get('target_id'))}<br><span class=\"muted\">{_esc(row.get('target_id'))}</span></td>"
            f"<td>{_esc(row.get('signal_count') or 0)}</td>"
            f"<td>{_esc(row.get('measured_count') or 0)} / {_esc(row.get('pending_count') or 0)}</td>"
            f"<td>{_esc(_fmt_html_pct(row.get('hit_rate_pct')))}</td>"
            f"<td>{_esc(_fmt_html_pct(row.get('avg_alpha_pct')))}</td>"
            f"<td>{_esc(row.get('latest_action_label') or 'Review')}<br><span class=\"muted\">{_esc(row.get('benchmark') or 'Benchmark')}</span></td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Model</th><th>Signals</th><th>Measured / Pending</th>"
        "<th>Hit Rate</th><th>Alpha Vs Benchmark (Gross)</th><th>Latest</th></tr></thead><tbody>"
        + "".join(body) + "</tbody></table>"
    )


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
    preferred = {
        "executive_summary": 0,
        "action": 1,
        "assumptions": 2,
        "data_quality": 3,
        "evidence": 4,
        "forecast": 5,
        "provenance": 6,
        "risk": 7,
        "strategy": 8,
    }
    for key, item in sorted(
        value.items(),
        key=lambda row: (preferred.get(str(row[0]).lower(), 100), _label(row[0]).casefold()),
    ):
        rows.append(f"<dt>{_esc(_label(key))}</dt><dd>{_render_value(item)}</dd>")
    return "<dl>" + "".join(rows) + "</dl>"


def _label(value: Any) -> str:
    return str(value).replace("_", " ").replace("-", " ").title()


def _esc(value: Any) -> str:
    # Only None renders blank: numeric zeros must survive as '0'/'0.0'.
    return html.escape("" if value is None else str(value), quote=True)


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

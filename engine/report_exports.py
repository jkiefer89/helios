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
    snapshot = {
        "id": secrets.token_urlsafe(12),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target_kind": target_kind,
        "target_id": target_id,
        "target_name": target_name,
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
        "metadata": {
            "snapshot_version": 1,
            "analysis_only": True,
            "no_execution": True,
            "no_return_guarantee": True,
            "report_timestamp": _text(report.get("timestamp")),
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
    return out


def render_html(snapshot: dict[str, Any]) -> str:
    rows = {
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
        "main{max-width:1040px;margin:0 auto;background:#0d1622;border:1px solid #263548;border-radius:8px;padding:28px}",
        "h1,h2{margin:0 0 12px} h1{font-size:28px} h2{font-size:16px;text-transform:uppercase;letter-spacing:.08em;color:#9fb2c8}",
        "section{border-top:1px solid #263548;margin-top:22px;padding-top:18px} dl{display:grid;grid-template-columns:220px 1fr;gap:8px 18px}",
        "dt{color:#9fb2c8;font-weight:700} dd{margin:0} .warn{color:#ffd24a}.muted{color:#9fb2c8}.caveat{border:1px solid #775f18;background:#17180f;padding:12px;border-radius:6px}",
        "pre{white-space:pre-wrap;font-family:inherit}.footer{font-size:13px;color:#9fb2c8}",
        "</style>",
        "</head>",
        "<body>",
        "<main>",
        "<header>",
        "<p class=\"muted\">Helios Report Snapshot</p>",
        f"<h1>{_esc(snapshot.get('title'))}</h1>",
        f"<p class=\"muted\">Saved {_esc(snapshot.get('created_at'))} for {_esc(snapshot.get('target_kind'))}:{_esc(snapshot.get('target_id'))}</p>",
        "</header>",
        '<section class="caveat">',
        "<h2>Analysis-Only Caveat</h2>",
        f"<p>{_esc(report.get('disclaimer') or DISCLAIMER)}</p>",
        "</section>",
        "<section>",
        "<h2>Source And Provenance</h2>",
        _render_dict(rows),
        "</section>",
        _model_metadata_html(snapshot),
        _warnings_html(snapshot),
        _ai_html(snapshot),
        "<section>",
        "<h2>Report Sections</h2>",
        _render_value(sections),
        "</section>",
        f"<p class=\"footer\">{_esc(DISCLAIMER)}</p>",
        "</main>",
        "</body>",
        "</html>",
    ])


def render_pdf(snapshot: dict[str, Any]) -> bytes:
    lines = _pdf_lines(snapshot)
    escaped_lines = [_pdf_escape(line) for line in lines[:52]]
    text = "BT /F1 10 Tf 14 TL 50 780 Td " + " T* ".join(f"({line}) Tj" for line in escaped_lines) + " ET"
    stream = text.encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{index} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)


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
        "<p class=\"muted\">AI-generated narrative saved with this snapshot; calculations are from the Helios engine.</p>"
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


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

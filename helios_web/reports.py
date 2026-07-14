"""Reports blueprint: printable advisor reports and saved snapshot exports."""
from __future__ import annotations

from flask import Blueprint, Response, request

from engine import (
    data, evidence, model_governance, persistence, portfolio, report_exports,
    report_snapshots, reporting, research_gate,
)

from .core import ANALYSIS_ONLY_DISCLAIMER, _safe_int_arg, err, ok

bp = Blueprint("reports", __name__)


@bp.route("/api/report/instrument")
def report_instrument():
    symbol = data.clean_symbol(request.args.get("ticker", ""), fallback="")
    if not symbol:
        return err("Provide a ticker symbol.", 400)
    inst = data.get(symbol)
    if inst is None:
        return err(f"Unknown ticker '{symbol}'.", 404)
    try:
        return ok(reporting.instrument_report(inst))
    except ValueError as e:
        return err(str(e), 400)


@bp.route("/api/report/model")
def report_model():
    mdl = portfolio.get(request.args.get("id", ""))
    if mdl is None:
        return err("Unknown model.", 404)
    try:
        return ok(reporting.model_report(mdl))
    except ValueError as e:
        return err(str(e), 400)


@bp.route("/api/report/snapshots", methods=["GET"])
def report_snapshot_history():
    store = persistence.get_store()
    storage = report_snapshots.storage_status(store)
    if not store.available:
        return ok({
            "snapshots": [],
            "count": 0,
            "warning": store.warning or "Report history requires SQLite persistence.",
            "storage": storage,
            "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
        })
    limit, error = _safe_int_arg("limit", 50, 1, 200)
    if error:
        return err(error, 400)
    snapshots = [report_exports.public_snapshot(row) for row in store.report_snapshots(limit=limit or 50)]
    return ok({
        "snapshots": snapshots,
        "count": len(snapshots),
        "storage": storage,
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })


@bp.route("/api/report/snapshots", methods=["POST"])
def save_report_snapshot():
    store = persistence.get_store()
    if not store.available:
        return err(store.warning or "Report history requires SQLite persistence.", 503)
    body = request.get_json(silent=True) or {}
    kind = str(body.get("kind") or "").strip().lower()
    target_id = str(body.get("id") or body.get("target_id") or "").strip()
    ai_narrative = str(body.get("ai_narrative") or "").strip()
    # Tri-state: absent means "no toggle surfaced" (keep a provided narrative),
    # an explicit false excludes even a provided/generated one.
    raw_include = body.get("include_ai_narrative")
    include_ai_narrative = None if raw_include is None else bool(raw_include)
    prepared_for = str(body.get("prepared_for") or "").strip()
    prepared_by = str(body.get("prepared_by") or "").strip()
    reviewer = str(body.get("reviewer") or "").strip()
    report_purpose = str(body.get("report_purpose") or "").strip()
    try:
        report = report_snapshots.report_for_snapshot(kind, target_id)
    except ValueError as exc:
        return err(str(exc), 400)
    if report_purpose in {"client_review", "investment_committee"}:
        gate = _client_export_gate(kind, target_id)
        if not gate["passed"]:
            return err(gate["blocked_reason"] or "Client-ready report export is blocked.", 409)
    ai_narrative, ai_meta = report_snapshots.resolve_narrative(
        report,
        provided_narrative=ai_narrative,
        include_ai_narrative=include_ai_narrative,
    )
    snapshot = report_exports.build_snapshot(
        target_kind=kind,
        target_id=target_id,
        report=report,
        signal_journal_evidence=report_snapshots.signal_journal_evidence(kind, target_id),
        ai_narrative=ai_narrative,
        ai_narrative_status=ai_meta["status"],
        ai_provider=ai_meta["provider"],
        version=report_snapshots.next_version(store, kind, target_id),
        prepared_for=prepared_for,
        prepared_by=prepared_by,
        reviewer=reviewer,
        report_purpose=report_purpose,
    )
    evidence_id = evidence.new_id("report")
    snapshot["evidence"] = evidence.reference(evidence_id)
    snapshot["metadata"]["evidence"] = evidence.reference(evidence_id)
    snapshot["html"] = report_exports.render_html(snapshot)
    evidence_result = evidence.capture(
        evidence_id=evidence_id,
        artifact_kind="report",
        target_kind=kind,
        target_id=target_id,
        input_payload={
            "report": report,
            "signal_journal": snapshot.get("signal_journal") or {},
            "report_parameters": {
                "version": snapshot.get("version"),
                "prepared_for": prepared_for,
                "prepared_by": prepared_by,
                "reviewer": reviewer,
                "report_purpose": report_purpose,
                "ai_narrative_status": ai_meta["status"],
            },
        },
        output_payload=snapshot,
        evidence_manifest=evidence.manifest(
            source=str(snapshot.get("source") or ""),
            transformations=("deterministic_report", "institutional_snapshot_render"),
            model_version=(snapshot.get("model_metadata") or {}).get("version"),
            extra={
                "row_count": snapshot.get("row_count"),
                "first_date": snapshot.get("first_date"),
                "last_date": snapshot.get("last_date"),
                "source_counts": snapshot.get("source_counts") or {},
            },
        ),
    )
    if not evidence_result.get("recorded"):
        return err("Immutable report evidence could not be recorded.", 500)
    result = store.save_report_snapshot(snapshot)
    if not result.get("saved"):
        return err(result.get("warning") or "Report snapshot could not be saved.", 500)
    public = report_exports.public_snapshot(snapshot)
    return ok({
        "snapshot": public,
        "html_url": public["html_url"],
        "pdf_url": public["pdf_url"],
        "storage": report_snapshots.storage_status(store),
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })


def _client_export_gate(kind: str, target_id: str) -> dict:
    if kind == "instrument":
        inst = data.get(data.clean_symbol(target_id, fallback=""))
        if inst is None:
            return {"passed": False, "blocked_reason": "Unknown instrument."}
        return research_gate.instrument_readiness(inst)
    mdl = portfolio.get(target_id)
    if mdl is None:
        return {"passed": False, "blocked_reason": "Unknown model."}
    readiness = research_gate.model_readiness(mdl)
    governance = model_governance.payload([mdl])
    row = next((item for item in governance.get("models", []) if item.get("id") == mdl.id), {})
    approved = row.get("approval_status") == "approved"
    if approved and readiness["passed"]:
        return readiness
    reasons = []
    if not approved:
        reasons.append("The current model version requires committee approval before client export.")
    if not readiness["passed"]:
        reasons.append(readiness["blocked_reason"])
    return {**readiness, "passed": False, "state": "blocked", "blocked_reason": " ".join(reasons)}


@bp.route("/api/report/snapshots/<snapshot_id>.html")
def report_snapshot_html(snapshot_id: str):
    snapshot = persistence.get_store().get_report_snapshot(snapshot_id)
    if snapshot is None:
        return err("Unknown report snapshot.", 404)
    html_body = snapshot.get("html") or report_exports.render_html(snapshot)
    return Response(str(html_body), mimetype="text/html")


@bp.route("/api/report/snapshots/<snapshot_id>.pdf")
def report_snapshot_pdf(snapshot_id: str):
    snapshot = persistence.get_store().get_report_snapshot(snapshot_id)
    if snapshot is None:
        return err("Unknown report snapshot.", 404)
    filename = f"helios-report-{snapshot_id}.pdf"
    return Response(
        report_exports.render_pdf(snapshot),
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

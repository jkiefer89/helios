"""Reports blueprint: printable advisor reports and saved snapshot exports."""
from __future__ import annotations

from flask import Blueprint, Response, request

from engine import data, persistence, portfolio, report_exports, report_snapshots, reporting

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
    return ok(reporting.instrument_report(inst))


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
    include_ai_narrative = bool(body.get("include_ai_narrative"))
    prepared_for = str(body.get("prepared_for") or "").strip()
    prepared_by = str(body.get("prepared_by") or "").strip()
    reviewer = str(body.get("reviewer") or "").strip()
    report_purpose = str(body.get("report_purpose") or "").strip()
    try:
        report = report_snapshots.report_for_snapshot(kind, target_id)
    except ValueError as exc:
        return err(str(exc), 400)
    ai_narrative, ai_meta = report_snapshots.ai_narrative(
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

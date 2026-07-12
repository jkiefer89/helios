"""Ledger blueprint — actual fills, snapshots, and real vs paper performance."""
from __future__ import annotations

from flask import Blueprint, request

from engine import ledger, persistence, portfolio

from .core import ANALYSIS_ONLY_DISCLAIMER, err, ok

bp = Blueprint("ledger", __name__)

_MAX_UPLOAD_BYTES = 8 * 1024 * 1024


def _uploaded_file():
    file = request.files.get("file")
    if file is None or not file.filename:
        return None, "Attach a CSV file."
    raw = file.read(_MAX_UPLOAD_BYTES + 1)
    if len(raw) > _MAX_UPLOAD_BYTES:
        return None, "File exceeds the 8MB upload limit."
    return (raw, file.filename), ""


@bp.route("/api/ledger/fills/upload", methods=["POST"])
def upload_fills():
    payload, error = _uploaded_file()
    if error:
        return err(error, 400)
    raw, filename = payload
    account_id = str(request.form.get("account_id") or "").strip()
    if not account_id:
        return err("account_id is required.", 400)
    try:
        result = ledger.import_fills(raw, account_id, source_filename=filename)
    except ValueError as exc:
        return err(str(exc), 400)
    persistence.get_store().flush()
    return ok({**result, "disclaimer": ANALYSIS_ONLY_DISCLAIMER})


@bp.route("/api/ledger/positions/upload", methods=["POST"])
def upload_positions():
    payload, error = _uploaded_file()
    if error:
        return err(error, 400)
    raw, filename = payload
    account_id = str(request.form.get("account_id") or "").strip()
    if not account_id:
        return err("account_id is required.", 400)
    as_of = str(request.form.get("as_of") or "").strip()
    try:
        result = ledger.import_positions(raw, account_id, as_of=as_of)
    except ValueError as exc:
        return err(str(exc), 400)
    persistence.get_store().flush()
    return ok({**result, "source_file": filename, "disclaimer": ANALYSIS_ONLY_DISCLAIMER})


@bp.route("/api/ledger/accounts")
def list_accounts():
    return ok({"accounts": persistence.get_store().ledger_accounts()})


@bp.route("/api/ledger/account/map", methods=["POST"])
def map_account():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return err("Request body must be a JSON object.", 400)
    account_id = str(body.get("account_id") or "").strip()
    model_id = str(body.get("model_id") or "").strip()
    if not account_id:
        return err("account_id is required.", 400)
    if model_id and portfolio.get(model_id) is None:
        return err(f"Unknown model '{model_id}'.", 404)
    persistence.get_store().upsert_ledger_account(
        account_id, display_name=str(body.get("display_name") or ""), model_id=model_id)
    persistence.get_store().flush()
    return ok({"accounts": persistence.get_store().ledger_accounts()})


@bp.route("/api/ledger/account/<account_id>", methods=["DELETE"])
def delete_account(account_id: str):
    """Undo for a bad import: removes the account and all its ledger rows."""
    result = persistence.get_store().delete_ledger_account(str(account_id).strip())
    persistence.get_store().flush()
    return ok(result)


@bp.route("/api/ledger/performance")
def performance():
    account_id = str(request.args.get("account") or "").strip()
    if not account_id:
        return err("Provide ?account=<account_id>.", 400)
    return ok({
        **ledger.actual_vs_paper(account_id),
        "shortfall": ledger.decision_shortfall(account_id),
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })

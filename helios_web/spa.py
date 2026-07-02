"""SPA and legacy static serving: React dist when built, template dashboard otherwise."""
from __future__ import annotations

from flask import Blueprint, Response, abort, render_template, send_from_directory

from . import core

bp = Blueprint("spa", __name__)


@bp.route("/")
def index():
    if core._react_frontend_ready():
        return core._serve_react_index()
    return render_template("index.html")


@bp.route("/legacy")
def legacy_index():
    return render_template("index.html")


@bp.route("/favicon.ico")
def favicon():
    return Response(status=204)


@bp.route("/assets/<path:filename>")
def frontend_assets(filename: str):
    if not core._react_frontend_ready():
        abort(404)
    return send_from_directory(core.FRONTEND_DIST / "assets", filename)


@bp.route("/<path:path>")
def react_spa(path: str):
    if path.startswith("api/"):
        abort(404)
    if not core._react_frontend_ready():
        abort(404)
    requested = core.FRONTEND_DIST / path
    if requested.is_file():
        return send_from_directory(core.FRONTEND_DIST, path)
    return core._serve_react_index()

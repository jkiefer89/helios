"""SPA static serving: the React dist when built, build instructions otherwise."""
from __future__ import annotations

from flask import Blueprint, Response, abort, send_from_directory

from . import core

bp = Blueprint("spa", __name__)

# Self-contained fallback page served at / when frontend/dist is missing.
# No external assets: the CSP stays script-src 'self' with inline styles only.
_BUILD_INSTRUCTIONS_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Helios — frontend build required</title>
<style>
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #0d1117; color: #e6edf3; display: flex; min-height: 100vh;
         align-items: center; justify-content: center; }
  main { max-width: 40rem; padding: 2rem; }
  h1 { font-size: 1.4rem; margin: 0 0 0.75rem; }
  p { line-height: 1.5; }
  pre { background: #161b22; border: 1px solid #30363d; border-radius: 6px;
        padding: 0.9rem 1.1rem; overflow-x: auto; }
  code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9rem; }
</style>
</head>
<body>
<main>
<h1>Helios — React frontend not built</h1>
<p>The Helios web UI is a React app served from <code>frontend/dist/</code>,
which does not exist yet. Build it from the repository root, then reload
this page:</p>
<pre><code>npm --prefix frontend ci
npm --prefix frontend run build</code></pre>
<p>The JSON API remains fully available under <code>/api/</code> in the meantime.</p>
</main>
</body>
</html>
"""


def _build_instructions_page() -> Response:
    return Response(_BUILD_INSTRUCTIONS_HTML, status=200, mimetype="text/html")


@bp.route("/")
def index():
    if core._react_frontend_ready():
        return core._serve_react_index()
    return _build_instructions_page()


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

"""Shared Flask app plus cross-cutting web concerns.

Owns the singleton ``app`` object, the HTTP Basic-Auth gate and brute-force
throttle, the CSRF heuristic, security headers, JSON error handlers, the
_ok/_err response helpers, request-arg parsing, the shared upload/live-fetch
semaphores, and the startup banner. Section routes live in sibling blueprint
modules that import from here.
"""
from __future__ import annotations

import math
import os
import secrets
import socket
import threading
import time
from hmac import compare_digest
from urllib.parse import urlsplit

import numpy as np
import pandas as pd
from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from .localenv import APP_ROOT

# No Flask template/static folders: the React dist (plus the inline build-
# instructions fallback) is served by the spa blueprint from frontend/dist.
app = Flask(__name__, root_path=str(APP_ROOT), static_folder=None, template_folder=None)
# Cap request bodies so a huge upload can't exhaust memory (16 MB is ample for CSVs).
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
FRONTEND_DIST = APP_ROOT / "frontend" / "dist"


# --------------------------------------------------------------------------- #
# Access control — HTTP Basic Auth guards the WHOLE app (pages, API, static).
#   HELIOS_USER / HELIOS_PASSWORD   configure the credentials
#   (password is auto-generated and printed at startup if unset)
#   HELIOS_AUTH=0                   disable the gate (trusted localhost only)
# --------------------------------------------------------------------------- #
AUTH_ENABLED = os.environ.get("HELIOS_AUTH", "1") != "0"
AUTH_USER = os.environ.get("HELIOS_USER", "advisor")


def _resolve_auth_password(raw: str | None) -> tuple[str, bool]:
    """Returns (password, generated). Empty/whitespace HELIOS_PASSWORD (e.g. an
    untouched .env.example copy) counts as unset: auto-generate instead of
    silently locking every login out with an unusable blank password."""
    if raw is None or not raw.strip():
        return secrets.token_urlsafe(9), True
    return raw, False


AUTH_PASSWORD, PASSWORD_GENERATED = _resolve_auth_password(os.environ.get("HELIOS_PASSWORD"))


def _auth_ok(username: str, password: str) -> bool:
    """Constant-time credential check (always compares both fields)."""
    if not username or not password:
        return False
    u_ok = compare_digest(username.encode("utf-8"), AUTH_USER.encode("utf-8"))
    p_ok = compare_digest(password.encode("utf-8"), AUTH_PASSWORD.encode("utf-8"))
    return u_ok and p_ok


# Brute-force throttle: per-remote-IP failure counter with a temporary lockout,
# plus a constant delay on every failed credential check.
_AUTH_FAILURE_DELAY_SECONDS = 0.3
_AUTH_LOCKOUT_THRESHOLD = 10
_AUTH_LOCKOUT_SECONDS = 60.0
_AUTH_FAILURES: dict[str, tuple[int, float]] = {}
_AUTH_FAILURES_LOCK = threading.Lock()

# Browser requests that mutate state must originate from our own origin.
# curl / same-origin app fetches send no Sec-Fetch-Site header and pass.
#
# CONTRACT: methods listed here bypass the CSRF check entirely, so every GET
# handler must be a pure read of caches/stores — no journal writes, no alert
# counter bumps, no forced feed fetches. Anything that mutates state must be
# a POST/PUT/DELETE route (see /api/macro/refresh for the pattern). Caching
# reads that populate an in-process cache are fine; durable writes are not.
_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _is_cross_site_request() -> bool:
    """CSRF heuristic using browser-set fetch metadata headers."""
    sec_fetch_site = (request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if sec_fetch_site and sec_fetch_site not in {"same-origin", "none"}:
        return True
    origin = (request.headers.get("Origin") or "").strip()
    if origin:
        origin_host = urlsplit(origin).netloc.lower()
        if origin_host != (request.host or "").lower():
            return True
    return False


def _auth_locked_out(remote: str) -> bool:
    with _AUTH_FAILURES_LOCK:
        count, last_failure = _AUTH_FAILURES.get(remote, (0, 0.0))
        if count < _AUTH_LOCKOUT_THRESHOLD:
            return False
        if time.monotonic() - last_failure < _AUTH_LOCKOUT_SECONDS:
            return True
        del _AUTH_FAILURES[remote]
        return False


def _register_auth_failure(remote: str) -> None:
    with _AUTH_FAILURES_LOCK:
        count, _ = _AUTH_FAILURES.get(remote, (0, 0.0))
        _AUTH_FAILURES[remote] = (count + 1, time.monotonic())
    app.logger.warning("Failed Basic-auth attempt from %s", remote)
    time.sleep(_AUTH_FAILURE_DELAY_SECONDS)


def _clear_auth_failures(remote: str) -> None:
    with _AUTH_FAILURES_LOCK:
        _AUTH_FAILURES.pop(remote, None)


def _guard_response(message: str, code: int, headers: dict | None = None) -> Response:
    """Auth/CSRF/lockout refusal: JSON error envelope for API paths, plain text
    elsewhere so the browser Basic-auth prompt keeps working."""
    if request.path.startswith("/api/"):
        resp = jsonify({"error": message})
        resp.status_code = code
    else:
        resp = Response(message, code)
    for key, value in (headers or {}).items():
        resp.headers[key] = value
    return resp


@app.before_request
def _require_auth():
    if request.method not in _CSRF_SAFE_METHODS and _is_cross_site_request():
        return _guard_response("Forbidden.", 403)
    if not AUTH_ENABLED:
        return None
    remote = request.remote_addr or "unknown"
    if _auth_locked_out(remote):
        return _guard_response("Too many failed login attempts — try again shortly.", 429)
    auth = request.authorization
    if auth and auth.type == "basic" and _auth_ok(auth.username or "", auth.password or ""):
        _clear_auth_failures(remote)
        return None
    if auth is not None:
        _register_auth_failure(remote)
    return _guard_response(
        "Helios — authentication required.",
        401,
        {"WWW-Authenticate": 'Basic realm="Helios analytics", charset="UTF-8"'},
    )


def _react_frontend_ready() -> bool:
    return (FRONTEND_DIST / "index.html").is_file()


def _serve_react_index():
    return send_from_directory(FRONTEND_DIST, "index.html")


@app.after_request
def _security_headers(resp: Response) -> Response:
    """Defense-in-depth headers. The CSP restricts scripts to our own origin
    on every response — there are no per-route exceptions."""
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "base-uri 'none'; frame-ancestors 'none'; object-src 'none'"
    )
    return resp


# Bound concurrent outbound live fetches so they can't consume every worker
# thread (each /api/live makes several blocking network calls).
_LIVE_SEMAPHORE = threading.BoundedSemaphore(2)
# Bound concurrent spreadsheet parses so a few large uploads can't pin every
# worker thread on CPU-heavy XML parsing.
_UPLOAD_SEMAPHORE = threading.BoundedSemaphore(2)

ANALYSIS_ONLY_DISCLAIMER = (
    "Analysis only — Helios provides research evidence and does not provide "
    "investment advice, brokerage services, order execution, or return guarantees."
)


# --------------------------------------------------------------------------- #
# Startup banner (LAN URLs + login), shared by the dev and waitress entrypoints
# --------------------------------------------------------------------------- #
def lan_ips() -> list[str]:
    ips: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # no packets sent; just resolves the local iface
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


def print_banner(host: str, port: int, tls: bool = False) -> None:
    scheme = "https" if tls else "http"
    public = host in ("0.0.0.0", "::")
    line = "  " + "─" * 52
    print("\n  HELIOS — investment-model analytics")
    print(line)
    if public:
        print("  Reachable on your local network at:")
        print(f"    • this machine : {scheme}://127.0.0.1:{port}")
        for ip in lan_ips():
            print(f"    • on your LAN  : {scheme}://{ip}:{port}")
    else:
        print(f"    • {scheme}://{host}:{port}")
    print(line)
    if AUTH_ENABLED:
        print("  Login (HTTP Basic Auth):")
        print(f"    user     : {AUTH_USER}")
        if PASSWORD_GENERATED:
            print(f"    password : {AUTH_PASSWORD}")
            print("    (auto-generated — set HELIOS_PASSWORD to choose your own)")
        else:
            print("    password : set via HELIOS_PASSWORD")
    else:
        print("  ⚠  AUTH DISABLED (HELIOS_AUTH=0)")
        if public:
            print("     Anyone on this network can view all data — do not use this")
            print("     on a shared or untrusted network.")
    print(line)
    if public and not tls:
        print("  ⚠  Plain HTTP — the password travels unencrypted over the network.")
        print("     On untrusted Wi-Fi, encrypt it:   HELIOS_TLS=1 ./run.sh")
        print("     (or bind localhost: HELIOS_HOST=127.0.0.1 and use an SSH tunnel)")
        print(line)
    print("  macOS may prompt to allow incoming connections — click Allow.\n", flush=True)


# --------------------------------------------------------------------------- #
# JSON sanitation: numpy types + NaN/inf -> valid JSON
# --------------------------------------------------------------------------- #
def clean(obj):
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())
    if isinstance(obj, (pd.Timestamp,)):
        return obj.strftime("%Y-%m-%d")
    return obj


def ok(payload):
    return jsonify(clean(payload))


def err(message, code=400):
    return jsonify({"error": message}), code


def _safe_int_arg(name: str, default: int, min_value: int, max_value: int) -> tuple[int | None, str | None]:
    """Parse an int query arg: unparseable values yield a 400 message,
    out-of-range values are clamped into [min_value, max_value]."""
    raw = request.args.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, f"{name} must be an integer between {min_value} and {max_value}."
    return max(min_value, min(value, max_value)), None


def _safe_float_arg(name: str, default: float, min_value: float, max_value: float) -> tuple[float | None, str | None]:
    """Parse a float query arg: unparseable values yield a 400 message,
    out-of-range values are clamped into [min_value, max_value]."""
    raw = request.args.get(name, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, f"{name} must be a number between {min_value:g} and {max_value:g}."
    return max(min_value, min(value, max_value)), None


def _json_http_error(e: HTTPException):
    code = e.code or 500
    message = {
        400: "Bad request.",
        404: "Not found.",
        413: "Uploaded file is too large.",
        415: "Unsupported media type.",
        500: "Internal server error.",
    }.get(code, e.description or "Request failed.")
    return err(message, code)


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(413)
@app.errorhandler(415)
def handle_http_error(e: HTTPException):
    return _json_http_error(e)


@app.errorhandler(500)
def handle_internal_error(e: HTTPException):
    return err("Internal server error.", 500)


@app.errorhandler(Exception)
def handle_unexpected_error(e: Exception):
    if isinstance(e, HTTPException):
        return _json_http_error(e)
    app.logger.exception("Unhandled server error")
    return err("Internal server error.", 500)

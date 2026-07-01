"""Helios — investment-model analytics & trade-signal platform.

Flask backend exposing a JSON API consumed by the single-page dashboard.
Run with:  python app.py   (or ./run.sh)
"""
from __future__ import annotations

import math
import os
import secrets
import socket
import threading
from datetime import datetime, timezone
from hmac import compare_digest
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory
from werkzeug.exceptions import HTTPException

APP_ROOT = Path(__file__).resolve().parent


def _truthy_env(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _load_local_env_file(path: Path | None = None) -> dict:
    """Load local .env settings without overriding the process environment.

    This keeps secrets out of source control while making local server starts
    behave the way users expect after creating a repo-local .env file.
    """
    if not _truthy_env(os.environ.get("HELIOS_LOAD_DOTENV"), default=True):
        return {"loaded": False, "path": "", "count": 0}
    env_path = path or APP_ROOT / ".env"
    if not env_path.is_file():
        return {"loaded": False, "path": str(env_path), "count": 0}
    loaded = 0
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not all(ch.isalnum() or ch == "_" for ch in key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return {"loaded": True, "path": str(env_path), "count": loaded}


_LOCAL_ENV_STATUS = _load_local_env_file()

from engine import (
    ai_copilot, backtest, data, forecast, indicators, insights, mandate, model_library, opportunity, portfolio,
    portfolio_clinic, persistence, provenance, regime, report_exports, reporting, sentiment, signal_journal, signals, strategy,
)

app = Flask(__name__)
# Cap request bodies so a huge upload can't exhaust memory (16 MB is ample for CSVs).
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
FRONTEND_DIST = APP_ROOT / "frontend" / "dist"
data.load_samples()
data.load_persisted_instruments()
portfolio.load_persisted_models()


# --------------------------------------------------------------------------- #
# Access control — HTTP Basic Auth guards the WHOLE app (pages, API, static).
#   HELIOS_USER / HELIOS_PASSWORD   configure the credentials
#   (password is auto-generated and printed at startup if unset)
#   HELIOS_AUTH=0                   disable the gate (trusted localhost only)
# --------------------------------------------------------------------------- #
AUTH_ENABLED = os.environ.get("HELIOS_AUTH", "1") != "0"
AUTH_USER = os.environ.get("HELIOS_USER", "advisor")
AUTH_PASSWORD = os.environ.get("HELIOS_PASSWORD")
PASSWORD_GENERATED = AUTH_PASSWORD is None
if PASSWORD_GENERATED:
    AUTH_PASSWORD = secrets.token_urlsafe(9)


def _auth_ok(username: str, password: str) -> bool:
    """Constant-time credential check (always compares both fields)."""
    if not username or not password:
        return False
    u_ok = compare_digest(username.encode("utf-8"), AUTH_USER.encode("utf-8"))
    p_ok = compare_digest(password.encode("utf-8"), AUTH_PASSWORD.encode("utf-8"))
    return u_ok and p_ok


@app.before_request
def _require_auth():
    if not AUTH_ENABLED:
        return None
    auth = request.authorization
    if auth and auth.type == "basic" and _auth_ok(auth.username or "", auth.password or ""):
        return None
    return Response(
        "Helios — authentication required.",
        401,
        {"WWW-Authenticate": 'Basic realm="Helios analytics", charset="UTF-8"'},
    )


@app.after_request
def _security_headers(resp: Response) -> Response:
    """Defense-in-depth headers. The CSP restricts scripts to our own origin +
    the Chart.js CDN, which also blunts any residual injection."""
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net; "
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
_AUTO_LIVE_LOCK = threading.Lock()
_AUTO_LIVE_THREAD: threading.Thread | None = None
_AUTO_LIVE_STOP = threading.Event()
_AUTO_LIVE_LAST_RESULT: dict | None = None
_AUTO_LIVE_LAST_RUN: str | None = None
DEFAULT_AUTO_LIVE_SYMBOLS = "core"
DATA_QUALITY_DEFAULT_STALE_DAYS = 7
DATA_QUALITY_DEFAULT_MIN_RESEARCH_ROWS = 60
DATA_QUALITY_DEFAULT_INSTITUTIONAL_ROWS = 252


def _env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    try:
        value = int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


def _auto_live_config() -> dict:
    raw_symbols = os.environ.get("HELIOS_AUTO_LIVE_SYMBOLS")
    if raw_symbols is None or not raw_symbols.strip():
        raw_symbols = "" if os.environ.get("HELIOS_DB_PATH") == "off" else DEFAULT_AUTO_LIVE_SYMBOLS
    symbols = data.expand_live_symbols(raw_symbols)
    return {
        "enabled": bool(symbols),
        "live_available": data.HAS_YF,
        "symbols": symbols,
        "source": raw_symbols,
        "period": (os.environ.get("HELIOS_AUTO_LIVE_PERIOD") or "2y").strip() or "2y",
        "interval_seconds": _env_int("HELIOS_AUTO_LIVE_REFRESH_SECONDS", 300, 60, 86_400),
        "max_workers": _env_int(
            "HELIOS_AUTO_LIVE_MAX_WORKERS",
            data.DEFAULT_LIVE_REFRESH_WORKERS,
            1,
            data.MAX_LIVE_REFRESH_WORKERS,
        ),
    }


def _auto_live_worker(config: dict) -> None:
    global _AUTO_LIVE_LAST_RESULT, _AUTO_LIVE_LAST_RUN
    while not _AUTO_LIVE_STOP.is_set():
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            result = data.ensure_live_symbols(
                config["symbols"],
                period=config["period"],
                max_workers=config["max_workers"],
            )
        except Exception as exc:
            result = {
                "requested": config["symbols"],
                "refreshed": 0,
                "failed": len(config["symbols"]),
                "max_workers": config["max_workers"],
                "results": [{"symbol": symbol, "status": "error", "rows_added": 0, "message": str(exc)} for symbol in config["symbols"]],
            }
        with _AUTO_LIVE_LOCK:
            _AUTO_LIVE_LAST_RUN = started
            _AUTO_LIVE_LAST_RESULT = result
        _AUTO_LIVE_STOP.wait(config["interval_seconds"])


def _start_auto_live_refresh() -> dict:
    global _AUTO_LIVE_THREAD
    config = _auto_live_config()
    if not config["enabled"]:
        return config
    if not config["live_available"]:
        with _AUTO_LIVE_LOCK:
            global _AUTO_LIVE_LAST_RESULT, _AUTO_LIVE_LAST_RUN
            _AUTO_LIVE_LAST_RUN = datetime.now(timezone.utc).isoformat(timespec="seconds")
            _AUTO_LIVE_LAST_RESULT = {
                "requested": config["symbols"],
                "refreshed": 0,
                "failed": len(config["symbols"]),
                "max_workers": config["max_workers"],
                "results": [
                    {
                        "symbol": symbol,
                        "status": "error",
                        "rows_added": 0,
                        "message": "yfinance is not installed; live data unavailable.",
                    }
                    for symbol in config["symbols"]
                ],
            }
        return config
    with _AUTO_LIVE_LOCK:
        if _AUTO_LIVE_THREAD and _AUTO_LIVE_THREAD.is_alive():
            return config
        _AUTO_LIVE_STOP.clear()
        _AUTO_LIVE_THREAD = threading.Thread(target=_auto_live_worker, args=(config,), name="helios-auto-live", daemon=True)
        _AUTO_LIVE_THREAD.start()
    return config


def _auto_live_status() -> dict:
    config = _auto_live_config()
    with _AUTO_LIVE_LOCK:
        running = bool(_AUTO_LIVE_THREAD and _AUTO_LIVE_THREAD.is_alive())
        last_result = _AUTO_LIVE_LAST_RESULT
        last_run = _AUTO_LIVE_LAST_RUN
    return {
        **config,
        "running": running,
        "last_run": last_run,
        "last_result": last_result,
    }


_start_auto_live_refresh()


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
    raw = request.args.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, f"{name} must be an integer between {min_value} and {max_value}."
    return max(min_value, min(value, max_value)), None


def _safe_float_arg(name: str, default: float, min_value: float, max_value: float) -> tuple[float | None, str | None]:
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


# --------------------------------------------------------------------------- #
# Command Center helpers
# --------------------------------------------------------------------------- #
ANALYSIS_ONLY_DISCLAIMER = (
    "Analysis only — Helios provides research evidence and does not provide "
    "investment advice, brokerage services, order execution, or return guarantees."
)


def _quick_instrument_screen(inst: data.Instrument) -> dict | None:
    close = inst.df["close"].dropna()
    if len(close) < 60:
        return None
    try:
        fc = forecast.forecast(close, horizon=21, n_paths=600)
        sent = sentiment.score_headlines(inst.headlines)
        sig = signals.evaluate(close, fc, sent, history_days=len(close))
        bt = backtest.run(close)
        metrics = indicators.metrics_summary(close)
    except Exception:
        app.logger.exception("command-center screen failed for %s", inst.symbol)
        return None

    quality = fc.get("quality", {}) or {}
    dir_acc = quality.get("directional_accuracy")
    evidence = 50.0
    if dir_acc is not None:
        evidence += (float(dir_acc) - 0.50) * 80
    evidence += max(min(bt["strategy"]["sharpe"], 3.0), -3.0) * 8
    if inst.source == "sample":
        evidence -= 18
    risk_score = max(0.0, metrics["annual_vol_pct"] * 0.75 + abs(metrics["max_drawdown_pct"]) * 0.70)
    opportunity_score = (
        48.0
        + sig["score"] * 28.0
        + max(min(fc["expected_return_pct"], 12.0), -12.0) * 1.2
        + max(min(evidence - 50.0, 25.0), -25.0) * 0.45
        - max(risk_score - 35.0, 0.0) * 0.35
    )
    warnings = list(sig.get("caveats") or [])
    if inst.source == "sample":
        warnings.append("Uses bundled sample data; treat as a workflow demonstration, not live market evidence.")
    if dir_acc is not None and dir_acc <= 0.50:
        warnings.append("Forecast edge is weak or below coin-flip on the out-of-sample window.")

    return {
        "id": f"instrument:{inst.symbol}",
        "kind": "instrument",
        "symbol": inst.symbol,
        "name": inst.name,
        "source": inst.source,
        "action": sig["action"],
        "score": round(float(np.clip(opportunity_score, 0, 100)), 1),
        "risk_score": round(float(np.clip(risk_score, 0, 100)), 1),
        "evidence_score": round(float(np.clip(evidence, 0, 100)), 1),
        "expected_return_pct": round(float(fc["expected_return_pct"]), 2),
        "expected_vol_pct": round(float(fc["expected_vol_pct"]), 2),
        "max_drawdown_pct": round(float(metrics["max_drawdown_pct"]), 2),
        "strategy_return_pct": round(float(bt["strategy"]["total_return_pct"]), 2),
        "benchmark_return_pct": round(float(bt["benchmark"]["total_return_pct"]), 2),
        "beat_benchmark": bool(bt["strategy"]["total_return_pct"] > bt["benchmark"]["total_return_pct"]),
        "reason": sig.get("headline_rationale", ""),
        "warnings": warnings[:3],
    }


def _command_center_payload() -> dict:
    instruments = data.all_instruments()
    prov = provenance.universe(instruments)
    market = regime.market_regime(instruments)
    real_instruments = [inst for inst in instruments if provenance.is_real_source(inst.source)]
    cards = [c for inst in real_instruments if (c := _quick_instrument_screen(inst))]
    ranked = sorted(cards, key=lambda c: c["score"], reverse=True)
    risks = sorted(cards, key=lambda c: (c["risk_score"], abs(c["max_drawdown_pct"])), reverse=True)

    model_alerts = []
    for mdl in portfolio.all_models():
        try:
            ps = portfolio.build_series(mdl, allow_sample=False, allow_simulated=False)
            pprov = provenance.portfolio(ps.provenance)
            if not pprov["eligible_for_real_research"]:
                model_alerts.append({
                    "id": mdl.id,
                    "name": mdl.name,
                    "severity": "high",
                    "message": f"{mdl.name}: data quality blocked. {pprov['reason']}",
                    "next_step": pprov["required_action"],
                    "eligible_for_real_research": False,
                    "missing_tickers": pprov["missing_tickers"],
                })
                continue
            metrics = indicators.metrics_summary(ps.close)
            source_weights = ps.provenance.get("source_weight_pct", {})
            source_summary = ", ".join(
                f"{src} {weight:.0f}%" for src, weight in sorted(source_weights.items()) if weight
            ) or "live/uploaded history"
            model_alerts.append({
                "id": mdl.id,
                "name": mdl.name,
                "severity": "medium",
                "message": (
                    f"{mdl.name}: vol {metrics['annual_vol_pct']:.1f}%, "
                    f"max drawdown {metrics['max_drawdown_pct']:.1f}%, "
                    f"sources {source_summary}."
                ),
                "next_step": "Open Portfolio Clinic when this model is selected.",
                "eligible_for_real_research": True,
            })
        except Exception as exc:
            model_alerts.append({
                "id": mdl.id,
                "name": mdl.name,
                "severity": "high",
                "message": f"{mdl.name}: data quality blocked ({exc}).",
                "next_step": "Upload real price history for every model holding before running Pro research.",
                "eligible_for_real_research": False,
            })

    research_queue = []
    if ranked:
        top = ranked[0]
        research_queue.append({
            "priority": "high",
            "title": f"Validate {top['symbol']} before any recommendation",
            "detail": "Check data source, forecast quality, historical strategy evidence, and invalidating risks.",
        })
    if risks:
        risk = risks[0]
        research_queue.append({
            "priority": "medium",
            "title": f"Review risk in {risk['symbol']}",
            "detail": f"Risk score {risk['risk_score']}/100 with max drawdown {risk['max_drawdown_pct']:.1f}%.",
        })
    if not ranked and not risks:
        research_queue.append({
            "priority": "high",
            "title": "Demo mode: real research is locked",
            "detail": provenance.DEMO_ACTION,
        })
    if not model_alerts:
        research_queue.append({
            "priority": "medium",
            "title": "Upload a client model for portfolio-level diagnostics",
            "detail": "Use uploaded or live price history for every holding to unlock Pro model alerts.",
        })
    for warning in market.get("warnings", []):
        research_queue.append({"priority": "medium", "title": "Verify regime proxy", "detail": warning})

    return {
        "data_mode": prov["data_mode"],
        "display_label": prov["display_label"],
        "eligible_for_real_research": bool(ranked or risks),
        "reason": "" if ranked or risks else prov["reason"],
        "required_action": "" if ranked or risks else prov["required_action"],
        "data_provenance": {
            "source_counts": prov["source_counts"],
            "warnings": prov["warnings"],
        },
        "regime": {**market, "data_mode": prov["data_mode"], "display_label": prov["display_label"]},
        "top_opportunities": ranked[:5],
        "top_risks": risks[:5],
        "model_alerts": model_alerts[:5],
        "research_queue": research_queue[:6],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    }


# --------------------------------------------------------------------------- #
# Series helpers for the price/indicator chart (downsampled for transport)
# --------------------------------------------------------------------------- #
def _series_payload(inst: data.Instrument, lookback: int = 400):
    df = inst.df
    close = df["close"].dropna()
    ind_df = indicators.indicator_frame(close).tail(lookback)
    sigs = signals.historical_signals(close).tail(lookback)
    dates = [d.strftime("%Y-%m-%d") for d in ind_df.index]

    def col(name):
        return [None if pd.isna(v) else float(v) for v in ind_df[name]]

    # Buy/sell markers where the causal score crosses thresholds.
    markers = []
    prev = 0.0
    for d, s in zip(ind_df.index, sigs):
        if prev <= 0.15 < s:
            markers.append({"date": d.strftime("%Y-%m-%d"), "type": "buy",
                            "price": float(close.loc[d])})
        elif prev >= -0.05 > s:
            markers.append({"date": d.strftime("%Y-%m-%d"), "type": "sell",
                            "price": float(close.loc[d])})
        prev = s

    return {
        "dates": dates,
        "close": col("close"),
        "sma50": col("sma50"),
        "sma200": col("sma200"),
        "bb_upper": col("bb_upper"),
        "bb_lower": col("bb_lower"),
        "rsi": col("rsi"),
        "macd": col("macd"),
        "macd_signal": col("macd_signal"),
        "macd_hist": col("macd_hist"),
        "markers": markers,
    }


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def _react_frontend_ready() -> bool:
    return (FRONTEND_DIST / "index.html").is_file()


def _serve_react_index():
    return send_from_directory(FRONTEND_DIST, "index.html")


@app.route("/")
def index():
    if _react_frontend_ready():
        return _serve_react_index()
    return render_template("index.html")


@app.route("/legacy")
def legacy_index():
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    return Response(status=204)


@app.route("/api/command-center")
def command_center():
    return ok(_command_center_payload())


@app.route("/api/data/status")
def data_status():
    return ok(_data_status_payload())


@app.route("/api/data-quality")
def data_quality():
    return ok(_data_quality_payload())


@app.route("/api/signal-journal")
def signal_journal_endpoint():
    limit, error = _safe_int_arg("limit", 100, 1, 500)
    if error:
        return err(error, 400)
    entries = signal_journal.list_entries(limit=limit)
    dashboard = signal_journal.dashboard_payload(entries)
    return ok({
        "entries": entries,
        "count": len(entries),
        **dashboard,
        "methodology": {
            "analysis_only": True,
            "paper_tracking_only": True,
            "raw_price_history_stored": False,
            "hit_rate_basis": "Measured paper signals are scored against their action intent: BUY/ADD/OVERWEIGHT must beat the benchmark when alpha is available; SELL/REDUCE/UNDERWEIGHT must underperform; HOLD/REVIEW must avoid material benchmark lag.",
            "forward_results": "Pending results resolve only after later live or persisted price history covers the original signal horizon.",
        },
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })


@app.route("/api/data/refresh", methods=["POST"])
def data_refresh():
    payload = request.get_json(silent=True) or {}
    raw_symbol = str(payload.get("symbol") or "").strip()
    refresh_all = bool(payload.get("all")) or raw_symbol.lower() in {"", "all", "*"}
    if refresh_all:
        symbols = [inst.symbol for inst in data.all_instruments() if inst.source == "live"]
    else:
        symbol = data.clean_symbol(raw_symbol, fallback="")
        if not symbol:
            return err("Provide a valid live ticker symbol.", 400)
        symbols = [symbol]
    if not _LIVE_SEMAPHORE.acquire(blocking=False):
        return err("Too many live-data requests in flight — try again in a moment.", 429)
    try:
        results = [data.refresh_live_symbol(symbol) for symbol in symbols]
    finally:
        _LIVE_SEMAPHORE.release()
    refreshed = sum(1 for item in results if item["status"] == "ok")
    failed = sum(1 for item in results if item["status"] == "error")
    skipped = sum(1 for item in results if item["status"] == "skipped")
    warnings = []
    if refresh_all and not symbols:
        warnings.append("No persisted live instruments are available to refresh.")
    if failed:
        warnings.append("One or more live refreshes failed; existing stored history was left unchanged.")
    return ok({
        "requested": "all" if refresh_all else symbols[0],
        "refreshed": refreshed,
        "failed": failed,
        "skipped": skipped,
        "results": results,
        "warnings": warnings,
        "data_status": _data_status_payload(),
    })


@app.route("/api/tickers")
def tickers():
    out = []
    refresh_by_symbol = _refresh_by_symbol()
    for inst in data.all_instruments():
        close = inst.df["close"]
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) > 1 else last
        dates = pd.to_datetime(close.dropna().index)
        out.append({
            "symbol": inst.symbol,
            "name": inst.name,
            "source": inst.source,
            "last_price": last,
            "change_pct": (last / prev - 1) * 100 if prev else 0.0,
            "row_count": int(close.dropna().shape[0]),
            "first_date": str(dates.min().date()) if len(dates) else None,
            "last_date": str(dates.max().date()) if len(dates) else None,
            "last_refresh": refresh_by_symbol.get(inst.symbol),
            "eligible_for_real_research": provenance.is_real_source(inst.source) and len(close.dropna()) >= 60,
        })
    out.sort(key=lambda d: d["symbol"])
    return ok({"tickers": out, "live_available": data.HAS_YF})


@app.route("/api/analyze")
def analyze():
    symbol = request.args.get("ticker", "").upper()
    horizon, error = _safe_int_arg("horizon", 21, 5, 90)
    if error:
        return err(error, 400)
    inst = data.get(symbol)
    if inst is None:
        return err(f"Unknown ticker '{symbol}'.", 404)

    close = inst.df["close"].dropna()
    if len(close) < 60:
        return err("Need at least 60 rows of history to analyze.", 400)

    fc = forecast.forecast(close, horizon=horizon)
    sent = sentiment.score_headlines(inst.headlines)
    sig = signals.evaluate(close, fc, sent)
    bt = backtest.run(close)
    metrics = indicators.metrics_summary(close)
    journal_entry = _record_instrument_signal(inst, close, sig, horizon)

    return ok({
        "symbol": inst.symbol,
        "name": inst.name,
        "source": inst.source,
        "metrics": metrics,
        "series": _series_payload(inst),
        "forecast": fc,
        "sentiment": sent,
        "signal": sig,
        "signal_journal_entry": journal_entry,
        "backtest": bt,
    })


def _strategy_request_args():
    cost_bps, cost_error = _safe_float_arg("cost_bps", 5.0, 0.0, 500.0)
    if cost_error:
        return None, None, None, cost_error
    slippage_bps, slippage_error = _safe_float_arg("slippage_bps", 0.0, 0.0, 500.0)
    if slippage_error:
        return None, None, None, slippage_error
    return cost_bps, slippage_bps, {
        "start": request.args.get("start") or None,
        "end": request.args.get("end") or None,
    }, None


@app.route("/api/strategy/analyze")
def strategy_analyze():
    symbol = data.clean_symbol(request.args.get("ticker", ""), fallback="")
    if not symbol:
        return err("Provide a ticker symbol.", 400)
    inst = data.get(symbol)
    if inst is None:
        return err(f"Unknown ticker '{symbol}'.", 404)
    cost_bps, slippage_bps, window, arg_error = _strategy_request_args()
    if arg_error:
        return err(arg_error, 400)
    try:
        result = strategy.analyze_strategy(
            inst.df["close"], cost_bps=cost_bps, slippage_bps=slippage_bps, **window)
    except ValueError as e:
        return err(str(e), 400)
    p = provenance.instrument(inst.source, len(inst.df["close"].dropna()))
    return ok({
        "series_kind": "instrument",
        "symbol": inst.symbol,
        "name": inst.name,
        "source": inst.source,
        "data_mode": p["data_mode"],
        "display_label": (
            "Demo strategy result — synthetic data, not investment evidence."
            if p["data_mode"] == "demo" else p["display_label"]
        ),
        "eligible_for_real_research": p["eligible_for_real_research"],
        "reason": p["reason"],
        "required_action": p["required_action"],
        "data_provenance": p,
        "warnings": p["warnings"],
        **result,
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })


@app.route("/api/opportunities")
def opportunities_api():
    limit, limit_error = _safe_int_arg("limit", 25, 1, 100)
    if limit_error:
        return err(limit_error, 400)
    min_score, score_error = _safe_float_arg("min_score", 0.0, 0.0, 100.0)
    if score_error:
        return err(score_error, 400)
    kind = (request.args.get("kind") or "all").lower()
    if kind not in {"all", "instrument", "model"}:
        return err("kind must be all, instrument, or model.", 400)
    include_hold = (request.args.get("include_hold", "1").lower() not in {"0", "false", "no"})
    payload = opportunity.opportunities(
        kind=kind,
        include_hold=include_hold,
        min_score=min_score,
        limit=limit,
    )
    return ok({**payload, "disclaimer": ANALYSIS_ONLY_DISCLAIMER})


# --------------------------------------------------------------------------- #
# Optional AI Copilot — provider calls are server-side and fail closed.
# --------------------------------------------------------------------------- #
@app.route("/api/ai/status")
def ai_status():
    provider = ai_copilot.get_provider()
    return ok(provider.status())


@app.route("/api/ai/opportunity/explain", methods=["POST"])
def ai_opportunity_explain():
    return _ai_call("explain_opportunity")


@app.route("/api/ai/opportunity/critique", methods=["POST"])
def ai_opportunity_critique():
    return _ai_call("critique_opportunity")


@app.route("/api/ai/strategy/summary", methods=["POST"])
def ai_strategy_summary():
    return _ai_call("summarize_strategy")


@app.route("/api/ai/clinic/summary", methods=["POST"])
def ai_clinic_summary():
    return _ai_call("summarize_portfolio_clinic")


@app.route("/api/ai/report", methods=["POST"])
def ai_report():
    return _ai_call("write_advisor_report")


@app.route("/api/ai/question", methods=["POST"])
def ai_question():
    return _ai_call("answer_question", require_question=True)


def _ai_call(method_name: str, require_question: bool = False):
    parsed, parse_error = _ai_request_payload(require_question=require_question)
    if parse_error:
        return err(parse_error, 400)
    payload, question, regenerate = parsed
    provider = ai_copilot.get_provider()
    status = provider.status()
    data_quality = _ai_data_quality(payload)
    if not status.get("available"):
        return jsonify(clean({
            "error": status.get("reason") or "AI provider unavailable.",
            "status": status,
            "data_quality": data_quality,
        })), 503
    try:
        if method_name == "answer_question":
            result = provider.answer_question(payload, question, regenerate=regenerate)
        else:
            result = getattr(provider, method_name)(payload, regenerate=regenerate)
    except ai_copilot.AIError as exc:
        safe_status = exc.status or provider.status()
        return jsonify(clean({
            "error": str(exc),
            "status": safe_status,
            "data_quality": data_quality,
        })), getattr(exc, "status_code", 503)
    except (AttributeError, TypeError, ValueError):
        return err("Invalid AI request.", 400)
    return ok({
        "result": result,
        "status": provider.status(),
        "provider": result.get("provider"),
        "model": result.get("model"),
        "data_quality": data_quality,
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })


def _ai_request_payload(require_question: bool = False):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, "Request body must be a JSON object."
    payload = body.get("payload")
    if payload is None:
        payload = {k: v for k, v in body.items() if k not in {"question", "regenerate"}}
    if not isinstance(payload, dict):
        return None, "payload must be a JSON object."
    question = str(body.get("question") or "").strip()
    if require_question and not question:
        return None, "question is required."
    regenerate = bool(body.get("regenerate", False))
    return (payload, question, regenerate), None


def _ai_data_quality(payload: dict) -> dict:
    found = _ai_find_quality(payload)
    return {
        "data_mode": found.get("data_mode") or found.get("mode"),
        "display_label": found.get("display_label"),
        "eligible_for_real_research": found.get("eligible_for_real_research"),
        "source": found.get("source"),
        "row_count": found.get("row_count") or found.get("history_days"),
        "first_date": found.get("first_date"),
        "last_date": found.get("last_date"),
        "last_refresh": found.get("last_refresh"),
        "reason": found.get("reason"),
        "required_action": found.get("required_action"),
        "warnings": found.get("warnings") or [],
        "missing_tickers": found.get("missing_tickers") or [],
    }


def _ai_find_quality(value) -> dict:
    if isinstance(value, dict):
        provenance_payload = value.get("data_provenance") or value.get("provenance")
        base = provenance_payload if isinstance(provenance_payload, dict) else {}
        keys = {
            "data_mode", "mode", "display_label", "eligible_for_real_research", "source",
            "row_count", "history_days", "first_date", "last_date", "last_refresh",
            "reason", "required_action", "warnings", "missing_tickers",
        }
        out = {key: value.get(key) for key in keys if key in value}
        out.update({key: base.get(key) for key in keys if key not in out and key in base})
        if out:
            return out
        for child in value.values():
            found = _ai_find_quality(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _ai_find_quality(child)
            if found:
                return found
    return {}


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return err("No file uploaded.")
    f = request.files["file"]
    raw_symbol = request.form.get("symbol") or (f.filename or "").rsplit(".", 1)[0] or "CLIENT"
    symbol = data.clean_symbol(raw_symbol)
    name = data.clean_name(request.form.get("name"), fallback=symbol)
    try:
        inst = data.parse_csv(f.read(), symbol, name, source_filename=f.filename or "")
    except ValueError as e:
        # parse_csv raises ValueError only with intentionally user-facing messages.
        return err(str(e), 400)
    except Exception:
        app.logger.exception("upload parse failed")
        return err("Could not read that file. Expected a CSV with date and price columns.", 400)
    p = provenance.instrument(inst.source, len(inst.df["close"].dropna()))
    return ok({
        "symbol": inst.symbol,
        "name": inst.name,
        "rows": len(inst.df),
        "source": inst.source,
        "data_provenance": p,
    })


@app.route("/api/live", methods=["POST"])
def live():
    if not data.HAS_YF:
        return err("Live data unavailable (yfinance not installed).", 400)
    payload = request.get_json(silent=True) or {}
    symbol = data.clean_symbol(payload.get("symbol", ""), fallback="")
    if not symbol:
        return err("Provide a valid ticker symbol.")
    if not _LIVE_SEMAPHORE.acquire(blocking=False):
        return err("Too many live-data requests in flight — try again in a moment.", 429)
    try:
        inst = data.fetch_live(symbol)
    except ValueError as e:
        return err(str(e), 400)
    except Exception:
        app.logger.exception("live fetch failed for %s", symbol)
        return err(f"Could not fetch live data for '{symbol}'.", 502)
    finally:
        _LIVE_SEMAPHORE.release()
    p = provenance.instrument(inst.source, len(inst.df["close"].dropna()))
    return ok({
        "symbol": inst.symbol,
        "name": inst.name,
        "rows": len(inst.df),
        "headlines": len(inst.headlines),
        "source": inst.source,
        "data_provenance": p,
    })


# --------------------------------------------------------------------------- #
# Portfolio models
# --------------------------------------------------------------------------- #
@app.route("/api/mandates")
def mandates():
    return ok({"mandates": mandate.public_list()})


@app.route("/api/models")
def models():
    out = []
    for mdl in portfolio.all_models():
        coverage = _model_coverage(mdl)
        out.append({
            "id": mdl.id, "name": mdl.name, "mandate": mdl.mandate_key,
            "mandate_label": mandate.get(mdl.mandate_key)["label"],
            "n_holdings": len(mdl.holdings),
            "top": mdl.holdings[0].ticker if mdl.holdings else None,
            "real_coverage_count": coverage["real_coverage_count"],
            "missing_tickers": coverage["missing_tickers"],
            "coverage_state": coverage["coverage_state"],
        })
    out.sort(key=lambda d: d["name"])
    return ok({"models": out})


@app.route("/api/model-library")
def model_library_list():
    return ok({"templates": model_library.public_list()})


@app.route("/api/model-library/import", methods=["POST"])
def model_library_import():
    payload = request.get_json(silent=True) or {}
    slug = str(payload.get("slug") or "")
    template = model_library.get(slug)
    if not template:
        return err("Unknown model library template.", 404)
    mdl = model_library.import_template(slug)
    if not mdl:
        return err("Unknown model library template.", 404)
    coverage = _model_coverage(mdl)
    return ok({
        "id": mdl.id,
        "name": mdl.name,
        "mandate": mdl.mandate_key,
        "n_holdings": len(mdl.holdings),
        "coverage_state": coverage["coverage_state"],
        "missing_tickers": coverage["missing_tickers"],
        "template_only": template["template_only"],
        "benchmark": template["benchmark"],
        "rebalance_rules": template["rebalance_rules"],
        "risk_limits": template["risk_limits"],
        "provenance": template["provenance"],
    })


@app.route("/api/model/upload", methods=["POST"])
def model_upload():
    if "file" not in request.files:
        return err("No file uploaded.")
    f = request.files["file"]
    name = request.form.get("name") or (f.filename or "Client Model").rsplit(".", 1)[0]
    mkey = mandate.key_or_default(request.form.get("mandate"))
    context = request.form.get("context", "")
    if not _UPLOAD_SEMAPHORE.acquire(blocking=False):
        return err("Server busy parsing another upload — try again in a moment.", 429)
    try:
        mdl = portfolio.parse_model_file(f.read(), f.filename or "", name, mkey, context)
    except ValueError as e:
        return err(str(e), 400)
    except Exception:
        app.logger.exception("model parse failed")
        return err("Could not read that file. Expected columns for Ticker and (optionally) Weight.", 400)
    finally:
        _UPLOAD_SEMAPHORE.release()
    coverage = _model_coverage(mdl)
    return ok({
        "id": mdl.id,
        "name": mdl.name,
        "mandate": mdl.mandate_key,
        "n_holdings": len(mdl.holdings),
        "missing_tickers": coverage["missing_tickers"],
        "coverage_state": coverage["coverage_state"],
    })


def _resolve_horizon(raw: str):
    """Return (kind, value, error). kind 'short' -> int days; 'long' -> preset days."""
    raw = (raw or "21").strip().upper()
    if raw in forecast.LONG_HORIZONS:
        return "long", forecast.LONG_HORIZONS[raw], None
    try:
        return "short", max(5, min(int(float(raw)), 90)), None
    except (TypeError, ValueError, OverflowError):
        return None, None, "horizon must be an integer between 5 and 90 or one of 6M, 1Y, 3Y, 5Y."


def _available_long(n_days: int) -> list[str]:
    out = []
    if n_days >= 90:           # don't project 6 months from < ~4 months of data
        out.append("6M")
    if n_days >= 126:
        out.append("1Y")
    if n_days >= 252:
        out += ["3Y", "5Y"]
    return out


@app.route("/api/model/analyze")
def model_analyze():
    mdl = portfolio.get(request.args.get("id", ""))
    if mdl is None:
        return err("Unknown model.", 404)
    hkind, hval, horizon_error = _resolve_horizon(request.args.get("horizon"))
    if horizon_error:
        return err(horizon_error, 400)

    try:
        ps = portfolio.build_series(mdl)
    except ValueError as e:
        return err(str(e), 400)
    close = ps.close
    if len(close) < 60:
        return err("Holdings have too little available analyzed history (need 60+ sessions).", 400)

    avail = _available_long(ps.n_days)
    long_label = forecast._long_label(hval) if hkind == "long" else None
    if hkind == "long" and long_label not in avail:
        hkind, hval = "short", 21  # requested long horizon unavailable for this history
        long_label = None

    # Aggregate any known headlines from holdings (sample tickers carry demo news).
    headlines = []
    for h in mdl.holdings:
        inst = data.get(h.ticker)
        if inst and inst.headlines:
            headlines.extend(inst.headlines[:2])
    sent = sentiment.score_headlines(headlines)

    signal_horizon = hval if hkind == "short" else 21
    fc_short = forecast.forecast(close, horizon=signal_horizon)
    metrics = indicators.metrics_summary(close)
    pmeta = {"n_holdings": len(mdl.holdings),
             "top_weight": mdl.holdings[0].weight if mdl.holdings else 0,
             "top_ticker": mdl.holdings[0].ticker if mdl.holdings else "?"}
    sig = signals.evaluate(close, fc_short, sent, mandate_key=mdl.mandate_key,
                           portfolio_meta=pmeta, history_days=ps.n_days, data_honesty=ps.provenance)
    bt = backtest.run(close)
    journal_entry = _record_model_signal(mdl, ps, close, sig, signal_horizon)

    # 1Y projection always computed for the drawdown-breach insight; reuse if displayed.
    fc_long_1y = forecast.forecast_long(close, 252, mdl.mandate_key) if ps.n_days >= 126 else None
    if hkind == "long":
        forecast_panel = (fc_long_1y if hval == 252 and fc_long_1y
                          else forecast.forecast_long(close, hval, mdl.mandate_key))
    else:
        forecast_panel = fc_short

    ins = insights.generate(mdl, ps, metrics, sig, fc_short, fc_long_1y)
    holdings = _holdings_with_signals(ps, mdl)

    return ok({
        "id": mdl.id, "name": mdl.name,
        "mandate": {"key": mdl.mandate_key, **mandate.get(mdl.mandate_key)},
        "context": mdl.mandate_context,
        "metrics": metrics,
        "series": _model_series_payload(close),
        "holdings": holdings,
        "concentration": {"hhi": ps.hhi, "n_eff": ps.n_eff, "corr_mean": ps.corr_mean},
        "provenance": ps.provenance,
        "horizon": {"kind": hkind, "value": hval, "label": long_label,
                    "available_long": avail},
        "forecast": forecast_panel,
        "forecast_short": fc_short,
        "signal": sig,
        "signal_journal_entry": journal_entry,
        "backtest": bt,
        "insights": ins,
        "warnings": ps.warnings,
    })


@app.route("/api/model/strategy/analyze")
def model_strategy_analyze():
    mdl = portfolio.get(request.args.get("id", ""))
    if mdl is None:
        return err("Unknown model.", 404)
    cost_bps, slippage_bps, window, arg_error = _strategy_request_args()
    if arg_error:
        return err(arg_error, 400)
    try:
        ps = portfolio.build_series(mdl, allow_sample=False, allow_simulated=False)
        p = provenance.portfolio(ps.provenance)
        if not p["eligible_for_real_research"]:
            return ok({
                "series_kind": "model",
                "id": mdl.id,
                "name": mdl.name,
                "data_mode": p["data_mode"],
                "display_label": p["display_label"],
                "eligible_for_real_research": False,
                "reason": p["reason"],
                "required_action": p["required_action"],
                "missing_tickers": p["missing_tickers"],
                "warnings": p["warnings"],
                "data_provenance": p,
                "methodology": {"analysis_only": True, "no_lookahead": True},
                "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
            })
        result = strategy.analyze_strategy(
            ps.close, cost_bps=cost_bps, slippage_bps=slippage_bps, **window)
    except ValueError as e:
        return ok({
            "series_kind": "model",
            "id": mdl.id,
            "name": mdl.name,
            "data_mode": "invalid_for_research",
            "display_label": "Data Quality Blocked",
            "eligible_for_real_research": False,
            "reason": str(e),
            "required_action": "Upload real price history for every model holding before running Strategy Lab.",
            "missing_tickers": [h.ticker for h in mdl.holdings],
            "warnings": [str(e)],
            "data_provenance": {"missing_tickers": [h.ticker for h in mdl.holdings]},
            "methodology": {"analysis_only": True, "no_lookahead": True},
            "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
        })
    return ok({
        "series_kind": "model",
        "id": mdl.id,
        "name": mdl.name,
        "mandate": {"key": mdl.mandate_key, **mandate.get(mdl.mandate_key)},
        "provenance": ps.provenance,
        "data_mode": p["data_mode"],
        "display_label": p["display_label"],
        "eligible_for_real_research": p["eligible_for_real_research"],
        "reason": p["reason"],
        "required_action": p["required_action"],
        "data_provenance": p,
        "warnings": ps.warnings,
        **result,
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })


@app.route("/api/model/clinic")
def model_clinic():
    mdl = portfolio.get(request.args.get("id", ""))
    if mdl is None:
        return err("Unknown model.", 404)
    try:
        result = portfolio_clinic.analyze_clinic(mdl)
    except ValueError as e:
        return err(str(e), 400)
    return ok({**result, "disclaimer": ANALYSIS_ONLY_DISCLAIMER})


@app.route("/api/report/instrument")
def report_instrument():
    symbol = data.clean_symbol(request.args.get("ticker", ""), fallback="")
    if not symbol:
        return err("Provide a ticker symbol.", 400)
    inst = data.get(symbol)
    if inst is None:
        return err(f"Unknown ticker '{symbol}'.", 404)
    return ok(reporting.instrument_report(inst))


@app.route("/api/report/model")
def report_model():
    mdl = portfolio.get(request.args.get("id", ""))
    if mdl is None:
        return err("Unknown model.", 404)
    try:
        return ok(reporting.model_report(mdl))
    except ValueError as e:
        return err(str(e), 400)


@app.route("/api/report/snapshots", methods=["GET"])
def report_snapshot_history():
    store = persistence.get_store()
    storage = _report_snapshot_storage(store)
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


@app.route("/api/report/snapshots", methods=["POST"])
def save_report_snapshot():
    store = persistence.get_store()
    if not store.available:
        return err(store.warning or "Report history requires SQLite persistence.", 503)
    body = request.get_json(silent=True) or {}
    kind = str(body.get("kind") or "").strip().lower()
    target_id = str(body.get("id") or body.get("target_id") or "").strip()
    ai_narrative = str(body.get("ai_narrative") or "").strip()
    include_ai_narrative = bool(body.get("include_ai_narrative"))
    try:
        report = _report_for_snapshot(kind, target_id)
    except ValueError as exc:
        return err(str(exc), 400)
    ai_narrative, ai_meta = _report_snapshot_ai_narrative(
        report,
        provided_narrative=ai_narrative,
        include_ai_narrative=include_ai_narrative,
    )
    snapshot = report_exports.build_snapshot(
        target_kind=kind,
        target_id=target_id,
        report=report,
        ai_narrative=ai_narrative,
        ai_narrative_status=ai_meta["status"],
        ai_provider=ai_meta["provider"],
    )
    result = store.save_report_snapshot(snapshot)
    if not result.get("saved"):
        return err(result.get("warning") or "Report snapshot could not be saved.", 500)
    public = report_exports.public_snapshot(snapshot)
    return ok({
        "snapshot": public,
        "html_url": public["html_url"],
        "pdf_url": public["pdf_url"],
        "storage": _report_snapshot_storage(store),
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })


@app.route("/api/report/snapshots/<snapshot_id>.html")
def report_snapshot_html(snapshot_id: str):
    snapshot = persistence.get_store().get_report_snapshot(snapshot_id)
    if snapshot is None:
        return err("Unknown report snapshot.", 404)
    html_body = snapshot.get("html") or report_exports.render_html(snapshot)
    return Response(str(html_body), mimetype="text/html")


@app.route("/api/report/snapshots/<snapshot_id>.pdf")
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


def _report_for_snapshot(kind: str, target_id: str) -> dict:
    if kind == "instrument":
        symbol = data.clean_symbol(target_id, fallback="")
        if not symbol:
            raise ValueError("Provide a ticker symbol.")
        inst = data.get(symbol)
        if inst is None:
            raise ValueError(f"Unknown ticker '{symbol}'.")
        return reporting.instrument_report(inst)
    if kind == "model":
        mdl = portfolio.get(target_id)
        if mdl is None:
            raise ValueError("Unknown model.")
        return reporting.model_report(mdl)
    raise ValueError("Report snapshot kind must be 'instrument' or 'model'.")


def _report_snapshot_ai_narrative(
    report: dict,
    *,
    provided_narrative: str,
    include_ai_narrative: bool,
) -> tuple[str, dict]:
    if provided_narrative:
        return provided_narrative, {
            "status": "provided",
            "provider": {"source": "current_session", "needs_review": True},
        }
    if not include_ai_narrative:
        return "", {"status": "not_requested", "provider": {}}
    provider = ai_copilot.get_provider()
    status = provider.status()
    provider_meta = {
        "provider": status.get("provider") or "",
        "model": status.get("model") or "",
        "available": bool(status.get("available")),
        "needs_review": True,
    }
    if not status.get("available"):
        return "", {"status": "provider_unavailable", "provider": provider_meta}
    try:
        result = provider.write_advisor_report({
            "report": report,
            "title": report.get("title"),
            "preview_locked": not bool(report.get("eligible_for_real_research")),
            "analysis_only_disclaimer": ANALYSIS_ONLY_DISCLAIMER,
        }, regenerate=True)
    except ai_copilot.AIError as exc:
        safe_status = exc.status or status
        return "", {
            "status": "provider_error",
            "provider": {
                "provider": safe_status.get("provider") or provider_meta["provider"],
                "model": safe_status.get("model") or provider_meta["model"],
                "available": bool(safe_status.get("available")),
                "needs_review": True,
            },
        }
    provider_meta.update({
        "provider": result.get("provider") or provider_meta["provider"],
        "model": result.get("model") or provider_meta["model"],
        "needs_review": bool(result.get("needs_review", True)),
    })
    return report_exports.ai_result_to_narrative(result), {
        "status": "generated",
        "provider": provider_meta,
    }


def _report_snapshot_storage(store) -> dict:
    status = store.status()
    encryption = status.get("encryption") or {}
    return {
        "backend": "sqlite",
        "scope": "local",
        "durable": bool(status.get("available")),
        "configured": bool(status.get("configured")),
        "encrypted_at_rest": bool(encryption.get("enabled")),
        "at_rest_format": encryption.get("at_rest_format") or "sqlite",
        "warning": status.get("warning") or "",
    }


@app.route("/assets/<path:filename>")
def frontend_assets(filename: str):
    if not _react_frontend_ready():
        abort(404)
    return send_from_directory(FRONTEND_DIST / "assets", filename)


@app.route("/<path:path>")
def react_spa(path: str):
    if path.startswith("api/"):
        abort(404)
    if not _react_frontend_ready():
        abort(404)
    requested = FRONTEND_DIST / path
    if requested.is_file():
        return send_from_directory(FRONTEND_DIST, path)
    return _serve_react_index()


def _refresh_by_symbol() -> dict:
    logs = persistence.get_store().refresh_log(limit=250)
    out = {}
    for row in logs:
        out.setdefault(row["symbol"], row)
    return out


def _model_coverage(mdl: portfolio.Model) -> dict:
    missing = []
    real_count = 0
    sources: dict[str, int] = {}
    for holding in mdl.holdings:
        inst = data.get(holding.ticker)
        source = inst.source if inst is not None else "missing"
        sources[source] = sources.get(source, 0) + 1
        history_days = len(inst.df["close"].dropna()) if inst is not None else 0
        if provenance.is_real_source(source) and history_days >= 60:
            real_count += 1
            continue
        missing.append(holding.ticker)
    if not mdl.holdings:
        state = "empty"
    elif real_count == len(mdl.holdings):
        state = "real"
    elif real_count:
        state = "mixed"
    else:
        state = "blocked"
    return {
        "real_coverage_count": real_count,
        "missing_tickers": missing,
        "source_counts": sources,
        "coverage_state": state,
    }


def _data_status_payload() -> dict:
    instruments = data.all_instruments()
    models = portfolio.all_models()
    universe = provenance.universe(instruments)
    store_status = persistence.get_store().status()
    refresh_logs = persistence.get_store().refresh_log(limit=10)
    real_instruments = [
        inst for inst in instruments
        if provenance.is_real_source(inst.source) and len(inst.df["close"].dropna()) >= 60
    ]
    model_rows = []
    all_missing: set[str] = set()
    for mdl in models:
        coverage = _model_coverage(mdl)
        all_missing.update(coverage["missing_tickers"])
        model_rows.append({
            "id": mdl.id,
            "name": mdl.name,
            "n_holdings": len(mdl.holdings),
            **coverage,
        })
    warnings = []
    if store_status.get("warning"):
        warnings.append(store_status["warning"])
    if not real_instruments:
        warnings.append("No eligible live or uploaded price histories are available for real research.")
    if all_missing:
        warnings.append("Missing real price history for model holdings: " + ", ".join(sorted(all_missing)))
    return {
        "database": store_status,
        "real_instrument_count": len(real_instruments),
        "persisted_model_count": int(store_status.get("persisted_model_count") or 0),
        "loaded_model_count": len(models),
        "last_refresh": store_status.get("last_refresh"),
        "data_mode_summary": universe,
        "source_counts": universe["source_counts"],
        "auto_live": _auto_live_status(),
        "warnings": _dedupe_strings(warnings),
        "missing_data": {
            "models": model_rows,
            "missing_tickers": sorted(all_missing),
            "blocked_model_count": sum(1 for row in model_rows if row["coverage_state"] != "real"),
        },
        "refresh_log": refresh_logs,
    }


def _data_quality_thresholds() -> dict[str, int]:
    min_research_rows = _env_int(
        "HELIOS_DATA_QUALITY_MIN_RESEARCH_ROWS",
        DATA_QUALITY_DEFAULT_MIN_RESEARCH_ROWS,
        20,
        10_000,
    )
    institutional_rows = _env_int(
        "HELIOS_DATA_QUALITY_INSTITUTIONAL_ROWS",
        DATA_QUALITY_DEFAULT_INSTITUTIONAL_ROWS,
        min_research_rows,
        20_000,
    )
    return {
        "stale_days": _env_int("HELIOS_DATA_QUALITY_STALE_DAYS", DATA_QUALITY_DEFAULT_STALE_DAYS, 1, 3_650),
        "min_research_rows": min_research_rows,
        "institutional_history_rows": institutional_rows,
    }


def _data_quality_threshold_config() -> dict:
    env_names = {
        "stale_days": "HELIOS_DATA_QUALITY_STALE_DAYS",
        "min_research_rows": "HELIOS_DATA_QUALITY_MIN_RESEARCH_ROWS",
        "institutional_history_rows": "HELIOS_DATA_QUALITY_INSTITUTIONAL_ROWS",
    }
    return {
        "source": "environment" if any(os.environ.get(name) for name in env_names.values()) else "defaults",
        "env": env_names,
        "range_guards": {
            "stale_days": "1-3650",
            "min_research_rows": "20-10000",
            "institutional_history_rows": "at least min_research_rows, max 20000",
        },
    }


def _data_quality_payload() -> dict:
    thresholds = _data_quality_thresholds()
    instruments = data.all_instruments()
    models = portfolio.all_models()
    refresh_logs = persistence.get_store().refresh_log(limit=250)
    refresh_by_symbol = {}
    for row in refresh_logs:
        refresh_by_symbol.setdefault(row["symbol"], row)
    symbols = [_data_quality_symbol_row(inst, refresh_by_symbol.get(inst.symbol), thresholds) for inst in instruments]
    model_rows = [_data_quality_model_row(mdl) for mdl in models]
    issues = []
    for row in symbols:
        if row["is_stale"]:
            issues.append(_quality_issue(
                "stale_symbols",
                "warning",
                row["symbol"],
                f"{row['symbol']} latest data is {row['days_stale']} calendar days old.",
                "Refresh live data or verify the uploaded date range before current research use.",
            ))
        if row["source"] != "sample" and row["row_count"] < thresholds["institutional_history_rows"]:
            severity = "blocker" if row["row_count"] < thresholds["min_research_rows"] else "warning"
            issues.append(_quality_issue(
                "short_histories",
                severity,
                row["symbol"],
                f"{row['symbol']} has {row['row_count']} rows; institutional review target is {thresholds['institutional_history_rows']}.",
                "Fetch/upload a longer adjusted history before relying on model evidence.",
            ))
        if row["refresh_evidence"]["requires_refresh_log"] and not row["refresh_evidence"]["has_refresh_log"]:
            issues.append(_quality_issue(
                "refresh_observability_gaps",
                "warning",
                row["symbol"],
                f"{row['symbol']} is marked live but has no persisted provider refresh attempt in the local log.",
                "Refresh the symbol or enable automatic live polling so freshness evidence is auditable.",
            ))
    for row in refresh_logs:
        if str(row.get("status") or "").lower() == "error":
            issues.append(_quality_issue(
                "refresh_failures",
                "warning",
                row["symbol"],
                f"{row['symbol']} refresh failed: {row.get('message') or 'provider error'}",
                "Retry refresh and keep the prior stored history until the provider succeeds.",
            ))
    missing_rows = []
    coverage_gaps = []
    source_conflicts = []
    for model_row in model_rows:
        missing_rows.extend({"symbol": symbol, "model_id": model_row["id"], "model_name": model_row["name"]} for symbol in model_row["missing_tickers"])
        if model_row["coverage_state"] != "real":
            coverage_gaps.append({
                "model_id": model_row["id"],
                "model_name": model_row["name"],
                "coverage_state": model_row["coverage_state"],
                "real_coverage_count": model_row["real_coverage_count"],
                "n_holdings": model_row["n_holdings"],
                "missing_tickers": model_row["missing_tickers"],
            })
            issues.append(_quality_issue(
                "coverage_gaps",
                "blocker" if model_row["real_coverage_count"] == 0 else "warning",
                model_row["name"],
                f"{model_row['name']} has {model_row['real_coverage_count']}/{model_row['n_holdings']} holdings with real research coverage.",
                "Fetch live histories for each uncovered holding before treating the model as research-ready.",
            ))
        nonzero_sources = {k: v for k, v in model_row["source_counts"].items() if v}
        if len(nonzero_sources) > 1:
            conflict = {
                "model_id": model_row["id"],
                "model_name": model_row["name"],
                "source_counts": nonzero_sources,
                "coverage_state": model_row["coverage_state"],
            }
            source_conflicts.append(conflict)
            issues.append(_quality_issue(
                "source_conflicts",
                "warning",
                model_row["name"],
                f"{model_row['name']} mixes sources: " + ", ".join(f"{source}={count}" for source, count in sorted(nonzero_sources.items())),
                "Resolve mixed/missing/sample coverage before presenting a unified model evidence pack.",
            ))
    for row in missing_rows:
        issues.append(_quality_issue(
            "missing_data",
            "blocker",
            row["symbol"],
            f"{row['symbol']} is required by {row['model_name']} but has no eligible real history.",
            "Fetch live data or upload an eligible adjusted price history.",
        ))
    blockers = sum(1 for issue in issues if issue["severity"] == "blocker")
    warnings = sum(1 for issue in issues if issue["severity"] == "warning")
    research_ready_symbols = [row for row in symbols if row["research_ready"]]
    research_ready_models = [row for row in model_rows if row["research_ready"]]
    refresh_observability_gaps = [
        row for row in symbols
        if row["refresh_evidence"]["requires_refresh_log"] and not row["refresh_evidence"]["has_refresh_log"]
    ]
    refresh_observed = [
        row for row in symbols
        if row["refresh_evidence"]["requires_refresh_log"] and row["refresh_evidence"]["has_refresh_log"]
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "research_ready": blockers == 0 and bool(research_ready_symbols or research_ready_models),
        "thresholds": thresholds,
        "threshold_config": _data_quality_threshold_config(),
        "refresh_observability": {
            "requires_log_for_sources": ["live"],
            "observed_count": len(refresh_observed),
            "gap_count": len(refresh_observability_gaps),
            "refresh_log_window": 250,
            "basis": "provider refresh log plus price-history date range",
        },
        "summary": {
            "symbol_count": len(symbols),
            "model_count": len(model_rows),
            "issue_count": len(issues),
            "blocker_count": blockers,
            "warning_count": warnings,
            "research_ready_count": len(research_ready_symbols) + len(research_ready_models),
            "stale_symbol_count": sum(1 for row in symbols if row["is_stale"]),
            "missing_symbol_count": len({row["symbol"] for row in missing_rows}),
            "refresh_failure_count": sum(1 for row in refresh_logs if str(row.get("status") or "").lower() == "error"),
            "refresh_observability_gap_count": len(refresh_observability_gaps),
            "coverage_gap_count": len(coverage_gaps),
        },
        "symbols": symbols,
        "models": model_rows,
        "issues": issues,
        "stale_symbols": [row for row in symbols if row["is_stale"]],
        "short_histories": [row for row in symbols if row["source"] != "sample" and row["row_count"] < thresholds["institutional_history_rows"]],
        "missing_data": missing_rows,
        "refresh_failures": [row for row in refresh_logs if str(row.get("status") or "").lower() == "error"],
        "refresh_observability_gaps": refresh_observability_gaps,
        "coverage_gaps": coverage_gaps,
        "source_conflicts": source_conflicts,
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    }


def _data_quality_symbol_row(inst: data.Instrument, last_refresh: dict | None, thresholds: dict[str, int]) -> dict:
    close = inst.df["close"].dropna() if "close" in inst.df else pd.Series(dtype=float)
    dates = pd.to_datetime(close.index, errors="coerce")
    dates = dates[~pd.isna(dates)]
    first_date = str(dates.min().date()) if len(dates) else None
    last_date = str(dates.max().date()) if len(dates) else None
    today = datetime.now(timezone.utc).date()
    days_stale = (today - dates.max().date()).days if len(dates) else None
    is_real_source = provenance.is_real_source(inst.source)
    requires_refresh_log = inst.source == "live"
    return {
        "symbol": inst.symbol,
        "name": inst.name,
        "source": inst.source,
        "row_count": int(len(close)),
        "first_date": first_date,
        "last_date": last_date,
        "days_stale": days_stale,
        "freshness_basis": "price_history_last_date" if last_date else "missing_price_history",
        "is_stale": bool(is_real_source and days_stale is not None and days_stale > thresholds["stale_days"]),
        "is_short": bool(is_real_source and len(close) < thresholds["institutional_history_rows"]),
        "research_ready": bool(is_real_source and len(close) >= thresholds["min_research_rows"]),
        "last_refresh": last_refresh,
        "refresh_evidence": {
            "requires_refresh_log": requires_refresh_log,
            "has_refresh_log": bool(last_refresh),
            "last_attempted_at": last_refresh.get("attempted_at") if last_refresh else None,
            "status": last_refresh.get("status") if last_refresh else None,
            "rows_added": last_refresh.get("rows_added") if last_refresh else None,
            "source": last_refresh.get("source") if last_refresh else None,
        },
        "next_step": _data_quality_symbol_next_step(inst.source, len(close), days_stale, thresholds, bool(last_refresh)),
    }


def _data_quality_model_row(mdl: portfolio.Model) -> dict:
    coverage = _model_coverage(mdl)
    return {
        "id": mdl.id,
        "name": mdl.name,
        "mandate": mdl.mandate_key,
        "n_holdings": len(mdl.holdings),
        "research_ready": coverage["coverage_state"] == "real",
        **coverage,
    }


def _data_quality_symbol_next_step(source: str, row_count: int, days_stale: int | None, thresholds: dict[str, int], has_refresh_log: bool) -> str:
    if not provenance.is_real_source(source):
        return "Sample data is demo-only; fetch live or upload real history."
    if row_count < thresholds["min_research_rows"]:
        return f"Fetch/upload at least {thresholds['min_research_rows']} valid rows before real research."
    if row_count < thresholds["institutional_history_rows"]:
        return "Extend history toward a one-year institutional review window."
    if source == "live" and not has_refresh_log:
        return "Refresh once so provider freshness evidence is logged."
    if days_stale is not None and days_stale > thresholds["stale_days"]:
        return "Refresh or verify the latest available provider date."
    return "Research-ready, subject to analysis-only caveats."


def _quality_issue(category: str, severity: str, target: str, detail: str, next_step: str) -> dict:
    return {
        "category": category,
        "severity": severity,
        "target": target,
        "detail": detail,
        "next_step": next_step,
    }


def _dedupe_strings(items: list[str]) -> list[str]:
    out = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def _record_instrument_signal(inst: data.Instrument, close: pd.Series, sig: dict, horizon_days: int) -> dict | None:
    try:
        p = provenance.instrument(inst.source, len(close.dropna()))
        return signal_journal.record_signal(
            target_kind="instrument",
            target_id=inst.symbol,
            target_name=inst.name,
            close=close,
            input_close=close,
            signal=sig,
            horizon_days=horizon_days,
            benchmark="SPY",
            source_counts={inst.source: 1},
            eligible_for_real_research=p["eligible_for_real_research"],
            data_mode=p["data_mode"],
            metadata={"endpoint": "/api/analyze"},
        )
    except Exception:
        return None


def _record_model_signal(mdl: portfolio.Model, ps: portfolio.PortfolioSeries, close: pd.Series, sig: dict, horizon_days: int) -> dict | None:
    try:
        p = provenance.portfolio(ps.provenance)
        return signal_journal.record_signal(
            target_kind="model",
            target_id=mdl.id,
            target_name=mdl.name,
            close=close,
            input_close=close,
            signal=sig,
            horizon_days=horizon_days,
            benchmark=signal_journal.benchmark_for_model(mdl),
            source_counts={str(k): int(v) for k, v in ps.sources.items()},
            eligible_for_real_research=p["eligible_for_real_research"],
            data_mode=p["data_mode"],
            metadata={"endpoint": "/api/model/analyze", "mandate": mdl.mandate_key},
        )
    except Exception:
        return None


def _holdings_with_signals(ps: portfolio.PortfolioSeries, mdl: portfolio.Model) -> list:
    out = []
    for h in ps.holdings:
        try:
            psr = data.resolve_series(h["ticker"])
            sc = signals.historical_signals(psr.close).iloc[-1]
            action = "BUY" if sc > 0.15 else "SELL" if sc < -0.05 else "HOLD"
        except Exception:
            action = "—"
        out.append({**h, "signal": action})
    return out


def _model_series_payload(close, lookback: int = 400):
    ind_df = indicators.indicator_frame(close).tail(lookback)
    dates = [d.strftime("%Y-%m-%d") for d in ind_df.index]

    def col(name):
        return [None if pd.isna(v) else float(v) for v in ind_df[name]]

    return {"dates": dates, "close": col("close"), "sma50": col("sma50"),
            "sma200": col("sma200"), "bb_upper": col("bb_upper"), "bb_lower": col("bb_lower"),
            "rsi": col("rsi"), "macd": col("macd"), "macd_signal": col("macd_signal"),
            "macd_hist": col("macd_hist"), "markers": []}


if __name__ == "__main__":
    # Dev entrypoint: localhost only. For network ("live") use ./serve.py.
    print_banner("127.0.0.1", 5000)
    app.run(host="127.0.0.1", port=5000, debug=False)

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
from hmac import compare_digest

import numpy as np
import pandas as pd
from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from engine import (
    backtest, data, forecast, indicators, insights, mandate, portfolio, sentiment, signals,
)

app = Flask(__name__)
# Cap request bodies so a huge upload can't exhaust memory (16 MB is ample for CSVs).
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
data.load_samples()


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
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    return Response(status=204)


@app.route("/api/tickers")
def tickers():
    out = []
    for inst in data.all_instruments():
        close = inst.df["close"]
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) > 1 else last
        out.append({
            "symbol": inst.symbol,
            "name": inst.name,
            "source": inst.source,
            "last_price": last,
            "change_pct": (last / prev - 1) * 100 if prev else 0.0,
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

    return ok({
        "symbol": inst.symbol,
        "name": inst.name,
        "source": inst.source,
        "metrics": metrics,
        "series": _series_payload(inst),
        "forecast": fc,
        "sentiment": sent,
        "signal": sig,
        "backtest": bt,
    })


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return err("No file uploaded.")
    f = request.files["file"]
    raw_symbol = request.form.get("symbol") or (f.filename or "").rsplit(".", 1)[0] or "CLIENT"
    symbol = data.clean_symbol(raw_symbol)
    name = data.clean_name(request.form.get("name"), fallback=symbol)
    try:
        inst = data.parse_csv(f.read(), symbol, name)
    except ValueError as e:
        # parse_csv raises ValueError only with intentionally user-facing messages.
        return err(str(e), 400)
    except Exception:
        app.logger.exception("upload parse failed")
        return err("Could not read that file. Expected a CSV with date and price columns.", 400)
    return ok({"symbol": inst.symbol, "name": inst.name, "rows": len(inst.df)})


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
    return ok({"symbol": inst.symbol, "name": inst.name, "rows": len(inst.df),
               "headlines": len(inst.headlines)})


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
        out.append({
            "id": mdl.id, "name": mdl.name, "mandate": mdl.mandate_key,
            "mandate_label": mandate.get(mdl.mandate_key)["label"],
            "n_holdings": len(mdl.holdings),
            "top": mdl.holdings[0].ticker if mdl.holdings else None,
        })
    out.sort(key=lambda d: d["name"])
    return ok({"models": out})


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
    return ok({"id": mdl.id, "name": mdl.name, "mandate": mdl.mandate_key,
               "n_holdings": len(mdl.holdings)})


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
        "backtest": bt,
        "insights": ins,
        "warnings": ps.warnings,
    })


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

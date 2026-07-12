"""Data blueprint: tickers, uploads, live fetch/refresh, status, and quality."""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd
from flask import Blueprint, request

from engine import data, data_quality, persistence, portfolio, provenance
from engine._common import dedupe as _dedupe_strings, env_int as _env_int

from .core import _LIVE_SEMAPHORE, _UPLOAD_SEMAPHORE, _safe_int_arg, app, err, ok

bp = Blueprint("data", __name__)

_AUTO_LIVE_LOCK = threading.Lock()
_AUTO_LIVE_THREAD: threading.Thread | None = None
_AUTO_LIVE_STOP = threading.Event()
_AUTO_LIVE_LAST_RESULT: dict | None = None
_AUTO_LIVE_LAST_RUN: str | None = None
DEFAULT_AUTO_LIVE_SYMBOLS = "core"


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
        # Batch boundary: one encrypted snapshot write per refresh cycle.
        persistence.get_store().flush()
        with _AUTO_LIVE_LOCK:
            _AUTO_LIVE_LAST_RUN = started
            _AUTO_LIVE_LAST_RESULT = result
        # Warm the SEC event cache in the background so the radar's cached-only
        # read has data without ever paying EDGAR latency inside a request. The
        # 1h TTL in sec_events makes this a no-op on most 5-minute cycles.
        try:
            from engine import provenance as _prov, sec_events as _sec
            for inst in data.all_instruments():
                if _AUTO_LIVE_STOP.is_set():
                    break
                if _prov.is_real_source(inst.source):
                    _sec.events_for(inst.symbol)
        except Exception:
            pass  # events are context, never worth failing the refresh loop
        # Warm the macro snapshot (Fed/policy/geopolitics) the same way — the
        # radar and command center read cached-only; 30-min TTL inside.
        try:
            from engine import macro_events as _macro_ev
            _macro_ev.macro_snapshot()
        except Exception:
            pass
        # Daily composite auto-record: the prospective evidence track accrues
        # for every real-eligible target even when the operator doesn't click
        # (view-triggered-only recording had a usage bias — review finding).
        # Cached inputs only; a per-day guard inside makes re-runs no-ops.
        try:
            from . import analysis as _analysis
            _analysis.auto_record_daily_signals()
        except Exception:
            pass
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


def _refresh_by_symbol() -> dict:
    logs = persistence.get_store().refresh_log(limit=250)
    out = {}
    for row in logs:
        out.setdefault(row["symbol"], row)
    return out


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
        coverage = data_quality.model_coverage(mdl)
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


@bp.route("/api/data/status")
def data_status():
    return ok(_data_status_payload())


@bp.route("/api/data-quality")
def data_quality_dashboard():
    return ok(data_quality.dashboard_payload())


@bp.route("/api/data/refresh", methods=["POST"])
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
        if refresh_all:
            # Batch refresh: bounded workers (same cap as ensure_live_symbols),
            # one signal-journal forward refresh after the batch instead of 250.
            worker_count = data._live_refresh_worker_count(None, len(symbols))

            def refresh_one(symbol: str) -> dict:
                return data.refresh_live_symbol(symbol, refresh_journal=False)

            if worker_count > 1:
                with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="helios-refresh") as executor:
                    results = list(executor.map(refresh_one, symbols))
            else:
                results = [refresh_one(symbol) for symbol in symbols]
            if any(item["status"] == "ok" for item in results):
                data._refresh_signal_journal_forward_results()
        else:
            results = [data.refresh_live_symbol(symbol) for symbol in symbols]
    finally:
        _LIVE_SEMAPHORE.release()
    # Batch boundary: one encrypted snapshot write for the whole refresh.
    persistence.get_store().flush()
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


@bp.route("/api/tickers")
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


@bp.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return err("No file uploaded.")
    f = request.files["file"]
    raw_symbol = request.form.get("symbol") or (f.filename or "").rsplit(".", 1)[0] or "CLIENT"
    symbol = data.clean_symbol(raw_symbol)
    name = data.clean_name(request.form.get("name"), fallback=symbol)
    if not _UPLOAD_SEMAPHORE.acquire(blocking=False):
        return err("Server busy parsing another upload — try again in a moment.", 429)
    try:
        inst = data.parse_csv(f.read(), symbol, name, source_filename=f.filename or "")
    except ValueError as e:
        # parse_csv raises ValueError only with intentionally user-facing messages.
        return err(str(e), 400)
    except Exception:
        app.logger.exception("upload parse failed")
        return err("Could not read that file. Expected a CSV with date and price columns.", 400)
    finally:
        _UPLOAD_SEMAPHORE.release()
    p = provenance.instrument(inst.source, len(inst.df["close"].dropna()))
    return ok({
        "symbol": inst.symbol,
        "name": inst.name,
        "rows": len(inst.df),
        "source": inst.source,
        "data_provenance": p,
    })


@bp.route("/api/live", methods=["POST"])
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

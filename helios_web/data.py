"""Data blueprint: tickers, uploads, live fetch/refresh, status, and quality."""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd
from flask import Blueprint, request

from engine import data, data_quality, operations, persistence, portfolio, provenance
from engine._common import dedupe as _dedupe_strings, env_int as _env_int

from .core import (
    _LIVE_SEMAPHORE, _UPLOAD_SEMAPHORE, _safe_int_arg, app, current_actor,
    defer_privileged_state, err, ok, privileged_transaction_active,
)

bp = Blueprint("data", __name__)

_AUTO_LIVE_LOCK = threading.Lock()
_AUTO_LIVE_THREAD: threading.Thread | None = None
_AUTO_LIVE_STOP = threading.Event()
_AUTO_LIVE_LAST_RESULT: dict | None = None
_AUTO_LIVE_LAST_RUN: str | None = None
DEFAULT_AUTO_LIVE_SYMBOLS = "core"


def _structured_refresh_row(row: dict) -> dict:
    """Add stable, secret-free remediation metadata to failed refresh logs."""
    if str(row.get("status") or "").lower() not in {"error", "skipped"}:
        return row
    return {**row, **data.live_failure_metadata(str(row.get("message") or "Live data operation failed."))}


def _auto_live_config() -> dict:
    raw_symbols = os.environ.get("HELIOS_AUTO_LIVE_SYMBOLS")
    if raw_symbols is None or not raw_symbols.strip():
        raw_symbols = "" if os.environ.get("HELIOS_DB_PATH") == "off" else DEFAULT_AUTO_LIVE_SYMBOLS
    symbols = data.expand_live_symbols(raw_symbols)
    return {
        "enabled": bool(symbols),
        "live_available": data.live_provider_available(),
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
            failure = data.live_failure_metadata(str(exc) or "Automatic live refresh failed.")
            result = {
                "requested": config["symbols"],
                "refreshed": 0,
                "failed": len(config["symbols"]),
                "max_workers": config["max_workers"],
                "results": [{"symbol": symbol, "status": "error", "rows_added": 0, **failure} for symbol in config["symbols"]],
            }
        # Batch boundary: one encrypted snapshot write per refresh cycle.
        persistence.get_store().flush()
        with _AUTO_LIVE_LOCK:
            _AUTO_LIVE_LAST_RUN = started
            _AUTO_LIVE_LAST_RESULT = result
        try:
            quality = data_quality.dashboard_payload(sync_alerts=True)
            operations.sync_incidents(quality.get("issues") or [], actor="auto-live")
        except Exception:
            pass
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
        # Warm the earnings-breadth snapshot (6h TTL — a no-op most cycles) so
        # the damper and Command Center read cached-only, never paying FMP
        # latency inside a request.
        try:
            from engine import earnings_breadth as _eb
            _eb.breadth_snapshot()
        except Exception:
            pass
        # Warm the per-instrument fundamentals cache so the strategic (CMA)
        # track — and the leveraged/daily-reset product guard — are populated
        # for standalone instrument analysis, which reads cached-only (never
        # fetches inside the GET). The 6h fetch TTL makes this a no-op on most
        # cycles; bounded and best-effort like the SEC/macro warmers above.
        try:
            from engine import fundamentals as _fnd, provenance as _prov2
            for inst in data.all_instruments():
                if _AUTO_LIVE_STOP.is_set():
                    break
                if _prov2.is_real_source(inst.source):
                    _fnd.fetch(inst.symbol)
        except Exception:
            pass  # fundamentals are best-effort; never fail the refresh loop
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
        failure = data.live_failure_metadata("No configured live price provider is available.")
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
                        **failure,
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
    logs = [_structured_refresh_row(row) for row in persistence.get_store().refresh_log(limit=250)]
    out = {}
    for row in logs:
        out.setdefault(row["symbol"], row)
    return out


def _data_status_payload() -> dict:
    instruments = data.all_instruments()
    models = portfolio.all_models()
    universe = provenance.universe(instruments)
    store_status = persistence.get_store().status()
    refresh_logs = [_structured_refresh_row(row) for row in persistence.get_store().refresh_log(limit=10)]
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


@bp.route("/api/data/jobs")
def data_jobs():
    """Run history + freshness for every background data job — the review's
    orchestrator-visibility intent at single-operator scale: last run, per-
    symbol freshness/failures, provider labels, and audit-chain integrity."""
    from datetime import date as _date

    store = persistence.get_store()
    freshness = []
    today = _date.today()
    for inst in data.all_instruments():
        if inst.source != "live" or "close" not in inst.df:
            continue
        close = inst.df["close"].dropna()
        if close.empty:
            continue
        last_bar = close.index.max()
        freshness.append({
            "symbol": inst.symbol,
            "last_bar": str(last_bar.date()),
            "age_calendar_days": int((today - last_bar.date()).days),
            "rows": int(len(close)),
            "price_provider": getattr(inst, "price_provider", "") or "",
        })
    freshness.sort(key=lambda row: -row["age_calendar_days"])
    logs = [_structured_refresh_row(row) for row in store.refresh_log(limit=100)]
    failures = [row for row in logs if row.get("status") == "error"]
    return ok({
        "auto_live": _auto_live_status(),
        "refresh_log": logs,
        "recent_failures": failures[:20],
        "freshness": freshness,
        "stale_count": sum(1 for row in freshness if row["age_calendar_days"] > 5),
        "audit_chain": store.audit_verify(limit=50000),
        "vault_recent": store.vault_entries(limit=10),
        "price_revisions_recent": store.price_revisions(limit=10),
        "basis": ("Freshness ages are calendar days since the last stored bar "
                  "(weekends/holidays inflate them by design — a Monday shows 2). "
                  "Audit chain is re-derived hash-by-hash on every call."),
    })


@bp.route("/api/data-quality")
def data_quality_dashboard():
    return ok(data_quality.dashboard_payload(sync_alerts=False))


@bp.route("/api/data-quality/sync", methods=["POST"])
def sync_data_quality_dashboard():
    """Recompute and persist alert occurrences after an explicit operation."""
    quality = data_quality.dashboard_payload(sync_alerts=True)
    quality["incident_sync"] = operations.sync_incidents(
        quality.get("issues") or [], actor=current_actor()
    )
    return ok(quality)


@bp.route("/api/data/price-reconciliation", methods=["POST"])
def price_reconciliation():
    """Side-by-side FMP vs yfinance closes for one symbol — the evidence
    gate before HELIOS_PRICE_SOURCE=fmp cutover (never rewrites history)."""
    symbol = data.clean_symbol(request.args.get("ticker", ""), fallback="")
    if not symbol:
        return err("Provide a ticker symbol.", 400)
    if not _LIVE_SEMAPHORE.acquire(blocking=False):
        return err("Too many live-data requests in flight — try again in a moment.", 429)
    try:
        return ok(data.reconcile_price_sources(
            symbol,
            period=request.args.get("period", "3mo"),
            primary_provider=request.args.get("primary", "fmp"),
            backup_provider=request.args.get("backup", "yfinance"),
        ))
    except ValueError as exc:
        return err(str(exc), 400)
    finally:
        _LIVE_SEMAPHORE.release()


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
            failure = data.live_failure_metadata("Provide a valid live ticker symbol.", preserved=False)
            return err(
                failure["message"], 400, code=failure["reason_code"],
                reason_code=failure["reason_code"], retryable=False,
                next_step=failure["next_step"], stale_result_preserved=failure["stale_result_preserved"],
            )
        symbols = [symbol]
    if not _LIVE_SEMAPHORE.acquire(blocking=False):
        return err(
            "Too many live-data requests are in flight.", 429,
            code="request_busy", reason_code="request_busy", retryable=True,
            next_step="Wait briefly and retry; the last persisted histories remain available.",
            stale_result_preserved=bool(symbols),
        )
    in_memory_before = data.snapshot_instruments()
    defer_privileged_state(
        rollback=lambda snapshot=in_memory_before: data.restore_instruments(snapshot)
    )
    try:
        if refresh_all:
            # Batch refresh: bounded workers (same cap as ensure_live_symbols),
            # one signal-journal forward refresh after the batch instead of 250.
            worker_count = data._live_refresh_worker_count(None, len(symbols))
            if privileged_transaction_active():
                worker_count = min(worker_count, 1)

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
    quality = data_quality.dashboard_payload(sync_alerts=True)
    incident_sync = operations.sync_incidents(
        quality.get("issues") or [], actor=current_actor()
    )
    return ok({
        "requested": "all" if refresh_all else symbols[0],
        "refreshed": refreshed,
        "failed": failed,
        "skipped": skipped,
        "results": results,
        "warnings": warnings,
        "data_status": _data_status_payload(),
        "data_quality": quality,
        "incident_sync": incident_sync,
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
    return ok({"tickers": out, "live_available": data.live_provider_available()})


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
    in_memory_before = data.snapshot_instruments()
    defer_privileged_state(
        rollback=lambda snapshot=in_memory_before: data.restore_instruments(snapshot)
    )
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
    p = provenance.instrument(
        inst.source,
        len(inst.df["close"].dropna()),
        price_provider=getattr(inst, "price_provider", ""),
    )
    return ok({
        "symbol": inst.symbol,
        "name": inst.name,
        "rows": len(inst.df),
        "source": inst.source,
        "data_provenance": p,
    })


@bp.route("/api/live", methods=["POST"])
def live():
    payload = request.get_json(silent=True) or {}
    symbol = data.clean_symbol(payload.get("symbol", ""), fallback="")
    if not data.live_provider_available():
        failure = data.live_failure_metadata(
            "Live data unavailable (no configured provider).",
            preserved=bool(symbol and data.get(symbol) is not None),
        )
        return err(
            failure["message"], 503, code=failure["reason_code"],
            reason_code=failure["reason_code"], retryable=failure["retryable"],
            next_step=failure["next_step"], stale_result_preserved=failure["stale_result_preserved"],
            diagnostics={"live_provider_available": False},
        )
    if not symbol:
        failure = data.live_failure_metadata("Provide a valid ticker symbol.", preserved=False)
        return err(
            failure["message"], 400, code=failure["reason_code"],
            reason_code=failure["reason_code"], retryable=False,
            next_step=failure["next_step"], stale_result_preserved=failure["stale_result_preserved"],
        )
    if not _LIVE_SEMAPHORE.acquire(blocking=False):
        return err(
            "Too many live-data requests are in flight.", 429,
            code="request_busy", reason_code="request_busy", retryable=True,
            next_step="Wait briefly and retry; the last persisted history remains available.",
            stale_result_preserved=bool(data.get(symbol) is not None),
        )
    in_memory_before = data.snapshot_instruments()
    defer_privileged_state(
        rollback=lambda snapshot=in_memory_before: data.restore_instruments(snapshot)
    )
    try:
        inst = data.fetch_live(symbol)
    except ValueError as e:
        failure = data.live_failure_metadata(str(e), preserved=bool(data.get(symbol) is not None))
        return err(
            failure["message"], 400, code=failure["reason_code"],
            reason_code=failure["reason_code"], retryable=failure["retryable"],
            next_step=failure["next_step"], stale_result_preserved=failure["stale_result_preserved"],
            diagnostics={"symbol": symbol},
        )
    except Exception:
        app.logger.exception("live fetch failed for %s", symbol)
        failure = data.live_failure_metadata(
            f"Could not fetch live data for '{symbol}'.",
            preserved=bool(data.get(symbol) is not None),
        )
        return err(
            failure["message"], 502, code=failure["reason_code"],
            reason_code=failure["reason_code"], retryable=failure["retryable"],
            next_step=failure["next_step"], stale_result_preserved=failure["stale_result_preserved"],
            diagnostics={"symbol": symbol, "live_provider_available": True},
        )
    finally:
        _LIVE_SEMAPHORE.release()
    p = provenance.instrument(
        inst.source,
        len(inst.df["close"].dropna()),
        price_provider=getattr(inst, "price_provider", ""),
    )
    return ok({
        "symbol": inst.symbol,
        "name": inst.name,
        "rows": len(inst.df),
        "headlines": len(inst.headlines),
        "source": inst.source,
        "data_provenance": p,
    })

"""Earnings-season breadth across the real book: are reports beating or missing?

The operator's question — "could we be entering broadly poor earnings, and
what would that do?" — decomposes into an OBSERVATION (how are reports
actually coming in across the names Helios tracks?) and an EXPOSURE
calculation (the broad de-rating scenario in risk_exposure). This module is
the observation half: FMP's earnings calendar carries epsActual next to
epsEstimated once a company reports, so trailing-window beat/miss breadth is
a fact, not a forecast.

House rules: cached per symbol (12h), fetches bounded, offline or unkeyed ->
available False; breadth only aggregates REAL instruments; a thin sample says
"insufficient reports" instead of a noise percentage.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from . import data, provenance

_WINDOW_DAYS = 120           # trailing earnings-season window
_MIN_REPORTS = 10            # below this, breadth is honestly "insufficient"
_WEAK_BREADTH_PCT = 40.0     # sub-40% beats across a real sample = deteriorating
_SYMBOL_TTL_S = 12 * 3600.0
_SNAPSHOT_TTL_S = 6 * 3600.0
_MAX_SYMBOLS = 80
_MAX_BYTES = 4 * 1024 * 1024

_HTTP = None
_LOCK = threading.RLock()
_SYMBOL_CACHE: dict[str, tuple[float, list[dict]]] = {}
_SNAPSHOT_CACHE: list = [0.0, None]


def set_http(fn) -> None:
    """Test seam: ``callable(url) -> parsed JSON``. ``None`` restores urllib."""
    global _HTTP
    _HTTP = fn
    invalidate_cache()


def invalidate_cache() -> None:
    with _LOCK:
        _SYMBOL_CACHE.clear()
        _SNAPSHOT_CACHE[0], _SNAPSHOT_CACHE[1] = 0.0, None


def _fetch_json(url: str):
    if _HTTP is not None:
        return _HTTP(url)
    req = urllib.request.Request(url, headers={"User-Agent": "Helios Research Terminal"})
    with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310 (fixed https host)
        return json.loads(resp.read(_MAX_BYTES + 1).decode("utf-8", errors="replace"))


def _reported_quarters(symbol: str) -> list[dict]:
    """Reported (actual+estimate) EPS rows for one symbol, cached 12h."""
    key = os.environ.get("HELIOS_FMP_KEY", "").strip()
    if not key and _HTTP is None:   # an injected test upstream needs no key
        return []
    now = time.monotonic()
    with _LOCK:
        hit = _SYMBOL_CACHE.get(symbol)
        if hit and now - hit[0] < _SYMBOL_TTL_S:
            return hit[1]
    base = os.environ.get("HELIOS_FMP_BASE_URL", "https://financialmodelingprep.com").rstrip("/")
    try:
        payload = _fetch_json(f"{base}/stable/earnings?symbol={symbol}&limit=8&apikey={key}")
    except Exception:
        return []
    rows = []
    for row in payload if isinstance(payload, list) else []:
        if not isinstance(row, dict):
            continue
        actual, est = row.get("epsActual"), row.get("epsEstimated")
        date = str(row.get("date") or "")[:10]
        if actual is None or est is None or len(date) != 10:
            continue
        rows.append({"date": date, "eps_actual": float(actual), "eps_estimated": float(est),
                     "revenue_actual": row.get("revenueActual"),
                     "revenue_estimated": row.get("revenueEstimated")})
    with _LOCK:
        _SYMBOL_CACHE[symbol] = (now, rows)
    return rows


def breadth_snapshot(force: bool = False) -> dict[str, Any]:
    """Trailing-window beat/miss breadth across every REAL instrument."""
    with _LOCK:
        ts, cached = _SNAPSHOT_CACHE
        if cached is not None and not force and time.monotonic() - ts < _SNAPSHOT_TTL_S:
            return dict(cached)
    symbols = [inst.symbol for inst in data.all_instruments()
               if provenance.is_real_source(inst.source)][:_MAX_SYMBOLS]
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=_WINDOW_DAYS)).isoformat()
    today = datetime.now(timezone.utc).date().isoformat()
    beats = misses = inline = 0
    rev_beats = rev_misses = 0
    reporters: list[dict] = []
    for symbol in symbols:
        for row in _reported_quarters(symbol):
            if not (cutoff <= row["date"] <= today):
                continue
            surprise = row["eps_actual"] - row["eps_estimated"]
            verdict = "beat" if surprise > 0 else "miss" if surprise < 0 else "inline"
            beats += verdict == "beat"
            misses += verdict == "miss"
            inline += verdict == "inline"
            if row.get("revenue_actual") is not None and row.get("revenue_estimated"):
                rev_beats += row["revenue_actual"] >= row["revenue_estimated"]
                rev_misses += row["revenue_actual"] < row["revenue_estimated"]
            reporters.append({"symbol": symbol, "date": row["date"], "verdict": verdict,
                              "surprise_pct": (round(surprise / abs(row["eps_estimated"]) * 100, 1)
                                               if row["eps_estimated"] else None)})
    decided = beats + misses
    snapshot: dict[str, Any] = {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_days": _WINDOW_DAYS,
        "symbols_checked": len(symbols),
        "reports": len(reporters),
        "beats": beats,
        "misses": misses,
        "inline": inline,
        "min_required": _MIN_REPORTS,
        "sufficient": decided >= _MIN_REPORTS,
        "basis": ("EPS actual vs consensus estimate over the trailing window, real "
                  "instruments only (FMP calendar). Observation of reported results — "
                  "not a forecast."),
    }
    if decided >= _MIN_REPORTS:
        snapshot["breadth_pct"] = round(100.0 * beats / decided, 1)
        snapshot["deteriorating"] = snapshot["breadth_pct"] < _WEAK_BREADTH_PCT
        if rev_beats + rev_misses >= _MIN_REPORTS:
            snapshot["revenue_breadth_pct"] = round(100.0 * rev_beats / (rev_beats + rev_misses), 1)
        snapshot["latest_reports"] = sorted(reporters, key=lambda r: r["date"], reverse=True)[:10]
    else:
        snapshot["note"] = (f"Only {decided} decided report(s) in the window — breadth "
                            "is not a meaningful statistic yet.")
    with _LOCK:
        _SNAPSHOT_CACHE[0], _SNAPSHOT_CACHE[1] = time.monotonic(), snapshot
    return dict(snapshot)


def snapshot_cached() -> dict[str, Any] | None:
    """Get-only read for latency-sensitive paths; never a network call."""
    with _LOCK:
        ts, cached = _SNAPSHOT_CACHE
    if cached is not None and time.monotonic() - ts < _SNAPSHOT_TTL_S:
        return dict(cached)
    return None

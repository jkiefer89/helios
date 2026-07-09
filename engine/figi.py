"""OpenFIGI CUSIP -> ticker mapping — the bridge from N-PORT to fundamentals.

N-PORT positions usually carry only a CUSIP (no ticker), so without this the
forward CMA can't join look-through holdings to ticker-keyed fundamentals and
coverage collapses. OpenFIGI is free (an API key only raises rate limits).

Offline-safe and injectable: ``http_post`` defaults to urllib; a failure leaves
the ticker unmapped (blank) rather than fabricating one. Results are cached
process-wide (CUSIP→ticker is stable), and empty results are cached too so a
miss isn't retried in a storm.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
_CACHE: dict[str, str] = {}
_LOCK = threading.RLock()
_MAX_RESP_BYTES = 5 * 1024 * 1024
_BACKOFF_UNTIL = 0.0   # transient-failure cooldown; failures are NEVER cached


def set_default_post(fn) -> None:
    """Test seam: override the HTTP POST (also clears the cache)."""
    global _default_post, _BACKOFF_UNTIL
    with _LOCK:
        _CACHE.clear()
        _BACKOFF_UNTIL = 0.0
    globals()["_default_post"] = fn or _urllib_post


def _urllib_post(url: str, headers: dict, body: bytes):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310 (fixed https host)
        return json.loads(resp.read(_MAX_RESP_BYTES + 1).decode("utf-8", errors="replace"))


_default_post = _urllib_post


def map_cusips(cusips, http_post=None, api_key: str | None = None, budget: int = 120) -> dict:
    """Return {cusip: ticker} for the given CUSIPs (cached; bounded by ``budget``)."""
    global _BACKOFF_UNTIL
    api_key = api_key if api_key is not None else os.environ.get("HELIOS_OPENFIGI_KEY", "")
    batch = 100 if api_key else 10   # OpenFIGI job-per-request limit (keyed vs not)
    post = http_post or _default_post

    out: dict[str, str] = {}
    want: list[str] = []
    with _LOCK:
        for raw in cusips:
            c = (raw or "").strip().upper()
            if not c:
                continue
            if c in _CACHE:
                if _CACHE[c]:
                    out[c] = _CACHE[c]
            elif c not in want:
                want.append(c)
    want = want[:budget]
    if not want:
        return out
    with _LOCK:
        if time.monotonic() < _BACKOFF_UNTIL:
            return out  # cooling down after a transient failure — retry later

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key
    for i in range(0, len(want), batch):
        chunk = want[i:i + batch]
        # Constrain to the US composite EQUITY line: without exchCode /
        # marketSecDes, OpenFIGI's first row can be a foreign venue whose
        # ticker collides with a DIFFERENT US company (verified live: CNQ's
        # CUSIP mapped to 'CRC' on a German venue = California Resources in
        # US-keyed fundamentals — the wrong-name-on-wrong-anchor failure).
        # Foreign-only listings and bonds return no rows and stay unmapped.
        body = json.dumps([{"idType": "ID_CUSIP", "idValue": c,
                            "exchCode": "US", "marketSecDes": "Equity"}
                           for c in chunk]).encode("utf-8")
        try:
            res = post(OPENFIGI_URL, headers, body)
        except Exception:
            res = None
        if not isinstance(res, list):
            # Transient failure (429/network): back off, never poison the
            # cache — a rate-limited chunk must stay retryable.
            with _LOCK:
                _BACKOFF_UNTIL = time.monotonic() + 60.0
            break
        for c, item in zip(chunk, res):
            ticker = ""
            if isinstance(item, dict):
                rows = item.get("data")
                if isinstance(rows, list) and rows:
                    ticker = str(rows[0].get("ticker") or "").upper()
            with _LOCK:
                _CACHE[c] = ticker  # includes honest no-match ('') results
            if ticker:
                out[c] = ticker
    return out

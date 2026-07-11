"""Free news-headline feed via the GDELT DOC 2.0 API (no key required).

Supplements yfinance's per-ticker headlines with broad news-wire coverage so
the sentiment component digests more than one source. Fully optional and
offline-safe: any network failure returns an empty list and the sentiment
layer simply scores whatever headlines it has — nothing is fabricated.

The HTTP getter is injectable (tests supply canned JSON), and results are
TTL-cached per query so repeated /api/analyze calls do not hammer GDELT.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request

_GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_MAX_RESP_BYTES = 4 * 1024 * 1024
_CACHE_TTL_S = 15 * 60.0
# Failures negative-cache briefly so a degraded GDELT is probed once per
# window instead of adding a 10s stall to every analyze call (review finding).
_CACHE_NEG_TTL_S = 5 * 60.0
_CACHE_MAX = 256

_HTTP = None
_LOCK = threading.RLock()
_CACHE: dict[str, tuple[float, list[str], float]] = {}   # (ts, titles, ttl)


def set_http(fn) -> None:
    """Test seam: ``callable(url) -> parsed JSON``. ``None`` restores urllib."""
    global _HTTP
    _HTTP = fn


def invalidate_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def _urllib_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Helios Research Terminal"})
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (fixed https host)
        return json.loads(resp.read(_MAX_RESP_BYTES + 1).decode("utf-8", errors="replace"))


def _clean_title(title: str) -> str:
    t = re.sub(r"\s+", " ", (title or "")).strip()
    return t[:300]


def headlines_for(symbol: str, company_name: str = "", max_records: int = 12) -> list[str]:
    """Recent English-language news headlines mentioning the symbol/company.

    Returns [] offline or on any provider error — callers merge with whatever
    other headline sources they already have.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return []
    # Prefer the company name (fewer false hits than a bare ticker like "DY");
    # quote multi-word names so GDELT treats them as a phrase.
    name = re.sub(r"[^A-Za-z0-9 .&'-]", " ", company_name or "").strip()
    term = f'"{name}"' if len(name.split()) > 1 else (name or sym)
    # Language filtering is a QUERY OPERATOR in the GDELT DOC 2.0 API; the
    # standalone "sourcelang" URL parameter is ignored (verified live: 30/50
    # non-English results with the parameter, 50/50 English with the operator)
    # and non-English text fed the English-lexicon sentiment scorer.
    query = f"{term} (stock OR shares OR earnings OR market) sourcelang:english"

    # Cache key includes the query inputs — a symbol-only key served one
    # (name, max_records) variant's results to every caller (review finding).
    cache_key = f"{sym}|{name.lower()}|{int(max_records)}"
    with _LOCK:
        hit = _CACHE.get(cache_key)
        if hit and time.monotonic() - hit[0] < (hit[2] if len(hit) > 2 else _CACHE_TTL_S):
            return list(hit[1])

    url = _GDELT_URL + "?" + urllib.parse.urlencode({
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": str(max(1, min(int(max_records), 50))),
        "timespan": "3d",
    })
    fn = _HTTP or _urllib_json
    try:
        payload = fn(url) or {}
    except Exception:
        with _LOCK:
            if cache_key not in _CACHE and len(_CACHE) >= _CACHE_MAX:
                _CACHE.pop(next(iter(_CACHE)), None)
            _CACHE[cache_key] = (time.monotonic(), [], _CACHE_NEG_TTL_S)
        return []
    seen: set[str] = set()
    titles: list[str] = []
    for art in payload.get("articles") or []:
        if not isinstance(art, dict):
            continue
        title = _clean_title(str(art.get("title") or ""))
        key = title.lower()
        if title and key not in seen:
            seen.add(key)
            titles.append(title)
    with _LOCK:
        if cache_key not in _CACHE and len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)), None)
        _CACHE[cache_key] = (time.monotonic(), titles, _CACHE_TTL_S)
    return list(titles)

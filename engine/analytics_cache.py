"""Keyed memo cache for deterministic per-series analytics.

Helios analytics (signals, walk-forward evidence, screening cards) are pure
functions of the stored price/model data, so recomputing them on every request
is wasted work. Entries are keyed by the caller-supplied identity (symbol or
model id plus parameters) together with a series token (last price date + row
count) and the global data version. The version is bumped whenever price or
model data changes (upload, live fetch/refresh, model registration), which
invalidates every prior entry at once.
"""
from __future__ import annotations

import copy
import threading
from typing import Any, Callable

import pandas as pd

MAX_ENTRIES = 256

_LOCK = threading.RLock()
_CACHE: dict[tuple, Any] = {}
_VERSION = 0


def version() -> int:
    """Current data version; changes whenever stored price/model data changes."""
    with _LOCK:
        return _VERSION


def invalidate() -> None:
    """Drop all memoized analytics and advance the data version."""
    global _VERSION
    with _LOCK:
        _VERSION += 1
        _CACHE.clear()


def series_token(close: pd.Series | None) -> tuple:
    """Identity token for a stored price series: last date + row count."""
    if close is None or len(close) == 0:
        return ("empty", 0)
    return (str(close.index[-1]), int(len(close)))


def memoize(key: tuple, compute: Callable[[], Any]) -> Any:
    """Return the value for key, computing and storing it on first use.

    Keys are scoped to the current data version. Cache hits return a deep copy
    so callers can mutate results without corrupting later reads.
    """
    with _LOCK:
        versioned = (_VERSION, *key)
        if versioned in _CACHE:
            return copy.deepcopy(_CACHE[versioned])
    value = compute()
    with _LOCK:
        if versioned[0] != _VERSION:
            return value  # data changed while computing; result is already stale
        if len(_CACHE) >= MAX_ENTRIES:
            for stale in list(_CACHE)[: MAX_ENTRIES // 4]:
                _CACHE.pop(stale, None)
        _CACHE[versioned] = copy.deepcopy(value)
    return value

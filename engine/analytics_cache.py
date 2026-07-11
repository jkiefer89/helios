"""Bounded, keyed memo cache for per-series analytics results.

Keys are derived from the analyzed series identity — (symbol or model id,
last price date, row count) — so a data refresh or upload that changes a
series naturally produces a new key. The refresh/upload/model-edit paths also
clear the whole cache via :func:`invalidate`, which covers edits that keep
the row count and last date unchanged (e.g. reweighting a model).

Values are deep-copied on both put and get so cached payloads can never be
mutated by callers. The store is size-bounded with LRU eviction; there is no
TTL by design.
"""
from __future__ import annotations

import copy
import threading
from collections import OrderedDict
from typing import Any

import pandas as pd

MAX_ENTRIES = 256

_LOCK = threading.RLock()
_STORE: "OrderedDict[tuple, Any]" = OrderedDict()
# Bumped on every invalidate(). A slow computation that began BEFORE an
# invalidation must not publish its (now stale) result AFTER it — callers
# capture generation() up front and pass it to put(..., if_generation=...)
# (review finding: an in-flight /api/model/forward re-published an anchor
# computed from pre-edit holdings after a model edit invalidated the cache).
_GENERATION = 0


def series_key(target_id: str, close: pd.Series | None) -> tuple | None:
    """Cache key (target id, last price date, row count, last close).

    The last close is included because an intraday refresh revises today's
    partial bar in place — same date, same row count, different price — and
    without it the radar kept serving the pre-revision candidate all day
    (review finding)."""
    if close is None or len(close) == 0:
        return None
    try:
        last_px = round(float(close.iloc[-1]), 6)
    except (TypeError, ValueError):
        last_px = None
    return (str(target_id), str(close.index[-1]), int(len(close)), last_px)


def get(namespace: str, key: tuple | None) -> Any:
    """Deep copy of the cached value, or None on a miss (None keys never hit)."""
    if key is None:
        return None
    with _LOCK:
        value = _STORE.get((namespace, key))
        if value is None:
            return None
        _STORE.move_to_end((namespace, key))
        return copy.deepcopy(value)


def generation() -> int:
    """Capture before a slow computation; pass to put(if_generation=...)."""
    with _LOCK:
        return _GENERATION


def put(namespace: str, key: tuple | None, value: Any, if_generation: int | None = None) -> None:
    """Store a value under (namespace, key). None keys and values are ignored.

    With ``if_generation``, the put is dropped when an invalidate() happened
    since that generation was captured — the value was computed from inputs
    that no longer exist."""
    if key is None or value is None:
        return
    with _LOCK:
        if if_generation is not None and if_generation != _GENERATION:
            return
        _STORE[(namespace, key)] = copy.deepcopy(value)
        _STORE.move_to_end((namespace, key))
        while len(_STORE) > MAX_ENTRIES:
            _STORE.popitem(last=False)


def invalidate() -> None:
    """Drop every cached result (called on data refresh/upload/model edits)."""
    global _GENERATION
    with _LOCK:
        _STORE.clear()
        _GENERATION += 1


def size() -> int:
    with _LOCK:
        return len(_STORE)

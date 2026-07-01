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


def series_key(target_id: str, close: pd.Series | None) -> tuple | None:
    """Cache key (target id, last price date, row count); None for empty series."""
    if close is None or len(close) == 0:
        return None
    return (str(target_id), str(close.index[-1]), int(len(close)))


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


def put(namespace: str, key: tuple | None, value: Any) -> None:
    """Store a value under (namespace, key). None keys and values are ignored."""
    if key is None or value is None:
        return
    with _LOCK:
        _STORE[(namespace, key)] = copy.deepcopy(value)
        _STORE.move_to_end((namespace, key))
        while len(_STORE) > MAX_ENTRIES:
            _STORE.popitem(last=False)


def invalidate() -> None:
    """Drop every cached result (called on data refresh/upload/model edits)."""
    with _LOCK:
        _STORE.clear()


def size() -> int:
    with _LOCK:
        return len(_STORE)

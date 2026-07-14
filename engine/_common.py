"""Shared deterministic helpers used across engine modules.

Small, dependency-light utilities that several analytics modules previously
duplicated: warning de-duplication, defensive numeric parsing, price-series
normalisation, and the paper-tracking hit rule shared by Evidence Lab and the
Signal Journal.
"""
from __future__ import annotations

import math
import os
from typing import Any, Iterable

import pandas as pd


def env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    """Read an integer environment variable, clamped into [min_value, max_value]."""
    try:
        value = int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


def dedupe(items: Iterable[str]) -> list[str]:
    """Order-preserving de-duplication that also drops empty entries."""
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def finite(value: Any) -> float | None:
    """Parse ``value`` as a finite float, or return None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def avg(values: Iterable[Any]) -> float | None:
    """Mean of the finite entries in ``values`` rounded to 4 dp, or None."""
    clean = [number for number in (finite(value) for value in values) if number is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 4)


def pct(numerator: int, denominator: int) -> float | None:
    """``numerator / denominator`` as a percentage (2 dp), or None if empty."""
    if denominator <= 0:
        return None
    return round(float(numerator) / denominator * 100.0, 2)


def clean_close(close: pd.Series | None) -> pd.Series:
    """Normalise a close series: numeric, tz-naive, date-normalised, sorted."""
    if close is None:
        return pd.Series(dtype=float)
    out = pd.Series(close).dropna().copy()
    out.index = pd.to_datetime(out.index, errors="coerce")
    out = out[~out.index.isna()]
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    out.index = out.index.normalize()
    return pd.to_numeric(out.sort_index(), errors="coerce").dropna()


def paper_hit(action: str, forward: float | None, alpha: float | None) -> bool | None:
    """Paper-tracking hit rule shared by Evidence Lab and the Signal Journal.

    A directional call is measurable only against benchmark-relative alpha.
    HOLD/REVIEW are abstentions and are measured separately by
    :func:`hold_preserved`; missing benchmark evidence is never replaced with
    an absolute return.
    """
    action = str(action or "").upper()
    if action in {"HOLD", "REVIEW"} or alpha is None:
        return None
    if action in {"SELL", "REDUCE", "UNDERWEIGHT"}:
        return alpha < 0
    if action in {"BUY", "ADD", "OVERWEIGHT"}:
        return alpha > 0
    return None


def hold_preserved(action: str, forward: float | None, tolerance_pct: float = 1.0) -> bool | None:
    """Whether a HOLD/REVIEW abstention stayed inside a disclosed move band."""
    if str(action or "").upper() not in {"HOLD", "REVIEW"} or forward is None:
        return None
    return abs(float(forward)) <= abs(float(tolerance_pct))


def journal_paper_hit(entry: dict[str, Any]) -> bool | None:
    """Apply the paper-hit rule to a measured Signal Journal entry dict."""
    if entry.get("forward_status") != "measured":
        return None
    return paper_hit(
        str(entry.get("action_label") or ""),
        finite(entry.get("forward_result_pct")),
        finite(entry.get("alpha_pct")),
    )


def journal_hold_preserved(entry: dict[str, Any], tolerance_pct: float = 1.0) -> bool | None:
    """Apply the HOLD-preservation rule to a measured journal entry."""
    if entry.get("forward_status") != "measured":
        return None
    return hold_preserved(
        str(entry.get("action_label") or ""),
        finite(entry.get("forward_result_pct")),
        tolerance_pct,
    )

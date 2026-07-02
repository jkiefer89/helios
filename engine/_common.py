"""Shared deterministic helpers used across engine modules.

Small, dependency-light utilities that several analytics modules previously
duplicated: warning de-duplication, defensive numeric parsing, price-series
normalisation, and the paper-tracking hit rule shared by Evidence Lab and the
Signal Journal.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

import pandas as pd


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

    Evidence is alpha when a benchmark result exists, otherwise the raw forward
    return. Directional actions must land on the right side of zero; hold-style
    actions tolerate a small drawdown.
    """
    reference = alpha if alpha is not None else forward
    if reference is None:
        return None
    action = str(action or "").upper()
    if action in {"SELL", "REDUCE", "UNDERWEIGHT"}:
        return reference < 0
    if action in {"HOLD", "REVIEW"}:
        return reference >= -1.0
    return reference > 0


def journal_paper_hit(entry: dict[str, Any]) -> bool | None:
    """Apply the paper-hit rule to a measured Signal Journal entry dict."""
    if entry.get("forward_status") != "measured":
        return None
    return paper_hit(
        str(entry.get("action_label") or ""),
        finite(entry.get("forward_result_pct")),
        finite(entry.get("alpha_pct")),
    )

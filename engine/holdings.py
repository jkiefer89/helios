"""Fund look-through: see *inside* an ETF/mutual fund, and roll exposures up.

This is the first piece of Helios's forward-looking spine. Where the price
engine treats a fund as an opaque NAV series, this module pulls the fund's real
holdings from SEC EDGAR (engine/edgar.py), normalizes them, and rolls them up to
a model-level exposure so downstream forward analytics (valuation, earnings,
news, policy) can operate on what the model *actually owns* — including a
newly-launched fund with no price history, because N-PORT filings exist for the
fund regardless of any price track record.

Everything here is offline-safe: a network or resolution failure returns a
``LookThrough`` with ``resolved=False`` and a warning. Nothing is fabricated and
no synthetic composition is ever invented.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from . import edgar

# Cap how many EDGAR resolutions one model roll-up may trigger, and how long it
# may run, so a large model can't pin a request thread on sequential fetches.
_RESOLVE_BUDGET = 40
_RESOLVE_TIME_BUDGET_S = 18.0
_MAX_POSITIONS = 200

_DEFAULT_CLIENT: "edgar.EdgarClient | None" = None
_DEFAULT_LOCK = threading.RLock()

# Per-symbol look-through cache (bounded). Stores resolved LookThrough objects so
# repeated model analyses don't refetch the same fund within a session.
_CACHE: dict[str, "LookThrough"] = {}
_CACHE_LOCK = threading.RLock()
_CACHE_MAX = 256


def default_client() -> "edgar.EdgarClient":
    global _DEFAULT_CLIENT
    with _DEFAULT_LOCK:
        if _DEFAULT_CLIENT is None:
            _DEFAULT_CLIENT = edgar.EdgarClient()
        return _DEFAULT_CLIENT


def set_default_client(client) -> None:
    """Override the process EDGAR client (used by tests to inject a fake)."""
    global _DEFAULT_CLIENT
    with _DEFAULT_LOCK:
        _DEFAULT_CLIENT = client
    with _CACHE_LOCK:
        _CACHE.clear()


@dataclass
class LookThrough:
    symbol: str
    resolved: bool
    kind: str               # "fund" | "stock" | "unresolved"
    source: str             # "sec_nport" | "leaf" | "none"
    cik: str = ""
    series_id: str = ""
    as_of: str = ""
    total_net_assets: float | None = None
    positions: list = field(default_factory=list)   # list[dict]
    former_names: list = field(default_factory=list)
    parse_errors: int = 0       # positions the filing carried but we could not parse
    truncated: bool = False     # filing exceeded the position cap
    warning: str = ""


# --------------------------------------------------------------------------- #
# Single-symbol look-through
# --------------------------------------------------------------------------- #
def fetch_lookthrough(symbol: str, client=None, use_cache: bool = True) -> LookThrough:
    """Resolve one symbol to its underlying holdings.

    A fund -> its N-PORT positions. A stock -> a transparent leaf (no inner
    holdings; it *is* the analyzable security). Anything unresolved -> a flagged
    ``unresolved`` result with a warning, never a fabricated composition.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return LookThrough(symbol="", resolved=False, kind="unresolved", source="none",
                           warning="Empty ticker symbol.")
    if use_cache:
        with _CACHE_LOCK:
            if sym in _CACHE:
                return _CACHE[sym]

    client = client or default_client()
    result = _resolve_uncached(sym, client)

    if use_cache and result.resolved:
        # Only cache SUCCESSFUL look-throughs. The old kind != "unresolved"
        # guard permanently cached funds whose N-PORT fetch FAILED (they come
        # back kind='fund' with resolved=False), freezing a transient SEC
        # failure into a forever-empty look-through (review finding).
        with _CACHE_LOCK:
            if sym not in _CACHE and len(_CACHE) >= _CACHE_MAX:
                _CACHE.pop(next(iter(_CACHE)), None)
            _CACHE[sym] = result
    return result


def _resolve_uncached(sym: str, client) -> LookThrough:
    try:
        res = client.resolve(sym)
    except edgar.EdgarError as exc:
        return LookThrough(symbol=sym, resolved=False, kind="unresolved", source="none",
                           warning=str(exc))

    if res.kind == "stock":
        return LookThrough(symbol=sym, resolved=True, kind="stock", source="leaf",
                           cik=res.cik, warning="")

    # Fund: pull submissions (for former names + filing list) then N-PORT.
    former: list = []
    try:
        subs = client.get_submissions(res.cik)
        former = client.former_names(subs)
    except edgar.EdgarError:
        subs = None
    try:
        report = client.fetch_nport(res, submissions=subs)
    except edgar.EdgarError as exc:
        return LookThrough(symbol=sym, resolved=False, kind="fund", source="none",
                           cik=res.cik, series_id=res.series_id, former_names=former,
                           warning=str(exc))

    positions = _normalize_positions(report.positions)
    return LookThrough(
        symbol=sym, resolved=True, kind="fund", source="sec_nport",
        cik=res.cik, series_id=res.series_id, as_of=report.as_of,
        total_net_assets=report.total_net_assets, positions=positions,
        former_names=former, parse_errors=report.parse_errors,
        truncated=report.truncated, warning="",
    )


def _normalize_positions(positions) -> list[dict]:
    out = []
    for p in positions[:_MAX_POSITIONS]:
        out.append({
            "name": p.name,
            "ticker": p.ticker,
            "cusip": p.cusip,
            "isin": p.isin,
            "weight_pct": p.weight_pct,
            "value_usd": p.value_usd,
            "asset_class": p.asset_label,
            "asset_cat_code": p.asset_cat,
            "identified": bool(p.ticker),
        })
    return out


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def summarize(lt: LookThrough) -> dict:
    """Composition summary for a single fund look-through.

    ``covered_weight_pct`` is the share of the fund's NAV represented by the
    listed positions; ``identified_weight_pct`` is the subset that carries a
    ticker. Anything not represented (cash, derivatives, unparsed or
    beyond-cap positions) is surfaced as ``uncovered_weight_pct`` plus an
    explicit warning rather than being implied to be zero.
    """
    positions = lt.positions or []
    covered = sum((p["weight_pct"] or 0.0) for p in positions)
    matched = sum((p["weight_pct"] or 0.0) for p in positions if p["identified"])
    uncovered = round(max(0.0, 100.0 - covered), 2)
    by_class: dict[str, float] = {}
    for p in positions:
        by_class[p["asset_class"]] = by_class.get(p["asset_class"], 0.0) + (p["weight_pct"] or 0.0)
    hhi = sum(((p["weight_pct"] or 0.0) / 100.0) ** 2 for p in positions)
    top = [
        {"name": p["name"], "ticker": p["ticker"], "weight_pct": round(p["weight_pct"] or 0.0, 3),
         "asset_class": p["asset_class"]}
        for p in positions[:15]
    ]
    warnings = []
    if uncovered > 1.0:
        warnings.append(
            f"{uncovered}% of net assets are not represented in the listed positions "
            f"(cash, derivatives, or holdings beyond the top {_MAX_POSITIONS})."
        )
    if lt.parse_errors:
        warnings.append(f"{lt.parse_errors} position(s) in the filing could not be parsed and are excluded.")
    if lt.truncated:
        warnings.append(f"Filing exceeded {_MAX_POSITIONS}+ positions; the list is truncated.")
    return {
        "n_positions": len(positions),
        "covered_weight_pct": round(covered, 2),
        "identified_weight_pct": round(matched, 2),
        "uncovered_weight_pct": uncovered,
        "parse_errors": lt.parse_errors,
        "asset_class_weights_pct": {k: round(v, 2) for k, v in
                                    sorted(by_class.items(), key=lambda kv: kv[1], reverse=True)},
        "lookthrough_hhi": round(hhi, 4),
        "top_holdings": top,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
# Model-level roll-up
# --------------------------------------------------------------------------- #
def model_lookthrough(model, client=None, budget: int = _RESOLVE_BUDGET) -> dict:
    """Roll every holding's look-through up to a single model exposure.

    For each holding (weighted ``w`` of the model):
      * fund we can see into -> each underlying position contributes
        ``w * pos_weight`` to the combined exposure (looked-through),
      * stock leaf -> contributes ``w`` to itself (transparent; no inner book),
      * fund we could not resolve/fetch -> recorded as uncovered (NOT invented).

    Returns combined exposure plus a ``coverage`` provenance block describing how
    much model weight is transparently characterized vs still opaque.
    """
    client = client or default_client()
    holdings = list(getattr(model, "holdings", []) or [])
    total_w = sum(max(0.0, float(h.weight)) for h in holdings) or 1.0

    combined: dict[str, dict] = {}   # key -> {name, ticker, asset_class, weight_pct}
    by_class: dict[str, float] = {}
    looked_through_w = leaf_w = uncovered_w = 0.0
    per_holding, unresolved, as_of_dates = [], [], []
    resolves = 0
    deadline = time.monotonic() + _RESOLVE_TIME_BUDGET_S

    for h in holdings:
        w = max(0.0, float(h.weight)) / total_w  # holding share of the model (0..1)
        if w <= 0:
            continue
        if resolves >= budget or time.monotonic() > deadline:
            lt = LookThrough(symbol=h.ticker, resolved=False, kind="unresolved", source="none",
                             warning="Look-through budget reached for this analysis.")
        else:
            lt = fetch_lookthrough(h.ticker, client=client)
            resolves += 1

        intra = 0.0
        if lt.kind == "fund" and lt.resolved and lt.positions:
            intra = min(1.0, max(0.0, sum((p["weight_pct"] or 0.0) for p in lt.positions) / 100.0))
        if lt.kind == "fund" and lt.resolved and intra > 0:
            # Only the represented share of the fund is "looked through"; the
            # unseen remainder (cash/derivatives/unparsed) counts as uncovered so
            # composition coverage can't overstate what we actually see.
            looked_through_w += w * intra
            uncovered_w += w * (1.0 - intra)
            if lt.as_of:
                as_of_dates.append(lt.as_of)
            for p in lt.positions:
                contrib = w * ((p["weight_pct"] or 0.0) / 100.0)
                if contrib <= 0:
                    continue
                _accumulate(combined, p["name"], p["ticker"], p["asset_class"], contrib,
                            cusip=p.get("cusip", ""))
                by_class[p["asset_class"]] = by_class.get(p["asset_class"], 0.0) + contrib
            state = "looked_through"
        elif lt.kind == "stock" and lt.resolved:
            leaf_w += w
            _accumulate(combined, lt.symbol, lt.symbol, "Equity (common)", w)
            by_class["Equity (common)"] = by_class.get("Equity (common)", 0.0) + w
            state = "leaf"
        else:
            uncovered_w += w
            unresolved.append({"ticker": h.ticker, "reason": lt.warning or "could not look through"})
            state = "uncovered"

        per_holding.append({
            "ticker": h.ticker, "weight_pct": round(w * 100.0, 3), "state": state,
            "kind": lt.kind, "as_of": lt.as_of, "n_positions": len(lt.positions),
            "intra_covered_pct": round(intra * 100.0, 2) if lt.kind == "fund" else None,
            "parse_errors": lt.parse_errors,
        })

    underlying = sorted(combined.values(), key=lambda d: d["weight_pct"], reverse=True)
    hhi = sum((d["weight_pct"] / 100.0) ** 2 for d in underlying)
    underlyings_full = [
        {"name": d["name"], "ticker": d["ticker"], "cusip": d.get("cusip", ""),
         "weight_pct": round(d["weight_pct"], 4), "asset_class": d["asset_class"]}
        for d in underlying[:300]   # bound JSON/CMA work; covers the material weight
    ]
    coverage = {
        "n_holdings": len(holdings),
        "looked_through_pct": round(looked_through_w * 100.0, 2),
        "leaf_pct": round(leaf_w * 100.0, 2),
        "uncovered_pct": round(uncovered_w * 100.0, 2),
        "composition_coverage_pct": round((looked_through_w + leaf_w) * 100.0, 2),
        "n_resolved": resolves,
        "unresolved": unresolved,
        "as_of_range": _date_range(as_of_dates),
    }
    return {
        "exposure": {
            "n_underlying": len(underlying),
            "asset_class_weights_pct": {k: round(v * 100.0, 2) for k, v in
                                        sorted(by_class.items(), key=lambda kv: kv[1], reverse=True)},
            "lookthrough_hhi": round(hhi, 4),
            "top_holdings": underlyings_full[:25],
            "underlyings": underlyings_full,
        },
        "per_holding": per_holding,
        "coverage": coverage,
    }


def _accumulate(combined: dict, name: str, ticker: str, asset_class: str, contrib_frac: float,
                cusip: str = "") -> None:
    key = (ticker or cusip or name or "").upper() or name
    slot = combined.get(key)
    add = contrib_frac * 100.0
    if slot is None:
        combined[key] = {"name": name, "ticker": ticker, "cusip": cusip,
                         "asset_class": asset_class, "weight_pct": add}
    else:
        slot["weight_pct"] += add
        if not slot["ticker"] and ticker:
            slot["ticker"] = ticker
        if not slot.get("cusip") and cusip:
            slot["cusip"] = cusip


def _date_range(dates: list[str]) -> dict:
    clean = sorted(d for d in dates if d)
    if not clean:
        return {"oldest": "", "newest": ""}
    return {"oldest": clean[0], "newest": clean[-1]}

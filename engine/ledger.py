"""Minimum-viable portfolio accounting: fills, snapshots, real TWR.

The review's sharpest structural point: Helios models are tickers+weights, so
nothing in the terminal could report an ACTUAL return, fee, or implementation
shortfall. This module closes the loop with the smallest honest layer that
works for an operator who executes externally:

    custodian CSV exports (fills + positions/cash snapshots)
        -> idempotent import (dedupe keys; re-imports are no-ops)
        -> Modified-Dietz sub-period returns chain-linked to TWR (gross + net)
        -> side-by-side with the model's weight-rescaled PAPER series
        -> per-decision implementation shortfall when fills carry decision ids

Honesty rules: unparsed rows are returned as warnings, never dropped silently;
non-trade activity (dividends, interest) is counted and disclosed; deposits/
withdrawals are the external flows Dietz needs — without at least two
snapshots the answer is "insufficient_snapshots", not a fabricated return.
Every actual number is labeled actual; every paper number stays labeled paper.
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from . import data, persistence, portfolio

# Custodian export column aliases (same loose-matching pattern as model upload).
_DATE_ALIASES = ("trade date", "date", "trade_date", "transaction date", "activity date", "run date")
_SETTLE_ALIASES = ("settle date", "settlement date", "settle_date")
_TICKER_ALIASES = ("symbol", "ticker", "security", "security id")
_SIDE_ALIASES = ("side", "action", "transaction type", "type", "activity", "description",
                 "buy/sell", "buysell")
_SHARES_ALIASES = ("shares", "quantity", "qty", "units")
_PRICE_ALIASES = ("price", "fill price", "execution price", "trade price", "price ($)")
_FEES_ALIASES = ("fees", "commission", "commission & fees", "fees & comm", "fee")
_AMOUNT_ALIASES = ("amount", "net amount", "amount ($)", "net amount ($)",
                   "netcash", "net cash", "proceeds")
_MV_ALIASES = ("market value", "value", "market value ($)", "current value")
_COST_ALIASES = ("cost basis", "cost", "cost basis total")

_BUY_WORDS = ("buy", "bot", "bought", "purchase", "reinvest")
# Sub-type resolution for non-trade activity (dividends, interest, account
# fees, taxes) — persisted as typed rows with their dollar amounts so actual
# economics are DISCLOSED even before they enter return math (review finding:
# "classified then skipped" meant net/gross labels could mislead).
_NON_TRADE_SUBTYPES = (
    ("dividend", "dividend"), ("interest", "interest"), ("reinvest", "dividend"),
    ("fee", "fee"), ("tax", "tax"), ("adr", "fee"), ("spin", "corporate_action"),
    ("split", "corporate_action"),
)
_SELL_WORDS = ("sell", "sld", "sold", "sale")
_DEPOSIT_WORDS = ("deposit", "contribution", "wire in", "transfer in", "journal in", "ach in", "funds received")
_WITHDRAW_WORDS = ("withdraw", "distribution", "wire out", "transfer out", "journal out", "ach out", "disbursement")
_NON_TRADE_WORDS = ("dividend", "interest", "reinvestment", "fee", "tax", "adr", "spin", "split")

_CASH_TICKERS = {"CASH", "USD", "SWEEP", "CORE", "SPAXX", "FDRXX", "CASH & CASH INVESTMENTS"}


def _pick(cols: dict[str, str], aliases) -> str | None:
    for alias in aliases:
        if alias in cols:
            return cols[alias]
    for alias in aliases:
        for low, orig in cols.items():
            if alias in low:
                return orig
    return None


def _num(value) -> float | None:
    if value is None:
        return None
    text = re.sub(r"[$,()\s]", "", str(value))
    if not text or text.lower() in {"nan", "none", "-", "--"}:
        return None
    try:
        num = float(text)
    except ValueError:
        return None
    # Custodians print negatives as (1,234.56) — the paren strip loses the sign.
    if "(" in str(value):
        num = -abs(num)
    return num


def _classify_side(raw: str) -> str:
    text = (raw or "").strip().lower()
    for words, side in ((_BUY_WORDS, "buy"), (_SELL_WORDS, "sell"),
                        (_DEPOSIT_WORDS, "deposit"), (_WITHDRAW_WORDS, "withdraw")):
        if any(word in text for word in words):
            return side
    if any(word in text for word in _NON_TRADE_WORDS):
        return "non_trade"
    return "unknown"


def parse_fills_csv(raw: bytes, account_id: str, source_filename: str = "") -> dict[str, Any]:
    """Parse a custodian activity/fills export. Nothing is dropped silently."""
    account_id = (account_id or "").strip()
    if not account_id:
        raise ValueError("account_id is required for a fills import.")
    df = pd.read_csv(io.BytesIO(raw), nrows=50_000)
    cols = {str(c).lower().strip(): c for c in df.columns}
    date_col = _pick(cols, _DATE_ALIASES)
    side_col = _pick(cols, _SIDE_ALIASES)
    if date_col is None or side_col is None:
        raise ValueError(
            "Fills CSV needs a date column (Trade Date/Date) and an action column "
            "(Side/Action/Transaction Type). Found: " + ", ".join(str(c) for c in df.columns))
    ticker_col = _pick(cols, _TICKER_ALIASES)
    shares_col = _pick(cols, _SHARES_ALIASES)
    price_col = _pick(cols, _PRICE_ALIASES)
    fees_col = _pick(cols, _FEES_ALIASES)
    amount_col = _pick(cols, _AMOUNT_ALIASES)
    settle_col = _pick(cols, _SETTLE_ALIASES)
    decision_col = _pick(cols, ("decision id", "decision_id", "decision"))
    exec_col = _pick(cols, ("exec id", "execution id", "order id", "confirm", "confirmation",
                            "ibexecid", "ib exec id", "exec_id", "tradeid", "trade id"))

    today_plus = pd.Timestamp(datetime.now(timezone.utc).date()) + pd.Timedelta(days=1)
    fills: list[dict[str, Any]] = []
    warnings: list[str] = []
    skipped_non_trade = 0
    for index, row in df.iterrows():
        date = pd.to_datetime(row[date_col], errors="coerce")
        if pd.isna(date):
            warnings.append(f"Row {index + 2}: unreadable date '{row[date_col]}' — skipped.")
            continue
        if date > today_plus:
            warnings.append(f"Row {index + 2}: future-dated {date.date()} — rejected (broken export?).")
            continue
        raw_side = str(row[side_col])
        side = _classify_side(raw_side)
        non_trade_subtype = ""
        if side == "non_trade":
            skipped_non_trade += 1   # counter kept: "non-trade rows recorded"
            text = raw_side.strip().lower()
            non_trade_subtype = next((sub for word, sub in _NON_TRADE_SUBTYPES
                                      if word in text), "other")
        if side == "unknown":
            warnings.append(f"Row {index + 2}: unrecognized action '{row[side_col]}' — skipped; "
                            "map it manually if it is a trade or a cash flow.")
            continue
        ticker = (data.clean_symbol(str(row[ticker_col]), fallback="")
                  if ticker_col is not None and pd.notna(row[ticker_col]) else "")
        shares = _num(row[shares_col]) if shares_col else None
        price = _num(row[price_col]) if price_col else None
        fees = _num(row[fees_col]) if fees_col else None
        amount = _num(row[amount_col]) if amount_col else None
        if side == "non_trade":
            nt_ticker = (data.clean_symbol(str(row[ticker_col]), fallback="")
                         if ticker_col is not None and pd.notna(row[ticker_col]) else "")
            nt_amount = _num(row[amount_col]) if amount_col else None
            nt_key = (f"{account_id}|{date.date().isoformat()}|{nt_ticker}|non_trade"
                      f"|{non_trade_subtype}|{nt_amount if nt_amount is not None else 'na'}|{index}")
            fills.append({
                "fill_id": hashlib.sha256((nt_key + "|id").encode()).hexdigest()[:32],
                "account_id": account_id,
                "trade_date": date.date().isoformat(),
                "ticker": nt_ticker,
                "side": "non_trade",
                "shares": 0.0, "price": 0.0, "fees": 0.0,
                "amount": nt_amount,
                "source_file": source_filename,
                "metadata": {"subtype": non_trade_subtype, "raw_action": raw_side.strip()[:60]},
                "dedupe_key": hashlib.sha256(nt_key.encode()).hexdigest()[:40],
            })
            continue
        if side in {"buy", "sell"}:
            if not ticker:
                warnings.append(f"Row {index + 2}: {side} with no ticker — skipped.")
                continue
            if shares is not None:
                # IBKR-style exports sign sell quantities negative; the side
                # column already carries direction, so magnitude is the fact.
                shares = abs(shares)
            if (shares is None or shares == 0) and amount and price:
                shares = abs(amount) / price
            if (price is None or price <= 0) and amount and shares:
                price = abs(amount) / abs(shares)
            if not shares or not price or shares <= 0 or price <= 0:
                warnings.append(f"Row {index + 2}: {side} {ticker} missing shares/price — skipped.")
                continue
        else:  # deposit / withdraw — the EXTERNAL flows Dietz needs
            flow = amount if amount is not None else (shares or 0) * (price or 0)
            if not flow:
                warnings.append(f"Row {index + 2}: {side} with no amount — skipped.")
                continue
            amount = abs(float(flow)) * (1 if side == "deposit" else -1)
            shares, price = 0.0, 0.0
        trade_date = date.date().isoformat()
        exec_id = (str(row[exec_col]).strip()
                   if exec_col is not None and pd.notna(row[exec_col]) else "")
        key_raw = (f"{account_id}|{trade_date}|{ticker}|{side}|{shares or 0:.6f}"
                   f"|{price or 0:.6f}|{amount or 0:.2f}|{exec_id}")
        fills.append({
            "fill_id": hashlib.sha256((key_raw + "|id").encode()).hexdigest()[:32],
            "account_id": account_id,
            "trade_date": trade_date,
            "settle_date": (str(pd.to_datetime(row[settle_col], errors="coerce").date())
                            if settle_col is not None and pd.notna(pd.to_datetime(row[settle_col], errors="coerce")) else ""),
            "ticker": ticker,
            "side": side,
            "shares": abs(float(shares or 0.0)),
            "price": float(price or 0.0),
            "fees": abs(float(fees or 0.0)),
            "amount": amount,
            "source_file": source_filename,
            "decision_id": (str(row[decision_col]).strip()
                            if decision_col is not None and pd.notna(row[decision_col]) else ""),
            "metadata": ({"exec_id": exec_id} if exec_id else {}),
            "dedupe_key": hashlib.sha256(key_raw.encode()).hexdigest()[:40],
        })
    if not fills and not warnings:
        raise ValueError("No trade or cash-flow rows found in the file.")
    return {"fills": fills, "warnings": warnings, "skipped_non_trade": skipped_non_trade}


def import_fills(raw: bytes, account_id: str, source_filename: str = "") -> dict[str, Any]:
    parsed = parse_fills_csv(raw, account_id, source_filename)
    store = persistence.get_store()
    store.upsert_ledger_account(account_id)
    result = store.record_fills(parsed["fills"])
    warnings = list(parsed["warnings"])
    if result.get("duplicates", 0) and not any(
            f.get("metadata", {}).get("exec_id") for f in parsed["fills"]):
        warnings.append("Duplicates collapsed WITHOUT an execution-id column — identical "
                        "same-day fills merge; include an Exec/Order Id column if these "
                        "were distinct executions.")
    return {
        "account_id": account_id,
        "parsed": len(parsed["fills"]),
        "inserted": result.get("inserted", 0),
        "duplicates": result.get("duplicates", 0),
        # Counter renamed in meaning: these rows are now RECORDED as typed
        # activity (dividend/interest/fee/tax), not dropped.
        "skipped_non_trade": parsed["skipped_non_trade"],
        "non_trade_recorded": parsed["skipped_non_trade"],
        "warnings": warnings[:25],
    }


def parse_positions_csv(raw: bytes, account_id: str, as_of: str = "") -> dict[str, Any]:
    """Parse a custodian positions export into one dated account snapshot."""
    account_id = (account_id or "").strip()
    if not account_id:
        raise ValueError("account_id is required for a positions import.")
    df = pd.read_csv(io.BytesIO(raw), nrows=5_000)
    cols = {str(c).lower().strip(): c for c in df.columns}
    ticker_col = _pick(cols, _TICKER_ALIASES)
    mv_col = _pick(cols, _MV_ALIASES)
    if ticker_col is None or mv_col is None:
        raise ValueError(
            "Positions CSV needs a Symbol column and a Market Value column. Found: "
            + ", ".join(str(c) for c in df.columns))
    shares_col = _pick(cols, _SHARES_ALIASES)
    price_col = _pick(cols, _PRICE_ALIASES)
    cost_col = _pick(cols, _COST_ALIASES)
    as_of_clean = (as_of or "").strip()[:10] or datetime.now(timezone.utc).date().isoformat()
    if as_of_clean > (datetime.now(timezone.utc).date() + pd.Timedelta(days=1)).isoformat()[:10]:
        raise ValueError(f"as_of {as_of_clean} is in the future.")

    positions: list[dict[str, Any]] = []
    warnings: list[str] = []
    cash = 0.0
    saw_cash = False
    for index, row in df.iterrows():
        raw_ticker = str(row[ticker_col] or "").strip().upper()
        mv = _num(row[mv_col])
        if mv is None:
            warnings.append(f"Row {index + 2}: unreadable market value — skipped.")
            continue
        if raw_ticker in _CASH_TICKERS or "CASH" in raw_ticker or "MONEY MARKET" in raw_ticker:
            cash += mv
            saw_cash = True
            continue
        ticker = data.clean_symbol(raw_ticker, fallback="")
        if not ticker:
            warnings.append(f"Row {index + 2}: unrecognized symbol '{raw_ticker}' — kept in "
                            "the total value but it will not resolve to an instrument.")
            positions.append({"ticker": raw_ticker[:16], "shares": 0.0, "market_value": mv})
            continue
        positions.append({
            "ticker": ticker,
            "shares": float(_num(row[shares_col]) or 0.0) if shares_col else 0.0,
            "price": _num(row[price_col]) if price_col else None,
            "market_value": mv,
            "cost_basis": _num(row[cost_col]) if cost_col else None,
        })
    if not positions and not saw_cash:
        raise ValueError("No position or cash rows found in the file.")
    total_value = cash + sum(float(p.get("market_value") or 0.0) for p in positions)
    snapshot = {
        "account_id": account_id,
        "as_of": as_of_clean,
        "cash": cash if saw_cash else None,
        "total_value": round(total_value, 2),
        "source": "positions_csv",
    }
    return {"snapshot": snapshot, "positions": positions, "warnings": warnings}


def import_positions(raw: bytes, account_id: str, as_of: str = "") -> dict[str, Any]:
    parsed = parse_positions_csv(raw, account_id, as_of)
    store = persistence.get_store()
    store.upsert_ledger_account(account_id)
    store.record_account_snapshot(parsed["snapshot"], parsed["positions"])
    return {
        "account_id": account_id,
        "as_of": parsed["snapshot"]["as_of"],
        "total_value": parsed["snapshot"]["total_value"],
        "cash": parsed["snapshot"]["cash"],
        "n_positions": len(parsed["positions"]),
        "warnings": parsed["warnings"][:25],
    }


# --------------------------------------------------------------------------- #
# Real performance: Modified-Dietz sub-periods chain-linked to TWR
# --------------------------------------------------------------------------- #
def account_performance(account_id: str) -> dict[str, Any]:
    """ACTUAL account return between snapshots, gross and net of fees.

    Modified Dietz per sub-period: r = (V1 - V0 - F) / (V0 + sum(w_i * f_i))
    with F the external flows (deposits/withdrawals) and w_i the fraction of
    the period each flow was invested. Sub-periods chain-link to TWR-style
    returns. Fees observed on fills are reported and added back for gross.
    """
    store = persistence.get_store()
    snaps = store.account_snapshots(account_id)
    if len(snaps) < 2:
        return {
            "status": "insufficient_snapshots",
            "account_id": account_id,
            "snapshot_count": len(snaps),
            "note": ("At least two dated position/cash snapshots are required to measure an "
                     "actual return — upload a positions CSV per statement period."),
        }
    fills = store.fills(account_id, limit=20000)
    warnings: list[str] = []
    if len(fills) >= 20000:
        warnings.append("Fill history hit the 20,000-row query cap — older activity is "
                        "excluded and these results are unreliable; archive or split the account.")
    flows = [f for f in fills if f["side"] in {"deposit", "withdraw"} and f.get("amount") is not None]
    trade_fees = [(f["trade_date"], float(f.get("fees") or 0.0)) for f in fills
                  if f["side"] in {"buy", "sell"}]
    # Non-trade economics (dividends, interest, account fees, taxes) are
    # DISCLOSED per period — never silently absent from a "net" label. They
    # are NOT Dietz flows: income already lands inside the snapshot values.
    non_trade = [f for f in fills if f["side"] == "non_trade"]
    span_start, span_end = pd.Timestamp(snaps[0]["as_of"]), pd.Timestamp(snaps[-1]["as_of"])
    stray = [f for f in fills if not (span_start < pd.Timestamp(f["trade_date"]) <= span_end)
             and f["side"] in {"buy", "sell", "deposit", "withdraw"}]
    if stray:
        warnings.append(f"{len(stray)} fill(s) dated outside the snapshot span "
                        f"{snaps[0]['as_of']}–{snaps[-1]['as_of']} are excluded from the "
                        "measured window — upload the covering snapshots.")

    periods = []
    net_chain = gross_chain = 1.0
    total_fees = total_flows = 0.0
    total_income = total_nontrade_costs = 0.0
    for prev, curr in zip(snaps, snaps[1:]):
        t0, t1 = pd.Timestamp(prev["as_of"]), pd.Timestamp(curr["as_of"])
        days = max((t1 - t0).days, 1)
        v0, v1 = float(prev["total_value"]), float(curr["total_value"])
        period_flows = [(pd.Timestamp(f["trade_date"]), float(f["amount"])) for f in flows
                        if t0 < pd.Timestamp(f["trade_date"]) <= t1]
        flow_sum = sum(amount for _, amount in period_flows)
        weighted = sum(amount * ((t1 - date).days / days) for date, amount in period_flows)
        fees = sum(fee for date, fee in trade_fees if t0 < pd.Timestamp(date) <= t1)
        nt_rows = [f for f in non_trade if t0 < pd.Timestamp(f["trade_date"]) <= t1
                   and f.get("amount") is not None]
        income = sum(float(f["amount"]) for f in nt_rows
                     if (f.get("metadata") or {}).get("subtype") in {"dividend", "interest"})
        nt_costs = sum(abs(float(f["amount"])) for f in nt_rows
                       if (f.get("metadata") or {}).get("subtype") in {"fee", "tax"})
        total_income += income
        total_nontrade_costs += nt_costs
        denom = v0 + weighted
        if denom <= 0:
            periods.append({"start": prev["as_of"], "end": curr["as_of"],
                            "status": "unmeasurable", "note": "non-positive Dietz denominator"})
            continue
        r_net = (v1 - v0 - flow_sum) / denom
        r_gross = (v1 + fees - v0 - flow_sum) / denom
        net_chain *= 1 + r_net
        gross_chain *= 1 + r_gross
        total_fees += fees
        total_flows += flow_sum
        # Cash reconciliation control: when BOTH snapshots report cash, the
        # cash delta must be explained by recorded activity — a partial import
        # producing a "measured" return was the review's sharpest ledger point.
        reconciliation: dict = {"status": "uncheckable",
                                "note": "both snapshots need a cash row to reconcile"}
        if prev.get("cash") is not None and curr.get("cash") is not None:
            buys = sum(f["shares"] * f["price"] + f["fees"] for f in fills
                       if f["side"] == "buy" and t0 < pd.Timestamp(f["trade_date"]) <= t1)
            sells = sum(f["shares"] * f["price"] - f["fees"] for f in fills
                        if f["side"] == "sell" and t0 < pd.Timestamp(f["trade_date"]) <= t1)
            expected_delta = flow_sum + sells - buys + income - nt_costs
            actual_delta = float(curr["cash"]) - float(prev["cash"])
            diff = actual_delta - expected_delta
            tolerance = max(abs(v0) * 0.005, 1.0)
            reconciliation = {
                "status": "ok" if abs(diff) <= tolerance else "mismatch",
                "cash_delta_usd": round(actual_delta, 2),
                "explained_usd": round(expected_delta, 2),
                "diff_usd": round(diff, 2),
                "tolerance_usd": round(tolerance, 2),
            }
            if reconciliation["status"] == "mismatch":
                warnings.append(
                    f"Cash reconciliation mismatch {prev['as_of']}–{curr['as_of']}: "
                    f"${diff:,.0f} of cash movement is unexplained by recorded activity "
                    "(missing fills, dividends, or fees?) — the return may be unreliable.")
        periods.append({
            "start": prev["as_of"], "end": curr["as_of"], "days": days,
            "return_net_pct": round(r_net * 100, 4),
            "return_gross_pct": round(r_gross * 100, 4),
            "flows_usd": round(flow_sum, 2), "fees_usd": round(fees, 2),
            "income_usd": round(income, 2), "nontrade_fees_taxes_usd": round(nt_costs, 2),
            "reconciliation": reconciliation,
            "start_value": v0, "end_value": v1, "status": "measured",
        })
    measured = [p for p in periods if p["status"] == "measured"]
    if not measured:
        return {"status": "unmeasurable", "account_id": account_id, "periods": periods,
                "note": "No sub-period had a positive Dietz denominator."}
    # Cash drag estimate: average observed cash weight x (return - T-bill proxy).
    cash_weights = [float(s["cash"]) / float(s["total_value"]) for s in snaps
                    if s.get("cash") is not None and float(s["total_value"]) > 0]
    from . import macro as _macro
    span_days = (pd.Timestamp(snaps[-1]["as_of"]) - pd.Timestamp(snaps[0]["as_of"])).days or 1
    rf_period = _macro.risk_free() * span_days / 365.0
    twr_net = net_chain - 1
    cash_drag = (float(np.mean(cash_weights)) * (twr_net - rf_period)
                 if cash_weights else None)
    return {
        "status": "measured",
        "account_id": account_id,
        "label": "ACTUAL account performance (custodian snapshots + fills)",
        "method": "snapshot-period Modified-Dietz estimate (chain-linked) — not daily TWR",
        "income_usd": round(total_income, 2),
        "nontrade_fees_taxes_usd": round(total_nontrade_costs, 2),
        "warnings": warnings,
        "period": {"start": snaps[0]["as_of"], "end": snaps[-1]["as_of"], "days": span_days},
        "twr_net_pct": round(twr_net * 100, 4),
        "twr_gross_pct": round((gross_chain - 1) * 100, 4),
        "fees_usd": round(total_fees, 2),
        "external_flows_usd": round(total_flows, 2),
        "avg_cash_weight_pct": round(float(np.mean(cash_weights)) * 100, 2) if cash_weights else None,
        "cash_drag_est_pct": round(cash_drag * 100, 4) if cash_drag is not None else None,
        "n_periods": len(measured),
        "periods": periods,
        "basis": ("Modified-Dietz sub-periods between custodian snapshots, chain-linked; "
                  "gross adds back fill commissions only — account-level fees/taxes and "
                  "income appear as disclosed dollars, not in gross. Cash drag is an "
                  "estimate from snapshot cash weights vs the configured risk-free rate."),
    }


def actual_vs_paper(account_id: str) -> dict[str, Any]:
    """Side-by-side: the account's ACTUAL TWR vs the mapped model's PAPER series."""
    actual = account_performance(account_id)
    out: dict[str, Any] = {"actual": actual}
    account = next((a for a in persistence.get_store().ledger_accounts()
                    if a["account_id"] == account_id), None)
    model_id = (account or {}).get("model_id") or ""
    if not model_id:
        out["paper"] = {"status": "no_model_mapped",
                        "note": "Map this account to a model to compare against the research series."}
        return out
    mdl = portfolio.get(model_id)
    if mdl is None:
        out["paper"] = {"status": "model_not_found", "model_id": model_id}
        return out
    if actual.get("status") != "measured":
        out["paper"] = {"status": "awaiting_actual", "model_id": model_id}
        return out
    try:
        ps = portfolio.build_series(mdl, allow_sample=False, allow_simulated=False)
    except ValueError as exc:
        out["paper"] = {"status": "paper_unavailable", "model_id": model_id, "note": str(exc)}
        return out
    close = ps.close.dropna()
    start, end = pd.Timestamp(actual["period"]["start"]), pd.Timestamp(actual["period"]["end"])
    window = close[(close.index >= start) & (close.index <= end)]
    if len(window) < 2:
        out["paper"] = {"status": "insufficient_overlap", "model_id": model_id}
        return out
    paper_return = float(window.iloc[-1] / window.iloc[0] - 1) * 100
    out["paper"] = {
        "status": "measured",
        "model_id": model_id,
        "model_name": mdl.name,
        "label": "PAPER research series (weight-rescaled, cost-free — not a track record)",
        "return_pct": round(paper_return, 4),
        "series_basis": ps.provenance.get("series_basis", ""),
    }
    out["gap_pct"] = round(actual["twr_net_pct"] - paper_return, 4)
    out["gap_note"] = ("actual net minus paper over the same window — fees, cash drag, "
                       "timing, and implementation differences live in this gap")
    return out


def decision_shortfall(account_id: str = "") -> list[dict[str, Any]]:
    """Implementation shortfall for fills linked to decision-journal entries.

    shortfall_bps = signed difference between the average fill price and the
    target's close on the decision date: positive = you paid up / sold cheap.
    Only fills that carry a decision_id are measured — linking is explicit,
    never inferred.
    """
    store = persistence.get_store()
    fills = [f for f in store.fills(account_id, limit=20000) if f.get("decision_id")
             and f["side"] in {"buy", "sell"}]
    if not fills:
        return []
    decisions = {d["decision_id"]: d for d in store.decision_journal(limit=500)}
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for fill in fills:
        grouped.setdefault((fill["decision_id"], fill["ticker"], fill["side"]), []).append(fill)
    out = []
    for (decision_id, ticker, side), rows in grouped.items():
        decision = decisions.get(decision_id)
        if not decision:
            out.append({"decision_id": decision_id, "ticker": ticker, "side": side,
                        "status": "decision_not_found"})
            continue
        # FROZEN anchor first: the journal stored decision_price at record
        # time, and recomputing from current adjusted history let historical
        # shortfall drift after dividends/revisions (review finding). The
        # instrument/ticker guard is load-bearing — a model decision_price is
        # a series level, not this ticker's price.
        anchor_px = None
        anchor_basis = ""
        if (decision.get("target_kind") == "instrument"
                and str(decision.get("target_id") or "").upper() == ticker
                and decision.get("decision_price") is not None):
            anchor_px = float(decision["decision_price"])
            anchor_basis = "frozen decision_price (immutable at record time)"
        if anchor_px is None:
            inst = data.get(ticker)
            if inst is not None and "close" in inst.df:
                close = inst.df["close"].dropna()
                at_or_before = close[close.index <= pd.Timestamp(decision["decision_date"])]
                if len(at_or_before):
                    anchor_px = float(at_or_before.iloc[-1])
                    anchor_basis = ("recomputed from adjusted close history "
                                    "(can drift after dividends/revisions)")
        total_shares = sum(f["shares"] for f in rows)
        if not anchor_px or not total_shares:
            out.append({"decision_id": decision_id, "ticker": ticker, "side": side,
                        "status": "anchor_unavailable"})
            continue
        avg_fill = sum(f["shares"] * f["price"] for f in rows) / total_shares
        signed = 1 if side == "buy" else -1
        shortfall_bps = signed * (avg_fill - anchor_px) / anchor_px * 10_000
        out.append({
            "decision_id": decision_id,
            "ticker": ticker,
            "side": side,
            "status": "measured",
            "n_fills": len(rows),
            "shares": round(total_shares, 4),
            "avg_fill_price": round(avg_fill, 4),
            "decision_date": decision["decision_date"],
            "decision_close": round(anchor_px, 4),
            "anchor_basis": anchor_basis,
            "shortfall_bps": round(shortfall_bps, 1),
            "fees_usd": round(sum(f["fees"] for f in rows), 2),
            "basis": "avg fill vs decision-date close; positive = paid up / sold cheap",
        })
    return sorted(out, key=lambda row: row.get("decision_date") or "", reverse=True)

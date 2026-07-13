"""Analysis blueprint: command center, instrument analysis, strategy evidence,
opportunity radar, evidence lab, and the signal journal."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from flask import Blueprint, request

from engine import (
    backtest, cma, data, evidence_lab, forecast, fundamentals, holdings, indicators, macro,
    macro_events, news, opportunity, portfolio, provenance, regime, sec_events, sentiment,
    signal_journal, signals, strategy,
)

from .core import (
    ANALYSIS_ONLY_DISCLAIMER, _LIVE_SEMAPHORE, _safe_float_arg, _safe_int_arg, app, err, ok,
)

bp = Blueprint("analysis", __name__)


# --------------------------------------------------------------------------- #
# Command Center helpers
# --------------------------------------------------------------------------- #
def _quick_instrument_screen(inst: data.Instrument, market: dict | None = None) -> dict | None:
    """Command-center card for one instrument, scored by the shared engine.

    Delegates to opportunity.instrument_candidate / score_candidate so the
    Command Center and the Opportunity Radar report identical numbers. The
    candidate evidence is memoized per price series inside the engine.
    """
    try:
        candidate = opportunity.instrument_candidate(inst)
    except Exception:
        app.logger.exception("command-center screen failed for %s", inst.symbol)
        return None
    if candidate is None:
        return None
    scored = opportunity.score_candidate(candidate, regime=market)
    return {
        "id": scored["id"],
        "kind": scored["kind"],
        "symbol": scored["symbol"],
        "name": scored["name"],
        "source": scored["source"],
        "action": scored["action"],
        "score": scored["opportunity_score"],
        "risk_score": scored["risk_score"],
        "evidence_score": scored["evidence_score"],
        "expected_return_pct": scored["expected_return_pct"],
        "expected_vol_pct": scored["expected_vol_pct"],
        "max_drawdown_pct": scored["max_drawdown_pct"],
        "strategy_return_pct": scored["strategy_return_pct"],
        "benchmark_return_pct": scored["benchmark_return_pct"],
        "beat_benchmark": scored["beat_benchmark"],
        "reason": scored["reason"],
        "warnings": scored["warnings"][:3],
    }


def _command_center_payload() -> dict:
    instruments = data.all_instruments()
    prov = provenance.universe(instruments)
    market = regime.market_regime(instruments)
    real_instruments = [inst for inst in instruments if provenance.is_real_source(inst.source)]
    cards = [c for inst in real_instruments if (c := _quick_instrument_screen(inst, market))]
    ranked = sorted(cards, key=lambda c: c["score"], reverse=True)
    risks = sorted(cards, key=lambda c: (c["risk_score"], abs(c["max_drawdown_pct"])), reverse=True)

    model_alerts = []
    for mdl in portfolio.all_models():
        try:
            ps = portfolio.build_series(mdl, allow_sample=False, allow_simulated=False)
            pprov = provenance.portfolio(ps.provenance)
            if not pprov["eligible_for_real_research"]:
                model_alerts.append({
                    "id": mdl.id,
                    "name": mdl.name,
                    "severity": "high",
                    "message": f"{mdl.name}: data quality blocked. {pprov['reason']}",
                    "next_step": pprov["required_action"],
                    "eligible_for_real_research": False,
                    "missing_tickers": pprov["missing_tickers"],
                })
                continue
            metrics = indicators.metrics_summary(ps.close)
            source_weights = ps.provenance.get("source_weight_pct", {})
            source_summary = ", ".join(
                f"{src} {weight:.0f}%" for src, weight in sorted(source_weights.items()) if weight
            ) or "live/uploaded history"
            model_alerts.append({
                "id": mdl.id,
                "name": mdl.name,
                "severity": "medium",
                "message": (
                    f"{mdl.name}: vol {metrics['annual_vol_pct']:.1f}%, "
                    f"max drawdown {metrics['max_drawdown_pct']:.1f}%, "
                    f"sources {source_summary}."
                ),
                "next_step": "Open Portfolio Clinic when this model is selected.",
                "eligible_for_real_research": True,
            })
        except Exception as exc:
            model_alerts.append({
                "id": mdl.id,
                "name": mdl.name,
                "severity": "high",
                "message": f"{mdl.name}: data quality blocked ({exc}).",
                "next_step": "Upload real price history for every model holding before running Pro research.",
                "eligible_for_real_research": False,
            })

    research_queue = []
    if ranked:
        top = ranked[0]
        research_queue.append({
            "priority": "high",
            "title": f"Validate {top['symbol']} before any recommendation",
            "detail": "Check data source, forecast quality, historical strategy evidence, and invalidating risks.",
        })
    if risks:
        risk = risks[0]
        research_queue.append({
            "priority": "medium",
            "title": f"Review risk in {risk['symbol']}",
            "detail": f"Risk score {risk['risk_score']}/100 with max drawdown {risk['max_drawdown_pct']:.1f}%.",
        })
    if not ranked and not risks:
        research_queue.append({
            "priority": "high",
            "title": "Demo mode: real research is locked",
            "detail": provenance.DEMO_ACTION,
        })
    if not model_alerts:
        research_queue.append({
            "priority": "medium",
            "title": "Upload a client model for portfolio-level diagnostics",
            "detail": "Use uploaded or live price history for every holding to unlock Pro model alerts.",
        })
    for warning in market.get("warnings", []):
        research_queue.append({"priority": "medium", "title": "Verify regime proxy", "detail": warning})

    return {
        "data_mode": prov["data_mode"],
        "display_label": prov["display_label"],
        "eligible_for_real_research": bool(ranked or risks),
        "reason": "" if ranked or risks else prov["reason"],
        "required_action": "" if ranked or risks else prov["required_action"],
        "data_provenance": {
            "source_counts": prov["source_counts"],
            "warnings": prov["warnings"],
        },
        "regime": {**market, "data_mode": prov["data_mode"], "display_label": prov["display_label"]},
        "top_opportunities": ranked[:5],
        "top_risks": risks[:5],
        "model_alerts": model_alerts[:5],
        "research_queue": research_queue[:6],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    }


# --------------------------------------------------------------------------- #
# Series helpers for the price/indicator chart (downsampled for transport)
# --------------------------------------------------------------------------- #
def _series_payload(close, lookback: int = 400, *, markers: bool = False):
    close = close.dropna()
    ind_df = indicators.indicator_frame(close).tail(lookback)
    dates = [d.strftime("%Y-%m-%d") for d in ind_df.index]

    def col(name):
        return [None if pd.isna(v) else float(v) for v in ind_df[name]]

    # Buy/sell markers where the causal score crosses thresholds.
    marker_rows = []
    if markers:
        sigs = signals.historical_signals(close).tail(lookback)
        prev = 0.0
        for d, s in zip(ind_df.index, sigs):
            if prev <= 0.15 < s:
                marker_rows.append({"date": d.strftime("%Y-%m-%d"), "type": "buy",
                                    "price": float(close.loc[d])})
            elif prev >= -0.05 > s:
                marker_rows.append({"date": d.strftime("%Y-%m-%d"), "type": "sell",
                                    "price": float(close.loc[d])})
            prev = s

    return {
        "dates": dates,
        "close": col("close"),
        "sma50": col("sma50"),
        "sma200": col("sma200"),
        "bb_upper": col("bb_upper"),
        "bb_lower": col("bb_lower"),
        "rsi": col("rsi"),
        "macd": col("macd"),
        "macd_signal": col("macd_signal"),
        "macd_hist": col("macd_hist"),
        "markers": marker_rows,
    }


def _record_instrument_signal(inst: data.Instrument, close: pd.Series, sig: dict, horizon_days: int) -> dict | None:
    try:
        p = provenance.instrument(inst.source, len(close.dropna()))
        return signal_journal.record_signal(
            target_kind="instrument",
            target_id=inst.symbol,
            target_name=inst.name,
            close=close,
            input_close=close,
            signal=sig,
            horizon_days=horizon_days,
            benchmark="SPY",
            source_counts={inst.source: 1},
            eligible_for_real_research=p["eligible_for_real_research"],
            data_mode=p["data_mode"],
            metadata=_signal_evidence_metadata(sig, endpoint="/api/analyze"),
        )
    except Exception:
        return None


def _signal_evidence_metadata(sig: dict, endpoint: str) -> dict:
    """Persist the NEW rating tracks alongside each journal entry so the
    Evidence Lab can score them out-of-sample as forward results measure in —
    the honest, prospective validation of the strategic/macro layers (no
    point-in-time fundamentals exist for a retrospective backtest; pretending
    otherwise would be look-ahead bias)."""
    meta: dict = {"endpoint": endpoint}
    # FULL component breakdown, weights, and dampers: the persisted record
    # was a subset while the UI claimed "exact composite" (review finding).
    # The prose clause is dropped to keep metadata_json compact — everything
    # numeric that produced the rating is here.
    meta["components"] = [
        {"name": c.get("name"), "raw": c.get("raw"),
         "effective_weight": c.get("effective_weight"),
         "contribution": c.get("contribution")}
        for c in (sig.get("components") or [])
    ]
    for field in ("vol_penalty", "mandate_fit", "conviction_pct"):
        if field in sig:
            meta[field] = sig.get(field)
    tactical = sig.get("tactical") or {}
    if tactical:
        meta["tactical_action"] = tactical.get("action")
        meta["tactical_score"] = tactical.get("score")
    strategic = sig.get("strategic") or {}
    if strategic.get("usable"):
        meta["strategic_action"] = strategic.get("action")
        meta["strategic_gap_pp"] = strategic.get("gap_vs_anchor_pct")
        meta["strategic_er_pct"] = strategic.get("expected_return_pct")
    else:
        # A missing leg is RECORDED, never silent: model entries have no
        # fundamentals track, and that fact is part of the evidence.
        meta["strategic_usable"] = False
        if strategic.get("reason"):
            meta["strategic_reason"] = strategic.get("reason")
    if "event_risk_damper" in sig:
        meta["event_risk_damper"] = sig.get("event_risk_damper")
        meta["macro"] = sig.get("macro") or {}
    return meta


def _record_model_signal(mdl: portfolio.Model, ps: portfolio.PortfolioSeries, close: pd.Series, sig: dict, horizon_days: int) -> dict | None:
    try:
        p = provenance.portfolio(ps.provenance)
        return signal_journal.record_signal(
            target_kind="model",
            target_id=mdl.id,
            target_name=mdl.name,
            close=close,
            input_close=close,
            signal=sig,
            horizon_days=horizon_days,
            benchmark=signal_journal.benchmark_for_model(mdl),
            source_counts={str(k): int(v) for k, v in ps.sources.items()},
            eligible_for_real_research=p["eligible_for_real_research"],
            data_mode=p["data_mode"],
            metadata={**_signal_evidence_metadata(sig, endpoint="/api/model/analyze"),
                      "mandate": mdl.mandate_key},
        )
    except Exception:
        return None


def auto_record_daily_signals(max_targets: int = 200) -> dict:
    """Once per UTC day per real-eligible target, record the composite rating
    (score, action, and — for entries from 2026-07-12 onward — the full
    component breakdown, weights, and dampers) in the signal journal.

    Runs from the auto-live loop so prospective evidence accrues even when the
    operator doesn't click — view-triggered-only recording gave the journal a
    usage bias toward whatever was being watched (review finding). CACHED
    inputs only (stored headlines, fundamentals cache, macro snapshot cache):
    this must never block the background loop on a network call. The journal's
    own real-eligibility and freshness guards still apply to every entry.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    already: set[tuple[str, str]] = set()
    for e in signal_journal.list_entries(limit=500):
        if (str(e.get("created_at") or "")[:10] == today
                and (e.get("metadata") or {}).get("endpoint") == "auto_snapshot"):
            already.add((str(e.get("target_kind")), str(e.get("target_id"))))
    recorded = skipped = 0
    macro_snap = macro_events.snapshot_cached()

    for inst in data.all_instruments():
        if recorded >= max_targets:
            break
        if ("instrument", inst.symbol) in already or not provenance.is_real_source(inst.source):
            continue
        close = inst.df["close"].dropna() if "close" in inst.df else pd.Series(dtype=float)
        if len(close) < 60:
            continue
        try:
            fc = forecast.forecast(close, horizon=21)
            sent = sentiment.score_headlines(list(inst.headlines or []))
            fnd = fundamentals.fetch_cached(inst.symbol) or fundamentals.Fundamentals(
                ticker=inst.symbol, source="none")
            fwd = cma.instrument_forward(inst.symbol, fnd)
            ctx = macro_events.build_macro_context(fwd.get("sector") or "", macro_snap)
            sig = signals.evaluate(close, fc, sent, history_days=len(close),
                                   fundamental_result=fwd, macro_context=ctx)
            p = provenance.instrument(inst.source, len(close))
            signal_journal.record_signal(
                target_kind="instrument", target_id=inst.symbol, target_name=inst.name,
                close=close, input_close=close, signal=sig, horizon_days=21,
                benchmark="SPY", source_counts={inst.source: 1},
                eligible_for_real_research=p["eligible_for_real_research"],
                data_mode=p["data_mode"],
                metadata=_signal_evidence_metadata(sig, endpoint="auto_snapshot"),
            )
            recorded += 1
        except Exception:
            skipped += 1  # one bad target never stops the sweep

    for mdl in portfolio.all_models():
        if recorded >= max_targets:
            break
        if ("model", mdl.id) in already:
            continue
        try:
            ps = portfolio.build_series(mdl, allow_sample=False, allow_simulated=False)
            close = ps.close.dropna()
            if len(close) < 60:
                continue
            headlines = []
            for h in mdl.holdings:
                held = data.get(h.ticker)
                if held and held.headlines:
                    headlines.extend(held.headlines[:2])
            sent = sentiment.score_headlines(headlines)
            fc = forecast.forecast(close, horizon=21)
            pmeta = {"n_holdings": len(mdl.holdings),
                     "top_weight": mdl.holdings[0].weight if mdl.holdings else 0,
                     "top_ticker": mdl.holdings[0].ticker if mdl.holdings else "?"}
            ctx = macro_events.build_macro_context("", macro_snap)
            sig = signals.evaluate(close, fc, sent, mandate_key=mdl.mandate_key,
                                   portfolio_meta=pmeta, history_days=ps.n_days,
                                   data_honesty=ps.provenance, macro_context=ctx)
            p = provenance.portfolio(ps.provenance)
            signal_journal.record_signal(
                target_kind="model", target_id=mdl.id, target_name=mdl.name,
                close=close, input_close=close, signal=sig, horizon_days=21,
                benchmark=signal_journal.benchmark_for_model(mdl),
                source_counts={str(k): int(v) for k, v in ps.sources.items()},
                eligible_for_real_research=p["eligible_for_real_research"],
                data_mode=p["data_mode"],
                metadata={**_signal_evidence_metadata(sig, endpoint="auto_snapshot"),
                          "mandate": mdl.mandate_key},
            )
            recorded += 1
        except Exception:
            skipped += 1
    return {"recorded": recorded, "skipped": skipped, "date": today}


def _strategy_request_args():
    cost_bps, cost_error = _safe_float_arg("cost_bps", 5.0, 0.0, 500.0)
    if cost_error:
        return None, None, None, cost_error
    slippage_bps, slippage_error = _safe_float_arg("slippage_bps", 0.0, 0.0, 500.0)
    if slippage_error:
        return None, None, None, slippage_error
    return cost_bps, slippage_bps, {
        "start": request.args.get("start") or None,
        "end": request.args.get("end") or None,
    }, None


@bp.route("/api/command-center")
def command_center():
    payload = _command_center_payload()
    # Macro block rides the warmed cache: the auto-live loop refreshes it, so
    # the landing view never blocks on three feed fetches.
    payload["macro"] = macro_events.compact_summary(macro_events.snapshot_cached())
    return ok(payload)


@bp.route("/api/macro")
def macro_intelligence():
    """Full macro snapshot: Fed stance (hawk/dove over official press+speeches),
    White House policy themes with sector pressure, geopolitical risk index,
    FOMC proximity, and the live rate context. Pure read — a ?refresh param is
    ignored; force a re-fetch via POST /api/macro/refresh."""
    return _macro_payload(force=False)


@bp.route("/api/macro/refresh", methods=["POST"])
def macro_refresh():
    """Force a macro feed re-fetch. POST because it mutates the cached
    snapshot + persisted history (and so gets CSRF protection)."""
    return _macro_payload(force=True)


def _macro_payload(force: bool):
    snap = macro_events.macro_snapshot(force=force)
    return ok({
        **snap,
        "history": macro_events.history_and_changes(),
        "rates": macro.rate_context(),
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })


@bp.route("/api/analyze")
def analyze():
    symbol = data.clean_symbol(request.args.get("ticker", ""), fallback="")
    if not symbol:
        return err("Provide a ticker symbol.", 400)
    horizon, error = _safe_int_arg("horizon", 21, 5, 90)
    if error:
        return err(error, 400)
    inst = data.get(symbol)
    if inst is None:
        return err(f"Unknown ticker '{symbol}'.", 404)

    close = inst.df["close"].dropna()
    if len(close) < 60:
        return err("Need at least 60 rows of history to analyze.", 400)

    fc = forecast.forecast(close, horizon=horizon)
    # Outbound-network phase, bounded by the same semaphore as /api/live: only
    # a couple of analyze requests may fan out to GDELT/FMP/Intrinio/EDGAR/Fed
    # feeds at once; the rest degrade to cached-only reads instead of pinning
    # worker threads behind stacks of 10-12s provider timeouts (review
    # finding: analyze was the most network-heavy route and the only unbounded
    # one). Every degraded block still reports its own availability honestly.
    live_slot = _LIVE_SEMAPHORE.acquire(blocking=False)
    try:
        # Headlines: yfinance per-ticker plus the free GDELT news wire (deduped;
        # GDELT is TTL-cached and returns [] offline, so this never blocks honesty).
        merged_headlines = list(inst.headlines or [])
        seen_titles = {h.lower() for h in merged_headlines}
        if live_slot:
            for title in news.headlines_for(inst.symbol, inst.name):
                if title.lower() not in seen_titles:
                    seen_titles.add(title.lower())
                    merged_headlines.append(title)
        sent = sentiment.score_headlines(merged_headlines)
        # Fundamentals (6h-cached) drive the strategic track; offline -> usable=False
        # and the signal honestly reports a technicals-only rating.
        if live_slot:
            fnd = fundamentals.fetch(inst.symbol)
        else:
            fnd = fundamentals.fetch_cached(inst.symbol) or fundamentals.Fundamentals(
                ticker=inst.symbol, source="none")
        fwd = cma.instrument_forward(inst.symbol, fnd)
        # Macro layer: Fed stance, White House policy pressure on this sector, and
        # geopolitical event risk feed the rating as transparent conviction dampers
        # and caveats (30-min cached; unavailable sources are never assumed calm).
        macro_snap = macro_events.macro_snapshot() if live_slot else macro_events.snapshot_cached()
        macro_ctx = macro_events.build_macro_context(fwd.get("sector") or "", macro_snap)
        sec = (sec_events.events_for(inst.symbol) if live_slot
               else sec_events.events_cached(inst.symbol)
               or {"available": False, "reason": "Live provider slots busy; no cached SEC events yet."})
    finally:
        if live_slot:
            _LIVE_SEMAPHORE.release()
    sig = signals.evaluate(close, fc, sent, history_days=len(close),
                           fundamental_result=fwd, macro_context=macro_ctx)
    bt = backtest.run(close)
    metrics = indicators.metrics_summary(close)
    journal_entry = _record_instrument_signal(inst, close, sig, horizon)

    return ok({
        "symbol": inst.symbol,
        "name": inst.name,
        "source": inst.source,
        # Same server-side provenance verdict shape /api/live returns.
        "data_provenance": provenance.instrument(inst.source, len(close)),
        "metrics": metrics,
        "series": _series_payload(close, markers=True),
        "forecast": fc,
        "sentiment": sent,
        "fundamentals": fwd,
        "rates": macro.rate_context(),
        # Regulatory event context (8-K material events + Form 4 insider trades).
        # Cached 1h; offline -> {"available": False} rather than fabricated calm.
        "sec_events": sec,
        "macro": macro_events.compact_summary(macro_snap),
        "signal": sig,
        "signal_journal_entry": journal_entry,
        "backtest": bt,
    })


@bp.route("/api/lookthrough")
def lookthrough_instrument():
    """See inside one ETF/mutual fund via SEC N-PORT (no price history needed)."""
    symbol = data.clean_symbol(request.args.get("ticker", ""), fallback="")
    if not symbol:
        return err("Provide a fund ticker symbol.", 400)
    lt = holdings.fetch_lookthrough(symbol)
    summary = holdings.summarize(lt)
    # Forward provenance reflects REAL intra-fund coverage: a fund whose listed
    # N-PORT positions only sum to e.g. 60% of NAV is 60% covered, not 100%.
    if lt.kind == "fund" and lt.resolved:
        covered = float(summary["covered_weight_pct"])
        looked, leaf = covered, 0.0
    elif lt.kind == "stock" and lt.resolved:
        covered, looked, leaf = 100.0, 0.0, 100.0
    else:
        covered, looked, leaf = 0.0, 0.0, 0.0
    forward = provenance.forward_research({
        "n_holdings": 1,
        "looked_through_pct": looked,
        "leaf_pct": leaf,
        "uncovered_pct": round(max(0.0, 100.0 - covered), 2),
        "composition_coverage_pct": covered,
        "unresolved": [] if lt.resolved else [{"ticker": symbol, "reason": lt.warning}],
        "as_of_range": {"oldest": lt.as_of, "newest": lt.as_of},
    })
    return ok({
        "symbol": lt.symbol,
        "resolved": lt.resolved,
        "kind": lt.kind,
        "source": lt.source,
        "cik": lt.cik,
        "series_id": lt.series_id,
        "as_of": lt.as_of,
        "total_net_assets": lt.total_net_assets,
        "former_names": lt.former_names,
        "summary": summary,
        "positions": lt.positions[:50],
        "forward": forward,
        "warning": lt.warning,
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })


@bp.route("/api/strategy/analyze")
def strategy_analyze():
    symbol = data.clean_symbol(request.args.get("ticker", ""), fallback="")
    if not symbol:
        return err("Provide a ticker symbol.", 400)
    inst = data.get(symbol)
    if inst is None:
        return err(f"Unknown ticker '{symbol}'.", 404)
    cost_bps, slippage_bps, window, arg_error = _strategy_request_args()
    if arg_error:
        return err(arg_error, 400)
    try:
        result = strategy.analyze_strategy(
            inst.df["close"], cost_bps=cost_bps, slippage_bps=slippage_bps, **window)
    except ValueError as e:
        return err(str(e), 400)
    p = provenance.instrument(inst.source, len(inst.df["close"].dropna()))
    return ok({
        "series_kind": "instrument",
        "symbol": inst.symbol,
        "name": inst.name,
        "source": inst.source,
        "data_mode": p["data_mode"],
        "display_label": (
            "Demo strategy result — synthetic data, not investment evidence."
            if p["data_mode"] == "demo" else p["display_label"]
        ),
        "eligible_for_real_research": p["eligible_for_real_research"],
        "reason": p["reason"],
        "required_action": p["required_action"],
        "data_provenance": p,
        "warnings": p["warnings"],
        **result,
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })


@bp.route("/api/opportunities")
def opportunities_api():
    limit, limit_error = _safe_int_arg("limit", 25, 1, 100)
    if limit_error:
        return err(limit_error, 400)
    min_score, score_error = _safe_float_arg("min_score", 0.0, 0.0, 100.0)
    if score_error:
        return err(score_error, 400)
    kind = (request.args.get("kind") or "all").lower()
    if kind not in {"all", "instrument", "model"}:
        return err("kind must be all, instrument, or model.", 400)
    include_hold = (request.args.get("include_hold", "1").lower() not in {"0", "false", "no"})
    payload = opportunity.opportunities(
        kind=kind,
        include_hold=include_hold,
        min_score=min_score,
        limit=limit,
    )
    return ok({**payload, "disclaimer": ANALYSIS_ONLY_DISCLAIMER})


@bp.route("/api/evidence-lab")
def evidence_lab_endpoint():
    kind = (request.args.get("kind") or "model").strip().lower()
    target_id = (request.args.get("id") or request.args.get("ticker") or "").strip()
    horizon, horizon_error = _safe_int_arg("horizon", 21, 5, 252)
    if horizon_error:
        return err(horizon_error, 400)
    train_window, train_error = _safe_int_arg("train_window", 252, 90, 756)
    if train_error:
        return err(train_error, 400)
    step, step_error = _safe_int_arg("step", 21, 5, 63)
    if step_error:
        return err(step_error, 400)
    if kind == "model":
        mdl = portfolio.get(target_id)
        if mdl is None:
            return err("Unknown model.", 404)
        return ok({
            **evidence_lab.analyze_model(mdl, horizon_days=horizon or 21, train_window=train_window or 252, step=step or 21),
            "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
        })
    if kind == "instrument":
        symbol = data.clean_symbol(target_id, fallback="")
        if not symbol:
            return err("Provide an instrument symbol.", 400)
        inst = data.get(symbol)
        if inst is None:
            return err(f"Unknown ticker '{symbol}'.", 404)
        return ok({
            **evidence_lab.analyze_instrument(inst, horizon_days=horizon or 21, train_window=train_window or 252, step=step or 21),
            "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
        })
    return err("kind must be instrument or model.", 400)


@bp.route("/api/signal-journal")
def signal_journal_endpoint():
    limit, error = _safe_int_arg("limit", 100, 1, 500)
    if error:
        return err(error, 400)
    entries = signal_journal.list_entries(limit=limit)
    dashboard = signal_journal.dashboard_payload(entries)
    return ok({
        "entries": entries,
        "count": len(entries),
        **dashboard,
        "methodology": {
            "analysis_only": True,
            "paper_tracking_only": True,
            "raw_price_history_stored": False,
            "hit_rate_basis": "Measured paper signals are scored against their action intent: BUY/ADD/OVERWEIGHT must beat the benchmark when alpha is available; SELL/REDUCE/UNDERWEIGHT must underperform; HOLD/REVIEW must avoid material benchmark lag.",
            "forward_results": "Pending results resolve only after later live or persisted price history covers the original signal horizon.",
            "measurement_scope": "Signals recorded from demo/sample data are marked not_measurable and never scored; headline hit rate and alpha aggregate only real-eligible signals.",
        },
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })

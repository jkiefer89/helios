"""Models blueprint: model list/upload/analysis, library, editor, governance,
validation, portfolio clinic, and risk analytics."""
from __future__ import annotations

import time

from flask import Blueprint, Response, request

from engine import (
    analytics_cache, backtest, cma, data, data_quality, figi, forecast, fundamentals,
    holdings, indicators, insights, macro_events, mandate, model_governance,
    model_library, model_validation, portfolio, portfolio_clinic, provenance,
    risk_exposure, sentiment, signals, strategy,
)

from .analysis import _record_model_signal, _series_payload, _strategy_request_args
from .core import ANALYSIS_ONLY_DISCLAIMER, _UPLOAD_SEMAPHORE, _safe_int_arg, app, err, ok

bp = Blueprint("models", __name__)


@bp.route("/api/mandates")
def mandates():
    return ok({"mandates": mandate.public_list()})


@bp.route("/api/models")
def models():
    out = []
    for mdl in portfolio.all_models():
        coverage = data_quality.model_coverage(mdl)
        out.append({
            "id": mdl.id, "name": mdl.name, "mandate": mdl.mandate_key,
            "mandate_label": mandate.get(mdl.mandate_key)["label"],
            "n_holdings": len(mdl.holdings),
            "top": mdl.holdings[0].ticker if mdl.holdings else None,
            "holdings": [
                {
                    "ticker": holding.ticker,
                    "weight": round(float(holding.weight), 6),
                    "weight_pct": round(float(holding.weight) * 100, 2),
                    "source": holding.source or "pending",
                }
                for holding in mdl.holdings
            ],
            "real_coverage_count": coverage["real_coverage_count"],
            "missing_tickers": coverage["missing_tickers"],
            "coverage_state": coverage["coverage_state"],
        })
    out.sort(key=lambda d: d["name"])
    return ok({"models": out})


@bp.route("/api/model-library")
def model_library_list():
    return ok({"templates": model_library.public_list()})


@bp.route("/api/model-library/import", methods=["POST"])
def model_library_import():
    payload = request.get_json(silent=True) or {}
    slug = str(payload.get("slug") or "")
    template = model_library.get(slug)
    if not template:
        return err("Unknown model library template.", 404)
    mdl = model_library.import_template(slug)
    if not mdl:
        return err("Unknown model library template.", 404)
    analytics_cache.invalidate()
    coverage = data_quality.model_coverage(mdl)
    return ok({
        "id": mdl.id,
        "name": mdl.name,
        "mandate": mdl.mandate_key,
        "n_holdings": len(mdl.holdings),
        "coverage_state": coverage["coverage_state"],
        "missing_tickers": coverage["missing_tickers"],
        "template_only": template["template_only"],
        "benchmark": template["benchmark"],
        "rebalance_rules": template["rebalance_rules"],
        "risk_limits": template["risk_limits"],
        "provenance": template["provenance"],
    })


@bp.route("/api/model-governance")
def model_governance_workspace():
    return ok(model_governance.payload())


@bp.route("/api/model-validation")
def model_validation_dashboard():
    horizon, horizon_error = _safe_int_arg("horizon", 21, 5, 252)
    if horizon_error:
        return err(horizon_error, 400)
    train_window, train_error = _safe_int_arg("train_window", 252, 90, 756)
    if train_error:
        return err(train_error, 400)
    step, step_error = _safe_int_arg("step", 21, 5, 63)
    if step_error:
        return err(step_error, 400)
    return ok(model_validation.dashboard(
        horizon_days=horizon or 21,
        train_window=train_window or 252,
        step=step or 21,
    ))


@bp.route("/api/model-governance/<model_id>/events", methods=["POST"])
def model_governance_event(model_id):
    mdl = portfolio.get(model_id)
    if mdl is None:
        return err("Unknown model.", 404)
    payload = request.get_json(silent=True) or {}
    result = model_governance.record_event(
        mdl,
        actor=str(payload.get("actor") or "Advisor Console"),
        action=str(payload.get("action") or ""),
        note=str(payload.get("note") or ""),
        approval_status=str(payload.get("approval_status") or ""),
        committee_identity=payload.get("committee_identity") if isinstance(payload.get("committee_identity"), dict) else None,
    )
    if not result.get("recorded"):
        return err(result.get("warning") or "Could not record model governance event.", int(result.get("status_code") or 503))
    return ok({"event": result["event"]})


@bp.route("/api/model-governance/<model_id>/approval-packet")
def model_governance_approval_packet(model_id):
    mdl = portfolio.get(model_id)
    if mdl is None:
        return err("Unknown model.", 404)
    packet = model_governance.approval_packet(mdl)
    if not packet.get("available", True):
        return err(packet.get("warning") or "Model approval packet unavailable.", 503)
    return ok({"packet": packet})


@bp.route("/api/model-governance/<model_id>/approval-packet.html")
def model_governance_approval_packet_html(model_id):
    mdl = portfolio.get(model_id)
    if mdl is None:
        return err("Unknown model.", 404)
    packet = model_governance.approval_packet(mdl)
    if not packet.get("available", True):
        return err(packet.get("warning") or "Model approval packet unavailable.", 503)
    return Response(
        model_governance.render_approval_packet_html(packet),
        mimetype="text/html",
        headers={"Content-Disposition": f'inline; filename="helios-model-approval-{model_id}.html"'},
    )


@bp.route("/api/model-governance/<model_id>/approval-packet.pdf")
def model_governance_approval_packet_pdf(model_id):
    mdl = portfolio.get(model_id)
    if mdl is None:
        return err("Unknown model.", 404)
    packet = model_governance.approval_packet(mdl)
    if not packet.get("available", True):
        return err(packet.get("warning") or "Model approval packet unavailable.", 503)
    return Response(
        model_governance.render_approval_packet_pdf(packet),
        mimetype="application/pdf",
        headers={"Content-Disposition": f'inline; filename="helios-model-approval-{model_id}.pdf"'},
    )


@bp.route("/api/models/<model_id>/editor", methods=["GET"])
def model_editor(model_id):
    mdl = portfolio.get(model_id)
    if mdl is None:
        return err("Unknown model.", 404)
    return ok(model_governance.preview_edit(mdl, holdings=[
        {"ticker": holding.ticker, "weight_pct": float(holding.weight) * 100}
        for holding in mdl.holdings
    ]))


@bp.route("/api/models/<model_id>/editor/preview", methods=["POST"])
def model_editor_preview(model_id):
    mdl = portfolio.get(model_id)
    if mdl is None:
        return err("Unknown model.", 404)
    payload = request.get_json(silent=True) or {}
    try:
        result = model_governance.preview_edit(
            mdl,
            holdings=payload.get("holdings") or [],
            rebalance_to_target=bool(payload.get("rebalance_to_target")),
        )
    except ValueError as exc:
        return err(str(exc), 400)
    return ok(result)


@bp.route("/api/models/<model_id>/editor", methods=["POST"])
def model_editor_save(model_id):
    mdl = portfolio.get(model_id)
    if mdl is None:
        return err("Unknown model.", 404)
    payload = request.get_json(silent=True) or {}
    try:
        result = model_governance.save_edit(
            mdl,
            holdings=payload.get("holdings") or [],
            change_note=str(payload.get("change_note") or ""),
            actor=str(payload.get("actor") or "Advisor Console"),
            rebalance_to_target=bool(payload.get("rebalance_to_target")),
        )
    except ValueError as exc:
        return err(str(exc), 400)
    if not result.get("saved"):
        return err(result.get("warning") or "Could not save model edits.", 400)
    # Reweighting can keep row count and last date unchanged, so drop memos.
    analytics_cache.invalidate()
    return ok(result)


@bp.route("/api/model/upload", methods=["POST"])
def model_upload():
    if "file" not in request.files:
        return err("No file uploaded.")
    f = request.files["file"]
    name = request.form.get("name") or (f.filename or "Client Model").rsplit(".", 1)[0]
    mkey = mandate.key_or_default(request.form.get("mandate"))
    context = request.form.get("context", "")
    if not _UPLOAD_SEMAPHORE.acquire(blocking=False):
        return err("Server busy parsing another upload — try again in a moment.", 429)
    try:
        mdl = portfolio.parse_model_file(f.read(), f.filename or "", name, mkey, context)
    except ValueError as e:
        return err(str(e), 400)
    except Exception:
        app.logger.exception("model parse failed")
        return err("Could not read that file. Expected columns for Ticker and (optionally) Weight.", 400)
    finally:
        _UPLOAD_SEMAPHORE.release()
    analytics_cache.invalidate()
    coverage = data_quality.model_coverage(mdl)
    return ok({
        "id": mdl.id,
        "name": mdl.name,
        "mandate": mdl.mandate_key,
        "n_holdings": len(mdl.holdings),
        "missing_tickers": coverage["missing_tickers"],
        "coverage_state": coverage["coverage_state"],
    })


def _resolve_horizon(raw: str):
    """Return (kind, value, error). kind 'short' -> int days; 'long' -> preset days."""
    raw = (raw or "21").strip().upper()
    if raw in forecast.LONG_HORIZONS:
        return "long", forecast.LONG_HORIZONS[raw], None
    try:
        return "short", max(5, min(int(float(raw)), 90)), None
    except (TypeError, ValueError, OverflowError):
        return None, None, "horizon must be an integer between 5 and 90 or one of 6M, 1Y, 3Y, 5Y."


def _available_long(n_days: int) -> list[str]:
    out = []
    if n_days >= 90:           # don't project 6 months from < ~4 months of data
        out.append("6M")
    if n_days >= 126:
        out.append("1Y")
    if n_days >= 252:
        out += ["3Y", "5Y"]
    return out


@bp.route("/api/model/analyze")
def model_analyze():
    mdl = portfolio.get(request.args.get("id", ""))
    if mdl is None:
        return err("Unknown model.", 404)
    hkind, hval, horizon_error = _resolve_horizon(request.args.get("horizon"))
    if horizon_error:
        return err(horizon_error, 400)

    try:
        ps = portfolio.build_series(mdl)
    except ValueError as e:
        return err(str(e), 400)
    close = ps.close
    if len(close) < 60:
        return err("Holdings have too little available analyzed history (need 60+ sessions).", 400)

    avail = _available_long(ps.n_days)
    long_label = forecast._long_label(hval) if hkind == "long" else None
    if hkind == "long" and long_label not in avail:
        hkind, hval = "short", 21  # requested long horizon unavailable for this history
        long_label = None

    # Aggregate any known headlines from holdings (sample tickers carry demo news).
    headlines = []
    for h in mdl.holdings:
        inst = data.get(h.ticker)
        if inst and inst.headlines:
            headlines.extend(inst.headlines[:2])
    sent = sentiment.score_headlines(headlines)

    signal_horizon = hval if hkind == "short" else 21
    fc_short = forecast.forecast(close, horizon=signal_horizon)
    metrics = indicators.metrics_summary(close)
    pmeta = {"n_holdings": len(mdl.holdings),
             "top_weight": mdl.holdings[0].weight if mdl.holdings else 0,
             "top_ticker": mdl.holdings[0].ticker if mdl.holdings else "?"}
    # Event risk (FOMC proximity, geopolitical stress) applies to models too;
    # sector policy pressure is per-name and stays on the instrument path.
    macro_ctx = macro_events.build_macro_context()
    sig = signals.evaluate(close, fc_short, sent, mandate_key=mdl.mandate_key,
                           portfolio_meta=pmeta, history_days=ps.n_days,
                           data_honesty=ps.provenance, macro_context=macro_ctx)
    bt = backtest.run(close)
    journal_entry = _record_model_signal(mdl, ps, close, sig, signal_horizon)

    # Long-horizon drift reverts toward the holdings-derived CMA anchor when the
    # forward endpoint has already computed one (get-only: a cold cache falls
    # back to the generic mandate anchor rather than paying look-through latency).
    cached_anchor = analytics_cache.get("model_forward_anchor", (mdl.id,)) or {}
    anchor_kwargs = ({"anchor_return": cached_anchor["anchor"], "anchor_basis": cached_anchor["basis"]}
                     if cached_anchor.get("anchor") is not None else {})

    # 1Y projection always computed for the drawdown-breach insight; reuse if displayed.
    fc_long_1y = (forecast.forecast_long(close, 252, mdl.mandate_key, **anchor_kwargs)
                  if ps.n_days >= 126 else None)
    if hkind == "long":
        forecast_panel = (fc_long_1y if hval == 252 and fc_long_1y
                          else forecast.forecast_long(close, hval, mdl.mandate_key, **anchor_kwargs))
    else:
        forecast_panel = fc_short

    ins = insights.generate(mdl, ps, metrics, sig, fc_short, fc_long_1y)
    holdings = _holdings_with_signals(ps, mdl)

    return ok({
        "id": mdl.id, "name": mdl.name,
        "mandate": {"key": mdl.mandate_key, **mandate.get(mdl.mandate_key)},
        "context": mdl.mandate_context,
        "metrics": metrics,
        "series": _series_payload(close),
        "holdings": holdings,
        "concentration": {"hhi": ps.hhi, "n_eff": ps.n_eff, "corr_mean": ps.corr_mean},
        "provenance": ps.provenance,
        "series_basis": ps.provenance.get("series_basis", ""),
        "series_basis_note": ps.provenance.get("series_basis_note", ""),
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
        # Server-side provenance verdict (same shape as /api/live); the raw
        # "provenance" block above is kept unchanged for backward compatibility.
        "data_provenance": provenance.portfolio(ps.provenance),
        "horizon": {"kind": hkind, "value": hval, "label": long_label,
                    "available_long": avail},
        "forecast": forecast_panel,
        "forecast_short": fc_short,
        "signal": sig,
        "signal_journal_entry": journal_entry,
        "backtest": bt,
        "insights": ins,
        # Structured verdicts for the mandate-fit panel — computed from the
        # SAME variables the insights use, so the UI never infers pass/fail
        # from insight-id absence (review finding: mismatched trigger bands).
        "mandate_checks": insights.mandate_checks(mdl, metrics, fc_long_1y),
        "warnings": ps.warnings,
    })


@bp.route("/api/model/strategy/analyze")
def model_strategy_analyze():
    mdl = portfolio.get(request.args.get("id", ""))
    if mdl is None:
        return err("Unknown model.", 404)
    cost_bps, slippage_bps, window, arg_error = _strategy_request_args()
    if arg_error:
        return err(arg_error, 400)
    try:
        ps = portfolio.build_series(mdl, allow_sample=False, allow_simulated=False)
    except ValueError as e:
        # Genuine provenance block: no holding has real price history.
        return ok({
            "series_kind": "model",
            "id": mdl.id,
            "name": mdl.name,
            "data_mode": "invalid_for_research",
            "display_label": "Data Quality Blocked",
            "eligible_for_real_research": False,
            "reason": str(e),
            "required_action": "Upload real price history for every model holding before running Strategy Lab.",
            "missing_tickers": [h.ticker for h in mdl.holdings],
            "warnings": [str(e)],
            "data_provenance": {"missing_tickers": [h.ticker for h in mdl.holdings]},
            "methodology": {"analysis_only": True, "no_lookahead": True},
            "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
        })
    p = provenance.portfolio(ps.provenance)
    if not p["eligible_for_real_research"]:
        return ok({
            "series_kind": "model",
            "id": mdl.id,
            "name": mdl.name,
            "data_mode": p["data_mode"],
            "display_label": p["display_label"],
            "eligible_for_real_research": False,
            "reason": p["reason"],
            "required_action": p["required_action"],
            "missing_tickers": p["missing_tickers"],
            "warnings": p["warnings"],
            "data_provenance": p,
            "methodology": {"analysis_only": True, "no_lookahead": True},
            "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
        })
    try:
        result = strategy.analyze_strategy(
            ps.close, cost_bps=cost_bps, slippage_bps=slippage_bps, **window)
    except ValueError as e:
        # Window/short-history errors are not data-quality blocks: report the
        # actual message (mirrors the instrument twin in analysis.py).
        return err(str(e), 400)
    return ok({
        "series_kind": "model",
        "id": mdl.id,
        "name": mdl.name,
        "mandate": {"key": mdl.mandate_key, **mandate.get(mdl.mandate_key)},
        "provenance": ps.provenance,
        "data_mode": p["data_mode"],
        "display_label": p["display_label"],
        "eligible_for_real_research": p["eligible_for_real_research"],
        "reason": p["reason"],
        "required_action": p["required_action"],
        "data_provenance": p,
        "warnings": ps.warnings,
        **result,
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })


@bp.route("/api/model/clinic")
def model_clinic():
    mdl = portfolio.get(request.args.get("id", ""))
    if mdl is None:
        return err("Unknown model.", 404)
    try:
        result = portfolio_clinic.analyze_clinic(mdl)
    except ValueError as e:
        return err(str(e), 400)
    return ok({**result, "disclaimer": ANALYSIS_ONLY_DISCLAIMER})


@bp.route("/api/model/lookthrough")
def model_lookthrough():
    """Roll every holding's look-through up to a model-level real exposure."""
    mdl = portfolio.get(request.args.get("id", ""))
    if mdl is None:
        return err("Unknown model.", 404)
    roll = holdings.model_lookthrough(mdl)
    forward = provenance.forward_research(roll["coverage"])
    return ok({
        "id": mdl.id,
        "name": mdl.name,
        "mandate": {"key": mdl.mandate_key, "label": mandate.get(mdl.mandate_key)["label"]},
        "exposure": roll["exposure"],
        "per_holding": roll["per_holding"],
        "coverage": roll["coverage"],
        "forward": forward,
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })


# Bound how many per-holding fundamentals one forward analysis may fetch, so a
# wide model can't trigger an unbounded fan-out of (network) provider calls.
_FORWARD_FUNDAMENTALS_BUDGET = 60
# ...and how LONG the whole fan-out may run: 60 tickers x sequential provider
# chains had no aggregate deadline (review finding). Tickers beyond the
# deadline get cache-only reads; misses just lower reported coverage honestly.
_FORWARD_FUNDAMENTALS_TIME_BUDGET_S = 45.0


def _fundamentals_map_for(underlyings: list) -> dict:
    """Resolve CUSIP-only N-PORT holdings to tickers (OpenFIGI) and fetch
    fundamentals for the heaviest equity sleeves. N-PORT usually omits tickers,
    so without the CUSIP bridge most underlyings fall back to the generic anchor.
    Mutates each resolved underlying's ticker in place so the CMA can match it;
    bounded by the fundamentals budget and offline-safe."""
    equity = [u for u in underlyings if (u.get("asset_class") or "").lower().startswith("equity")]
    equity.sort(key=lambda u: u.get("weight_pct", 0.0), reverse=True)
    top = equity[:_FORWARD_FUNDAMENTALS_BUDGET]

    need = [(u.get("cusip") or "").upper() for u in top
            if not (u.get("ticker") or "").strip() and u.get("cusip")]
    cusip_to_ticker = figi.map_cusips(need) if need else {}

    fmap: dict = {}
    deadline = time.monotonic() + _FORWARD_FUNDAMENTALS_TIME_BUDGET_S
    for u in top:
        ticker = (u.get("ticker") or "").upper()
        if not ticker:
            ticker = cusip_to_ticker.get((u.get("cusip") or "").upper(), "")
            if ticker:
                u["ticker"] = ticker  # so cma.aggregate can join the fundamentals
        if ticker and ticker not in fmap:
            if time.monotonic() > deadline:
                cached = fundamentals.fetch_cached(ticker)
                if cached is not None:
                    fmap[ticker] = cached
                continue
            fmap[ticker] = fundamentals.fetch(ticker)
    return fmap


@bp.route("/api/model/forward")
def model_forward():
    """Forward expected return from holdings look-through + building-block CMA.

    Needs no price history, so it works for a newly-launched fund: it sees what
    the model holds, values each sleeve forward, and blends uncovered weight to
    the mandate anchor — reporting coverage and every building block honestly.
    """
    mdl = portfolio.get(request.args.get("id", ""))
    if mdl is None:
        return err("Unknown model.", 404)
    # Captured BEFORE the slow look-through/fundamentals fan-out: if a model
    # edit or data refresh invalidates the analytics cache while this request
    # is in flight, the anchor publish below is dropped instead of landing a
    # stale value under the same key after the invalidation (review finding).
    cache_gen = analytics_cache.generation()
    roll = holdings.model_lookthrough(mdl)
    underlyings = roll["exposure"].get("underlyings", [])
    fmap = _fundamentals_map_for(underlyings)
    fundamentals_sources = sorted({f.source for f in fmap.values() if getattr(f, "usable", False)})
    # model_lookthrough normalizes weights to fractions of the WHOLE book, so
    # 100.0 is the honest coverage denominator — unresolved/truncated weight
    # counts as uncovered instead of being extrapolated away (review finding).
    forward_return = cma.aggregate(underlyings, fmap, mdl.mandate_key, total_weight_pct=100.0)
    forward_return["fundamentals_sources"] = fundamentals_sources
    forward_prov = provenance.forward_research(roll["coverage"])
    blended = mandate.blended_anchor(
        mdl.mandate_key,
        cma_return=forward_return["expected_return_covered_pct"] / 100.0,
        coverage=forward_return["coverage_pct"] / 100.0,
    )
    # Publish the holdings-derived anchor so the model-analyze route can revert
    # its long-horizon cone toward it (get-only there — the cone never pays this
    # endpoint's look-through latency; invalidated with the analytics cache).
    analytics_cache.put("model_forward_anchor", (mdl.id,), {
        "anchor": blended,
        "coverage_pct": forward_return["coverage_pct"],
        "basis": "lookthrough_cma_blend",
    }, if_generation=cache_gen)
    return ok({
        "id": mdl.id,
        "name": mdl.name,
        "mandate": {"key": mdl.mandate_key, "label": mandate.get(mdl.mandate_key)["label"]},
        "forward_return": forward_return,
        "blended_anchor_pct": round(blended * 100.0, 2),
        # Forward hypotheses in the Portfolio Clinic's schema so both feed one
        # unified research-hypothesis surface (backward + forward), not two.
        "research_hypotheses": cma.research_hypotheses(forward_return, mdl.mandate_key),
        "exposure": {k: v for k, v in roll["exposure"].items() if k != "underlyings"},
        "coverage": roll["coverage"],
        "forward": forward_prov,
        "disclaimer": ANALYSIS_ONLY_DISCLAIMER,
    })


@bp.route("/api/model/risk")
def model_risk():
    mdl = portfolio.get(request.args.get("id", ""))
    if mdl is None:
        return err("Unknown model.", 404)
    return ok({**risk_exposure.analyze_model_risk(mdl), "disclaimer": ANALYSIS_ONLY_DISCLAIMER})


def _holdings_with_signals(ps: portfolio.PortfolioSeries, mdl: portfolio.Model) -> list:
    out = []
    for h in ps.holdings:
        try:
            # allow_live=False: build_series already resolved every holding
            # under its fetch/time budget; re-resolving live here retried each
            # budget-simulated ticker sequentially (8s timeout apiece) on the
            # request thread, and could compute the chip from a fresher series
            # than the one the analysis reported (review finding).
            psr = data.resolve_series(h["ticker"], allow_live=False)
            sc = signals.latest_signal_score(psr.close)
            action = "BUY" if sc > 0.15 else "SELL" if sc < -0.05 else "HOLD"
        except Exception:
            action = "—"
        out.append({**h, "signal": action})
    return out

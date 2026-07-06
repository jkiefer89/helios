"""Model insights: rule-based suggestions to improve a portfolio model.

Each rule has an explicit numeric trigger, a plain-English message an advisor can
repeat to a client, a concrete suggested action, and a rationale. All thresholds
come from the mandate config so nothing is a magic number. Deterministic and
offline — no LLM — so identical inputs always yield identical insights.
"""
from __future__ import annotations

from . import mandate as mnd


def generate(model, ps, metrics: dict, sig: dict,
             fc_short: dict, fc_long_1y: dict | None = None) -> list[dict]:
    key = mnd.key_or_default(model.mandate_key)
    m = mnd.get(key)
    label = m["label"]
    out: list[dict] = []

    weights = {h["ticker"]: h["weight"] for h in ps.holdings}
    # Excluded holdings carry mrc_pct=None; drop them so formatting never crashes.
    mrc = {h["ticker"]: h["mrc_pct"] for h in ps.holdings if h.get("mrc_pct") is not None}
    vol = metrics["annual_vol_pct"] / 100.0
    tgt_vol = m["target_vol_pct"] / 100.0
    maxdd = metrics["max_drawdown_pct"] / 100.0           # negative
    tol = m["max_drawdown_tolerance_pct"] / 100.0
    cap = m["single_name_cap"]
    mod_hhi, high_hhi = mnd.hhi_thresholds(key)
    growth, income = m["growth_orientation"], m["income_orientation"]

    def add(id, category, severity, message, action, rationale):
        out.append({"id": id, "category": category, "severity": severity,
                    "message": message, "suggested_action": action, "rationale": rationale})

    # 1. Single-name concentration
    over = [(t, w) for t, w in weights.items() if w > cap]
    flagged_single = bool(over)
    for t, w in sorted(over, key=lambda x: -x[1]):
        add("conc_single_name", "concentration", "high",
            f"{t} is {w:.0%} of the portfolio — above the {cap:.0%} single-name limit for a "
            f"{label} mandate. Its marginal risk contribution is {mrc.get(t, 0):.0f}% of portfolio volatility.",
            f"Trim {t} toward {cap:.0%} and redistribute into lowly-correlated holdings, or document it "
            f"as a deliberate concentrated thesis that breaches the mandate's diversification norm.",
            f"Single-name cap {cap:.0%} reflects the RIC-style diversification heuristic; concentration "
            f"this high makes drawdowns lumpy.")

    # 2. Herfindahl concentration (only if no single name already flagged)
    if ps.hhi > high_hhi and not flagged_single:
        add("conc_hhi", "concentration", "medium",
            f"Portfolio behaves like only {ps.n_eff:.1f} independent holdings (HHI {ps.hhi:.2f}); "
            f"diversification is thinner than the holding count suggests.",
            "Add 2–4 lowly-correlated positions (correlation <0.4) to lift the effective count above 4–5.",
            f"HHI>{high_hhi:.2f} (N_eff<{1/high_hhi:.0f}) mirrors the antitrust 'highly concentrated' line.")

    # 3. Volatility above mandate budget
    if vol > tgt_vol * 1.15:
        add("vol_above_mandate", "mandate-fit", "high",
            f"Realized volatility {vol:.0%} exceeds the {label} target of {tgt_vol:.0%} by more than 15% — "
            f"this portfolio is running hotter than its stated mandate.",
            f"Shift weight from the highest-vol sleeves (largest MRC) toward lower-vol/income holdings "
            f"until realized vol is within {tgt_vol:.0%}, or re-rate the mandate.",
            "15% tolerance band avoids flagging normal noise while catching genuine over-risk.")

    # 4. Drawdown breach — fires on an actual historical breach or a materially
    #    high simulated-breach probability. (A negative p05 CAGR is normal for any
    #    equity book and is NOT used as a trigger — it would fire on almost everything.)
    breach_1y = (fc_long_1y or {}).get("prob_breach_maxdd", 0.0)
    if maxdd < -tol or breach_1y > 0.20:
        # Only quote simulated-path numbers when a 1-year forecast actually ran;
        # otherwise 0%-breach / 0%-loss claims would be fabricated.
        if fc_long_1y:
            prob_loss = 1 - fc_long_1y.get("prob_positive", 1.0)
            sim_txt = (f"{breach_1y:.0%} of 1-year simulated paths breach it, and there is a "
                       f"{prob_loss:.0%} chance of ending a 1-year horizon below today's value.")
        else:
            sim_txt = "no 1-year simulation is available to estimate forward breach odds."
        add("drawdown_breaches_tolerance", "risk", "high",
            f"Historical max drawdown {maxdd:.0%} vs the {label} tolerance of {-tol:.0%}; "
            + sim_txt,
            f"De-risk (reduce high-beta, add a defensive sleeve) to bring projected drawdown within {-tol:.0%}, "
            f"or formally re-rate the mandate with the client."
            + (" For CD/preservation mandates this is a hard breach — not fit for purpose."
               if key in ("cd_alternative", "capital_preservation") else ""),
            f"Tolerance {-tol:.0%} is the mandate's stated drawdown budget.")

    # 5. High pairwise correlation
    if ps.corr_mean > 0.70 and len(ps.holdings) > 1:
        add("high_pairwise_correlation", "diversification", "medium",
            f"Holdings move together: average pairwise correlation is {ps.corr_mean:.2f}. "
            f"Diversification benefit is limited.",
            "Add holdings from different sectors/asset classes (bonds, commodities, international) with "
            "correlation <0.4 to genuinely reduce volatility.",
            "Above ~0.70 correlation the names behave as one bet and the forecast cone understates tail risk.")

    # 6. No forecast edge
    da = (fc_short.get("quality", {}) or {}).get("directional_accuracy")
    if da is not None and da <= 0.50:
        n_test = (fc_short.get("quality", {}) or {}).get("n_test", 0)
        strong = abs(_fc_raw(sig)) >= 0.8 and da < 0.52
        add("no_forecast_edge", "signal", "medium",
            f"The return model shows no measured edge: out-of-sample directional accuracy is {da*100:.0f}% "
            f"on {n_test} days (coin-flip is 50%)" + (", yet the forecast is leaning strongly." if strong else "."),
            "Down-weight or ignore the forecast component for this model; rely on trend, mandate-fit and "
            "realized risk until OOS accuracy clears 55%.",
            "Daily-return direction is near-random; honesty requires disclosing weak skill rather than quoting a point estimate.")

    # 7. Volatility penalty active
    vp = sig.get("vol_penalty", 1.0)
    if vp < 0.85:
        add("vol_penalty_active", "signal", "info",
            f"Conviction was reduced {(1-vp)*100:.0f}% by the volatility penalty (realized vol {vol:.0%}; "
            f"penalty floor 0.60 at vol≥70%). The raw signal is stronger than the displayed conviction.",
            "If the high vol is expected for this asset class the penalty is doing its job; otherwise size "
            "positions smaller and require trend+forecast agreement before acting.",
            "Penalty engages above 35% annualized vol with slope 0.5, floored at 0.60.")

    # 8. Income yield gap
    floor = mnd.income_floor(key)
    if income >= 0.40 and floor is not None:
        add("income_yield_gap", "income", "info",
            f"This is an income-oriented mandate ({label}) but holding-level yield data is not available, "
            f"so income adequacy versus the {floor:.1%} floor cannot be confirmed.",
            "Supply estimated yields (or add known income holdings — dividend equity, REITs, preferreds, "
            "bond funds) so the model can verify it pays the income it promises.",
            f"Income floor {floor:.1%} is the minimum distribution rate consistent with the mandate.")

    # 9. Growth mandate under-risked
    if growth >= 0.8 and vol < tgt_vol * 0.5:
        add("growth_overdefensive", "mandate-fit", "low",
            f"Growth mandate but realized vol {vol:.0%} is under half the {tgt_vol:.0%} target — the portfolio "
            f"may be under-risked, leaving expected return on the table.",
            "Consider adding higher-beta/growth holdings or reducing cash/bond ballast to align risk with the objective.",
            "A growth book carrying <50% of its risk budget is likely over-allocated to defensives.")

    # 10. Mandate label mismatch (low-risk mandate carrying growth-level risk)
    if key in ("capital_preservation", "cd_alternative") and vol > 1.5 * tgt_vol:
        add("mandate_label_mismatch", "mandate-fit", "high",
            f"A {label} book is running {vol/tgt_vol:.1f}× its target volatility — this is a growth book "
            f"wearing a preservation/CD label.",
            "Rotate the high-risk sleeve into low-vol/income holdings, or correct the mandate with the client.",
            "Selling preservation while running growth risk is the costliest mis-classification.")

    # 11. Simulated data warning
    prov = ps.provenance
    if prov.get("simulated_weight_pct", 0) > 0:
        syms = ", ".join(prov.get("simulated_symbols", [])[:8])
        add("simulated_data_warning", "risk", "high",
            f"{prov['simulated_weight_pct']:.0f}% of portfolio weight uses SIMULATED price history "
            f"({prov['n_simulated']} of {prov['n_holdings']} holdings had no live/sample data). Forecasts and "
            f"metrics are illustrative, not market-calibrated.",
            f"Provide real price history (live data or CSV) for: {syms}. Until then treat every number as a "
            f"structural illustration only.",
            "No simulated value is ever presented as real market data.")

    # 12. Short history
    if ps.n_days < 252:
        sev = "info" if ps.n_days >= 126 else "medium"
        months = ps.n_days / 21.0
        add("short_history_low_confidence", "risk", sev,
            f"Portfolio analyzed history is only {ps.n_days} trading days (~{months:.0f} months). Mixed-history "
            "holdings are weight-rescaled over their available dates, so long-horizon projections extrapolate "
            "beyond some observed data.",
            f"Supply longer price history for {ps.binding_ticker or 'the shortest-history holding'} "
            f"(or a longer-history proxy) before relying on "
            f"multi-year projections. Short-horizon (≤90d) signals remain usable.",
            "≥252 analyzed days (1y) is the floor for more trustworthy annualization.")

    sev_rank = {"high": 0, "medium": 1, "low": 2, "info": 3}
    out.sort(key=lambda d: sev_rank.get(d["severity"], 9))
    return out


def _fc_raw(sig: dict) -> float:
    for c in sig.get("components", []):
        if c["name"] == "forecast":
            return c.get("raw", 0.0)
    return 0.0

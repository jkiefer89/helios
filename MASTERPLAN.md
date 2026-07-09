# Helios Master Plan — Review & Implementation Roadmap

*Written 2026-07-08 during the overnight build session. This is the standing
review of what Helios is, what it does today, what was verified and fixed, and
the prioritized path forward. Update it as slices ship.*

## What Helios is

A private, never-client-facing research terminal the operator uses to evaluate,
analyze, and forecast models spanning the full mandate spectrum (ultra growth →
income → CD alternatives), then execute trades manually in a separate platform.
**Cardinal rule: honesty.** A wrong number is worse than a missing one — every
source is flagged, every degradation is visible, nothing is fabricated.

## The forecasting architecture (as of tonight)

Every instrument rating is built from five transparent components plus three
overlays, each auditable end to end:

| Layer | Source | Nature |
|---|---|---|
| Trend / momentum | price history | deterministic technicals |
| Ridge forecast | price features over the user's horizon | tactical, horizon-sensitive |
| Sentiment | yfinance + GDELT headlines, finance lexicon | deterministic |
| **Fundamentals (strategic)** | FMP consensus → Intrinio → yfinance merge | building-block CMA E[r] vs mandate anchor, horizon-FREE |
| **Macro event risk** | Fed RSS hawk/dove, GDELT geopolitics, FOMC calendar | bounded conviction damper (×0.70 floor), never flips direction |
| **Policy pressure** | White House actions, theme→sector tagging | explicit caveat on affected names |
| SEC events | EDGAR 8-K items + Form 4 open-market trades | context + radar flags |
| Rates | live Treasury curve (3m/5y/10y/30y) | anchors + drift alert vs configured RF |

The **dual-track output** (tactical vs strategic) decouples the verdict from
the chart-horizon slider. The **AI copilot (Claude Fable 5, Opus 4.8 fallback)**
argues over all of it in dialogue — it may DISAGREE with the engine (recorded,
never censored) but can never alter a deterministic number. The **decision
journal** records the operator's calls vs the engine's and scores both at
21/63/252 trading days: the standing answer to "who is adding value."

## Tonight's multi-agent review — confirmed & fixed (commit 34d5a97)

8 specialist reviewers + adversarial verification with live reproductions.
8 findings confirmed, all fixed and regression-locked (`tests/test_review_fixes.py`):

1. yfinance debt/equity heuristic overstated low-leverage names **100x** (NVDA 0.066x shown as 6.5x).
2. `_coerce_fraction` corrupted >150% earnings growth (5.8 → 0.058; ~19pp E[r] understatement).
3. OpenFIGI joins could bind a CUSIP to the **wrong company** via foreign-venue ticker collisions (CNQ→"CRC"→California Resources). Now pinned to US composite equity.
4. Transient FIGI failures permanently cache-poisoned whole chunks → 60s backoff, retryable.
5. Stale past earnings dates shown as "next report" → today-or-later filter.
6. Past-only FMP estimate rows manufactured "forward" P/E → honest None.
7. A degraded provider stalled every analyze up to ~108s → 15-min negative cache.
8. Repo sleeves earned the derivatives premium; bond labels missed the debt branch → fixed anchors.

**Review dimensions still owed a deep pass** (subagent capacity hit the 4am
session limit): signal-math internals, web-layer threading, frontend state,
ai-copilot sanitization, decision-journal edge cases, test-gap analysis.
Spot-checked inline tonight (loop-closure in sec_events `_recent_rows` is safe;
chat send has a loading guard against double-submit; quick-log guards
duplicates; model-analyze macro path is cached-only so it adds no latency) —
but each deserves the full adversarial treatment. **Re-run the review workflow
on these six dimensions next session.**

## Shipped this week (all pushed to main)

dc46193 dual-track fundamentals ratings · ea5260a Fable/Opus copilot + dialogue
+ Treasury backbone · a8f0e5b Intrinio merge chain · b63ee92 decision journal +
SEC events · 4acfadd FMP consensus estimates + earnings calendar · 34d5a97
review fixes · c2514f2 macro intelligence layer.

## Roadmap — prioritized

**P0 (next session):**
1. Re-run the six unfinished review dimensions; fix confirmed findings.
2. **Fed full-text scoring** — RSS titles score near-zero (verified live:
   stance 0.0 across 16 docs because titles are administrative). Fetch the
   linked statement/speech body (bounded, cached) and run the hawk/dove lexicon
   over the full text. This is the single biggest macro-signal upgrade.
3. **Evidence Lab validation of the strategic track** — backtest whether
   CMA-vs-anchor gaps predicted relative forward returns across the book.
   Trust requires out-of-sample proof, not architecture.

**P1:**
4. Macro history persistence — store daily stance/GPR readings so the regime
   model can use *changes* (a hawkish turn matters more than a hawkish level).
5. Decision-journal review cadence — weekly digest: decisions pending outcome,
   scoreboard drift, biggest agree/override divergences.
6. Copilot tools: let the dialogue *fetch* (call /api/macro, sec_events, the
   decision journal) instead of relying on the pasted context.
7. FOMC calendar auto-refresh from federalreserve.gov each January (static
   list is honest but expires).

**P2:**
8. Options-implied vol/skew prototype (free yfinance chains) → conviction
   damper candidate; decides whether ORATS ($1.2k/yr) is worth buying.
9. EODHD for bond/mutual-fund fundamentals (duration, credit quality) — the
   income/CD-alt side of the book still has the thinnest data.
10. Earnings-call transcripts (FMP Ultimate upgrade) → copilot digestion.

## Data stack

FMP Premium (consensus estimates, calendars — stable API), Intrinio
(standardized fundamentals), yfinance (gap fill), SEC EDGAR (N-PORT, 8-K,
Form 4), GDELT (news + geopolitics), federalreserve.gov + whitehouse.gov RSS
(macro), CBOE yield indices (curve). Total paid: ~$95/mo. All licensed for
internal business use.

## Honesty invariants (do not break)

- Deterministic numbers never come from an LLM; the copilot argues, never edits.
- Unknown ≠ calm and unknown ≠ risk: unavailable sources apply no damper.
- Dampers shrink conviction; they never flip a direction.
- Every cap/clamp that binds is visible in the payload (and the copilot calls
  capped numbers suspect).
- No fetches in latency-sensitive request paths: radar/command-center read
  caches that background loops warm.
- Demo/sample data is never scored, never benchmarked, never presented as real.

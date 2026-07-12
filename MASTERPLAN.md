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

**Second review pass (2026-07-09) — 48 findings confirmed, 32 refuted.**
Fixed and regression-locked in this pass: bond funds receiving fabricated
equity growth blocks (BND read strategic BUY at ~95% conviction — provider
sector labels no longer count as equity evidence); FMP fiscal-year filter
(reported years masqueraded as forward for non-December filers); EDGAR client
forever-cache (stale 8-K/Form 4 served with fresh timestamps — 15-min TTL);
multi-series N-PORT fallback returning a SIBLING fund's holdings; failed
look-throughs cached forever; horizon-scaled forecast saturation (the tactical
action no longer flips mechanically with the slider); NaN-truthy fallbacks;
intraday cache-key collisions; insider signal now share-volume weighted with
honest parse counts + 8-K items 1.05/2.04; copilot dict-KEY redaction, stance
number-validation, chat-path scrubbing + injection guard, broadened dissent
detection; measured journal results made immutable; decision outcomes gated on
provenance-at-evaluation and stale-anchor hindsight; GDELT cache keying; FIGI
share-class normalization; stale AI narratives cleared on target switch;
sub-0.5% breach probabilities no longer render as "0%".

**Backlog from pass 2 (confirmed, not yet fixed):** /api/analyze does ~30
sequential network calls inline with no deadline (needs a fetch budget or
background hydration); forecast_long band-inflation leaks into drift and
monthly path sampling understates drawdown-breach probabilities (switch to
weekly steps); trend/momentum/Ridge triple-count the same momentum evidence
(reweight or orthogonalize); number-validation year-exemption and
payload-key harvesting weaken the invented-number check; decision scoreboard
mixes 21/63/252d outcomes in one hit rate and benchmarks everything vs SPY;
expected_return_pct reports the median (relabel or report mean); rationale
silently truncates at 800 chars; derivative-table Form 4s ignored; radar
error-state and >200-decision count polish. Plus 19 findings whose verifiers
hit the session limit — re-verify next session.

**Third review pass (2026-07-10/11) — 56 findings confirmed, ~53 fixed
(commits 83d4ab7…916d60e), all regression-locked.** The six previously-owed
dimensions got their full adversarial treatment. Highlights:

*Return math & CMA:* annualized return is geometric CAGR (arithmetic-mean
compounding reported +34.8%/yr on a flat round trip); CMA coverage measures
against the whole book (40% visibility no longer extrapolates to 100%);
building-block breakdown includes the asset-class anchor and reconciles with
the covered E[r]; genuine 0.0% forward returns display honestly; growth
provenance (forward CAGR / trailing annual / single-quarter YoY) is stamped
per provider and single-quarter comps shrink 70% toward the sector anchor;
FMP far-year estimates with 1-2 analysts can't set the growth block;
dividend yields pass through as true fractions (covered-call ETF 277% no
longer double-divided to 2.77%); forecast drawdowns count the t=0 base peak.

*Signals:* RSI reads 100/0 at the monotonic extremes (was fabricated neutral
50 forever on bill/cash funds); momentum score continuous at the 30/70 bands
(was a rating-flipping cliff); REIT sector anchor via industry_group.

*SEC/EDGAR:* stock-map hits classified via registrant submissions (SPY-style
N-PORT filers get real look-throughs, GLD/USO get honest ETP labels); fund
ticker-map outages refuse to guess fund-vs-stock (QQQ-as-permanent-stock-leaf
poisoning); N-PORT truncation keeps the top 10k BY WEIGHT; negative pctVal
rows accumulate signed; process-wide EDGAR throttle; Form 4/A supersedes;
unquantified share counts fall back to transaction-count direction;
malformed EDGAR payloads degrade instead of 500ing analyze.

*Copilot:* series structurally cannot reach the provider (token blocking +
size caps on lists AND dicts, observed audit flags); advisor free prose
blocked; identity keys redacted; colliding redacted keys keep every entry;
comma/e-notation numbers validate as single tokens with a context-aware year
exemption; the CHAT path runs the same number validation as tasks;
negation-aware, direction-aware dissent detection.

*Journals:* model decisions measurable (data_mode derived properly); pending
forward results can't starve (oldest-first pending queue); 7-day settlement
guards on both journals; outcomes score from the record date (no intraday
hindsight); benchmark alpha over the target's realized calendar window;
guarded merge on outcome writes; uploads trigger forward refresh; scoreboard
per-horizon breakdowns.

*Web/frontend:* analyze's provider fan-out bounded by the live semaphore with
honest cached-only degradation; fundamentals per-ticker (20s) and per-model
(45s) deadlines; single-flight macro snapshot; anchor cache generation token;
unchanged auto-live re-registration preserves memoized analytics; model
uploads reject blank weights and silent truncation; GDELT language filter
actually filters (query operator, verified 40/40 English); MandateFit
verdicts from a structured server block; stale AI narratives clear on data
change; no duplicate decision submits; failed refreshes don't render as
provenance lockouts.

**Not yet fixed from pass 3:** 3 findings whose verifiers hit the session
limit remain unverified; radar >200-decision count polish.

**External review (2026-07-11):** an independent full-project review arrived;
all 12 claims were adversarially re-verified against this repo (7 confirmed,
5 partial, 0 fully refuted — with a live reproduction of future-dated-data
contamination of the prospective journal). The phased response — integrity
hotfixes → honest validation → capacity engine → minimum-viable ledger + PIT
data layer → workspace UX — is the new working roadmap:
**see [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)**. It supersedes the
P0/P1 ordering below where they conflict.

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

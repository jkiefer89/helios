# Helios Implementation Plan — Response to the 2026-07-11 External Review

Every review claim was adversarially re-verified against this repo by 12 independent
verification passes (the reviewer worked from a different checkout). Verdicts:
**7 confirmed, 5 partially confirmed** (each with real substance but an overstated
framing), **0 fully refuted**. This plan sequences the fixes by risk-to-capital,
scoped for what Helios actually is: a private, single-operator research terminal
for a ~$1.1B book — never client-facing, execution always external.

## What the review got wrong (verified, no action)

- **Model library & onboarding examples (part of #1):** the seven preset portfolios
  are NOT loaded at startup — they enter only via explicit `import_template()`, are
  stamped `template_only` with a curated-template caveat, and face the same
  real-data gates as everything else. RealDataCenter's "example prices" are three
  copyable CSV *format snippets* in an import-help panel; they never enter any store.
- **"A page refresh becomes a published recommendation" (#9):** refuted as framed.
  Journal writes are idempotent upserts keyed on
  `(target, input_end, horizon, action, score)` with measured-immutability CASE
  guards; nothing is "published" (the deliberate-act channel is POST /api/decisions).
  The *real* residual defect is narrower — see Phase 0.4.
- **"No corporate-action normalization" (part of #7):** live fetches are
  split/dividend adjusted (`auto_adjust=True`) and each refresh rewrites the full
  series, so adjustment revisions propagate.
- **Frontend lint (part of verification):** confirmed at review time, fixed in this
  commit — `eslint` and `tsc -b` are both clean.

## What the review got right — the plan

### Phase 0 — Integrity hotfixes — ✅ SHIPPED (commit c2c4c06, 2026-07-11)

All five items below are implemented, regression-locked
(`tests/test_phase0_integrity.py`), and verified live: production boots with
55 live / 0 sample instruments, model analyze carries the series-basis label,
and the journal dashboard collapsed 10 real near-duplicate window observations
on first contact.

**0.1 Future-dated data is rejected, flagged, and unmeasurable** *(review #8 — confirmed
by live reproduction: a future-dated CSV parsed cleanly, showed `is_stale=False`, and a
"prospective" journal entry settled +2.02% against fabricated future bars)*
- `engine/data.py parse_csv`: reject any row beyond today+1 calendar day (tolerance
  for markets ahead of UTC), with a loud error naming the offending dates —
  rejection over silent dropping, because future dates almost always mean a broken
  export or DD/MM misparse that corrupted the past rows too.
- `engine/data.py fetch_live`: clamp provider bars to the same cutoff.
- `engine/data_quality.py`: `days_stale < 0` becomes a `future_dated` blocker issue
  (catches anything already persisted), not maximal freshness.
- `engine/signal_journal.py`: settlement guard becomes `0 <= lag_days <= 7` so a
  future-ending input window is never measurable.
- Constraint: do NOT touch `data.register` or `_forward_result` — test fixtures
  legitimately inject future bars there to exercise forward measurement offline.

**0.2 Samples are opt-in, and synthetic SPY can never move real scores** *(review #1 —
the sharp edges of a mostly-overstated claim)*
- `helios_web/__init__.py`: gate `load_samples()` behind `HELIOS_LOAD_SAMPLES=1`,
  default OFF. Six synthetic GBM histories currently park under REAL tickers
  (AAPL, MSFT, NVDA, TSLA, SPY, BTC-USD) in the same store as real data. A fresh
  start should land on the honest empty state (which already exists and renders
  correctly). Tests are unaffected — conftest calls `load_samples()` directly.
- Close the verified leak: `engine/regime.py market_regime` silently uses sample
  SPY as the market-regime proxy when live SPY is absent, and a sample-derived
  risk-off label penalizes REAL candidates −8 in the opportunity radar. Fix: warn
  on non-real proxy, and skip the regime penalty in `opportunity.score_candidate`
  when the proxy source isn't real.

**0.3 The model series says what it is, everywhere** *(review #2 — confirmed)*
- `build_series` provenance gains `series_basis: "weight_rescaled_research_series"`
  + one-sentence note ("daily-rebalanced, cost-free construction at target
  weights — a research basis, not a performance track record"). Because clinic,
  reports, and model-analyze forward `ps.provenance` verbatim, the label rides
  along for free.
- `model_analyze` payload gains the top-level field + the `ANALYSIS_ONLY_DISCLAIMER`
  it is currently missing (unlike every sibling endpoint), and the model Analysis
  view renders the one-line label + the currently-dropped `payload.warnings`.

**0.4 One forward window = one observation** *(review #9 — the real defect)*
- Re-viewing a ticker intraday (sentiment/macro/price drift → score moves ≥0.001 →
  new dedupe key) creates near-duplicate journal rows over the IDENTICAL forward
  window; frequently-viewed names accrue pseudo-replicated weight in hit-rate/alpha
  evidence. Fix in `engine/signal_journal.py`: collapse aggregates by
  `(target, input_end_date, horizon)` keeping latest, surface `superseded_count`
  honestly, leave raw rows as the audit trail. Normalize `-0.0` in the dedupe key.

**0.5 Walk-forward evidence is labeled at the point of consumption** *(review #3 —
partial; the composite genuinely cannot be backtested without look-ahead fabrication)*
- Evidence Lab headline stats test a trend+momentum proxy — disclosed today only in
  a methodology footer. Put the basis in the Hit Rate panel meta ("trend+momentum
  proxy — not the live composite rating") and label the prospective panel as the
  exact-composite record. No math changes.

### Phase 1 — Honest validation — ✅ SHIPPED (commit a448fa1, 2026-07-11)

All three items below are implemented, regression-locked
(`tests/test_phase1_validation.py`), and verified live: the champion now
reports "best of 2, selection-biased upward" with an honest
insufficient-holdout status and a Bonferroni-adjusted band, and the auto-live
loop recorded 57 composite snapshots on its first cycle.

**1.1 Champion selection stops flattering itself** *(review #4 — confirmed; Medium)*
- `engine/model_validation.py` crowns the max validation_score and reports that same
  score as the champion's evidence — max-of-N noisy estimates is upward-biased, no
  correction anywhere. Fix, proportionate to N≈handful of models:
  a) `selection` block in the payload: n_trials + plain-English selection-bias basis,
     rendered under the champion tile;
  b) Šidák/Bonferroni-adjusted champion CI (`z` at `1−0.10/(2N)`) reported next to
     the naive band — the honest analogue of deflated-Sharpe at this scale;
  c) chronological 80/20 split: rank on the first segment, report the champion's
     untouched last-segment stats as `holdout_confirmation` (or honest
     `insufficient_windows` below ~8 measured windows);
  d) champion-level `prospective_confirmation` from the signal-journal track.

**1.2 The composite signal accrues systematic out-of-sample evidence** *(review #3;
Medium)*
- Daily auto-record in the auto-live loop: once per UTC day per real-eligible
  instrument/model, compute `signals.evaluate` from cached inputs only (never block
  the loop on network) and record with `metadata.endpoint="auto_snapshot"`.
  Removes usage bias from the prospective track — evidence accrues even when the
  operator doesn't click. Existing real-eligibility + freshness guards apply.

**1.3 Assumptions become versioned, dated, and visible** *(review #10 — partial;
Medium)*
- New `engine/assumptions.py`: the four static tables (sector fair-PE/growth
  anchors, asset-class premia, factor loadings, static ADV proxies) wrapped with
  `as_of` + one honest methodology sentence + `ASSUMPTIONS_VERSION`; payloads
  surface `fair_pe_anchor`/`anchor_as_of` in CMA blocks and
  `factor_exposure_basis: "static hand-assigned loadings — not regression betas"`
  in risk methodology.
- Fix the verified data bug: the Liquidity-stress scenario scores from the static
  ADV map even when observed 60-day dollar volume exists — use observed-first
  precedence like the flags surface already does.
- Frontend truth-in-labeling: RiskAnalytics stops claiming factor exposure derives
  "from real model histories."
- Optional follow-on behind an env flag: dated FMP sector-PE ingest replacing the
  static fair-PE table when live (falls back to the labeled static table offline).

### Phase 2 — Sizing, capacity, and the UX quick fixes — ✅ SHIPPED (commit d278d0c, 2026-07-11)

Both items implemented, regression-locked (`tests/test_phase2_capacity.py`),
and verified live: at $1.1B AUM the capacity engine flagged ARTY at 5.3
days-to-liquidate @10% ADV (~200bps est. impact, posture "watch"→"elevated"),
the 13-item nav fits exactly at 1280px, and the fetch/upload CTAs reveal the
intake panel.

**2.1 Capacity engine** *(review #6 — confirmed; Medium)*
- `analyze_model_risk(aum_usd=...)` (query param + `HELIOS_MODEL_AUM_USD` default;
  `status: "aum_not_set"` when absent — never assume a number). Per holding:
  position $, one-day ADV participation %, days-to-liquidate at 10%/20% caps,
  square-root impact estimate labeled order-of-magnitude, `adv_source` carried so
  proxy-based rows are flagged. Summary: max/weighted days-to-liquidate, counts
  >5/>20 days, `capacity_constrained` verdict feeding risk posture and the Clinic.
  This directly answers "does this work at $1B."

**2.2 UX phase 1 — overflow + onboarding dead end** *(review UX — confirmed; Small)*
- Nav: overflow fade mask at ALL widths (currently ≤860px only), hide group labels
  1181–1460px (recovers ~400px → 13 items fit at 1280px), `scrollIntoView` on the
  active button.
- Dead end: the "Fetch live ticker data" CTA opens Instruments, but the only
  fetch/upload UI lives in a default-collapsed sidebar. Thread an
  `onRevealDataIntake()` callback so CTAs open the view AND expand the intake
  panel; give the Instruments/RealDataCenter empty states action buttons.

### Phase 3 — Real outcomes — ✅ CORE SHIPPED (commit 8f29c3b, 2026-07-12)

Slices 3.1 A–D and data stages 0–2 are implemented, regression-locked
(`tests/test_phase3_ledger.py`), and verified with a live end-to-end
roundtrip: fills + two snapshots imported → Modified-Dietz TWR net 1.326% /
gross 1.329% with a mid-period $50k flow and fees handled exactly; re-import
proved idempotent; PIT fundamentals snapshots now accrue on every fetch.
Stage 3 shipped with Phase 4 (opt-in behind HELIOS_PRICE_SOURCE=fmp with a
live reconciliation gate); only the deferred survivorship-vendor decision
remains open, by design.

**FMP cutover EXECUTED 2026-07-12**: book-wide reconciliation passed — 48/55
instruments compared over 62 overlapping days, worst mean divergence 0.0079%
(DTCR), zero above the 0.1% gate; the 7 without FMP EOD coverage (BTC-USD,
five Fidelity funds, SOIL) fall back to yfinance per fetch. HELIOS_PRICE_SOURCE=fmp
is set in .env; every persisted history now carries a `price_provider` label
(threaded through fetch/refresh/auto-live persist paths, locked in
tests/test_deep_review_2.py). The cutover test also exposed and fixed a
refresh-path bug: a hard yfinance pre-check that would have broken
FMP-source refreshes if yfinance were ever absent. Full-book refresh after
cutover: 55/55 ok, 48 on fmp_eod_adjusted / 7 on yfinance.

**3.1 Minimum viable ledger** *(review #5 — confirmed; Large, 4 slices)*
- Slice A (S): three tables — `trade_fills` (idempotent dedupe key, optional link
  to `decision_journal.decision_id`), `account_snapshots`, `account_positions`;
  account→model mapping in metadata.
- Slice B (M): `engine/ledger.py` custodian-CSV import (alias-based column matching
  like model upload; unparsed rows are warnings, never silent drops) + upload panel.
- Slice C (M): Modified-Dietz sub-period returns chain-linked to TWR, gross and
  net-of-fees, cash drag vs T-bill — rendered side-by-side with the paper NAV,
  every number labeled actual vs research.
- Slice D (M): implementation shortfall per decision — fills linked to a decision
  journal entry vs the decision-date paper price; closes the loop the review
  correctly says is open ("decision → outcome" becomes decision → fill → net P&L).

**3.2 Data layer, staged by value-per-dollar** *(review #7 — partial; Large program)*
- Stage 0 (S): `as_of` on Fundamentals, threaded into CMA blocks and analyze payloads
  (cached-vs-fresh becomes visible).
- Stage 1 (M, highest value): `fundamentals_snapshots` table — append every usable
  fetch per (symbol, date). Zero vendor cost; Helios's own operation becomes the
  point-in-time database that later validates the strategic track honestly. Check
  whether the Intrinio tier unlocks historical data-point backfill.
- Stage 2 (M): vendor reconciliation in `_merge_raw` — >25% disagreement on
  trailing_pe/dividend_yield/debt_to_equity attaches warnings through the existing
  provenance channel (warn, never block).
- Stage 3 (M): FMP stable EOD becomes the primary price source (paid, keyed SLA);
  yfinance demotes to fallback. Run side-by-side before cutover; never rewrite
  history inside an open measurement window.
- Deferred until cross-sectional strategy mining begins: survivorship-free history /
  index-membership vendors (Norgate ~$30/mo, Sharadar, EODHD). Do NOT buy
  enterprise lineage tooling.

### Phase 4 — Structural UX + test floor — ✅ SHIPPED (commit d94eec4, 2026-07-12)

Both items implemented and verified live: the five workspaces render with
view tabs and a persistent context bar, deep links resolve through the
unchanged hash routing, and 7 Vitest locks guard the honesty-critical
provenance rendering. Data stage 3 also shipped alongside: FMP EOD is
available as an opt-in primary price source behind HELIOS_PRICE_SOURCE=fmp,
gated by /api/data/price-reconciliation (first live run: JPM mean diff
0.0008% over 62 days — cutover-safe pending a book-wide check).

**4.1 Workspace consolidation** *(review UX phase 2; Large)*
- The code already groups nav into exactly the reviewer's five workspaces
  (Overview/Data/Research/Portfolio/Output) as visual dividers in one overflowing
  bar. Promote them: 5 workspace buttons + second-tier tab strip; persistent
  context bar (selected model, data-mode badge, as-of); ImportPanel folded into the
  Data workspace as a first-class destination. Keep `ViewId` + hash routing
  contract unchanged so every deep link, palette action, and `onOpenView` call
  keeps working — an IA change, not a rewrite.

**4.2 Frontend test floor** *(review verification gap; Medium)*
- Vitest + testing-library, 4–6 focused tests on the honesty-critical rendering:
  data-mode badge tones, research-locked vs eligible banners, source pills,
  missing-ticker lists. No Cypress/e2e program — one operator, manual workflow
  verification is proportionate.

## Explicitly rejected or rescoped from the review

- **RBAC, independent validation teams, DR, formal model inventory:** enterprise
  controls for organizations. For one operator: encrypted store + immutable
  measured results + append-only journals + git history already cover the intent;
  a periodic encrypted DB backup task is the only piece worth adding (folded into
  Phase 3 Slice A).
- **GIPS presentation:** Helios is never client-facing, so composite-presentation
  standards don't apply — but the actual/hypothetical/model separation principle is
  adopted everywhere (Phases 0.3, 3.1C).
- **"Zero-fabrication: fresh install shows empty state":** adopted (Phase 0.2) —
  but as an env-gated default, not deletion of the sample layer, which tests and
  offline demos legitimately use via explicit opt-in.

## Sequencing and effort

| Phase | Items | Effort | Risk it retires |
|---|---|---|---|
| 0 | 0.1–0.5 | ~1 session | Data contamination, silent mislabeling, evidence inflation |
| 1 | 1.1–1.3 | ~2 sessions | Winner's curse, unvalidated composite, silent assumptions |
| 2 | 2.1–2.2 | ~1 session | $1B-blindness, onboarding dead ends |
| 3 | 3.1–3.2 | multi-week, incremental | Paper-only outcomes, no PIT data |
| 4 | 4.1–4.2 | multi-week, incremental | Navigation debt, zero frontend tests |

Standing invariants (unchanged): never fabricate; degrade gracefully; label every
source; a wrong number is worse than a missing number; "Unavailable" is an
acceptable answer.

---

# Deep Review 2 — Economic Edge & Product OS (2026-07-12)

A second, deeper external review (verdict: NOT CAPITAL-READY — engineering
7/10, economic proof 2/10) landed after all five phases above shipped. All 12
of its checkable claim clusters were adversarially re-verified against this
repo: **10 confirmed, 2 partial, 0 refuted** — including two live
reproductions (a 42%-accuracy forecast flipping HOLD→BUY at full weight; the
champion CI collapsing to 100–100). Notable bounded sub-claims: Strategy Lab
DOES net costs, the ledger DOES label gross/net, and the Node-23.7 claim was
stale. The review's core verdict is accepted: Helios organizes research
honestly but has not proven repeatable net alpha — that proof can only come
from the prospective track now accruing.

## Batch 1 — evidence honesty — ✅ SHIPPED (2026-07-12)

- **Forecast edge gate** (D2, reproduced): the forecast component's weight is
  now EARNED — zero at ≤50% measured out-of-sample directional accuracy, full
  only at ≥55% (n_test ≥ 40; unmeasured keeps weight with a caveat). Gated
  weight is never redistributed. Live: JPM gated to 34%.
- **Gross-of-costs labeling + shared cost constant** (D1): every alpha surface
  says "gross of trading costs"; `engine/costs.py` holds the single 5 bps/side
  default (Strategy Lab semantics) and evidence summaries carry a
  presentation-level `avg_alpha_after_default_costs_pct` — stored journal rows
  stay raw.
- **Wilson champion band + honest trial disclosure** (D4): no more zero-width
  CIs at p=0/1; `n_trials_not_counted` names what the correction cannot see.
- **Overlap-adjusted confidence bands** (D5): CI uses effective
  N = windows/(horizon/step); the 63-day decay row at step 21 now widens by √3.
- **Span-aligned evidence benchmark** (D6): both endpoints last-bar-at/before
  the target's exact window dates; uncovered windows stay unresolved.
- **Exact composite made exact** (D3): journal metadata persists the full
  component breakdown, weights, vol/mandate/event dampers, and records a
  missing strategic leg explicitly; UI copy is date-honest about older rows.
- **Empty-state regime is unavailable, not NEUTRAL 50** (D7): no benchmark
  data → status "unavailable", score null, locked panel with one recovery
  action — never a fabricated meter.
- **Cloud narrative default-off** (D10b): report saves invoke the provider
  only on an explicit, persisted opt-in labeled with its consequence.
- **CI hardening** (D12): frontend lint + Vitest in CI, coverage floor 80,
  Node pinned (`.nvmrc` 22, engines ≥22.13); context-bar grammar fixed.

## Batch 2 — SHIPPED 2026-07-12

- **Ledger v2 honesty** (D8): non-trade activity persisted as typed rows
  (dividend/interest/fee/tax/corporate_action) with disclosed dollar impact;
  per-period cash reconciliation (ok/mismatch/uncheckable, tolerance
  max(0.5%·V0, $1)); Dietz labeled as an estimate in the UI; shortfall anchor
  frozen at the journaled decision_price (recomputed anchors labeled as
  drift-prone); exec-id joins the fill dedupe key when present; fills capped
  at 20k with a truncation warning.
- **PIT first+last-of-day slots** (D9): schema v9 — PK (symbol, as_of, slot)
  with retrieved_at + persisted reconciliation warnings; 'first' is immutable
  (INSERT OR IGNORE), 'last' is current (INSERT OR REPLACE); migration copied
  all existing rows as slot='last' (verified live: 52/52 preserved, no
  leftover tables). Same-day revisions no longer erase the morning
  observation, and first-vs-last diff exposes intraday flips for free.
- **GET purity where it matters** (D11): GET /api/decisions is a pure read —
  outcome scoring runs at the data-refresh choke point
  (`refresh_pending_outcomes` beside the signal-journal forward refresh);
  data-quality alert sync is idempotent (occurrence_count counts distinct
  raisings, not page views); macro force-refresh is POST /api/macro/refresh
  (GET ignores ?refresh); the contract is documented above
  _CSRF_SAFE_METHODS. View-triggered idempotent journal capture stays — it
  is the design.
- **UX remainder** (D10): palette aliases ("Ledger — Actual vs Paper" →
  Decision Journal, "Saved report snapshots" → Reports); "Fetch missing"
  recovery action on blocked model rows (sequential live fetch capped at 10,
  falls back to revealing data intake); context bar renders a clean empty
  state instead of comparing against a sentinel string. ("Auto-selected"
  labeling skipped: selection provenance isn't tracked in App state and the
  label would be a guess.)
- **Route tests** (D12): tests/test_web_ledger.py + tests/test_web_decisions.py
  pin all 6 ledger + 2 decisions route contracts at the HTTP level (status
  codes, validation, response shapes, idempotent re-import, exact cash
  reconciliation). Backend suite 551 green.

## Rescoped or rejected from Deep Review 2 (same single-operator logic)

Accepted in principle, deferred pending scale: security master + raw vendor
vault + corporate-action ledger (buy, don't build — revisit with
cross-sectional mining); experiment registry (MLflow) before adding model
variants; PBO/DSR as validation outputs; custodian API adapters (IBKR Flex
pattern) replacing CSV as the primary actuals path; constrained optimizer
(CVXPY) for current-to-target proposals. Rejected for one operator: SSO/RBAC/
MFA, Postgres/Alembic, Dagster/Celery, OpenTelemetry, hash-chained audit —
the intent (named actors, durable audit, tested restore) is already served at
this scale by the encrypted store, append-only journals, immutable measured
results, and git history. The review's own standard is adopted as the north
star: another qualified reviewer should be able to reproduce every input,
calculation, and decision as-of the time it occurred.


## Completion batch — SHIPPED 2026-07-13 (operator: "don't come back until all these are complete")

Everything previously rescoped/deferred, built pragmatically at single-operator
scale. Excluded by explicit operator decision: the experiment registry (its
value accrues only once model variants are being iterated) and the
shadow-evidence clock (inherently time-based).

- **Shared net-cost engine**: `engine/costs.py` is now the single source —
  Strategy/backtest/API defaults import `DEFAULT_COST_BPS_PER_SIDE` (were four
  duplicated literals); Signal Journal, Decision Journal, model validation,
  Evidence Lab (decay/regime/empty shapes), and PDF/HTML report exports all
  carry `avg_alpha_after_default_costs_pct` + basis labels beside gross;
  `opportunity._backtest_quality` charges the benchmark its round trip so the
  score's alpha is like-for-like (was net-strategy vs gross-benchmark).
  Stored journal rows stay gross (locked by test).
- **Forecast calibration**: `forecast._calibration` — Brier vs the 0.25 coin,
  P(up)=Phi(pred/train-residual sigma) (disclosed transform), realized accuracy
  by |prediction| terciles; surfaced in the Analysis forecast panel. Live JPM:
  Brier 0.253, flat bins — independently confirms the edge gate's verdict.
- **DSR + PBO**: Bailey–López de Prado Deflated Sharpe (luck hurdle rises with
  trial count) and CSCV PBO over date-aligned walk-forward alpha windows, both
  as validation-dashboard outputs with honest data floors (DSR ≥10 windows/≥2
  trials; PBO ≥3 models/≥16 aligned windows).
- **Schema v10 data spine**: security_master (identity upsert that never
  clobbers known facts), vendor_vault (hash-deduped raw fundamentals payloads),
  price_revisions (silent restatements only — uniform dividend re-adjustment
  shifts are excluded by median-ratio logic), corporate_actions
  (splits/dividends captured on live fetch), and a hash-chained audit over
  decision/outcome/fill writes with `audit_verify()` re-deriving every link
  (tamper names the first bad seq). Persisted `price_provider` now also
  hydrates into memory on restart.
- **IBKR Flex adapter** (`engine/custodian.py`, dormant until
  HELIOS_IBKR_FLEX_TOKEN/QUERY_ID): SendRequest→GetStatement with
  generation-retry, trades/cash-transactions/positions/cash-report mapped onto
  the ledger contract, fill dedupe keys byte-identical to the CSV path so both
  paths reconcile to no-ops. POST /api/ledger/flex/import.
- **Current-to-target optimizer** (`engine/rebalance.py`, CVXPY QP with a
  deterministic capped-projection fallback): position caps, one-way turnover,
  ADV-participation liquidity budget, cash buffer, long-only; cost-aware
  no-trade zone; dust suppression; real-prices-only (blocks, never invents);
  target-unreachability NAMED per constraint with shortfalls. POST
  /api/rebalance/propose + proposal panel in Decisions.
- **Jobs & freshness** (GET /api/data/jobs + Data Quality panel): auto-live
  state, refresh failures, per-symbol bar age + provider label, audit-chain
  status, recent vault entries and price revisions.
- **Frontend prescriptions**: Output workspace renamed Results; automatic
  first-ticker/model selection removed (App, Analysis, Evidence Lab — views
  with visible pickers keep their labeled dropdown defaults); net-alpha
  companions rendered beside gross in Evidence Lab and Signal Journal;
  calibration note in Analysis; DSR/PBO line in Model Validation; routing
  contract + Results/palette locks in Vitest (11 frontend tests) and
  tests/test_frontend_static.py.

Suites at ship: 586 backend + 11 frontend green; typecheck/lint/build clean;
live-verified against the running terminal (jobs panel, provider hydration
48 fmp/7 yfinance, calibration on JPM, DSR/PBO degradation, rebalance
blocked-path, Flex dormant-path).

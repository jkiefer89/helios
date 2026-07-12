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

### Phase 3 — Real outcomes (the biggest capability gap)

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

### Phase 4 — Structural UX + test floor

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

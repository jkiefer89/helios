# Helios Institutional Readiness Implementation Record

**Updated:** 2026-07-13
**Review baseline:** `6ba715e40a44fba88178c08f377e7cb1e2a97e07`
**Source review:** external review supplied out of band; this plan is the tracked implementation record.

This document records the implementation response to the institutional-readiness
review. It is not a claim that Helios can guarantee returns, execute trades, or
replace licensed data contracts, independent validation, an identity provider,
or an investment committee.

## Non-negotiable invariants

- Helios is analysis-only. It does not place orders, provide brokerage services,
  give investment advice, or guarantee returns.
- Deterministic Helios calculations remain authoritative. Optional AI may explain
  sanitized outputs but cannot change scores, forecasts, actions, or gates.
- Production startup contains no generated market universe, fabricated ranking,
  or filler research row. Ineligible fixture sources remain recognizable only so
  legacy/test data can be rejected; they cannot unlock research.
- Institutional controls fail closed. Missing provider approval, operational
  evidence, independent validation, governance approval, or trusted deployment
  controls blocks the capital-relevant action while leaving remediation available.
- Every capital-relevant record carries source, time range, row count, model or
  calculation version, and immutable evidence references sufficient for replay.

## Phase 0 - Integrity and reproducibility

Implemented:

- CVXPY is a pinned runtime dependency and CLARABEL is the required rebalance
  solver. No heuristic optimizer silently replaces a failed solve.
- Safe hash-route decoding rejects malformed deep links without crashing.
- GET endpoints are read-pure; journal, trial, governance, incident, report, and
  provider mutations require explicit write requests.
- React is the only UI. Missing `frontend/dist/` produces a build-instruction
  page rather than a legacy dashboard.
- Empty startup is honest: no runtime fixture loader, placeholder ranking, or
  generated research output.
- Clean-install verification includes lockfile installs, frontend lint/typecheck,
  component tests, production build, Playwright workflows, Python compile, JSON
  validation, and backend coverage.

Exit criterion: a fresh clone can reproduce the tested build and cannot produce
research from missing or ineligible evidence.

## Phase 1 - Economically correct decisions

Implemented:

- Signals use explicit BUY/HOLD/SELL direction. Directional calls require
  benchmark-relative alpha to count as a paper hit; missing benchmark evidence
  remains unavailable rather than becoming absolute-return alpha.
- Rebalance plans include shares, cash, as-of prices, transaction-cost estimates,
  residual drift, unreachable targets, and solver metadata.
- Rebalance sizing blocks stale or missing prices and fails closed when the
  optimization contract is unavailable.
- Strategy evidence includes entry and exit costs, uses log-return forecasting,
  and applies positions after observation to preserve no-lookahead behavior.
- Forecast contribution is earned from rolling-origin out-of-sample evidence and
  is withheld when measured edge is absent.
- Governance, independent-validation, provider, operational, and report-purpose
  gates block approval or external-facing export when required evidence is absent.

Exit criterion: an action cannot look investable merely because the benchmark,
cost, cash, or implementation facts were omitted.

## Phase 2 - Immutable data and evidence lineage

Implemented:

- Evidence envelopes capture canonical inputs and outputs, source/provider,
  retrieval time, transformations, model version, calculation version, series
  hashes, date range, and row count.
- Capital-relevant rows, immutable evidence, and required audit links commit in
  one durable transaction. Encrypted critical writes synchronously materialize
  the snapshot and restore their pre-write image if durability fails.
- Replay independently recalculates signal facts, operator decisions, governance
  snapshots, report HTML, forward returns, benchmark returns, alpha, and hit
  classifications. Hash integrity alone is not reported as calculation replay.
- Account snapshots are append-only revisions with correction reasons, content
  hashes, supersession links, and retained position revisions.
- Fundamentals are retained in point-in-time slots; corporate actions and price
  revisions are recorded rather than silently overwritten.
- Live fetch, refresh, automatic universe refresh, and uploads all pass through
  the same validation/persistence promotion boundary.
- Production does not manufacture missing prices, holdings, fundamentals,
  rankings, alerts, or model results.

Exit criterion: every signal, decision, approval, trial assessment, forward result,
and saved report can be tied to immutable evidence and checked deterministically.

## Phase 3 - Prospective economic evidence

Implemented:

- Prospective trial protocols freeze target/model snapshots, every holding's
  history hash, allowed sources, freshness, horizon, benchmark, costs, eligibility,
  and assessment rules before measurement.
- Trial assessment rejects post-registration history-prefix changes and evidence
  outside the registered window.
- Evidence Lab reports rolling and anchored out-of-sample results, regime
  sensitivity, false positives, decay, confidence intervals, and multiple-testing
  diagnostics.
- Signal and decision journals distinguish pending, partial, measured, and
  not-measurable outcomes without retroactively changing the recorded call.
- Paper, proposed, and mapped-custodian actual outcomes remain separate. Actual
  comparisons are scoped to explicitly mapped accounts and the trial window.
- Capacity and AUM inputs are explicit; absent capacity evidence remains a blocker
  rather than an assumed pass.

Exit criterion: a model can earn credibility only through preregistered,
prospective evidence. Historical paper evidence is not marketed as guaranteed
alpha or live implementation performance.

## Phase 4 - Institutional operations and security

Implemented in Helios:

- Provider registry for explicit license, entitlement, SLA owner, primary/backup
  reconciliation, integrity-checked cutover, and fail-closed provider selection.
- Governed price fetches do not silently call unapproved yfinance metadata, news,
  or corporate-action side channels.
- RBAC permissions, MFA-required privileged actions in institutional mode,
  trusted-proxy SSO boundaries, bounded sessions, CSRF/host checks, and hardened
  response headers.
- Privileged and application audit chains, explicit incident lifecycle, encrypted
  local persistence, encrypted backup creation, and isolated backup verification.
- Institutional readiness gate requiring persistence, encryption, recent restore
  verification, intact audit chains, no unresolved critical incident, named owner,
  authentication, MFA/SSO, and trusted TLS assertion.
- Independent model-review records with sponsor/validator separation, dated
  outcomes, bounded exceptions, expiration, and review evidence.

External controls still required:

- Executed primary and backup data-provider contracts, entitlements, and SLA.
- A separately managed IdP with enrolled MFA and a trusted reverse proxy or
  approved certificate authority.
- Off-host key custody, separately administered immutable audit retention/WORM or
  SIEM, backup custody, scheduled restore drills, incident runbooks, and named
  operational ownership.
- Independent human model validation and investment-committee authorization.

Helios cannot create or truthfully mark those external facts complete. The product
surfaces their absence and blocks external-ready use until operators record valid
evidence.

## Phase 5 - Operator workflow

Implemented:

- Five task-oriented groups: Setup, Research, Evidence & Risk, Decisions, Reports.
- Command Center presents readiness, blockers, recent changes, and direct
  remediation rather than decorative research rows.
- Shared data-quality gates provide the same reason and next action across
  Analysis, Opportunity Radar, Strategy Lab, Portfolio Clinic, and Reports.
- Data intake follows the operator: price import routes to Data Quality; model
  import routes to Models; coverage repair returns to the affected workflow.
- Accessible custom selects support listbox semantics and keyboard navigation;
  data-heavy journal and instrument surfaces use semantic tables with contained
  scrolling.
- Loading, blocked, empty, error, and retry states are explicit. A transient
  Command Center failure can be retried in place.
- Playwright covers blocked first run, price import, partial model coverage,
  missing-holding repair, validation, ranking, model analysis, explicit signal
  recording, strategy and evidence review, portfolio risk, decision recording,
  report preview and save history, deep routes, global controls, mobile ordering,
  and mobile overflow.

Exit criterion: an operator can answer "what is blocked, why, and what do I do
next?" without interpreting implementation details or encountering fabricated
content.

## Verification ladder

```bash
./.venv/bin/python -m pip install -r requirements.lock
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run test
npm --prefix frontend run build
HELIOS_E2E_PYTHON=../.venv/bin/python npm --prefix frontend run test:e2e
./.venv/bin/python -m compileall app.py serve.py engine helios_web tests
./.venv/bin/python -m json.tool .design_spec.json >/dev/null
./.venv/bin/python -m pytest --cov=engine --cov=helios_web --cov=app \
  --cov-report=term-missing --cov-fail-under=80
./.venv/bin/python -m pip check
git diff --check
```

Tests and browser runs use temporary persistence and mocked/offline provider
boundaries. They do not call cloud AI, local model servers, production services,
or live market endpoints.

## Capital-allocation gate

Helios is not ready to claim a durable money-making edge merely because this
implementation record is complete. Capital allocation requires, at minimum:

1. Approved licensed data and backup-provider operations.
2. Independent validation of the exact model version.
3. A preregistered prospective trial that reaches its declared horizon without
   history-prefix mutation or retrospective rule changes.
4. Benchmark-relative, after-cost evidence with capacity and liquidity support.
5. Committee approval and external operational controls appropriate to the
   mandate and capital at risk.

Until those conditions are satisfied, Helios remains a governed research and
paper-evidence system, not proof of future profit.

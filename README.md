# Helios — Investment-Model Analytics & Trade-Signal Platform

A local web dashboard for analyzing client investment models from price/return
history, forecasting forward returns with confidence bands, and producing
explainable **BUY / SELL / HOLD** trade signals that blend technical trend,
momentum, the return forecast, and news sentiment.

> **Analysis only.** Helios never places orders or moves money. It is a research
> and decision-support tool. Forecasts are statistical estimates, not guarantees.

---

## Quick start

Source-of-truth tooling is intentionally simple: Python dependency *ranges*
live in `requirements.txt` / `requirements-dev.txt`, the exact tested pins live
in `requirements.lock` (what CI installs), frontend dependencies are npm-based
and locked by `frontend/package-lock.json`, and CI is defined in
`.github/workflows/ci.yml`. There is no Makefile, Dockerfile, or
`pyproject.toml` in this repo.

The lockfile flow: `requirements.lock` is what both CI and `./run.sh` install,
so runtime and tests always use the exact versions the suite was verified
against. `requirements.txt` / `requirements-dev.txt` hold the human-edited
compatible ranges the lock is resolved from. When a Python dependency changes,
update the range file *and* regenerate the lock from a clean venv
(`pip freeze`) so CI keeps testing real pins.

Local development needs Python 3 with `venv`/`pip`; React frontend work also
needs Node/npm on `PATH`.

```bash
./run.sh
```

This starts Helios **live on your local network** (production `waitress` server,
bound to all interfaces) behind a password gate. It prints something like:

```
  Reachable on your local network at:
    • this machine : http://127.0.0.1:5000
    • on your LAN  : http://192.168.11.245:5000
  Login (HTTP Basic Auth):
    user     : advisor
    password : 7Qx-Lp2vK9    (auto-generated — set HELIOS_PASSWORD to choose your own)
```

Open the **LAN URL** on any device on the same Wi-Fi/office network and log in.
The first run creates `.venv/` and installs runtime Python dependencies,
including `openpyxl` for Excel model uploads. `yfinance` is installed for
optional live data, but the app remains fully usable offline with bundled sample
data. Live/imported price histories and uploaded model metadata are stored in a
local SQLite database at `.helios/helios.db` by default, so real-data work
survives an app restart without publishing client files to git.

Helios uses a React + Vite + TypeScript frontend. Build it with npm before
starting Flask when you want the React app at `/`:

```bash
npm --prefix frontend ci
npm --prefix frontend run build
./run.sh
```

The React app is the only UI. If `frontend/dist/` is absent, `/` serves a
minimal self-contained page with those build instructions (the JSON API stays
fully available) until the build exists.

### Going live on your network

Use placeholders only in committed docs/config. For local overrides, copy
`.env.example` to an untracked `.env` or export variables in the shell that
starts Helios. A repo-local `.env` is loaded automatically at startup without
overriding variables already exported in the shell; set `HELIOS_LOAD_DOTENV=0`
to disable that.

| Variable | Default | Purpose |
|----------|---------|---------|
| `HELIOS_USER` | `advisor` | Basic-auth username |
| `HELIOS_PASSWORD` | *auto-generated* | Basic-auth password — **set this** for a stable login |
| `HELIOS_PORT` | `5000` | Listen port |
| `HELIOS_HOST` | `0.0.0.0` | Bind address (`127.0.0.1` = localhost only) |
| `HELIOS_TLS` | `0` | `1` serves self-signed **HTTPS** (encrypts the login) |
| `HELIOS_AUTH` | `1` | `0` disables the password gate (localhost dev only) |
| `HELIOS_RF` | `0.02` | Risk-free / CD benchmark rate used by mandates and projections |
| `HELIOS_DB_PATH` | `.helios/helios.db` | Local SQLite store for parsed live/uploaded data (`off` disables persistence) |
| `HELIOS_DB_ENCRYPTION` | `auto` | Encrypt sensitive local persistence payloads; use `required` to fail closed without a key |
| `HELIOS_DB_ENCRYPTION_KEY` | empty | Optional Fernet key for persistence encryption; keep it local and never commit it |
| `HELIOS_DB_ENCRYPTION_KEY_PATH` | `<HELIOS_DB_PATH dir>/helios.key` | Optional local key-file path when `HELIOS_DB_ENCRYPTION=auto` |
| `HELIOS_LOAD_DOTENV` | `1` | Load a repo-local `.env` at startup (`0` disables; exported shell vars always win) |
| `HELIOS_AUTO_LIVE_SYMBOLS` | `core` (auto-disabled when `HELIOS_DB_PATH` is off) | Automatic live polling universe; use `off` to disable or provide tickers/presets |
| `HELIOS_AUTO_LIVE_PERIOD` | `2y` | Provider history window fetched by automatic live polling |
| `HELIOS_DATA_QUALITY_STALE_DAYS` | `7` | Data Quality stale-symbol diagnostic threshold |
| `HELIOS_DATA_QUALITY_MIN_RESEARCH_ROWS` | `60` | Minimum valid rows for research-readiness diagnostics |
| `HELIOS_DATA_QUALITY_INSTITUTIONAL_ROWS` | `252` | Institutional history target for short-history diagnostics |
| `HELIOS_GOVERNANCE_APPROVER_PIN` | empty | Local committee PIN; when set, governance approve/reject requires identity + PIN |
| `HELIOS_GOVERNANCE_APPROVER_PIN_HASH` | empty | SHA-256 hex of the PIN — preferred over the plain PIN on shared machines |
| `HELIOS_SEC_USER_AGENT` | empty | Identifying User-Agent for SEC EDGAR look-through requests |
| `HELIOS_FMP_KEY` / `HELIOS_FMP_BASE_URL` | empty | Financial Modeling Prep key/base for forward fundamentals (yfinance fallback otherwise) |
| `HELIOS_OPENFIGI_KEY` | empty | Optional OpenFIGI key — raises the CUSIP→ticker rate limit |

Failed Basic-Auth attempts are throttled per remote IP: after 10 failures the
address gets a 60-second lockout (HTTP 429). The thresholds are intentional
constants, not configuration.

```bash
HELIOS_USER=jkiefer HELIOS_PASSWORD='choose-a-strong-one' ./run.sh
```

- **macOS firewall:** the first launch may prompt *"allow incoming connections"* —
  click **Allow** so other devices can reach it.
- **Localhost-only dev** (Flask dev server, no network exposure): `./run.sh --dev`.
- **Untrusted networks** (cafés, shared Wi-Fi): the app is reachable by anyone on
  the subnet who has the password. Use a strong `HELIOS_PASSWORD`, or keep it on
  `HELIOS_HOST=127.0.0.1` and reach it via SSH tunnel instead.

The app ships with a synthetic 6-instrument universe (AAPL, MSFT, NVDA, TSLA,
SPY, BTC-USD) so it works **fully offline** out of the box.

### Frontend development

Run Flask on port 5000 and Vite on port 5173 in separate terminals. The Vite
command expects `npm --prefix frontend ci` to have been run at least once:

```bash
HELIOS_AUTH=0 ./run.sh --dev
npm --prefix frontend run dev
```

Vite proxies `/api` to Flask. The React app does not contain
placeholder rankings or mock research rows; unavailable research surfaces render
the backend's demo, mixed, or blocked provenance state.

---

## What it does

| Area | Detail |
|------|--------|
| **Price & trend** | Close, SMA-50/200 (golden/death cross), Bollinger bands, with ▲ buy / ▼ sell markers |
| **Return forecast** | Ridge-regression model on lagged technical features → expected next-day return, projected forward as a Monte-Carlo **confidence cone** (5/25/50/75/95th percentiles) |
| **Forecast honesty** | Out-of-sample directional accuracy & RMSE reported alongside every forecast — no overselling |
| **Trade signal** | Weighted blend of `trend (30%) · momentum (20%) · forecast (30%) · sentiment (20%)`, volatility-penalized, with a full per-component breakdown |
| **News sentiment** | Offline finance lexicon scorer; pulls free live headlines via yfinance when available |
| **Backtest** | Runs the signal rule over history vs buy-and-hold: returns, Sharpe, drawdown, win rate, exposure |
| **Client models (portfolios)** | Upload an **Excel/CSV of holdings** (`ticker, weight`); the platform resolves each holding's prices, builds a portfolio NAV, and runs the full analysis on it |
| **Mandates** | Tag each model with its purpose (pure growth, income, CD alternative, balanced, capital preservation); the mandate **intentionally** tilts the signal weights, risk budgets and forecast anchor |
| **Long-horizon projection** | 5–90 day tactical signal **plus** 6-month / 1-year / 3-year / 5-year strategic value cones (terminal value, CAGR bands, probability of meeting the mandate, drawdown-breach odds) |
| **Conviction rationale** | Every signal explains itself: per-component clauses with the actual numbers, the mandate tilt, the vol penalty, and honesty caveats |
| **Signal Journal** | Dedicated paper-performance workspace for signal history, pending/measured forward results, hit rate, benchmark comparison, model-by-model evidence and drift over time |
| **Evidence Lab** | Walk-forward validation: freezes history at prior dates, replays the causal trend/momentum signal, and measures forward returns vs a benchmark — hit rate, false positives, regime sensitivity, signal decay and confidence bands, plus prospective Signal Journal tracking |
| **Risk Analytics** | Sector/factor exposure, volatility budget vs mandate, correlation clusters, deterministic scenario stress, historical stress replay, liquidity flags and a client-ready risk pack with break-the-model language |
| **Model Governance** | Versioned audit trail with committee notes, a risk gate that blocks approval while limits are breached, and exportable JSON/HTML/PDF approval packets with optional PIN-verified committee identity |
| **Model Validation** | Champion/challenger review dashboard scoring each model on walk-forward hit rate, alpha, false positives, signal decay and governance state, with drift alerts |
| **Native model editor** | Edit holdings in-app with a non-mutating preview, weight normalization, a mandatory change note, and a governance snapshot recorded on every save |
| **Report Export + History** | Advisor report snapshots are saved locally with branded HTML/PDF exports, source/date range/row counts, model metadata, caveats and optional AI narrative |
| **Institutional Data Quality** | Dedicated research-readiness dashboard for stale symbols, missing data, short histories, source conflicts, refresh failures and model coverage gaps |
| **Data-quality alerts** | Blocker/warning alerts persisted across runs with stable ids, occurrence counts and a reopen/resolve lifecycle, so freshness regressions are auditable |
| **Insights** | 12 rule-based suggestions per model — concentration, mandate fit, drawdown, correlation, forecast skill, data honesty — each with a concrete action |

### Data quality modes

Helios separates interface demos from advisor-grade research:

| Mode | Meaning |
|------|---------|
| `demo` | Bundled sample data can demonstrate workflow, but cannot populate real rankings or model-level research evidence. |
| `real` | Live or uploaded history is available for the analyzed surface. |
| `mixed` | Some evidence is real but some holdings or sources require verification. The UI displays a warning and required action. |
| `invalid_for_research` | Research is blocked because required live/uploaded history is missing or unsuitable. |

### Local real-data persistence

Helios creates a small SQLite database on first use. It stores parsed
live/uploaded price history, instrument provenance, uploaded model metadata,
holdings, live-refresh logs, Signal Journal entries, model-governance events,
data-quality alert state, and saved advisor report snapshots. It does **not**
store raw uploaded files, API keys, secrets, browser artifacts, generated
builds, screenshots, or sample data as real research evidence. The database
path is controlled by `HELIOS_DB_PATH`; set `HELIOS_DB_PATH=off` for an
ephemeral session.

The default `.helios/` workspace layout is:

```
.helios/
  helios.db      encrypted database snapshot (not readable SQLite at rest)
  helios.key     auto-created Fernet key when HELIOS_DB_ENCRYPTION=auto
  backups/       startup safety copies of the encrypted snapshot (newest 5 kept)
```

At startup Helios copies the previous encrypted snapshot into
`.helios/backups/` before reusing it, rotating out all but the newest five
copies. While running, snapshot writes are debounced (about 3 seconds after the
last change) and flushed at process exit, so bursts of persistence activity do
not rewrite the encrypted file on every call.

Local persistence encryption is enabled by default. When
`HELIOS_DB_ENCRYPTION=auto`, Helios creates a local `.helios/helios.key` file if
no `HELIOS_DB_ENCRYPTION_KEY` is provided, keeps the active SQLite database in
process memory, and writes only a Fernet-encrypted database snapshot to disk.
Older plaintext SQLite files are migrated into the encrypted snapshot at startup.
Use
`HELIOS_DB_ENCRYPTION=required` for fail-closed operation when a configured key
must be present. In encrypted mode the on-disk `.db` file is not a readable
SQLite database and does not expose schema, lookup keys, model holdings, price
values, refresh logs, journal results, or metadata at rest. Keep the local key
outside Git and protect/back it up like other client research secrets; without
that key, the encrypted local research store cannot be opened.

The React **Real Data Center** shows database availability, persisted
instrument/model counts, date ranges, row counts, live-refresh status, model
coverage, missing tickers, and copyable import templates. Refresh controls only
refresh symbols already imported as `live`; bundled samples and uploaded CSVs
are never silently promoted into live market data.

The React **Data Quality** workspace adds an institutional readiness screen for
stale symbols, short histories, missing model holdings, source conflicts,
refresh failures, refresh-observability gaps, coverage gaps, and overall
research-ready status. Thresholds are configurable through environment
variables, and live histories are checked for persisted provider refresh
evidence so the dashboard can flag when freshness cannot be audited from the
local log. It is a diagnostic surface only; it does not change opportunity
scoring, strategy evidence, or provenance gates.

Data-quality issues are also synced into persistent alerts: each issue gets a
stable id, severity (`blocker` or `warning`), occurrence count, and a
new/changed/reopened/resolved lifecycle, so an alert that disappears and
returns is auditable rather than silently forgotten. Alerts live in the same
local encrypted store as the rest of the research workspace.

The **Reports** workspace can save an analysis-only snapshot of the current
instrument or model report. Saved snapshots are local persistence records and
can be reopened as an escaped HTML page or downloaded as a branded PDF evidence
pack. The history API reports whether the local snapshot store is durable and
encrypted at rest.
Snapshots include the report source, input date range, row count, source counts,
model metadata where applicable, warnings/caveats, and the analysis-only
disclaimer. If the optional AI Copilot has already generated a narrative in the
current report session, the saved snapshot can include that narrative. If the
advisor leaves **Include AI narrative when available** enabled and a provider is
configured, save can generate a sanitized report narrative at snapshot time.
Provider failures do not block deterministic report saving, and every exported
AI narrative remains marked for advisor review.

For a no-upload live workflow, automatic polling defaults to the built-in
`core` liquid advisor universe. To choose a different universe before startup:

```bash
HELIOS_AUTO_LIVE_SYMBOLS=core \
HELIOS_AUTO_LIVE_REFRESH_SECONDS=300 \
HELIOS_AUTO_LIVE_MAX_WORKERS=6 \
./run.sh
```

`HELIOS_AUTO_LIVE_SYMBOLS=core` fetches a built-in liquid advisor universe from
yfinance, persists those histories locally, and refreshes them every five
minutes. Use `HELIOS_AUTO_LIVE_SYMBOLS=starter_models` to cover every holding in
the example model templates under `examples/models/`. You can also provide a
comma-separated ticker list. Live polling fetches symbols with bounded parallel
network workers (`HELIOS_AUTO_LIVE_MAX_WORKERS`, default `6`, max `16`) and then
persists the validated provider histories sequentially. This is a polling
workflow using the latest available provider data, not a streaming quote feed;
failed provider calls leave existing/sample data untouched and logged as failed
refresh attempts. Successful live refreshes also re-check pending Signal Journal
entries, so paper forward results are measured automatically once refreshed
history covers the original signal horizon.

The React **Signal Journal** workspace shows logged signal history, pending
versus measured outcomes, paper hit rate, benchmark-relative alpha,
model-by-model evidence, and drift over time. It is paper tracking only and
never represents live orders, brokerage execution, or a performance guarantee.

Set `HELIOS_AUTO_LIVE_SYMBOLS=off` to disable automatic polling explicitly.

### Evidence Lab (walk-forward validation)

The **Evidence Lab** answers "would this signal have worked?" without touching
the live methodology. It freezes history at a series of prior dates, computes
the same causal trend/momentum signal Strategy Lab uses, then measures the
subsequent forward return over 5/21/63-day horizons against a benchmark. The
payload includes hit rate with a 90% confidence band, false-positive rate on
directional calls, per-regime (risk-on/neutral/risk-off) sensitivity, and
signal-decay rows across horizons. Every input is strictly backward-looking —
no lookahead — and the lab is blocked entirely for demo/simulated data or
histories shorter than the training window plus the longest horizon. A
prospective-validation panel layers in same-target Signal Journal entries so
historical walk-forward evidence and live paper tracking sit side by side.

### Risk Analytics and the client risk pack

The **Risk Analytics** workspace (`/api/model/risk`) summarizes deterministic
portfolio risk evidence: sector and factor exposure, volatility budget versus
the mandate target, marginal risk contribution per holding, correlation
clusters, drawdown stress, deterministic scenario shocks (equity, rates,
growth compression, defensive rotation, liquidity), historical stress replay,
and liquidity flags built from observed live/uploaded dollar volume where
available. A client-ready risk pack condenses this into advisor language,
including "what breaks this model" drivers and a risk posture
(review_ready/watch/elevated). It is analysis-only and never alters signal or
forecast methodology.

### Model governance, validation, and approval packets

The **Models** workspace records a versioned governance audit trail per model:
every edit, review, approval, or rejection is archived with actor, committee
note, holding snapshot, version diff, and the risk-gate state at that moment.
Approval is gated: a model with active risk-limit violations (single-position
cap, minimum holdings, non-normalized weights) cannot be approved until the
breach is resolved, and approve/reject decisions require a committee note.

When `HELIOS_GOVERNANCE_APPROVER_PIN` or `HELIOS_GOVERNANCE_APPROVER_PIN_HASH`
is configured, approve/reject additionally requires signer name, role,
committee, and a matching local PIN (verified as a SHA-256 hash in constant
time). Prefer the hash form on shared machines so the plain PIN never sits in
the environment. Without a configured PIN, decisions are recorded as unverified
local attestations.

An **approval packet** for committee review is exportable per model as JSON,
escaped HTML, or a branded PDF: current risk gate, risk limits,
before/after holding snapshots, version diff, committee notes with identity,
and the full audit trail.

The **Model Validation** dashboard ranks eligible models champion-first using
existing walk-forward evidence: hit rate, benchmark alpha, false-positive rate,
signal decay, and governance state combine into a 0–100 validation score and
letter grade, with drift alerts (negative alpha, elevated false positives,
prospective journal drift, governance breaches) surfaced per model and
workspace-wide. Blocked models stay blocked — validation never bypasses
provenance gates.

### Native model editor

Imported models can be edited in-app instead of re-uploading a spreadsheet.
The editor previews proposed holdings without mutating the model (weight
normalization, optional rebalance-to-target, per-row validation), requires a
change note of at least five characters, and every save records a
`model_edit` governance event with a before/after snapshot and version diff,
then resets approval status to pending review.

### Optional AI Copilot

AI Copilot is disabled by default and is not required for any Helios workflow.
Helios still computes returns, scores, forecasts, trade signals, strategy
evidence, clinic suggestions, and report sections deterministically. AI can only
explain, critique, summarize, red-team, or draft advisor-language from sanitized
Helios facts.

Enable Claude after exporting `ANTHROPIC_API_KEY` in the server shell:

```bash
HELIOS_AI_ENABLED=1 \
HELIOS_AI_PROVIDER=anthropic \
./run.sh
```

`ANTHROPIC_API_KEY` is read only by Flask on the server side. It is never sent to
frontend JavaScript, never returned by `/api/ai/status`, never stored in SQLite,
and never used by automated tests. `HELIOS_AI_MODEL_ANTHROPIC` can override the
Claude model; if unset, Helios uses a conservative default model name and reports
provider errors cleanly if the configured provider cannot serve the request.

Provider modes:

| Variable | Purpose |
|----------|---------|
| `HELIOS_AI_ENABLED=0/1` | master AI Copilot switch |
| `HELIOS_AI_PROVIDER=none` | disabled mode |
| `HELIOS_AI_PROVIDER=anthropic` | Claude Messages API using server-side `ANTHROPIC_API_KEY` |
| `HELIOS_AI_PROVIDER=local` | local Ollama or OpenAI-compatible server status/calls |
| `HELIOS_AI_PROVIDER=openai` | optional OpenAI cloud provider using server-side `OPENAI_API_KEY` |
| `HELIOS_AI_PROVIDER=dual/hybrid` | reserved; reports unavailable until a future routing pass |

Local AI support is opt-in. Helios does not install Ollama, download models,
start local model servers, or pull model weights. With
`HELIOS_LOCAL_AI_REQUIRE_LOCALHOST=1`, non-local local-provider URLs are rejected.
OpenAI-compatible local servers can be selected with
`HELIOS_LOCAL_AI_BACKEND=openai_compatible`.
The complete optional AI configuration surface is documented with placeholders in
`.env.example`, including local base URL/model/timeout, cloud model overrides,
payload privacy flags, and cache TTL.

Before any provider call, Helios builds a sanitized payload: client/model names
are redacted by default, raw uploaded files are omitted, full price histories are
omitted, and holdings are omitted unless `HELIOS_AI_SEND_HOLDINGS=1`. Payloads
prefer computed metrics, drivers, warnings, provenance, persistence metadata,
source/date ranges, row counts, and analysis-only disclaimers. Demo or blocked
payloads must remain labeled as not real market evidence.

Provider output is parsed into a fixed JSON schema and checked for unsupported
numeric claims, prohibited assurance phrases, data-mode drift, and attempts to
upgrade deterministic `HOLD`/`REVIEW` actions into `BUY`. Failed, malformed, or
unavailable provider responses surface as review-required or unavailable states;
they do not change Helios calculations.

To clear local real-data state, stop Helios and delete the configured database
file or the local store directory:

```bash
rm -rf .helios/
```

If `HELIOS_DB_PATH` points somewhere else, delete that file instead. The next
start recreates an empty schema and reloads only bundled demo samples.

### Importing a client model (portfolio)

Upload an **Excel (`.xlsx` / `.xlsm`) or CSV/TSV** with a **Ticker** column and
(optionally) a **Weight** column, pick a **mandate**, and add free-text context.
Weights given as percentages (summing ~100) or fractions (summing ~1) are both
accepted; duplicate tickers are merged; missing weights become equal weight.
Prices for each holding are resolved from samples/uploads/cache, then live
`yfinance` when available, then deterministic simulation, with every source
flagged.

The React **Models** workspace also includes a governed starter model library:
AI infrastructure, quality compounders, defense/security, energy/grid,
healthcare innovation, inflation hedges, and cash/defensive reserve. Each
template has real public tickers, a mandate, benchmark, rebalance rule, risk
limits, and provenance/caveats. These are workflow templates, not investment
advice or managed account models, and Helios keeps model research blocked until
every analyzed holding has eligible live or uploaded history.

```csv
Ticker,Weight
AAPL,30
MSFT,25
NVDA,20
SPY,15
BND,10
```

Mandates and their intentional parameters (risk budgets, signal tilts, return
anchors) are documented in [`.design_spec.json`](.design_spec.json) — every
threshold traces to a stated rationale.

### Importing a single price series

Upload a CSV with a **date** column and a **close/price/NAV** column (flexible header
names: `Date`, `Timestamp`, `Close`, `Adj Close`, `Price`, `NAV`, `Value`, …) to
analyze one instrument's history directly.

### Live market data

If `yfinance` is installed and you have a connection, enter a ticker in the
**Live market data** box to pull ~2y of history plus recent news headlines.
Live histories are dividend/split-adjusted total-return prices (`auto_adjust`);
uploaded CSVs are used exactly as provided. The
platform degrades gracefully to sample/uploaded data when offline.

---

## Architecture

```
serve.py              production entrypoint — waitress on plain HTTP; HELIOS_TLS=1 serves self-signed HTTPS via werkzeug (fails closed)
app.py                thin entry point: loads the local .env, then wires helios_web.init_app()
helios_web/           Flask web layer — one blueprint per section
  core.py             shared app, Basic-Auth gate + lockout, CSRF heuristic, security headers
  localenv.py         repo-local .env loading (before the engine reads the environment)
  data.py             tickers, uploads, live fetch/refresh, data status, data quality
  analysis.py         command center, analyze, strategy, opportunities, evidence lab, journal
  models.py           model list/upload/analysis, library, editor, governance, validation, clinic, risk
  reports.py          advisor reports and saved snapshot exports
  ai.py               optional AI Copilot endpoints
  spa.py              React SPA static serving (build-instructions page when dist is absent)
frontend/            React + Vite + TypeScript research terminal
  src/api/           typed client for the Flask APIs
  src/components/    charts (ECharts theme + adapters), layout, cards, forms, AI panel
  src/hooks/         shared view-fetch hook
  src/views/         Command Center, Instruments, Models, Opportunity Radar, Strategy Lab,
                     Evidence Lab, Portfolio Clinic, Risk Analytics, Reports, Signal Journal,
                     Data Quality, Analysis
engine/
  _common.py          shared deterministic helpers (paper-hit rule, env parsing, series utils)
  data.py             data store, samples, CSV import, live fetch, holding resolution
  edgar.py            SEC EDGAR client: ticker→registrant, N-PORT holdings, former names
  holdings.py         fund look-through: see inside ETFs/funds, roll exposures up to a model
  fundamentals.py     per-holding valuation/yield/growth (FMP analyst consensus preferred when HELIOS_FMP_KEY is set; yfinance fallback)
  figi.py             OpenFIGI CUSIP→ticker bridge for N-PORT holdings without tickers
  macro.py            forward macro & sector valuation anchors (offline fallbacks for RF/ERP)
  cma.py              building-block forward expected return (yield + growth + valuation reversion)
  persistence.py      encrypted SQLite store, snapshot debounce, startup backups, refresh logs
  provenance.py       data-provenance gates for real-research (price) and forward (composition) eligibility
  data_quality.py     freshness/coverage diagnostics and persisted alert inputs
  analytics_cache.py  bounded keyed memo cache for per-series analytics
  indicators.py       SMA/EMA/RSI/MACD/Bollinger + performance metrics
  forecast.py         Ridge cone (short) + long-horizon strategic projection
  regime.py           price-only market-regime classification for the Command Center
  mandate.py          mandate presets + intentional risk/return/weight parameters
  portfolio.py        Excel/CSV model parsing, NAV build, risk decomposition
  portfolio_clinic.py clinic diagnostics and hypothetical rebalance suggestions
  insights.py         12 rule-based model-improvement suggestions
  sentiment.py        finance-lexicon news sentiment
  signals.py          mandate-aware BUY/SELL/HOLD with numbers-backed rationale
  strategy.py         Strategy Lab causal signal evidence
  backtest.py         historical validation vs buy-and-hold (wraps strategy)
  opportunity.py      conservative Opportunity Radar review scoring
  evidence_lab.py     walk-forward evidence windows + prospective validation
  signal_journal.py   paper-performance journal and forward-result measurement
  risk_exposure.py    exposure/stress/liquidity analytics + client risk pack
  model_library.py    governed starter model templates
  model_governance.py audit trail, risk gate, PIN-verified approval packets
  model_validation.py champion/challenger validation dashboard
  reporting.py        printable advisor report composition
  report_snapshots.py snapshot orchestration: compose, version, enrich
  report_exports.py   saved snapshot store + HTML/PDF export renderers
  pdf_layout.py       shared ReportLab layout primitives
  ai_copilot.py       optional sanitized AI narrative provider layer
```

### API

| Endpoint | Purpose |
|----------|---------|
| `GET /api/command-center` | Pro dashboard payload with regime, real-data opportunities, risks, model alerts and research queue |
| `GET /api/data/status` | SQLite/database health, real-data counts, model coverage, missing tickers and refresh log |
| `GET /api/data-quality` | institutional research-readiness dashboard: stale symbols, missing data, short histories, source conflicts, refresh failures and coverage gaps |
| `GET /api/signal-journal` | local paper-performance journal with recorded signals, summary hit-rate metrics, benchmark comparison, model evidence and drift |
| `GET /api/evidence-lab` | walk-forward evidence for an instrument or model (`kind`, `id`, optional `horizon`/`train_window`/`step`) |
| `POST /api/data/refresh` | refresh existing live instruments (`{ "symbol": "AAPL" }` or `{ "all": true }`) |
| `GET /api/opportunities` | Opportunity Radar rankings; returns no placeholder rows when real data is unavailable |
| `GET /api/strategy/analyze` | Strategy Lab for a single instrument with no-lookahead evidence |
| `GET /api/model/strategy/analyze` | Strategy Lab for a client model; blocks when model provenance is invalid |
| `GET /api/model/clinic` | Portfolio Clinic diagnostics and hypothetical, analysis-only suggestions |
| `GET /api/lookthrough?ticker=SYM` | Fund look-through: real SEC N-PORT holdings of one ETF/mutual fund, with former-name (predecessor) linkage and forward-data provenance — needs no price history |
| `GET /api/model/lookthrough?id=ID` | Roll every holding's look-through up to a model-level real exposure (asset-class weights, underlying concentration) with composition-coverage provenance |
| `GET /api/model/forward?id=ID` | Forward expected return from look-through fundamentals via a building-block CMA (yield + earnings growth + valuation reversion), coverage-weighted toward the mandate anchor — needs no price history, with every block and coverage % exposed |
| `GET /api/model/risk` | risk and exposure analytics plus the client risk pack for a model |
| `GET /api/model-governance` | governance workspace: per-model version, approval status, risk gate and recent events |
| `POST /api/model-governance/<id>/events` | record a governance event; approve/reject requires a committee note (and PIN when configured) |
| `GET /api/model-governance/<id>/approval-packet` | committee approval packet as JSON (`.html` / `.pdf` variants export the same packet) |
| `GET /api/model-validation` | champion/challenger validation dashboard with scores, grades and drift alerts |
| `GET /api/models/<id>/editor` | current editable holdings with a save preview |
| `POST /api/models/<id>/editor/preview` | non-mutating preview of proposed holdings (normalization, validation, diff) |
| `POST /api/models/<id>/editor` | save edited holdings with a mandatory change note; records a governance snapshot |
| `GET /api/report/instrument` | Analysis-only advisor report for an instrument |
| `GET /api/report/model` | Analysis-only advisor report for a model |
| `GET /api/report/snapshots` | list saved local report snapshots and export links |
| `POST /api/report/snapshots` | save a deterministic report snapshot; optional AI narrative can be supplied or generated from sanitized payloads when explicitly requested |
| `GET /api/report/snapshots/<id>.html` | escaped HTML export for a saved report snapshot |
| `GET /api/report/snapshots/<id>.pdf` | branded PDF evidence-pack export for a saved report snapshot |
| `GET /api/mandates` | list mandate presets for the model-import form |
| `GET /api/models` | list imported portfolio models |
| `GET /api/model-library` | governed starter model templates with mandate, benchmark, rebalance, risk-limit and provenance metadata |
| `POST /api/model-library/import` | import a governed starter template by slug; still requires real holding histories before real model research |
| `POST /api/model/upload` | import an Excel/CSV model (multipart `file`, `name`, `mandate`, `context`) |
| `GET /api/model/analyze?id=ID&horizon=H` | full model analysis (`H` = 5–90 or `6M`/`1Y`/`3Y`/`5Y`) |
| `GET /api/tickers` | list single instruments + last price/change |
| `GET /api/analyze?ticker=SYM&horizon=N` | single-instrument analysis payload |
| `POST /api/upload` | import a single-instrument price CSV (multipart `file`, optional `symbol`) |
| `POST /api/live` | fetch live data `{ "symbol": "GOOG" }` |
| `GET /api/ai/status` | AI Copilot provider availability without exposing secrets |
| `POST /api/ai/opportunity/explain` | explain a sanitized Opportunity Radar payload |
| `POST /api/ai/opportunity/critique` | red-team a sanitized Opportunity Radar payload |
| `POST /api/ai/strategy/summary` | summarize sanitized Strategy Lab evidence |
| `POST /api/ai/clinic/summary` | explain sanitized Portfolio Clinic diagnostics |
| `POST /api/ai/report` | draft analysis-only advisor narrative from a report payload |
| `POST /api/ai/question` | answer a question using only the supplied sanitized Helios payload |

---

## Developer verification

Common local commands:

| Task | Command |
|------|---------|
| Create virtualenv if needed | `python3 -m venv .venv` |
| Install tested Python pins (CI parity) | `./.venv/bin/python -m pip install -r requirements.lock` |
| Install by ranges instead (fresh resolve) | `./.venv/bin/python -m pip install -r requirements-dev.txt` |
| Install frontend deps | `npm --prefix frontend ci` |
| Local/LAN server | `./run.sh` |
| Localhost Flask dev server | `./run.sh --dev` |
| Vite dev server | `npm --prefix frontend run dev` |
| Frontend typecheck | `npm --prefix frontend run typecheck` |
| Frontend lint (ESLint) | `npm --prefix frontend run lint` |
| Frontend production build | `npm --prefix frontend run build` |
| Python tests | `./.venv/bin/python -m pytest` |
| Python tests with coverage (CI flags) | `./.venv/bin/python -m pytest --cov=engine --cov=app --cov=helios_web --cov-report=term-missing` |
| Python syntax compile | `./.venv/bin/python -m compileall app.py serve.py engine helios_web tests` |
| Design spec JSON validation | `./.venv/bin/python -m json.tool .design_spec.json >/dev/null` |

ESLint is configured for the frontend (`frontend/eslint.config.js`); there is
still no Python lint or formatter command in this repository. The CI-equivalent
local ladder is:

```bash
./.venv/bin/python -m pip install -r requirements.lock
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend run build
./.venv/bin/python -m compileall app.py serve.py engine helios_web tests
./.venv/bin/python -m json.tool .design_spec.json >/dev/null
./.venv/bin/python -m pytest --cov=engine --cov=app --cov=helios_web --cov-report=term-missing
```

The test suite is offline-only: it exercises deterministic sample/upload data,
portfolio parsing/NAV construction, indicator/sign/forecast output shapes,
sentiment scoring, Flask JSON API smoke paths, startup env validation, SQLite
persistence workflow, and AI Copilot behavior with fake/mocked providers. Tests
disable the default local database, use temporary database paths for persistence
cases, and never call Claude, OpenAI, or local model servers. CI runs the ladder
above on push and pull request using Python 3.13 and Node 22.

No cloud deployment config is present in this repo. The verifiable production
entrypoint is `serve.py` via `./run.sh`, which serves a local/LAN WSGI app with
waitress unless `HELIOS_TLS=1` selects the self-signed HTTPS path (served
by werkzeug's TLS-capable server).

---

## Security

Going live on a network was reviewed adversarially; the following safeguards are
built in:

- **Authentication** — HTTP Basic Auth gates *every* route (pages, API, static
  assets) with a constant-time credential check.
- **Brute-force throttle** — failed logins are counted per remote IP with a
  short per-attempt delay; ten failures trigger a 60-second lockout (HTTP 429).
- **CSRF heuristic** — non-GET requests with cross-site browser fetch metadata
  (`Sec-Fetch-Site`/`Origin`) are rejected with 403 before any handler runs.
- **Encryption (opt-in)** — `HELIOS_TLS=1` serves self-signed HTTPS so the login
  is not sent in cleartext, and it fails closed: if the certificate cannot be
  created, startup aborts instead of silently falling back to plain HTTP. On
  plain HTTP the startup banner warns explicitly.
- **XSS-safe rendering** — all user-supplied / external strings (instrument
  names, news headlines) are HTML-escaped before display; a strict
  Content-Security-Policy (`script-src 'self'` on every response, no CDN
  exceptions) is the second layer.
- **CSP tradeoff** — `style-src 'unsafe-inline'` remains because the current
  UI uses a few inline styles and dynamic bar widths. It is scoped to
  styles only; scripts remain self-restricted.
- **Governance sign-off (opt-in)** — configuring a local approver PIN
  (`HELIOS_GOVERNANCE_APPROVER_PIN[_HASH]`) makes approve/reject decisions
  require committee identity plus a SHA-256-verified PIN compared in constant
  time.
- **Input sanitization** — ticker symbols are constrained to valid characters
  (closes injection / SSRF surface); request bodies are capped at 16 MB and CSV
  parsing is row-bounded.
- **Resource protection** — concurrent live fetches are bounded (can't exhaust
  the worker pool), each outbound call has a timeout, and the instrument store
  is capped and lock-guarded against concurrent corruption.
- **Minimal disclosure** — security headers (`X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`) are set and internal errors are not
  echoed to clients.
- **AI key isolation** — AI provider keys are read from server-side environment
  variables only. They are not exposed through `/api/ai/status`, frontend code,
  SQLite persistence, logs, `.env.example`, or tests.

**Residual notes:** uploaded instruments and portfolio models are a shared local
advisor workspace backed by SQLite, not a multi-tenant permission system. Keep
the database local and do not place `.helios/` in source control. The
self-signed TLS cert triggers a one-time browser warning; delete `certs/` to
regenerate it (e.g. after your LAN IP changes). On an untrusted network, prefer
`HELIOS_TLS=1` or bind to `127.0.0.1` and reach it over an SSH tunnel.

---

## Method notes & limitations

- **Forecasts are damped.** The drift estimate is capped so the implied
  annualized Sharpe stays ≤ 1.5, preventing naive momentum from extrapolating
  absurd moves on high-volatility names.
- **Daily-return prediction is hard.** Directional accuracy near 50% is normal
  and is reported transparently. Treat the cone width, not the median line, as
  the main takeaway.
- **The backtest is illustrative.** It uses next-day execution and 5 bps costs
  but is single-asset, long/flat, and ignores slippage and liquidity. It
  validates the signal logic; it is not a production performance claim.
- **Sentiment is lexicon-based**, not a transformer. It captures headline
  polarity, not nuance.
- **Long-horizon = strategic projection, not a trade signal.** Beyond 90 days the
  drift shrinks toward a CAPM anchor (`rf + ERP·growth`) as the horizon grows
  (`λ = clip(H/1260, 0, 0.8)`), the cone widens with √time plus a regime cushion,
  and the implied long-run Sharpe is capped at 0.60. Bands assume the trailing-vol
  regime persists; they are not a guarantee.
- **Portfolio model NAV uses a union/rescaled analysis basis.** Holdings with
  different histories are combined over the union of available dates; each day
  weights are rescaled across holdings that have data. This keeps a
  mixed-history model analyzable for forward research, but it is **not** a
  performance track record and will differ from a daily-rebalanced common-window
  book or real buy-and-hold/drifting-weight account.
- **Holding prices may be simulated.** Any ticker that can't be resolved live or
  from samples falls back to a deterministic simulation, flagged per-holding and
  with a portfolio-level honesty banner. Simulated numbers are never presented as
  real market data.
- **Insights and the conviction rationale are deterministic and offline** — no
  LLM. Every threshold comes from the mandate config, so identical inputs always
  yield the identical explanation.

These are deliberate, documented simplifications — extend any module to suit a
specific client mandate.

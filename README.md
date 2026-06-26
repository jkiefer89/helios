# Helios — Investment-Model Analytics & Trade-Signal Platform

A local web dashboard for analyzing client investment models from price/return
history, forecasting forward returns with confidence bands, and producing
explainable **BUY / SELL / HOLD** trade signals that blend technical trend,
momentum, the return forecast, and news sentiment.

> **Analysis only.** Helios never places orders or moves money. It is a research
> and decision-support tool. Forecasts are statistical estimates, not guarantees.

---

## Quick start

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
The first run creates the virtualenv and installs dependencies, including
`openpyxl` for Excel model uploads. `yfinance` is installed for optional live
data, but the app remains fully usable offline with bundled sample data.

Helios now uses a React + Vite + TypeScript frontend. For normal local use,
build the frontend once and then start Flask:

```bash
npm --prefix frontend install
npm --prefix frontend run build
./run.sh
```

If `frontend/dist/` is absent, Flask falls back to the legacy vanilla dashboard
at `/`; the legacy page is also available at `/legacy`.

### Going live on your network

| Variable | Default | Purpose |
|----------|---------|---------|
| `HELIOS_USER` | `advisor` | Basic-auth username |
| `HELIOS_PASSWORD` | *auto-generated* | Basic-auth password — **set this** for a stable login |
| `HELIOS_PORT` | `5000` | Listen port |
| `HELIOS_HOST` | `0.0.0.0` | Bind address (`127.0.0.1` = localhost only) |
| `HELIOS_TLS` | `0` | `1` serves self-signed **HTTPS** (encrypts the login) |
| `HELIOS_AUTH` | `1` | `0` disables the password gate (localhost dev only) |
| `HELIOS_RF` | `0.02` | Risk-free / CD benchmark rate used by mandates and projections |

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

Run Flask on port 5000 and Vite on port 5173:

```bash
HELIOS_AUTH=0 ./.venv/bin/python app.py
npm --prefix frontend run dev
```

Vite proxies `/api` and `/legacy` to Flask. The React app does not contain
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
| **Insights** | 12 rule-based suggestions per model — concentration, mandate fit, drawdown, correlation, forecast skill, data honesty — each with a concrete action |

### Data quality modes

Helios separates interface demos from advisor-grade research:

| Mode | Meaning |
|------|---------|
| `demo` | Bundled sample data can demonstrate workflow, but cannot populate real rankings or model-level research evidence. |
| `real` | Live or uploaded history is available for the analyzed surface. |
| `mixed` | Some evidence is real but some holdings or sources require verification. The UI displays a warning and required action. |
| `invalid_for_research` | Research is blocked because required live/uploaded history is missing or unsuitable. |

### Importing a client model (portfolio)

Upload an **Excel (`.xlsx` / `.xlsm`) or CSV/TSV** with a **Ticker** column and
(optionally) a **Weight** column, pick a **mandate**, and add free-text context.
Weights given as percentages (summing ~100) or fractions (summing ~1) are both
accepted; duplicate tickers are merged; missing weights become equal weight.
Prices for each holding are resolved from samples/uploads/cache, then live
`yfinance` when available, then deterministic simulation, with every source
flagged.

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
**Live market data** box to pull ~2y of history plus recent news headlines. The
platform degrades gracefully to sample/uploaded data when offline.

---

## Architecture

```
serve.py              production entrypoint — waitress on the local network
app.py                Flask app + JSON API + Basic-Auth gate + payload sanitation
frontend/            React + Vite + TypeScript research terminal
  src/api/           typed client for existing Flask APIs
  src/views/         Command Center, Opportunity Radar, Strategy Lab, Clinic, Reports
engine/
  data.py             data store, samples, CSV import, live fetch, holding resolution
  indicators.py       SMA/EMA/RSI/MACD/Bollinger + performance metrics
  forecast.py         Ridge cone (short) + long-horizon strategic projection
  mandate.py          mandate presets + intentional risk/return/weight parameters
  portfolio.py        Excel/CSV model parsing, NAV build, risk decomposition
  insights.py         12 rule-based model-improvement suggestions
  sentiment.py        finance-lexicon news sentiment
  signals.py          mandate-aware BUY/SELL/HOLD with numbers-backed rationale
  backtest.py         historical validation vs buy-and-hold
templates/index.html  legacy vanilla dashboard fallback (/legacy)
static/app.js         legacy front-end logic
static/styles.css     legacy dashboard theme
```

### API

| Endpoint | Purpose |
|----------|---------|
| `GET /api/command-center` | Pro dashboard payload with regime, real-data opportunities, risks, model alerts and research queue |
| `GET /api/opportunities` | Opportunity Radar rankings; returns no placeholder rows when real data is unavailable |
| `GET /api/strategy/analyze` | Strategy Lab for a single instrument with no-lookahead evidence |
| `GET /api/model/strategy/analyze` | Strategy Lab for a client model; blocks when model provenance is invalid |
| `GET /api/model/clinic` | Portfolio Clinic diagnostics and hypothetical, analysis-only suggestions |
| `GET /api/report/instrument` | Analysis-only advisor report for an instrument |
| `GET /api/report/model` | Analysis-only advisor report for a model |
| `GET /api/mandates` | list mandate presets for the model-import form |
| `GET /api/models` | list imported portfolio models |
| `POST /api/model/upload` | import an Excel/CSV model (multipart `file`, `name`, `mandate`, `context`) |
| `GET /api/model/analyze?id=ID&horizon=H` | full model analysis (`H` = 5–90 or `6M`/`1Y`/`3Y`/`5Y`) |
| `GET /api/tickers` | list single instruments + last price/change |
| `GET /api/analyze?ticker=SYM&horizon=N` | single-instrument analysis payload |
| `POST /api/upload` | import a single-instrument price CSV (multipart `file`, optional `symbol`) |
| `POST /api/live` | fetch live data `{ "symbol": "GOOG" }` |

---

## Developer verification

```bash
./.venv/bin/python -m pip install -r requirements-dev.txt
npm --prefix frontend install
npm --prefix frontend run typecheck
npm --prefix frontend run build
./.venv/bin/python -m pytest
./.venv/bin/python -m compileall app.py serve.py engine tests
```

The test suite is offline-only: it exercises deterministic sample/upload data,
portfolio parsing/NAV construction, indicator/sign/forecast output shapes,
sentiment scoring, Flask JSON API smoke paths, and startup env validation. CI
runs the same checks on push and pull request.

---

## Security

Going live on a network was reviewed adversarially; the following safeguards are
built in:

- **Authentication** — HTTP Basic Auth gates *every* route (pages, API, static
  assets) with a constant-time credential check.
- **Encryption (opt-in)** — `HELIOS_TLS=1` serves self-signed HTTPS so the login
  is not sent in cleartext. Recommended on any network you do not fully trust;
  on plain HTTP the startup banner warns about this explicitly.
- **XSS-safe rendering** — all user-supplied / external strings (instrument
  names, news headlines) are HTML-escaped before display; a strict
  Content-Security-Policy (`script-src 'self'` + the Chart.js CDN) is the second
  layer.
- **CSP tradeoff** — `style-src 'unsafe-inline'` remains because the current
  single-file UI uses a few inline styles and dynamic bar widths. It is scoped to
  styles only; scripts remain self/CDN restricted.
- **Input sanitization** — ticker symbols are constrained to valid characters
  (closes injection / SSRF surface); request bodies are capped at 16 MB and CSV
  parsing is row-bounded.
- **Resource protection** — concurrent live fetches are bounded (can't exhaust
  the worker pool), each outbound call has a timeout, and the instrument store
  is capped and lock-guarded against concurrent corruption.
- **Minimal disclosure** — security headers (`X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`) are set and internal errors are not
  echoed to clients.

**Residual notes:** uploaded instruments and portfolio models are process-local:
they are shared within the running app process and disappear on restart. This is
a shared advisor view, not a multi-tenant system. The self-signed TLS cert
triggers a one-time browser warning; delete `certs/` to regenerate it (e.g.
after your LAN IP changes). On an untrusted network, prefer `HELIOS_TLS=1` or
bind to `127.0.0.1` and reach it over an SSH tunnel.

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

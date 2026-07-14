# AGENTS.md

## Project Overview

Helios is a local investment-model analytics and trade-signal dashboard. It
analyzes uploaded or live price histories, portfolio model holdings, forecasts,
signals, strategy evidence, reports, and optional AI explanations. It is
analysis-only: it must never be treated as order execution, investment advice,
or a guarantee engine.

## Repo Layout

- `app.py`: thin entry point/facade; loads the repo-local `.env`, then wires
  `helios_web.init_app()`.
- `helios_web/`: Flask web layer, one blueprint per section (`core`, `data`,
  `analysis`, `models`, `reports`, `ai`, `evidence`, `trials`, `institutional`,
  `security`, `ledger`, and `spa`, plus `localenv`); auth/RBAC/MFA gate, CSRF
  heuristic, security headers, React static serving.
- `serve.py`: localhost production entrypoint using waitress, or loopback-only
  development HTTPS when `HELIOS_TLS=1`. Institutional mode refuses direct
  public/LAN binds; use a production reverse proxy while Helios stays loopback.
- `run.sh`: setup/start wrapper; creates `.venv`, installs pinned Python deps
  from `requirements.lock`, then runs `app.py` or `serve.py`.
- `engine/`: deterministic analytics, persistence, portfolio parsing, signals,
  evidence lab, risk/exposure, governance/validation, reports, optional AI
  provider layer.
- `frontend/`: React + Vite + TypeScript application — the only UI.
- `tests/`: offline pytest suite for engine behavior, API smoke paths,
  persistence, reports, frontend serving, and AI Copilot safety.
- `.env.example`: placeholder-only runtime configuration.
- `.github/workflows/ci.yml`: CI source of truth.

## Stack and Tooling

- Backend: Python 3, Flask, waitress, pandas/numpy, SQLite persistence.
- Frontend: React 19, Vite, TypeScript, ECharts, npm with
  `frontend/package-lock.json`.
- Tests: pytest (with coverage in CI) for Python; TypeScript build/typecheck
  and ESLint for the frontend.
- Package management: `requirements.txt` / `requirements-dev.txt` hold ranges;
  `requirements.lock` holds the exact tested pins CI installs. npm for the
  frontend. Regenerate the lock when Python dependency ranges change.
- No Makefile, Dockerfile, or `pyproject.toml` exists in this repo today.
  ESLint is configured for the frontend (`frontend/eslint.config.js`); there is
  no Python lint or formatter command.

## Setup

```bash
python3 -m venv .venv
# CI parity: exact tested pins (includes pytest/pytest-cov).
./.venv/bin/python -m pip install -r requirements.lock
# Or resolve fresh from ranges instead:
# ./.venv/bin/python -m pip install -r requirements-dev.txt
npm --prefix frontend ci
```

Do not commit `.env`, `.helios/`, SQLite databases, generated certs,
`frontend/dist/`, `frontend/node_modules/`, or Python cache artifacts. Keep all
secrets out of docs, tests, source, and logs.

## Common Commands

```bash
# Local production server. Installs runtime Python deps if needed.
./run.sh

# Localhost Flask dev server.
./run.sh --dev

# Vite dev server, with Flask running on port 5000.
npm --prefix frontend run dev

# Frontend typecheck.
npm --prefix frontend run typecheck

# Frontend lint (ESLint).
npm --prefix frontend run lint

# Frontend component tests.
npm --prefix frontend run test

# Frontend production build.
npm --prefix frontend run build

# Browser workflow tests (after the frontend build and Chromium install).
npx --prefix frontend playwright install chromium
npm --prefix frontend run test:e2e

# Python tests.
./.venv/bin/python -m pytest

# Python tests with coverage (CI flags).
./.venv/bin/python -m pytest --cov=engine --cov=helios_web --cov=app --cov-report=term-missing --cov-fail-under=80

# Python syntax compile.
./.venv/bin/python -m compileall app.py serve.py engine helios_web tests

# Design spec JSON validation.
./.venv/bin/python -m json.tool .design_spec.json >/dev/null
```

Full CI-equivalent local verification:

```bash
./.venv/bin/python -m pip install -r requirements.lock
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run test
npm --prefix frontend run build
npx --prefix frontend playwright install chromium
npm --prefix frontend run test:e2e
./.venv/bin/python -m compileall app.py serve.py engine helios_web tests
./.venv/bin/python -m json.tool .design_spec.json >/dev/null
./.venv/bin/python -m pytest --cov=engine --cov=helios_web --cov=app --cov-report=term-missing --cov-fail-under=80
```

Python lint/format commands are not configured. Do not invent them; if adding a
lint/format tool, add config, scripts, tests/docs, and explain the rationale.
The frontend lint command is `npm --prefix frontend run lint` only.

## Engineering Conventions

- Preserve analysis-only language and data-quality caveats. Do not present demo,
  sample, mixed, blocked, or simulated evidence as real market research.
- Keep analytics deterministic unless explicitly working in the optional AI
  provider layer. AI may explain or summarize sanitized payloads; it must not
  change Helios calculations or override deterministic actions.
- Do not call Claude, OpenAI, local model servers, production services, or live
  market endpoints as verification unless the task explicitly requires it.
  Prefer offline tests and mocked providers/fetchers.
- Do not inspect or print `.env` values unless the user explicitly asks and the
  task cannot be done safely without it. Use `.env.example` for documented
  configuration.
- Be careful around local persistence. `.helios/helios.db` may contain local
  client research data; do not delete, migrate, or mutate it casually. Tests
  default `HELIOS_DB_PATH=off` and use temporary paths for persistence cases.
- For API changes, update Flask route behavior, frontend typed client/types,
  and tests together.
- For frontend changes, preserve the React serving contract: React is the only
  UI, served from `frontend/dist/` when present; when the dist is absent, `/`
  serves a self-contained build-instructions page (there is no legacy
  dashboard or `/legacy` route).
- Keep dependency changes deliberate. Update lockfiles for npm dependency
  changes and requirements files for Python dependency changes.

## Codex Workflow

- Inspect before editing: read the relevant source, tests, config, README, and
  this file before changing files.
- Make minimal, high-confidence changes. Avoid broad rewrites, restyles, or
  unrelated cleanup.
- Do not modify application/source code for documentation-only tasks.
- Add or update focused tests for behavior changes. Run the smallest relevant
  tests first, then broader verification when risk or scope justifies it.
- Prefer repo config and source files over stale docs when they conflict.
- Keep generated artifacts and ignored local state out of commits unless the
  task explicitly requires them.
- Final summaries should list changed files, commands run, results, and any
  remaining risks or unverified commands.

## Multi-Agent Workflow

- Use read-only exploration agents first for broad audits or unfamiliar areas.
- Use only one writer/implementer at a time. Avoid multiple agents editing the
  same files concurrently.
- Use reviewer/tester agents after implementation for non-trivial changes.
- Prefer separate git worktrees for competing implementation attempts.
- Keep each agent's scope explicit: discovery, implementation, review, or
  verification.

## Definition of Done

A task is complete when:

- Requested behavior or documentation change is implemented.
- Relevant tests are added or updated when behavior changes.
- Applicable typecheck/build/test/compile checks pass, or any skipped checks are
  clearly explained.
- No secrets, local databases, generated certs, or unrelated artifacts are
  exposed or committed.
- The final response includes changed files, verification commands and results,
  and remaining risks.

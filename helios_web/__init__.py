"""Helios web layer: the shared Flask app plus one blueprint per section.

Modules:
    localenv - repo-local .env loading (imported before the engine reads env)
    core     - Flask app, auth gate, CSRF, security headers, JSON helpers
    data     - tickers, uploads, live fetch/refresh, status, data quality
    analysis - command center, analyze, strategy, opportunities, evidence lab,
               signal journal
    models   - model list/upload/analysis, library, editor, governance,
               validation, clinic, risk
    reports  - advisor reports and saved snapshot exports
    ai       - optional AI Copilot proxy
    spa      - React SPA static serving (build instructions when dist absent)

Import ordering matters: ``app.py`` loads the local .env first, then calls
``init_app()`` which imports the engine-backed blueprint modules.
"""
from __future__ import annotations


def init_app():
    """Wire the singleton Flask app once: engine warmup, blueprints, auto-live."""
    import os

    from engine import data as engine_data, portfolio

    from . import ai, analysis, core, data, decisions, ledger, models, reports, spa

    app = core.app
    if not app.blueprints:
        # Synthetic demo histories are OPT-IN (they park under REAL tickers —
        # AAPL/SPY/... — in the same store as live data, and a sample SPY once
        # leaked in as the market-regime proxy; review finding). A fresh start
        # lands on the honest empty state: fetch live or upload real CSVs.
        if os.environ.get("HELIOS_LOAD_SAMPLES", "0") == "1":
            engine_data.load_samples()
        engine_data.load_persisted_instruments()
        portfolio.load_persisted_models()
        for module in (data, analysis, models, reports, ai, decisions, ledger, spa):
            app.register_blueprint(module.bp)
        data._start_auto_live_refresh()
    return app

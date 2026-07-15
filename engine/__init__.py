"""Analytics engine for the investment-model platform.

Modules:
    data        - load, validate, persist, and fetch price history
    analytics_cache - bounded memo cache for per-series analytics results
    indicators  - technical indicators & performance metrics
    forecast    - return forecasting with Monte-Carlo confidence cones
    mandate     - client-purpose presets and risk/return anchors
    portfolio   - model upload parsing and weight-rescaled research series
    insights    - deterministic model-improvement suggestions
    sentiment   - news / headline sentiment scoring
    signals     - composite BUY / SELL / HOLD trade signals
    backtest    - historical validation of the signal rule
    regime      - market-regime classification for the command center
    strategy    - no-lookahead strategy evidence and trade statistics
    opportunity - conservative review ranking for opportunity radar
    portfolio_clinic - model diagnostics and hypothetical rebalance suggestions
    reporting   - printable advisor report composition
    report_exports - saved advisor report snapshots and export renderers
    report_snapshots - snapshot composition, versioning, and narrative orchestration
    data_quality - freshness/coverage dashboard and research-readiness evidence
    provenance  - eligible/ineligible/mixed data-quality gates
    persistence - local SQLite store for live/uploaded data
    ai_copilot  - optional narrative provider layer over sanitized Helios facts
    model_library - governed starter model templates/workspaces
    model_governance - versioned approval, rebalance, and snapshot workflow
    model_validation - champion/challenger model validation dashboard
    risk_exposure - portfolio-level risk, exposure, stress, and benchmark analytics
    signal_journal - local signal audit log and paper-performance tracking
    research_context - versioned operator thesis and mandate evidence
    evidence_lab - rolling walk-forward out-of-sample signal evidence
    evidence     - immutable calculation envelopes and deterministic replay
    provider_registry - licensed provider, reconciliation, and cutover controls
    independent_validation - independent model reviews and bounded exceptions
    operations   - privileged audit, incidents, and backup verification
    edgar       - SEC EDGAR client: ticker->registrant, N-PORT holdings, former names
    holdings    - fund look-through and model-level exposure roll-up
    figi        - OpenFIGI CUSIP->ticker bridge for N-PORT holdings
    fundamentals - per-holding forward fundamentals (yfinance / FMP provider seam)
    macro       - forward macro & sector valuation anchors with offline fallbacks
    cma         - building-block forward capital-market-assumption expected returns
"""

__all__ = [
    "analytics_cache",
    "data",
    "indicators",
    "forecast",
    "mandate",
    "portfolio",
    "insights",
    "sentiment",
    "signals",
    "backtest",
    "regime",
    "strategy",
    "opportunity",
    "portfolio_clinic",
    "reporting",
    "report_exports",
    "report_snapshots",
    "data_quality",
    "provenance",
    "persistence",
    "ai_copilot",
    "model_library",
    "model_governance",
    "model_validation",
    "risk_exposure",
    "signal_journal",
    "research_context",
    "evidence_lab",
    "evidence",
    "trials",
    "provider_registry",
    "independent_validation",
    "operations",
    "edgar",
    "holdings",
    "figi",
    "fundamentals",
    "macro",
    "cma",
]

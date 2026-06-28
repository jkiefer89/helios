"""Analytics engine for the investment-model platform.

Modules:
    data        - load / generate / fetch price history
    indicators  - technical indicators & performance metrics
    forecast    - return forecasting with Monte-Carlo confidence cones
    mandate     - client-purpose presets and risk/return anchors
    portfolio   - model upload parsing and synthetic NAV construction
    insights    - deterministic model-improvement suggestions
    sentiment   - news / headline sentiment scoring
    signals     - composite BUY / SELL / HOLD trade signals
    backtest    - historical validation of the signal rule
    regime      - market-regime classification for the command center
    strategy    - no-lookahead strategy evidence and trade statistics
    opportunity - conservative review ranking for opportunity radar
    portfolio_clinic - model diagnostics and hypothetical rebalance suggestions
    reporting   - printable advisor report composition
    provenance  - demo/real/mixed data eligibility gates
    persistence - local SQLite store for live/uploaded data
    ai_copilot  - optional narrative provider layer over sanitized Helios facts
"""

__all__ = [
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
    "provenance",
    "persistence",
    "ai_copilot",
]

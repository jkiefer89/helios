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
]

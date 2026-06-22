"""Analytics engine for the investment-model platform.

Modules:
    data        - load / generate / fetch price history
    indicators  - technical indicators & performance metrics
    forecast    - return forecasting with Monte-Carlo confidence cones
    sentiment   - news / headline sentiment scoring
    signals     - composite BUY / SELL / HOLD trade signals
    backtest    - historical validation of the signal rule
"""

__all__ = ["data", "indicators", "forecast", "sentiment", "signals", "backtest"]

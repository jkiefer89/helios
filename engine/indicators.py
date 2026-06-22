"""Technical indicators and performance metrics (pure pandas/numpy)."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


# --------------------------------------------------------------------------- #
# Technical indicators
# --------------------------------------------------------------------------- #
def sma(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window).mean()


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(s: pd.Series, period: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(s, fast) - ema(s, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger(s: pd.Series, window: int = 20, num_std: float = 2.0):
    mid = sma(s, window)
    sd = s.rolling(window).std()
    return mid + num_std * sd, mid, mid - num_std * sd


# --------------------------------------------------------------------------- #
# Returns & performance metrics
# --------------------------------------------------------------------------- #
def daily_returns(close: pd.Series) -> pd.Series:
    return close.pct_change().fillna(0.0)


def annualized_return(close: pd.Series) -> float:
    r = daily_returns(close)
    if len(r) < 2:
        return 0.0
    return float((1 + r.mean()) ** TRADING_DAYS - 1)


def annualized_vol(close: pd.Series) -> float:
    return float(daily_returns(close).std() * np.sqrt(TRADING_DAYS))


def sharpe(close: pd.Series, rf: float = 0.02) -> float:
    r = daily_returns(close)
    excess = r.mean() * TRADING_DAYS - rf
    vol = r.std() * np.sqrt(TRADING_DAYS)
    return float(excess / vol) if vol > 1e-9 else 0.0


def sortino(close: pd.Series, rf: float = 0.02) -> float:
    r = daily_returns(close)
    downside = r[r < 0].std() * np.sqrt(TRADING_DAYS)
    excess = r.mean() * TRADING_DAYS - rf
    return float(excess / downside) if downside and downside > 1e-9 else 0.0


def max_drawdown(close: pd.Series) -> float:
    cum = (1 + daily_returns(close)).cumprod()
    peak = cum.cummax()
    return float(((cum - peak) / peak).min())


def metrics_summary(close: pd.Series) -> dict:
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) > 1 else last
    return {
        "last_price": last,
        "daily_change_pct": (last / prev - 1) * 100 if prev else 0.0,
        "annual_return_pct": annualized_return(close) * 100,
        "annual_vol_pct": annualized_vol(close) * 100,
        "sharpe": sharpe(close),
        "sortino": sortino(close),
        "max_drawdown_pct": max_drawdown(close) * 100,
        "52w_high": float(close.tail(TRADING_DAYS).max()),
        "52w_low": float(close.tail(TRADING_DAYS).min()),
    }


def indicator_frame(close: pd.Series) -> pd.DataFrame:
    """All overlay/oscillator indicators aligned to the close index."""
    macd_line, signal_line, hist = macd(close)
    bb_up, bb_mid, bb_lo = bollinger(close)
    return pd.DataFrame(
        {
            "close": close,
            "sma20": sma(close, 20),
            "sma50": sma(close, 50),
            "sma200": sma(close, 200),
            "ema12": ema(close, 12),
            "bb_upper": bb_up,
            "bb_mid": bb_mid,
            "bb_lower": bb_lo,
            "rsi": rsi(close),
            "macd": macd_line,
            "macd_signal": signal_line,
            "macd_hist": hist,
        }
    )

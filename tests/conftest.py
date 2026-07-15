import os
import sys
from pathlib import Path

os.environ.setdefault("HELIOS_AUTH", "0")
os.environ.setdefault("HELIOS_RF", "0.02")
os.environ.setdefault("HELIOS_DB_PATH", "off")
os.environ.setdefault("HELIOS_LOAD_DOTENV", "0")
os.environ.setdefault("HELIOS_INSTITUTIONAL_CONTROLS", "0")
os.environ.setdefault("HELIOS_TENANT_ID", "test-tenant")
os.environ.setdefault("HELIOS_CLIENT_ID", "test-client")

import numpy as np
import pandas as pd
import pytest

from engine import analytics_cache, data, fundamentals, identity, macro, news, persistence, portfolio

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


_SAMPLE_SPEC = [
    ("AAPL", "Apple Inc.", 0.18, 0.26, 120.0, True, 1),
    ("MSFT", "Microsoft Corp.", 0.21, 0.24, 240.0, True, 2),
    ("NVDA", "NVIDIA Corp.", 0.42, 0.52, 45.0, True, 3),
    ("TSLA", "Tesla Inc.", 0.12, 0.58, 210.0, True, 4),
    ("SPY", "S&P 500 ETF", 0.10, 0.16, 380.0, False, 5),
    ("BTC-USD", "Bitcoin / USD", 0.35, 0.70, 22000.0, True, 6),
]


def load_sample_instruments() -> None:
    """Offline provenance fixtures. Production code cannot create samples."""
    for symbol, name, drift, vol, start, flips, seed in _SAMPLE_SPEC:
        if data.get(symbol) is not None:
            continue
        rng = np.random.default_rng(seed)
        days = 760
        dt = 1.0 / 252.0
        segments = 4 if flips else 1
        bounds = np.linspace(0, days, segments + 1).astype(int)
        regime_mult = rng.uniform(-1.2, 1.6, size=segments) if flips else np.array([1.0])
        daily = np.empty(days)
        for segment in range(segments):
            a, b = bounds[segment], bounds[segment + 1]
            seg_vol = max(vol * (1.0 + 0.4 * rng.standard_normal()) if flips else vol, 0.05)
            seg_drift = drift * (regime_mult[segment] if flips else 1.0)
            daily[a:b] = ((seg_drift - 0.5 * seg_vol**2) * dt
                          + seg_vol * np.sqrt(dt) * rng.standard_normal(b - a))
        close = start * np.exp(np.cumsum(daily))
        idx = pd.bdate_range(end="2025-06-13", periods=days)
        frame = pd.DataFrame({"close": close, "volume": rng.lognormal(15.5, 0.6, days)}, index=idx)
        data.register(data.Instrument(symbol, name, frame, "sample", []))


@pytest.fixture(autouse=True)
def isolate_provider_env():
    """Restore HELIOS_/ANTHROPIC_/OPENAI_ env keys after each test.

    Some code under test (e.g. the .env loader) writes os.environ directly,
    which monkeypatch cannot undo; this keeps such writes from leaking into
    later tests. Defined before reset_process_local_stores so it tears down
    last, after monkeypatch and store resets have already run.
    """
    prefixes = ("HELIOS_", "ANTHROPIC_", "OPENAI_")
    before = {k: v for k, v in os.environ.items() if k.startswith(prefixes)}
    yield
    for key in [k for k in os.environ if k.startswith(prefixes) and k not in before]:
        del os.environ[key]
    os.environ.update(before)


@pytest.fixture(autouse=True)
def reset_process_local_stores():
    """Keep tests independent while preserving test-only sample instruments."""
    load_sample_instruments()
    samples = {k: v for k, v in data._STORE.items() if v.source == "sample"}
    data._STORE.clear()
    data._STORE.update(samples)
    data._PRICE_CACHE.clear()
    portfolio._MODELS.clear()
    analytics_cache.invalidate()
    fundamentals.invalidate_cache()
    news.invalidate_cache()
    macro._YIELD_CACHE.clear()
    persistence.reset_store_for_tests()
    identity.reset_replay_cache_for_tests()
    yield
    data._STORE.clear()
    data._STORE.update(samples)
    data._PRICE_CACHE.clear()
    portfolio._MODELS.clear()
    analytics_cache.invalidate()
    fundamentals.invalidate_cache()
    news.invalidate_cache()
    macro._YIELD_CACHE.clear()
    persistence.reset_store_for_tests()
    identity.reset_replay_cache_for_tests()


def price_series(days: int = 260, start: float = 100.0, daily: float = 0.001,
                 future_days: int = 0) -> pd.Series:
    """Synthetic close series ending at the most recent business day.

    The journals' settlement guards treat an input window ending long ago as
    hindsight (correctly), so fixtures must be FRESH unless a test explicitly
    backdates. ``future_days`` extends the index past today with synthetic
    "future" bars — used to exercise forward-measurement paths offline.
    """
    end = pd.Timestamp.today().normalize()
    if future_days:
        end = end + pd.offsets.BDay(int(future_days))
    idx = pd.bdate_range(end=end, periods=days)
    values = start * np.cumprod(np.full(days, 1.0 + daily))
    return pd.Series(values, index=idx, name="close")


def price_csv(days: int = 40, price_col: str = "Close") -> bytes:
    s = price_series(days=days)
    df = pd.DataFrame({"Date": s.index.strftime("%Y-%m-%d"), price_col: s.values})
    return df.to_csv(index=False).encode("utf-8")

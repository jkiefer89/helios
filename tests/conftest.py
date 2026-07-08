import os
import sys
from pathlib import Path

os.environ.setdefault("HELIOS_AUTH", "0")
os.environ.setdefault("HELIOS_RF", "0.02")
os.environ.setdefault("HELIOS_DB_PATH", "off")
os.environ.setdefault("HELIOS_LOAD_DOTENV", "0")

import numpy as np
import pandas as pd
import pytest

from engine import analytics_cache, data, fundamentals, macro, news, persistence, portfolio

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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
    """Keep tests independent while preserving bundled sample instruments."""
    data.load_samples()
    samples = {k: v for k, v in data._STORE.items() if v.source == "sample"}
    data._STORE.clear()
    data._STORE.update(samples)
    data._PRICE_CACHE.clear()
    data._NEGATIVE_RESOLVE.clear()
    portfolio._MODELS.clear()
    analytics_cache.invalidate()
    fundamentals.invalidate_cache()
    news.invalidate_cache()
    macro._YIELD_CACHE.clear()
    persistence.reset_store_for_tests()
    yield
    data._STORE.clear()
    data._STORE.update(samples)
    data._PRICE_CACHE.clear()
    data._NEGATIVE_RESOLVE.clear()
    portfolio._MODELS.clear()
    analytics_cache.invalidate()
    fundamentals.invalidate_cache()
    news.invalidate_cache()
    macro._YIELD_CACHE.clear()
    persistence.reset_store_for_tests()


def price_series(days: int = 260, start: float = 100.0, daily: float = 0.001) -> pd.Series:
    idx = pd.bdate_range("2024-01-02", periods=days)
    values = start * np.cumprod(np.full(days, 1.0 + daily))
    return pd.Series(values, index=idx, name="close")


def price_csv(days: int = 40, price_col: str = "Close") -> bytes:
    s = price_series(days=days)
    df = pd.DataFrame({"Date": s.index.strftime("%Y-%m-%d"), price_col: s.values})
    return df.to_csv(index=False).encode("utf-8")

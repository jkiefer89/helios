import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from engine import data, portfolio

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("HELIOS_AUTH", "0")
os.environ.setdefault("HELIOS_RF", "0.02")


@pytest.fixture(autouse=True)
def reset_process_local_stores():
    """Keep tests independent while preserving bundled sample instruments."""
    data.load_samples()
    samples = {k: v for k, v in data._STORE.items() if v.source == "sample"}
    data._STORE.clear()
    data._STORE.update(samples)
    data._PRICE_CACHE.clear()
    portfolio._MODELS.clear()
    yield
    data._STORE.clear()
    data._STORE.update(samples)
    data._PRICE_CACHE.clear()
    portfolio._MODELS.clear()


def price_series(days: int = 260, start: float = 100.0, daily: float = 0.001) -> pd.Series:
    idx = pd.bdate_range("2024-01-02", periods=days)
    values = start * np.cumprod(np.full(days, 1.0 + daily))
    return pd.Series(values, index=idx, name="close")


def price_csv(days: int = 40, price_col: str = "Close") -> bytes:
    s = price_series(days=days)
    df = pd.DataFrame({"Date": s.index.strftime("%Y-%m-%d"), price_col: s.values})
    return df.to_csv(index=False).encode("utf-8")

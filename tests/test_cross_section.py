"""Cross-sectional relative-strength leg — evidence-gated, PIT-honest.

The component's weight is EARNED from measured walk-forward rank-IC evidence:
a planted cross-sectional signal must open the gate; pure noise must keep it
closed (zero weight, explicit caveat). Universe exclusions and no-look-ahead
are locked here too.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import cross_section, data, forecast, signals


def _register(symbol: str, close: pd.Series, name: str | None = None):
    frame = close.to_frame("close")
    data.register(data.Instrument(symbol, name or symbol, frame, "upload", []))


def _synthetic_universe(n_names=20, n_days=620, signal=True, seed=11):
    """Panel where 21d momentum genuinely persists cross-sectionally (signal=True)
    or returns are pure iid noise (signal=False)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-02", periods=n_days)
    out = {}
    for k in range(n_names):
        if signal:
            # Slowly-varying per-name drift: past relative momentum predicts
            # future relative return by construction. Strong SNR on purpose —
            # this locks the MECHANISM (gate opens on real signal), not a
            # market-realism claim.
            blocks = rng.normal(0.0, 0.004, size=n_days // 126 + 1)
            drift = np.repeat(blocks, 126)[:n_days]
            rets = drift + rng.normal(0, 0.010, n_days)
        else:
            rets = rng.normal(0.0004, 0.015, n_days)
        out[f"SY{k:02d}"] = pd.Series(100 * np.exp(np.cumsum(rets)), index=idx)
    return out


@pytest.fixture(autouse=True)
def _clean_store():
    saved = dict(data._STORE)
    data._STORE.clear()
    cross_section.invalidate_cache()
    yield
    data._STORE.clear()
    data._STORE.update(saved)
    cross_section.invalidate_cache()


def test_planted_signal_opens_the_gate():
    for sym, close in _synthetic_universe(signal=True).items():
        _register(sym, close)
    snap = cross_section.build_snapshot(extend_history=False)
    assert snap is not None
    m = snap["metrics"]
    assert m["status"] == "ok" and m["n_dates"] >= cross_section.GATE_MIN_DATES
    assert m["ic_t_stat"] >= 2.0                      # real signal measured
    assert snap["edge_multiplier"] == 1.0
    ranks = [v["rank"] for v in snap["scores"].values()]
    assert sorted(ranks) == list(range(1, len(ranks) + 1))
    top = min(snap["scores"].items(), key=lambda kv: kv[1]["rank"])
    assert top[1]["pctile"] == 1.0


def test_noise_universe_keeps_the_gate_closed():
    for sym, close in _synthetic_universe(signal=False, seed=23).items():
        _register(sym, close)
    snap = cross_section.build_snapshot(extend_history=False)
    assert snap is not None
    # Ranks still compute (they are just an ordering) but weight is EARNED:
    # noise evidence must not clear the t-stat gate.
    assert snap["edge_multiplier"] < 0.5
    assert cross_section.edge_multiplier({"status": "ok", "n_dates": 40,
                                          "ic_t_stat": 0.4}) == 0.0


def test_universe_exclusions_are_structural():
    base = _synthetic_universe(n_names=14, signal=True)
    for sym, close in base.items():
        _register(sym, close)
    _register("SOXL3", list(base.values())[0] * 3,
              name="Direxion Daily Semiconductor Bull 3X Shares")   # leveraged
    _register("SPYX", list(base.values())[1],
              name="State Street SPDR S&P 500 ETF")                 # broad index
    _register("BTC-USD", list(base.values())[2])                    # crypto calendar
    flat = pd.Series(100.0, index=list(base.values())[0].index)
    _register("CASHY", flat + np.arange(len(flat)) * 1e-4)          # sub-vol sleeve
    closes = cross_section._eligible_close_series()
    assert "SOXL3" not in closes and "SPYX" not in closes
    assert "BTC-USD" not in closes and "CASHY" not in closes
    assert len(closes) == 14


def test_no_look_ahead_in_features():
    """Predictions at date d must not change when FUTURE bars change."""
    uni = _synthetic_universe(n_names=16, signal=True, seed=7)
    for sym, close in uni.items():
        _register(sym, close)
    px1 = cross_section._build_panel(extend=False)
    rows1 = cross_section._eval_rows(px1)
    d_mid = rows1[len(rows1) // 2][0]

    # Mutate everything AFTER d_mid + horizon and rebuild.
    data._STORE.clear()
    for sym, close in uni.items():
        mutated = close.copy()
        cut = mutated.index.get_loc(d_mid) + cross_section.HORIZON_D + 1
        mutated.iloc[cut:] = mutated.iloc[cut:] * (1 + np.linspace(0.1, 0.5, len(mutated) - cut))
        _register(sym, mutated)
    px2 = cross_section._build_panel(extend=False)
    rows2 = cross_section._eval_rows(px2)

    r1 = next(r for r in rows1 if r[0] == d_mid)
    r2 = next(r for r in rows2 if r[0] == d_mid)
    pd.testing.assert_frame_equal(r1[1], r2[1])       # features at d unchanged
    pd.testing.assert_series_equal(r1[2], r2[2])      # target (<= d+H) unchanged


def _fc(close):
    fc = forecast.forecast(close, horizon=21, n_paths=200)
    return {**fc, "quality": {"directional_accuracy": 0.60, "n_test": 100}}


_SENT = {"aggregate_score": 0.0, "aggregate_label": "neutral", "count": 0}


def _slice(pctile, edge, rank=2, n=20):
    return {"pctile": pctile, "rank": rank, "universe_size": n, "n": n,
            "horizon_days": 21, "edge_multiplier": edge, "as_of": "2026-07-16",
            "metrics": {"status": "ok", "n_dates": 24, "ic_t_stat": 2.4,
                        "mean_rank_ic": 0.14}}


def test_signals_gain_earned_relative_component():
    close = list(_synthetic_universe(n_names=1, signal=False).values())[0]
    sig = signals.evaluate(close, _fc(close), _SENT,
                           cross_section_result=_slice(pctile=0.95, edge=1.0))
    names = [c["name"] for c in sig["components"]]
    assert "relative" in names
    rel = next(c for c in sig["components"] if c["name"] == "relative")
    assert rel["effective_weight"] == pytest.approx(0.15, abs=0.001)
    assert rel["raw"] == pytest.approx(0.9, abs=0.001)         # pctile 0.95 -> +0.9
    assert "Ranks #2 of 20" in rel["clause"]
    # Normalization holds: weights still sum to 1.
    total = sum(c["effective_weight"] for c in sig["components"])
    # forecast weight is edge-scaled (DA 0.60 -> full), so total ~= 1.
    assert total == pytest.approx(1.0, abs=0.01)


def test_signals_refuse_ungated_relative_weight():
    close = list(_synthetic_universe(n_names=1, signal=False).values())[0]
    sig = signals.evaluate(close, _fc(close), _SENT,
                           cross_section_result=_slice(pctile=0.95, edge=0.0))
    names = [c["name"] for c in sig["components"]]
    assert "relative" not in names                     # zero weight, no component
    assert any("NO weight" in c for c in sig["caveats"])
    assert any(">=12 windows" in c and "t>1.5" in c for c in sig["caveats"])
    assert any("survivor" in c.lower() for c in sig["caveats"])   # bias disclosed


def test_partial_edge_scales_the_share():
    close = list(_synthetic_universe(n_names=1, signal=False).values())[0]
    sig = signals.evaluate(close, _fc(close), _SENT,
                           cross_section_result=_slice(pctile=0.10, edge=0.5))
    rel = next(c for c in sig["components"] if c["name"] == "relative")
    assert rel["effective_weight"] == pytest.approx(0.075, abs=0.001)   # 15% × 0.5
    assert rel["raw"] == pytest.approx(-0.8, abs=0.001)                 # bottom decile



def test_gate_floor_prices_in_survivorship():
    """The t-floor sits ABOVE conventional significance because the evidence
    panel is a survivor universe — t=2.0 (nominally significant) earns only
    half weight; the 12-window floor binds regardless of t."""
    ok = {"status": "ok", "n_dates": 40}
    assert cross_section.edge_multiplier({**ok, "ic_t_stat": 1.5}) == 0.0
    assert cross_section.edge_multiplier({**ok, "ic_t_stat": 2.0}) == pytest.approx(0.5)
    assert cross_section.edge_multiplier({**ok, "ic_t_stat": 2.5}) == 1.0
    assert cross_section.edge_multiplier({"status": "ok", "n_dates": 11,
                                          "ic_t_stat": 9.0}) == 0.0


def test_journal_record_parity_with_analyze(monkeypatch, tmp_path):
    """BLOCKER lock: the recorded journal composite must equal the interactive
    analyze composite — including the earned relative leg when the gate opens."""
    import app as helios
    from io import BytesIO
    from engine import persistence
    from tests.conftest import price_csv

    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "h.db"))
    persistence.reset_store_for_tests()
    try:
        helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        client = helios.app.test_client()
        client.post("/api/upload",
                    data={"file": (BytesIO(price_csv(days=300)), "parity.csv"),
                          "symbol": "PARIT"},
                    content_type="multipart/form-data")
        # Open the gate via a cached snapshot slice for this symbol.
        monkeypatch.setattr(cross_section, "for_symbol",
                            lambda s: _slice(pctile=0.9, edge=1.0) if s == "PARIT" else None)
        analyzed = client.get("/api/analyze?ticker=PARIT&horizon=21").get_json()
        recorded = client.post("/api/signals/record",
                               json={"ticker": "PARIT", "horizon": 21}).get_json()
        entry = recorded.get("signal_journal_entry") or {}
        assert analyzed["signal"]["score"] == pytest.approx(
            float(entry.get("score")), abs=1e-6)
        assert any(c["name"] == "relative" for c in analyzed["signal"]["components"])
    finally:
        persistence.reset_store_for_tests()

"""Calibration + DSR + PBO validation outputs (deep-review completion slice)."""
from __future__ import annotations

import numpy as np
import pytest

from engine import forecast, model_validation


def _signal_xy(n=400, noise=0.01, seed=7):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 4))
    y = X @ np.array([0.02, -0.015, 0.01, 0.0]) + rng.normal(scale=noise, size=n)
    return X, y


def test_calibration_rewards_informative_models_only():
    X, y = _signal_xy()
    evaluated = forecast._evaluate(X, y)
    cal = evaluated["calibration"]
    assert evaluated["evaluation_method"] == "rolling_origin_expanding"
    assert evaluated["initial_train_size"] >= 60
    assert evaluated["refit_interval"] == 5
    assert cal["status"] == "ok"
    assert cal["brier_score"] < 0.25            # beats the coin
    assert cal["brier_skill"] > 0.3
    acc = {b["bin"]: b["realized_accuracy_pct"] for b in cal["bins"]}
    assert acc["strong"] > acc["weak"]          # conviction aligns with accuracy
    assert "not a fitted probability model" in cal["basis"]

    rng = np.random.default_rng(11)
    noise_cal = forecast._evaluate(X, rng.normal(scale=0.01, size=len(y)))["calibration"]
    assert abs(noise_cal["brier_skill"]) < 0.15  # no fabricated skill on noise


def test_calibration_degrades_below_data_floor():
    X, y = _signal_xy(n=30)
    cal = forecast._evaluate(X, y)["calibration"]
    assert cal["status"] == "insufficient_data"


def test_deflated_sharpe_penalizes_trial_count():
    rng = np.random.default_rng(3)
    alphas = list(rng.normal(loc=0.3, scale=1.0, size=60))
    few = model_validation._deflated_sharpe(alphas, [0.3, 0.1])
    many = model_validation._deflated_sharpe(
        alphas, list(rng.normal(loc=0.0, scale=0.3, size=40)))
    assert few["status"] == many["status"] == "ok"
    # Same champion evidence, more trials -> higher luck hurdle -> lower DSR.
    assert many["expected_max_sharpe_under_trials"] > few["expected_max_sharpe_under_trials"]
    assert many["deflated_sharpe_probability"] < few["deflated_sharpe_probability"]


def test_deflated_sharpe_degrades_honestly():
    out = model_validation._deflated_sharpe([0.5] * 5, [0.2, 0.3])
    assert out["status"] == "insufficient_data"
    assert "accumulates" in out["note"]


def test_pbo_separates_skill_from_noise():
    rng = np.random.default_rng(5)
    dates = [f"2026-01-{i:02d}" for i in range(1, 33)]
    # One genuinely better model -> IS winner keeps winning OOS -> low PBO.
    skill = {
        "GOOD": {d: 1.0 + rng.normal(scale=0.3) for d in dates},
        "MEH1": {d: rng.normal(scale=0.3) for d in dates},
        "MEH2": {d: rng.normal(scale=0.3) for d in dates},
        "MEH3": {d: rng.normal(scale=0.3) for d in dates},
    }
    low = model_validation._pbo_cscv(skill)
    assert low["status"] == "ok"
    assert low["pbo"] <= 0.15
    # Pure noise -> the IS winner is luck -> PBO near or above 0.5.
    noise = {m: {d: float(rng.normal()) for d in dates} for m in ("A", "B", "C", "D")}
    high = model_validation._pbo_cscv(noise)
    assert high["pbo"] >= 0.3
    assert high["n_aligned_windows"] == 32


def test_pbo_degrades_honestly():
    assert model_validation._pbo_cscv({"A": {"d1": 1.0}})["status"] == "insufficient_data"
    two_dates = {m: {"d1": 1.0, "d2": 2.0} for m in ("A", "B", "C")}
    out = model_validation._pbo_cscv(two_dates)
    assert out["status"] == "insufficient_data"
    assert "16" in out["note"]


def test_dsr_and_pbo_correct_for_overlapping_windows():
    """step < horizon means windows share forward days: DSR must deflate to the
    effective count and PBO must decimate to a non-overlapping subgrid —
    otherwise overlap manufactures champion legitimacy."""
    rng = np.random.default_rng(9)
    alphas = list(rng.normal(loc=0.3, scale=1.0, size=60))
    trials = [0.3, 0.1, 0.05]
    iid = model_validation._deflated_sharpe(alphas, trials, overlap_factor=1.0)
    overlapped = model_validation._deflated_sharpe(alphas, trials, overlap_factor=3.0)
    assert iid["status"] == overlapped["status"] == "ok"
    assert overlapped["n_effective"] == 20 and iid["n_effective"] == 60
    # Same evidence counted honestly -> less confidence under overlap.
    assert overlapped["deflated_sharpe_probability"] < iid["deflated_sharpe_probability"]
    # Below the effective floor -> honest degradation, with the floor named.
    tiny = model_validation._deflated_sharpe(alphas[:24], trials, overlap_factor=3.0)
    assert tiny["status"] == "insufficient_data"
    assert "EFFECTIVE" in tiny["note"]

    dates = [f"2026-01-{i:02d}" for i in range(1, 49)]
    series = {m: {d: float(rng.normal()) for d in dates} for m in ("A", "B", "C", "D")}
    decimated = model_validation._pbo_cscv(series, overlap_factor=3.0)
    assert decimated["status"] == "ok"
    assert decimated["n_after_overlap_decimation"] == 16   # 48 / stride 3
    assert decimated["overlap_stride"] == 3
    assert "DECIMATED" in decimated["basis"]

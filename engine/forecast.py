"""Return forecasting.

Two complementary pieces:
  1. A Ridge-regression model on lagged technical features that estimates the
     expected next-day return and is validated out-of-sample (directional
     accuracy + RMSE). Pure numpy closed-form fit -- no scikit-learn needed.
  2. A Monte-Carlo projection that turns the model's drift estimate plus
     recent volatility into a forward price cone with percentile bands.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind
from . import mandate as mnd

# Long-horizon presets -> trading days (21/month, 252/year).
LONG_HORIZONS = {"6M": 126, "1Y": 252, "3Y": 756, "5Y": 1260}
# Horizon-uncertainty floor: the cone widens beyond pure GBM the further out we
# look, because volatility itself is non-stationary and uncertain.
_KAPPA_H = ([126, 252, 756, 1260], [1.10, 1.20, 1.35, 1.50])
LONG_SHARPE_CAP = 0.60   # half-ish the 1.5 tactical cap: we believe edge less far out
LONG_N_PATHS = 4000
LONG_BASE_VALUE = 10_000.0
OUTER_BAND_VOL_INFLATION = 1.15  # vol-of-vol haircut on p05/p95


# --------------------------------------------------------------------------- #
# Feature engineering
# --------------------------------------------------------------------------- #
def _build_features(close: pd.Series) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (X, y, last_row_X). y = next-day return."""
    r = close.pct_change()
    feat = pd.DataFrame(index=close.index)
    feat["r1"] = r
    feat["r2"] = r.shift(1)
    feat["r3"] = r.shift(2)
    feat["mom5"] = close.pct_change(5)
    feat["mom10"] = close.pct_change(10)
    feat["sma_gap"] = close / ind.sma(close, 20) - 1.0
    feat["rsi"] = (ind.rsi(close) - 50.0) / 50.0
    _, _, hist = ind.macd(close)
    feat["macd_hist"] = hist / close
    feat["vol10"] = r.rolling(10).std()

    target = r.shift(-1)  # predict tomorrow's return
    data = feat.copy()
    data["__y"] = target
    last_row = feat.iloc[[-1]].to_numpy(dtype=float)  # features as of "today"
    data = data.dropna()
    X = data.drop(columns="__y").to_numpy(dtype=float)
    y = data["__y"].to_numpy(dtype=float)
    return X, y, last_row


def _standardize(X, mean, std):
    return (X - mean) / std


def _ridge_fit(X, y, alpha=1.0):
    """Closed-form ridge with an intercept. Returns (weights, intercept)."""
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std < 1e-9] = 1.0
    Xs = _standardize(X, mean, std)
    n, d = Xs.shape
    with np.errstate(all="ignore"):  # numpy 2.0 BLAS emits spurious matmul flags
        A = Xs.T @ Xs + alpha * np.eye(d)
        w = np.linalg.solve(A, Xs.T @ (y - y.mean()))
    b = float(y.mean())
    return np.nan_to_num(w), b, mean, std


def _ridge_predict(Xrow, w, b, mean, std):
    with np.errstate(all="ignore"):
        pred = _standardize(Xrow, mean, std) @ w + b
        return float(np.asarray(pred).reshape(-1)[0])


# --------------------------------------------------------------------------- #
# Model quality (walk-forward out-of-sample)
# --------------------------------------------------------------------------- #
def _evaluate(X, y, alpha=1.0):
    n = len(y)
    split = int(n * 0.7)
    if split < 30 or n - split < 10:
        return {"r2": None, "rmse": None, "directional_accuracy": None, "n_test": 0}
    w, b, mean, std = _ridge_fit(X[:split], y[:split], alpha)
    with np.errstate(all="ignore"):
        pred = _standardize(X[split:], mean, std) @ w + b
    actual = y[split:]
    rmse = float(np.sqrt(np.mean((pred - actual) ** 2)))
    ss_res = np.sum((actual - pred) ** 2)
    ss_tot = np.sum((actual - actual.mean()) ** 2)
    r2 = float(1 - ss_res / ss_tot) if ss_tot > 1e-12 else None
    nonzero = actual != 0
    dir_acc = float(np.mean(np.sign(pred[nonzero]) == np.sign(actual[nonzero]))) if nonzero.any() else None
    return {"r2": r2, "rmse": rmse, "directional_accuracy": dir_acc, "n_test": int(n - split)}


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def forecast(close: pd.Series, horizon: int = 21, n_paths: int = 2000, alpha: float = 1.0) -> dict:
    """Forecast `horizon` business days ahead with a Monte-Carlo cone.

    Returns a dict with median path, percentile bands, expected return, the
    model's next-day drift estimate, and out-of-sample quality metrics.
    """
    close = close.dropna()
    X, y, last_row = _build_features(close)
    quality = _evaluate(X, y, alpha)

    # Fit on all available data for the live drift estimate.
    w, b, mean, std = _ridge_fit(X, y, alpha)
    model_mu = _ridge_predict(last_row, w, b, mean, std)

    r = close.pct_change().dropna()
    hist_mu = float(r.tail(60).mean())
    sigma = float(r.tail(60).std())
    if not np.isfinite(sigma) or sigma <= 0:
        sigma = float(r.std()) or 0.01

    # Blend the model's view with the recent realized mean (shrinks noise),
    # then cap the drift so the implied annualized Sharpe stays <= ~1.5. This
    # stops naive momentum extrapolation from forecasting absurd moves, while
    # still scaling responsiveness with each instrument's volatility.
    mu_raw = 0.6 * float(np.clip(model_mu, -3 * sigma, 3 * sigma)) + 0.4 * hist_mu
    drift_cap = 1.5 / np.sqrt(252) * sigma  # |mu/sigma| * sqrt(252) <= 1.5
    mu = float(np.clip(mu_raw, -drift_cap, drift_cap))

    last_price = float(close.iloc[-1])
    rng = np.random.default_rng(12345)
    shocks = rng.standard_normal((n_paths, horizon))
    # Mix a gaussian shock with bootstrapped residuals (for fat tails). Both are
    # normalized to UNIT variance and combined with weights whose squares sum to
    # 1, so the cone's realized std equals `sigma` (= the reported expected vol)
    # rather than the ~0.7x-narrower blend of two half-weighted sigma terms.
    boot_sample = r.tail(252).to_numpy() - float(r.tail(252).mean())
    boot_std = float(boot_sample.std()) or sigma
    boot_z = rng.choice(boot_sample, size=(n_paths, horizon)) / boot_std
    gw = 0.5  # gaussian vs bootstrap mix; squares sum to 1 so total std == sigma
    steps = mu + sigma * (gw * shocks + np.sqrt(1 - gw * gw) * boot_z)
    paths = last_price * np.cumprod(1 + steps, axis=1)

    pct = lambda q: np.percentile(paths, q, axis=0)  # noqa: E731
    median = pct(50)
    bands = {
        "p05": pct(5).tolist(),
        "p25": pct(25).tolist(),
        "p50": median.tolist(),
        "p75": pct(75).tolist(),
        "p95": pct(95).tolist(),
    }

    future_idx = pd.bdate_range(start=close.index[-1] + pd.Timedelta(days=1), periods=horizon)
    exp_return = float(median[-1] / last_price - 1)
    prob_up = float(np.mean(paths[:, -1] > last_price))

    return {
        "dates": [d.strftime("%Y-%m-%d") for d in future_idx],
        "bands": bands,
        "horizon_days": horizon,
        "expected_return_pct": exp_return * 100,
        "prob_up": prob_up,
        "model_daily_drift_pct": float(mu) * 100,
        "annualized_drift_pct": (float(mu) * 252) * 100,
        "expected_vol_pct": sigma * np.sqrt(252) * 100,
        "quality": quality,
        "feature_weights": _named_weights(w),
    }


_FEATURE_NAMES = ["r1", "r2", "r3", "mom5", "mom10", "sma_gap", "rsi", "macd_hist", "vol10"]


def _named_weights(w):
    return sorted(
        [{"feature": n, "weight": float(v)} for n, v in zip(_FEATURE_NAMES, w)],
        key=lambda d: abs(d["weight"]),
        reverse=True,
    )


# --------------------------------------------------------------------------- #
# Long-horizon strategic projection (6M / 1Y / 3Y / 5Y)
#
# A multi-year cone is NOT the short Ridge drift extrapolated — that is the
# momentum-blowup trap. Instead the drift shrinks toward a defensible CAPM
# anchor (rf + ERP * growth_orientation) as the horizon grows, the cone widens
# with sqrt(time) plus a regime/vol-of-vol cushion, and the result is explicitly
# labelled a strategic projection rather than a trading forecast.
# --------------------------------------------------------------------------- #
def forecast_long(close: pd.Series, horizon_days: int, mandate_key: str = "balanced",
                  base: float = LONG_BASE_VALUE, anchor_return: float | None = None,
                  anchor_basis: str = "mandate_capm") -> dict:
    """``anchor_return`` (annual fraction) lets callers substitute a
    fundamentals-derived CMA anchor for the generic CAPM one, so the multi-year
    drift reverts toward what the instrument's own yield/growth/valuation imply
    rather than a one-size anchor. ``anchor_basis`` is echoed into the payload
    so the provenance of the anchor is always visible."""
    close = close.dropna()
    r = close.pct_change().dropna()

    sigma_252 = float(r.tail(252).std() * np.sqrt(252))
    if not np.isfinite(sigma_252) or sigma_252 <= 0:
        sigma_252 = float(r.std() * np.sqrt(252)) or 0.10
    sigma_60 = float(r.tail(60).std() * np.sqrt(252))
    if not np.isfinite(sigma_60) or sigma_60 <= 0:
        sigma_60 = sigma_252  # fall back so the regime multiplier stays 1.0
    mu_hist = float(r.tail(252).mean() * 252)

    # Drift: shrink trailing-mean (Sharpe<=1.5 capped) toward the anchor.
    mu_hist_capped = float(np.clip(mu_hist, -1.5 * sigma_252, 1.5 * sigma_252))
    if anchor_return is None:
        mu_anchor, anchor_basis = mnd.anchor_return(mandate_key), "mandate_capm"
    else:
        mu_anchor = float(anchor_return)
    lam = float(np.clip(horizon_days / 1260.0, 0.0, 0.8))
    mu_lt = (1 - lam) * mu_hist_capped + lam * mu_anchor
    # Final long-horizon Sharpe cap (no long-only book sustains >0.6 over years).
    cap = mnd.RF + LONG_SHARPE_CAP * sigma_252
    floor = mnd.RF - LONG_SHARPE_CAP * sigma_252
    mu_lt = float(np.clip(mu_lt, floor, cap))

    # Volatility: full-cycle vol, widened to a horizon floor or the current regime.
    kappa = float(np.interp(horizon_days, _KAPPA_H[0], _KAPPA_H[1]))
    regime = sigma_60 / sigma_252 if sigma_252 > 0 else 1.0
    sigma_eff = sigma_252 * max(kappa, regime)

    steps = max(1, int(round(horizon_days / 21.0)))   # monthly steps
    dt = 1.0 / 12.0
    rng = np.random.default_rng(12345)
    Z = rng.standard_normal((LONG_N_PATHS, steps))

    def _paths(sig):
        log_step = (mu_lt - 0.5 * sig**2) * dt + sig * np.sqrt(dt) * Z
        return base * np.exp(np.cumsum(log_step, axis=1))

    inner = _paths(sigma_eff)                              # central scenario
    outer = _paths(sigma_eff * OUTER_BAND_VOL_INFLATION)  # widened tails

    pct = lambda a, q: np.percentile(a, q, axis=0)  # noqa: E731
    bands = {
        "p05": pct(outer, 5).tolist(),
        "p25": pct(inner, 25).tolist(),
        "p50": pct(inner, 50).tolist(),
        "p75": pct(inner, 75).tolist(),
        "p95": pct(outer, 95).tolist(),
    }
    term = inner[:, -1]
    term_outer = outer[:, -1]
    years = horizon_days / 252.0
    cagr = lambda v: (np.asarray(v) / base) ** (1 / years) - 1  # noqa: E731

    # Worst intra-path drawdown distribution + mandate breach probability.
    run_max = np.maximum.accumulate(inner, axis=1)
    path_dd = ((inner - run_max) / run_max).min(axis=1)  # most-negative per path
    tol = -mnd.get(mandate_key)["max_drawdown_tolerance_pct"] / 100.0

    future = pd.bdate_range(start=close.index[-1] + pd.Timedelta(days=1),
                            periods=steps, freq="21B")
    return {
        "kind": "long",
        "label": _long_label(horizon_days),
        "horizon_days": horizon_days,
        "base_value": base,
        "dates": [d.strftime("%Y-%m-%d") for d in future],
        "bands": bands,
        "terminal": {q: float(np.percentile(term if q in ("p25", "p50", "p75") else term_outer, int(q[1:])))
                     for q in ("p05", "p25", "p50", "p75", "p95")},
        "cagr_pct": {q: float(np.percentile(cagr(term if q in ("p25", "p50", "p75") else term_outer), int(q[1:])) * 100)
                     for q in ("p05", "p25", "p50", "p75", "p95")},
        "prob_positive": float(np.mean(term > base)),
        "prob_meets_mandate": float(np.mean(cagr(term) >= mnd.target_return(mandate_key))),
        "mandate_target_pct": round(mnd.target_return(mandate_key) * 100, 2),
        "drawdown_median_pct": float(np.median(path_dd) * 100),
        "drawdown_p95_pct": float(np.percentile(path_dd, 5) * 100),  # 5th pct = worst tail
        "prob_breach_maxdd": float(np.mean(path_dd < tol)),
        "params": {
            "mu_long_pct": round(mu_lt * 100, 2),
            "mu_hist_pct": round(mu_hist * 100, 2),
            "mu_anchor_pct": round(mu_anchor * 100, 2),
            "anchor_basis": anchor_basis,
            "anchor_weight_lambda": round(lam, 2),
            "sigma_ann_pct": round(sigma_252 * 100, 1),
            "sigma_eff_pct": round(sigma_eff * 100, 1),
            "regime_mult": round(max(kappa, regime), 2),
            "n_paths": LONG_N_PATHS,
            "step": "monthly",
        },
        "disclaimer": "Strategic statistical projection, not a trading forecast or guarantee. "
                      "Bands widen with the square root of time and assume the trailing-vol regime persists.",
    }


def _long_label(h: int) -> str:
    for k, v in LONG_HORIZONS.items():
        if v == h:
            return k
    return f"{h}d"

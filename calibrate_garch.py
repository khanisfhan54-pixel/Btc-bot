"""
calibrate_garch.py — Offline MLE calibration of MS-GARCH(1,1) for BTCUSDT 1-minute data.

Run this ONCE on historical data before deploying the regime engine.
Fitted parameters are then passed to engine.garch.load_fitted_params().

Usage:
    returns = load_your_btcusdt_1min_returns()   # shape (N,), fractional returns
    params  = fit_msgarch_mle(returns)
    print(params)
    # Then hardcode or save these values and pass to load_fitted_params()
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp


def fit_msgarch_mle(returns: np.ndarray, n_regimes: int = 2) -> dict:
    """
    MLE fit of MS-GARCH(1,1) on 1-minute BTC returns.

    Args:
        returns:   1-D array of fractional price returns, shape (N,)
        n_regimes: Number of volatility regimes (default: 2)

    Returns:
        dict with keys: omega, alpha, beta, P, converged, log_lik
    """
    returns = np.asarray(returns, dtype=float)
    if returns.ndim != 1:
        raise ValueError(f"returns must be 1-D, got shape {returns.shape}")
    T = len(returns)
    target_var = float(np.var(returns))

    def neg_log_likelihood(params: np.ndarray) -> float:
        omega = np.abs(params[:2])
        alpha = np.clip(params[2:4], 1e-6, 0.5)
        beta_garch = np.clip(params[4:6], 0.1, 0.98)
        p11   = float(np.clip(params[6], 0.80, 0.999))
        p22   = float(np.clip(params[7], 0.80, 0.999))

        # Stationarity check: reject non-stationary candidates early
        for k in range(n_regimes):
            if alpha[k] + beta_garch[k] >= 0.999:
                return 1e10

        P_mat = np.array([[p11, 1.0 - p11], [1.0 - p22, p22]])
        var   = np.array([target_var, target_var * 3.0])
        prob  = np.ones(2) / 2.0
        log_lik = 0.0

        for t in range(T):
            r2 = returns[t] ** 2
            log_f = (
                -0.5 * np.log(2.0 * np.pi * var + 1e-12)
                - 0.5 * r2 / (var + 1e-12)
            )
            log_joint = np.log(np.clip(prob, 1e-300, None)) + log_f
            log_scale  = logsumexp(log_joint)
            log_lik   += log_scale

            prob  = np.exp(log_joint - log_scale)
            prob  = np.dot(prob, P_mat)
            prob  = np.clip(prob, 1e-6, None)
            prob /= prob.sum()

            var = np.clip(omega + alpha * r2 + beta_garch * var, 1e-8, None)

        return -log_lik

    # Initial guess calibrated for 1-minute BTCUSDT
    x0 = [5e-7, 3e-5, 0.12, 0.25, 0.85, 0.65, 0.97, 0.93]
    bounds = [
        (1e-10, 1e-3), (1e-8, 1e-2),   # omega
        (0.01,  0.45), (0.05, 0.45),   # alpha
        (0.50,  0.97), (0.45, 0.94),   # beta
        (0.80,  0.999),(0.80, 0.999),  # transition probs
    ]
    result = minimize(
        neg_log_likelihood, x0, method="L-BFGS-B", bounds=bounds,
        options={"maxiter": 1000, "ftol": 1e-12}
    )
    p = result.x
    return {
        "omega":     np.abs(p[:2]),
        "alpha":     np.clip(p[2:4], 1e-6, 0.5),
        "beta_garch": np.clip(p[4:6], 0.1,  0.98),
        "P":         np.array([[p[6], 1.0 - p[6]], [1.0 - p[7], p[7]]]),
        "converged": bool(result.success),
        "log_lik":   float(-result.fun),
    }


if __name__ == "__main__":
    # Smoke test on synthetic BTC-like data
    rng = np.random.default_rng(42)
    synthetic_returns = rng.normal(0, 0.003, size=5000)
    params = fit_msgarch_mle(synthetic_returns)
    print("Fitted GARCH params:")
    for k, v in params.items():
        print(f"  {k}: {v}")
    print(f"\nPersistence (alpha+beta): {params['alpha'] + params['beta']}")
    assert params["converged"], "MLE did not converge on synthetic data"
    print("\nSmoke test passed.")

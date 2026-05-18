"""
Hawkes Calibrator (U-08)
========================
Maximum-likelihood calibration of a univariate exponential-kernel Hawkes
process intensity:

    lambda(t) = mu + alpha * sum_{t_j < t} exp(-decay * (t - t_j))

We fit (alpha, decay) by minimising the negative log-likelihood of the
observed event timestamps. mu (baseline) is held fixed at 0.01.

Branching-ratio constraint:
    alpha / decay < 0.9          (stationarity / explosiveness guard)
"""
from __future__ import annotations

import logging
import math
from typing import List, Tuple

import numpy as np
from scipy.optimize import minimize

log = logging.getLogger("hawkes_calibrator")


def _neg_log_likelihood(params: np.ndarray,
                        t: np.ndarray,
                        T: float,
                        mu: float) -> float:
    alpha, decay = float(params[0]), float(params[1])
    if alpha <= 0 or decay <= 0:
        return 1e12

    # Recursive computation of A_i = sum_{t_j < t_i} exp(-decay*(t_i - t_j))
    A = np.zeros_like(t)
    for i in range(1, len(t)):
        A[i] = math.exp(-decay * (t[i] - t[i - 1])) * (1.0 + A[i - 1])

    lam = mu + alpha * A
    if np.any(lam <= 0) or not np.all(np.isfinite(lam)):
        return 1e12

    log_lam_sum = float(np.sum(np.log(lam)))

    # Integral term: int_0^T lambda(s) ds
    #   = mu*T + alpha/decay * sum_i (1 - exp(-decay * (T - t_i)))
    integral = mu * T + (alpha / decay) * float(
        np.sum(1.0 - np.exp(-decay * (T - t)))
    )

    nll = -(log_lam_sum - integral)
    if not math.isfinite(nll):
        return 1e12
    return nll


class HawkesCalibrator:
    """MLE calibrator for the (alpha, decay) of an exponential Hawkes process."""

    def __init__(self,
                 mu: float = 0.01,
                 alpha_bounds: Tuple[float, float] = (1e-4, 5.0),
                 decay_bounds: Tuple[float, float] = (1e-4, 20.0),
                 branching_max: float = 0.9):
        self.mu = float(mu)
        self.alpha_bounds = alpha_bounds
        self.decay_bounds = decay_bounds
        self.branching_max = float(branching_max)
        self.last_result_: dict = {}

    def fit(self,
            event_timestamps: List[float],
            method: str = "mle") -> Tuple[float, float]:
        if method != "mle":
            raise ValueError(f"Only method='mle' is supported (got {method!r}).")
        ts = np.asarray(sorted(float(x) for x in event_timestamps),
                        dtype=float)
        if ts.size < 5:
            log.warning("Hawkes MLE: too few events (%d) — returning defaults.",
                        ts.size)
            self.last_result_ = {
                "alpha": 0.1, "decay": 0.5, "branching_ratio": 0.2,
                "n_events": int(ts.size), "converged": False,
                "reason": "too_few_events",
            }
            return 0.1, 0.5

        t0 = ts[0]
        ts = ts - t0
        T = float(ts[-1])
        if T <= 0:
            T = 1.0

        x0 = np.array([0.5, 1.0], dtype=float)
        bounds = [self.alpha_bounds, self.decay_bounds]
        res = minimize(
            _neg_log_likelihood, x0=x0,
            args=(ts, T, self.mu),
            bounds=bounds, method="L-BFGS-B",
            options={"maxiter": 200, "ftol": 1e-7},
        )
        alpha_mle = float(res.x[0])
        decay_mle = float(res.x[1])
        branching = alpha_mle / max(decay_mle, 1e-12)

        clipped = False
        if branching >= self.branching_max:
            scale = (self.branching_max * 0.95) / branching
            alpha_mle = alpha_mle * scale
            branching = alpha_mle / max(decay_mle, 1e-12)
            clipped = True
            log.warning(
                "Hawkes MLE branching=%.4f >= %.2f — alpha clipped to %.6f.",
                alpha_mle / scale, self.branching_max, alpha_mle,
            )

        self.last_result_ = {
            "alpha": alpha_mle,
            "decay": decay_mle,
            "branching_ratio": branching,
            "n_events": int(ts.size),
            "converged": bool(res.success),
            "nll": float(res.fun),
            "clipped": clipped,
        }
        return alpha_mle, decay_mle

    @staticmethod
    def calibrate_from_trades(alpha: float, decay: float, target_obj) -> None:
        """Apply the MLE result to a LiquiditySweepAlpha instance."""
        try:
            target_obj.hawkes_alpha = float(alpha)
            target_obj.hawkes_decay = float(decay)
        except Exception:
            log.exception("Failed to apply Hawkes MLE params to target.")

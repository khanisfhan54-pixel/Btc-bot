from __future__ import annotations

import warnings
import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp


def fit_nhhmm_beta(
    X_norm: np.ndarray,
    sjm_labels: np.ndarray,
    n_states: int = 3,
    n_features: int = 3,
    l2_lambda: float = 0.01,
    max_iter: int = 500,
    random_seed: int = 42,
) -> np.ndarray:
    """Fit NHHMM transition logits P(next_state | current_state, features)."""
    X = np.asarray(X_norm, dtype=float)
    y = np.asarray(sjm_labels, dtype=int).reshape(-1)
    if X.ndim != 2 or X.shape[1] != n_features or X.shape[0] != y.size:
        raise ValueError("X_norm must be (T, n_features) and align with sjm_labels")
    if X.shape[0] < 2:
        raise ValueError("at least two observations required")
    if not np.all(np.isfinite(X)):
        raise ValueError("X_norm contains non-finite values")
    if not set(np.unique(y)).issubset(set(range(n_states))):
        raise ValueError("sjm_labels outside expected state range")

    rng = np.random.default_rng(random_seed)
    beta = rng.normal(0.0, 0.01, size=(n_states, n_states, n_features))
    beta[:, 0, :] = 0.0

    for k in range(n_states):
        mask = y[:-1] == k
        n = int(mask.sum())
        if n == 0:
            raise ValueError(f"state {k} has zero observed transitions")
        if n < 50:
            warnings.warn(f"state {k} has only {n} transition samples", RuntimeWarning)
        Xk = X[:-1][mask]
        yn = y[1:][mask]

        def unpack(theta: np.ndarray) -> np.ndarray:
            b = np.zeros((n_states, n_features), dtype=float)
            b[1:, :] = theta.reshape(n_states - 1, n_features)
            return b

        def obj_grad(theta: np.ndarray) -> tuple[float, np.ndarray]:
            b = unpack(theta)
            logits = Xk @ b.T
            logp = logits - logsumexp(logits, axis=1)[:, None]
            p = np.exp(logp)
            loss = -float(np.mean(logp[np.arange(n), yn])) + 0.5 * l2_lambda * float(np.sum(b[1:] ** 2))
            ind = np.zeros_like(p)
            ind[np.arange(n), yn] = 1.0
            grad_full = ((p - ind).T @ Xk) / n
            grad_full[1:] += l2_lambda * b[1:]
            return loss, grad_full[1:].reshape(-1)

        x0 = beta[k, 1:, :].reshape(-1)
        res = minimize(lambda th: obj_grad(th), x0, jac=True, method="L-BFGS-B", options={"maxiter": int(max_iter)})
        beta[k] = unpack(res.x)
    return beta.astype(np.float64)


def transition_cross_entropy(X_norm: np.ndarray, labels: np.ndarray, beta: np.ndarray) -> dict[int, float]:
    X = np.asarray(X_norm, dtype=float)
    y = np.asarray(labels, dtype=int)
    b = np.asarray(beta, dtype=float)
    out = {}
    for k in range(b.shape[0]):
        mask = y[:-1] == k
        if not np.any(mask):
            continue
        logits = X[:-1][mask] @ b[k].T
        logp = logits - logsumexp(logits, axis=1)[:, None]
        out[k] = float(-np.mean(logp[np.arange(mask.sum()), y[1:][mask]]))
    return out

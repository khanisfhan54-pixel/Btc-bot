"""Compatibility wrapper for the offline parquet calibration pipeline."""
from __future__ import annotations

import os
import numpy as np

from calibrate_pipeline import (
    _kmeans_numpy,
    _load_parquet_training_data,
    run_calibration,
    triple_barrier_labels,
)

DATA_SOURCE = os.environ.get("REGIME_DATA_SOURCE", "synthetic").strip().lower()
OUTPUT_DIR = os.environ.get("REGIME_OUTPUT_DIR", "weights")
OUTPUT_PATH = os.environ.get("REGIME_OUTPUT_PATH", os.path.join(OUTPUT_DIR, "advanced_regime_weights.npz"))
PROVENANCE_PATH = os.environ.get("REGIME_PROVENANCE_PATH", os.path.join(OUTPUT_DIR, "calibration_provenance.json"))


def estimate_emission_moments(returns: np.ndarray, labels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    labels = np.asarray(labels, dtype=int)
    returns = np.asarray(returns, dtype=float)
    k = 3 if labels.size == 0 or labels.max(initial=2) < 3 else 4
    mu = np.zeros(k, dtype=float)
    sigma = np.ones(k, dtype=float) * 0.005
    for state in range(k):
        vals = returns[labels == state]
        if vals.size:
            mu[state] = float(vals.mean())
            sigma[state] = float(max(vals.std(), 1e-4))
    return mu, sigma


def calibrate(ohlcv_csv_path: str, output_path: str) -> None:
    data = np.loadtxt(ohlcv_csv_path, delimiter=",", ndmin=2)
    if data.shape[0] < 8 or data.shape[1] < 6:
        raise ValueError("calibrate requires OHLCV columns and at least 8 rows")
    closes = data[:, 4].astype(float); volumes = data[:, 5].astype(float)
    returns = np.diff(np.log(closes)); vol = volumes[1:]
    X = np.column_stack([returns, np.zeros_like(returns), (vol - vol.mean()) / (vol.std() or 1.0)])
    fm = X.mean(axis=0); fs = np.where(X.std(axis=0) > 1e-12, X.std(axis=0), 1.0)
    Xn = (X - fm) / fs; km = _kmeans_numpy(Xn, 3, 42, 5, 100)
    labels = km["labels_"]; mu, sigma = estimate_emission_moments(returns, labels)
    from calibrate_nhhmm_beta import fit_nhhmm_beta
    beta = fit_nhhmm_beta(Xn, labels)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    np.savez(output_path, nhhmm_beta=beta, nhhmm_mu=mu, nhhmm_sigma=sigma, sjm_centroids=km["cluster_centers_"], sjm_feature_weights=np.ones(3)/np.sqrt(3), feature_mean=fm, feature_std=fs)


if __name__ == "__main__":
    raise SystemExit(0 if run_calibration(exit_on_invalid=True).get("production_valid") else 1)

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from model_weights import ModelWeightManager


def _load_ohlcv(path: Path) -> np.ndarray:
    data = np.loadtxt(path, delimiter=",", ndmin=2)
    if data.shape[1] < 6:
        raise ValueError("Expected OHLCV CSV with at least 6 columns")
    return data


def calibrate(input_csv: str, output_path: str) -> None:
    ohlcv = _load_ohlcv(Path(input_csv))
    closes = ohlcv[:, 4]
    rets = np.diff(np.log(np.clip(closes, 1e-9, None)))
    vols = ohlcv[1:, 5]
    X = np.column_stack([rets, (vols - vols.mean()) / (vols.std() + 1e-8)])

    rng = np.random.default_rng(42)
    centroids = X[rng.choice(len(X), size=3, replace=False)]
    for _ in range(25):
        dists = np.linalg.norm(X[:, None, :] - centroids[None, :, :], axis=2)
        labels = np.argmin(dists, axis=1)
        for i in range(3):
            if np.any(labels == i):
                centroids[i] = X[labels == i].mean(axis=0)
    beta = np.clip(np.array([rets.std(), np.abs(rets).mean(), 1.0]), 0.1, 5.0)

    weights = {
        "nhhmm_beta": beta,
        "nhhmm_mu": np.array([rets[labels == i].mean() if np.any(labels == i) else 0.0 for i in range(3)]),
        "nhhmm_sigma": np.array([rets[labels == i].std() + 1e-6 if np.any(labels == i) else 1e-3 for i in range(3)]),
        "sjm_centroids": centroids,
    }
    ModelWeightManager.save_weights("advanced_regime", weights, output_path)
    print(f"Saved calibrated weights to {output_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="CSV OHLCV path")
    ap.add_argument("--output", default="weights/advanced_regime_weights.npz")
    args = ap.parse_args()
    calibrate(args.input, args.output)

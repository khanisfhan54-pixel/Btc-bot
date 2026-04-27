from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from model_weights import ModelWeightManager

N_STATES = 3
MIN_OHLCV_ROWS = N_STATES + 1


def _load_ohlcv(path: Path) -> np.ndarray:
    data = np.loadtxt(path, delimiter=",", ndmin=2)
    if data.shape[1] < 6:
        raise ValueError("Expected OHLCV CSV with at least 6 columns")
    return data


def _build_feature_matrix(ohlcv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if ohlcv.shape[0] < MIN_OHLCV_ROWS:
        raise ValueError(
            f"Calibration requires at least {MIN_OHLCV_ROWS} OHLCV rows, got {ohlcv.shape[0]}"
        )
    closes = np.asarray(ohlcv[:, 4], dtype=float)
    volumes = np.asarray(ohlcv[:, 5], dtype=float)
    if not np.all(np.isfinite(closes)):
        raise ValueError("Calibration input contains non-finite close prices")
    if not np.all(np.isfinite(volumes)):
        raise ValueError("Calibration input contains non-finite volumes")
    if np.any(closes <= 0.0):
        raise ValueError("Calibration requires strictly positive close prices")

    rets = np.diff(np.log(closes))
    if rets.size < N_STATES:
        raise ValueError(
            f"Calibration requires at least {N_STATES} return observations, got {rets.size}"
        )
    vols = volumes[1:]
    vol_z = (vols - vols.mean()) / (vols.std() + 1e-8)
    X = np.column_stack([rets, vol_z])
    if X.shape[0] < N_STATES:
        raise ValueError(
            f"Calibration requires at least {N_STATES} feature rows, got {X.shape[0]}"
        )
    if not np.all(np.isfinite(X)):
        raise ValueError("Calibration features contain non-finite values")
    return X, rets


def _validate_weights(weights: dict[str, np.ndarray], n_features: int) -> None:
    expected_shapes = {
        "nhhmm_beta": (N_STATES, N_STATES, n_features),
        "nhhmm_mu": (N_STATES,),
        "nhhmm_sigma": (N_STATES,),
        "sjm_centroids": (N_STATES, n_features),
    }
    for key, expected in expected_shapes.items():
        if key not in weights:
            raise ValueError(f"Missing required calibration output key: {key}")
        arr = np.asarray(weights[key], dtype=float)
        if arr.shape != expected:
            raise ValueError(f"{key} has shape {arr.shape}, expected {expected}")
        if not np.all(np.isfinite(arr)):
            raise ValueError(f"{key} contains non-finite values")


def calibrate(input_csv: str, output_path: str) -> None:
    ohlcv = _load_ohlcv(Path(input_csv))
    X, rets = _build_feature_matrix(ohlcv)
    n_features = X.shape[1]

    initial_idx = np.linspace(0, len(X) - 1, N_STATES, dtype=int)
    centroids = X[initial_idx].copy()
    for _ in range(25):
        dists = np.linalg.norm(X[:, None, :] - centroids[None, :, :], axis=2)
        labels = np.argmin(dists, axis=1)
        for i in range(N_STATES):
            if np.any(labels == i):
                centroids[i] = X[labels == i].mean(axis=0)
    return_scale = np.clip(float(rets.std()), 1e-6, 5.0)
    feature_scale = np.clip(np.std(X, axis=0), 1e-6, 5.0)
    beta = np.zeros((N_STATES, N_STATES, n_features), dtype=float)
    for i in range(N_STATES):
        for j in range(N_STATES):
            direction = (j - i) / max(N_STATES - 1, 1)
            beta[i, j, :] = direction * feature_scale * return_scale

    weights = {
        "nhhmm_beta": beta,
        "nhhmm_mu": np.array(
            [rets[labels == i].mean() if np.any(labels == i) else 0.0 for i in range(N_STATES)],
            dtype=float,
        ),
        "nhhmm_sigma": np.array(
            [rets[labels == i].std() + 1e-6 if np.any(labels == i) else 1e-3 for i in range(N_STATES)],
            dtype=float,
        ),
        "sjm_centroids": centroids,
    }
    _validate_weights(weights, n_features=n_features)
    ModelWeightManager.save_weights("advanced_regime", weights, output_path)
    print(f"Saved calibrated weights to {output_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="CSV OHLCV path")
    ap.add_argument("--output", default="weights/advanced_regime_weights.npz")
    args = ap.parse_args()
    calibrate(args.input, args.output)

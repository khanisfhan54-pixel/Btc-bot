#!/usr/bin/env python3
"""Research-only 5m regime calibration artifact builder."""
from __future__ import annotations
import json
import os
import numpy as np
import pandas as pd

from bar_aggregator import resample_bars
from calibrate_garch import fit_msgarch_mle
from calibrate_confirmation_ticks import optimal_confirmation_ticks
from calibrate_regime import _kmeans_numpy
from triple_barrier_labeler import triple_barrier_labels

OUTPUT_PATH = "weights/advanced_regime_weights_5m.npz"
REQUIRED_KEYS = {
    "feature_mean", "feature_std", "sjm_centroids", "sjm_feature_weights", "nhhmm_beta", "nhhmm_mu", "nhhmm_sigma",
    "garch_omega", "garch_alpha", "garch_beta", "garch_P", "confirmation_ticks",
}

def _load_1m_csv(path: str) -> list[list]:
    df = pd.read_csv(path)
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    return df[cols].values.tolist()

def main() -> None:
    os.makedirs("weights", exist_ok=True)
    bars_1m = _load_1m_csv("data/ohlcv_1m.csv")
    bars_5m = resample_bars(bars_1m, minutes=5)
    if len(bars_5m) < 200:
        raise RuntimeError(f"insufficient 5m bars: {len(bars_5m)}")

    closes = np.asarray([float(r[4]) for r in bars_5m], dtype=float)
    vols = np.asarray([float(r[5]) for r in bars_5m], dtype=float)
    log_ret = np.diff(np.log(np.clip(closes, 1e-12, None)), prepend=np.log(max(closes[0], 1e-12)))
    ofi_z = np.zeros_like(log_ret)
    vol_z = (vols - vols.mean()) / (vols.std() + 1e-12)
    X = np.column_stack([log_ret, ofi_z, vol_z])

    cutoff = int(0.8 * len(X))
    X_train = X[:cutoff]
    fmean, fstd = X_train.mean(0), X_train.std(0)
    fstd = np.where(fstd > 1e-12, fstd, 1.0)

    _rv = np.zeros(len(closes), dtype=float)
    for _i in range(5, len(closes)):
        _sl = closes[max(0, _i - 5):_i + 1]
        _rv[_i] = float(np.std(np.diff(np.log(np.clip(_sl, 1e-12, None)))))
    _rv[:5] = _rv[5] if len(_rv) > 5 else 0.003
    labels = triple_barrier_labels(
        prices=closes,
        volatility=_rv,
        barrier_multiplier=1.5,
        max_bars=30,
        embargo_bars=30,
    )
    g = fit_msgarch_mle(log_ret[:cutoff])
    conf_ticks = int(optimal_confirmation_ticks(labels))

    X_norm = (X_train - fmean) / (fstd + 1e-8)
    km = _kmeans_numpy(X_norm, n_clusters=3, random_state=42, n_init=20, max_iter=500)
    sjm_centroids = np.asarray(km.cluster_centers_, dtype=float)
    within_var = np.zeros(3, dtype=float)
    for k in range(3):
        mask = km.labels_ == k
        if np.sum(mask) > 1:
            within_var += X_norm[mask].var(axis=0)
    within_var = within_var / 3.0
    within_var = np.where(within_var > 1e-12, within_var, 1.0)
    sjm_feature_weights = 1.0 / (within_var + 1e-8)
    sjm_feature_weights /= (np.linalg.norm(sjm_feature_weights) + 1e-12)

    rng = np.random.default_rng(42)
    nhhmm_beta = rng.normal(0.0, 0.01, size=(3, 3, 3))
    nhhmm_beta[:, 0, :] = 0.0
    nhhmm_mu = np.array([log_ret[:cutoff].mean(), log_ret[:cutoff].mean()*0.5, 0.0])
    nhhmm_sigma = np.array([
        log_ret[:cutoff].std() + 1e-6,
        log_ret[:cutoff].std() * 1.2 + 1e-6,
        log_ret[:cutoff].std() * 0.8 + 1e-6,
    ])

    np.savez(
        OUTPUT_PATH,
        nhhmm_beta=nhhmm_beta.astype(np.float64),
        feature_mean=fmean.astype(np.float64),
        feature_std=fstd.astype(np.float64),
        sjm_centroids=sjm_centroids.astype(np.float64),
        sjm_feature_weights=sjm_feature_weights.astype(np.float64),
        nhhmm_mu=nhhmm_mu.astype(np.float64),
        nhhmm_sigma=nhhmm_sigma.astype(np.float64),
        garch_omega=np.asarray(g["omega"], dtype=np.float64),
        garch_alpha=np.asarray(g["alpha"], dtype=np.float64),
        garch_beta=np.asarray(g["beta_garch"], dtype=np.float64),
        garch_P=np.asarray(g["P"], dtype=np.float64),
        confirmation_ticks=np.asarray([conf_ticks], dtype=np.int64),
    )

    saved = np.load(OUTPUT_PATH)
    missing = sorted(REQUIRED_KEYS - set(saved.files))
    if missing:
        raise RuntimeError(f"missing keys in {OUTPUT_PATH}: {missing}")
    for k in REQUIRED_KEYS:
        arr = np.asarray(saved[k])
        if arr.size and not np.all(np.isfinite(arr)):
            raise RuntimeError(f"non-finite in key={k}")

    print(json.dumps({"output_path": OUTPUT_PATH, "bars_5m": len(bars_5m), "confirmation_ticks": conf_ticks}, indent=2))

if __name__ == "__main__":
    main()

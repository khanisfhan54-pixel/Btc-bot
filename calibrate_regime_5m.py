#!/usr/bin/env python3
"""Research-only 5m regime calibration artifact builder (real 5m path only)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd

from bar_aggregator import resample_bars
from calibrate_confirmation_ticks import optimal_confirmation_ticks
from calibrate_garch import fit_msgarch_mle
from calibrate_regime import _kmeans_numpy
from data_tools.l2_to_backtest import align_book_to_bars, load_l2_csv
from triple_barrier_labeler import triple_barrier_labels

OUTPUT_PATH = "weights/advanced_regime_weights_5m.npz"
META_PATH = "weights/advanced_regime_weights_5m.meta.json"
REQUIRED_KEYS = {
    "feature_mean", "feature_std", "sjm_centroids", "sjm_feature_weights", "nhhmm_beta", "nhhmm_mu", "nhhmm_sigma",
    "garch_omega", "garch_alpha", "garch_beta", "garch_P", "confirmation_ticks",
}


@dataclass(frozen=True)
class CalibrationSlice:
    start_idx: int
    end_idx: int


def _load_1m_csv(path: str) -> list[list]:
    df = pd.read_csv(path)
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    return df[cols].values.tolist()


def _snapshot_ofi(s: Any) -> float:
    if isinstance(s, dict):
        if "ofi_z" not in s:
            raise RuntimeError("BLOCKER: aligned snapshot missing ofi_z")
        return float(s["ofi_z"])
    if not hasattr(s, "ofi_z"):
        raise RuntimeError("BLOCKER: aligned snapshot object missing ofi_z")
    return float(getattr(s, "ofi_z"))


def _snapshot_ts(s: Any) -> int:
    if isinstance(s, dict):
        return int(s.get("timestamp", 0))
    return int(getattr(s, "timestamp", 0))


def _real_5m_features(bars_1m: Sequence[Sequence[Any]], bars_5m: Sequence[Sequence[Any]]) -> np.ndarray:
    snaps_1m = load_l2_csv("data/bookDepth.csv")
    if not snaps_1m:
        raise RuntimeError("BLOCKER: no real L2 snapshots found in data/bookDepth.csv")
    snaps_5m = align_book_to_bars(bars_5m, snaps_1m)
    if len(snaps_5m) != len(bars_5m):
        raise RuntimeError("BLOCKER: book alignment length mismatch")
    if any(s is None for s in snaps_5m):
        raise RuntimeError("BLOCKER: missing aligned real-book snapshot for one or more 5m bars")

    closes = np.asarray([float(r[4]) for r in bars_5m], dtype=float)
    vols = np.asarray([float(r[5]) for r in bars_5m], dtype=float)
    log_ret = np.diff(np.log(np.clip(closes, 1e-12, None)), prepend=np.log(max(closes[0], 1e-12)))
    vol_z = (vols - vols.mean()) / (vols.std() + 1e-12)
    for i, s in enumerate(snaps_5m):
        if _snapshot_ts(s) <= 0 or _snapshot_ts(s) > int(bars_5m[i][0]):
            raise RuntimeError("BLOCKER: invalid snapshot timestamp alignment")
    ofi_z = np.asarray([_snapshot_ofi(s) for s in snaps_5m], dtype=float)
    if not np.isfinite(ofi_z).all():
        raise RuntimeError("BLOCKER: non-finite real ofi_z values")
    return np.column_stack([log_ret, ofi_z, vol_z])


def calibrate_5m_artifacts(*, bars_1m: list[list], out_path: str = OUTPUT_PATH, meta_path: str = META_PATH, cal_slice: CalibrationSlice | None = None) -> dict:
    os.makedirs("weights", exist_ok=True)
    bars_5m = resample_bars(bars_1m, minutes=5)
    if len(bars_5m) < 200:
        raise RuntimeError(f"insufficient 5m bars: {len(bars_5m)}")

    sl = cal_slice or CalibrationSlice(start_idx=0, end_idx=len(bars_5m))
    if sl.start_idx < 0 or sl.end_idx > len(bars_5m) or sl.start_idx >= sl.end_idx:
        raise RuntimeError("invalid calibration slice")
    bars_5m_slice = bars_5m[sl.start_idx:sl.end_idx]
    bars_1m_slice = [b for b in bars_1m if int(bars_5m_slice[0][0]) <= int(b[0]) <= int(bars_5m_slice[-1][0])]

    X = _real_5m_features(bars_1m_slice, bars_5m_slice)
    closes = np.asarray([float(r[4]) for r in bars_5m_slice], dtype=float)
    cutoff = int(0.8 * len(X))
    X_train = X[:cutoff]

    fmean, fstd = X_train.mean(0), X_train.std(0)
    fstd = np.where(fstd > 1e-12, fstd, 1.0)

    rv = pd.Series(np.diff(np.log(np.clip(closes, 1e-12, None)), prepend=np.log(max(closes[0], 1e-12)))).rolling(6, min_periods=2).std().fillna(method="bfill").fillna(0.003).to_numpy()
    labels = triple_barrier_labels(prices=closes, volatility=rv, barrier_multiplier=1.5, max_bars=30, embargo_bars=30)
    labels_arr = labels.fillna(0).to_numpy(dtype=float)
    conf_ticks = int(optimal_confirmation_ticks(labels_arr))

    log_ret = X[:, 0]
    g = fit_msgarch_mle(log_ret[:cutoff])

    X_norm = (X_train - fmean) / (fstd + 1e-8)
    km = _kmeans_numpy(X_norm, n_clusters=3, random_state=42, n_init=20, max_iter=500)
    sjm_centroids = np.asarray(km.cluster_centers_, dtype=float)
    within_var = np.zeros(3, dtype=float)
    for k in range(3):
        mask = km.labels_ == k
        if np.sum(mask) > 1:
            within_var += X_norm[mask].var(axis=0)
    within_var = np.where((within_var / 3.0) > 1e-12, within_var / 3.0, 1.0)
    sjm_feature_weights = 1.0 / (within_var + 1e-8)
    sjm_feature_weights /= (np.linalg.norm(sjm_feature_weights) + 1e-12)

    rng = np.random.default_rng(42)
    nhhmm_beta = rng.normal(0.0, 0.01, size=(3, 3, 3))
    nhhmm_beta[:, 0, :] = 0.0
    nhhmm_mu = np.array([log_ret[:cutoff].mean(), log_ret[:cutoff].mean() * 0.5, 0.0])
    nhhmm_sigma = np.array([log_ret[:cutoff].std() + 1e-6, log_ret[:cutoff].std() * 1.2 + 1e-6, log_ret[:cutoff].std() * 0.8 + 1e-6])

    np.savez(out_path, nhhmm_beta=nhhmm_beta.astype(np.float64), feature_mean=fmean.astype(np.float64), feature_std=fstd.astype(np.float64), sjm_centroids=sjm_centroids.astype(np.float64), sjm_feature_weights=sjm_feature_weights.astype(np.float64), nhhmm_mu=nhhmm_mu.astype(np.float64), nhhmm_sigma=nhhmm_sigma.astype(np.float64), garch_omega=np.asarray(g["omega"], dtype=np.float64), garch_alpha=np.asarray(g["alpha"], dtype=np.float64), garch_beta=np.asarray(g["beta_garch"], dtype=np.float64), garch_P=np.asarray(g["P"], dtype=np.float64), confirmation_ticks=np.asarray([conf_ticks], dtype=np.int64))

    saved = np.load(out_path)
    missing = sorted(REQUIRED_KEYS - set(saved.files))
    if missing:
        raise RuntimeError(f"missing keys in {out_path}: {missing}")

    meta = {
        "timeframe": "5m",
        "source_files": ["data/ohlcv_1m.csv", "data/bookDepth.csv"],
        "train_range": {"start": int(bars_5m_slice[0][0]), "end": int(bars_5m_slice[cutoff - 1][0])},
        "val_range": {"start": int(bars_5m_slice[cutoff][0]), "end": int(bars_5m_slice[-1][0])},
        "label_method": "triple_barrier_labels(prices_5m, vol_5m)",
        "feature_source": "real_l2_aligned_to_5m",
        "calibration_status": "calibrated",
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return {"output_path": out_path, "meta_path": meta_path, "bars_5m": len(bars_5m_slice), "confirmation_ticks": conf_ticks}


def main() -> None:
    bars_1m = _load_1m_csv("data/ohlcv_1m.csv")
    out = calibrate_5m_artifacts(bars_1m=bars_1m)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()

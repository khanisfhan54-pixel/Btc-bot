#!/usr/bin/env python3
"""Research-only 5m regime calibration artifact builder (real 5m path only)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, dataclass as _dc
from typing import Any, Sequence

import numpy as np
import pandas as pd

from bar_aggregator import resample_bars
from calibrate_confirmation_ticks import optimal_confirmation_ticks
from calibrate_garch import fit_msgarch_mle
from calibrate_regime import _kmeans_numpy
from data_tools.l2_to_backtest import align_book_to_bars
from triple_barrier_labeler import triple_barrier_labels

OUTPUT_PATH = "weights/advanced_regime_weights_5m.npz"
META_PATH = "weights/advanced_regime_weights_5m.meta.json"

_L1_BOOK_PATH = "data/bookTicker_dec2023_30s.csv"
_L2_BOOK_PATH = "data/bookDepth.csv"

_MIN_5M_BARS = 500
_MIN_TRAIN_BARS = 300

@_dc
class _L1Snap:
    timestamp: int
    bid_price: float
    ask_price: float
    bid_qty: float
    ask_qty: float
    spread_bps: float
    imbalance: float
    ofi_z: float

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


def _build_5m_features(bars_5m: Sequence[Sequence[Any]], bars_1m: Sequence[Sequence[Any]]) -> tuple[np.ndarray, str, bool]:
    closes = np.asarray([float(r[4]) for r in bars_5m], dtype=float)
    opens = np.asarray([float(r[1]) for r in bars_5m], dtype=float)
    vols = np.asarray([float(r[5]) for r in bars_5m], dtype=float)
    log_ret = np.diff(np.log(np.clip(closes, 1e-12, None)), prepend=np.log(max(closes[0], 1e-12)))
    vol_z = (vols - vols.mean()) / (vols.std() + 1e-12)

    used_real_l1 = False
    feature_source = "ohlcv_synthetic"
    ofi_z = None

    if os.path.exists(_L1_BOOK_PATH):
        try:
            l1 = pd.read_csv(_L1_BOOK_PATH)
            required = {"timestamp", "bidPrice", "askPrice", "bidQty", "askQty"}
            if required.issubset(set(l1.columns)):
                bid_qty = l1["bidQty"].astype(float).to_numpy()
                ask_qty = l1["askQty"].astype(float).to_numpy()
                imbalance = (bid_qty - ask_qty) / (bid_qty + ask_qty + 1e-12)
                imb_s = pd.Series(imbalance)
                roll_mean = imb_s.rolling(60, min_periods=5).mean()
                roll_std = imb_s.rolling(60, min_periods=5).std()
                ofi_vals = ((imb_s - roll_mean) / (roll_std + 1e-12)).fillna(0.0).to_numpy(dtype=float)
                bid_price = l1["bidPrice"].astype(float).to_numpy()
                ask_price = l1["askPrice"].astype(float).to_numpy()
                ts = l1["timestamp"].astype(np.int64).to_numpy()
                snaps = []
                for i in range(len(l1)):
                    mid = max((bid_price[i] + ask_price[i]) * 0.5, 1e-12)
                    spread_bps = ((ask_price[i] - bid_price[i]) / mid) * 1e4
                    snaps.append(_L1Snap(int(ts[i]), float(bid_price[i]), float(ask_price[i]), float(bid_qty[i]), float(ask_qty[i]), float(spread_bps), float(imbalance[i]), float(ofi_vals[i])))
                snaps_5m = align_book_to_bars(bars_5m, snaps)
                if len(snaps_5m) >= _MIN_5M_BARS and len(snaps_5m) == len(bars_5m) and not any(s is None for s in snaps_5m):
                    ofi_z = np.asarray([_snapshot_ofi(s) for s in snaps_5m], dtype=float)
                    used_real_l1 = True
                    feature_source = "real_l1_aligned"
        except Exception:
            used_real_l1 = False

    if ofi_z is None:
        print("calibrate_regime_5m: real L1 unavailable — using OHLCV synthetic features.")
        signed_ret = np.sign(closes - opens) * np.abs(log_ret)
        s = pd.Series(signed_ret)
        roll_mean = s.rolling(20, min_periods=5).mean()
        roll_std = s.rolling(20, min_periods=5).std()
        ofi_z = ((s - roll_mean) / (roll_std + 1e-12)).fillna(0.0).to_numpy(dtype=float)

    X = np.column_stack([log_ret, ofi_z, vol_z])
    if X.shape[1] != 3 or not np.all(np.isfinite(X)):
        raise RuntimeError("calibrate_regime_5m: non-finite feature matrix — calibration aborted.")
    return X, feature_source, used_real_l1


def calibrate_5m_artifacts(*, bars_1m: list[list], out_path: str = OUTPUT_PATH, meta_path: str = META_PATH, cal_slice: CalibrationSlice | None = None) -> dict:
    os.makedirs("weights", exist_ok=True)
    bars_5m = resample_bars(bars_1m, minutes=5)
    if len(bars_5m) < _MIN_5M_BARS:
        raise RuntimeError(
            f"calibrate_regime_5m: insufficient 5m bars: {len(bars_5m)} < {_MIN_5M_BARS}. "
            "Pass more 1m data covering at least Dec 2023 (8,910 bars)."
        )

    sl = cal_slice or CalibrationSlice(start_idx=0, end_idx=len(bars_5m))
    if sl.start_idx < 0 or sl.end_idx > len(bars_5m) or sl.start_idx >= sl.end_idx:
        raise RuntimeError("invalid calibration slice")
    bars_5m_slice = bars_5m[sl.start_idx:sl.end_idx]
    if len(bars_5m_slice) < _MIN_TRAIN_BARS:
        raise RuntimeError(
            f"calibrate_regime_5m: calibration slice too small: "
            f"{len(bars_5m_slice)} < {_MIN_TRAIN_BARS} bars. "
            "Widen CalibrationSlice or provide more data."
        )

    X, feature_source, used_real_l1 = _build_5m_features(bars_5m_slice, bars_1m)
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

    _arrays_to_save = {
        "nhhmm_beta": nhhmm_beta, "nhhmm_mu": nhhmm_mu, "nhhmm_sigma": nhhmm_sigma,
        "sjm_centroids": sjm_centroids, "sjm_feature_weights": sjm_feature_weights,
        "feature_mean": fmean, "feature_std": fstd,
        "garch_omega": np.asarray(g["omega"]), "garch_alpha": np.asarray(g["alpha"]),
        "garch_beta": np.asarray(g["beta_garch"]), "garch_P": np.asarray(g["P"]),
        "confirmation_ticks": np.asarray([conf_ticks], dtype=np.int64),
    }
    for _k, _v in _arrays_to_save.items():
        _arr = np.asarray(_v, dtype=float) if _k != "confirmation_ticks" else np.asarray(_v)
        if not np.all(np.isfinite(_arr.astype(float))):
            raise RuntimeError(
                f"calibrate_regime_5m: non-finite values in '{_k}' — "
                "calibration aborted. Check input data quality."
            )

    np.savez(out_path, nhhmm_beta=nhhmm_beta.astype(np.float64), feature_mean=fmean.astype(np.float64), feature_std=fstd.astype(np.float64), sjm_centroids=sjm_centroids.astype(np.float64), sjm_feature_weights=sjm_feature_weights.astype(np.float64), nhhmm_mu=nhhmm_mu.astype(np.float64), nhhmm_sigma=nhhmm_sigma.astype(np.float64), garch_omega=np.asarray(g["omega"], dtype=np.float64), garch_alpha=np.asarray(g["alpha"], dtype=np.float64), garch_beta=np.asarray(g["beta_garch"], dtype=np.float64), garch_P=np.asarray(g["P"], dtype=np.float64), confirmation_ticks=np.asarray([conf_ticks], dtype=np.int64))

    saved = np.load(out_path)
    missing = sorted(REQUIRED_KEYS - set(saved.files))
    if missing:
        raise RuntimeError(f"missing keys in {out_path}: {missing}")

    meta = {
        "timeframe": "5m",
        "feature_source": feature_source,
        "source_files": {"ohlcv_1m": "data/ohlcv_1m.csv", "l1_book": _L1_BOOK_PATH if used_real_l1 else None},
        "n_bars_total_5m": len(bars_5m),
        "n_bars_used": len(bars_5m_slice),
        "train_range": {"start_ts": int(bars_5m_slice[0][0]), "end_ts": int(bars_5m_slice[cutoff - 1][0]), "n_bars": cutoff},
        "val_range": {"start_ts": int(bars_5m_slice[cutoff][0]), "end_ts": int(bars_5m_slice[-1][0]), "n_bars": len(bars_5m_slice) - cutoff},
        "label_method": "triple_barrier_labels",
        "calibration_status": "calibrated",
        "min_bars_required": _MIN_5M_BARS,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return {"output_path": out_path, "meta_path": meta_path, "bars_5m": len(bars_5m_slice), "n_bars_used": len(bars_5m_slice), "confirmation_ticks": conf_ticks}


def main() -> None:
    print("calibrate_regime_5m: loading full Dec 2023 1m OHLCV …")
    bars_1m = _load_1m_csv("data/ohlcv_1m.csv")
    print(f"  loaded {len(bars_1m)} 1m bars")
    out = calibrate_5m_artifacts(bars_1m=bars_1m)
    print(json.dumps(out, indent=2))
    print(f"n_bars_used = {out['n_bars_used']}  (must be >= {_MIN_5M_BARS})")


if __name__ == "__main__":
    main()

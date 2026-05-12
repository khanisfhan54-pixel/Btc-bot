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


def _build_5m_features(
    bars_5m: Sequence[Sequence[Any]],
    bars_1m: Sequence[Sequence[Any]],
) -> tuple[np.ndarray, str, bool]:
    """
    Build 5m feature matrix from real L2 depth data only.
    Fail closed on any missing, malformed, empty, misaligned,
    or non-finite condition. No fallback to L1 or synthetic data.
    """
    closes = np.asarray([float(r[4]) for r in bars_5m], dtype=float)
    vols   = np.asarray([float(r[5]) for r in bars_5m], dtype=float)
    log_ret = np.diff(
        np.log(np.clip(closes, 1e-12, None)),
        prepend=np.log(max(closes[0], 1e-12)),
    )
    vol_z = (vols - vols.mean()) / (vols.std() + 1e-12)

    # ── L2 depth is the ONLY permitted source. No fallback. ──────────────
    if not os.path.exists(_L2_BOOK_PATH):
        raise RuntimeError(
            f"BLOCKER: real L2 depth file missing at '{_L2_BOOK_PATH}'. "
            "Provide data/bookDepth.csv before running 5m calibration."
        )
    try:
        l2 = pd.read_csv(_L2_BOOK_PATH)
    except Exception as exc:
        raise RuntimeError(
            f"BLOCKER: L2 depth file unreadable — {exc}."
        ) from exc

    if len(l2) == 0:
        raise RuntimeError(
            f"BLOCKER: L2 depth file is empty. File: '{_L2_BOOK_PATH}'."
        )
    if "timestamp" not in l2.columns:
        raise RuntimeError(
            f"BLOCKER: L2 depth file missing 'timestamp' column. "
            f"Found: {list(l2.columns)}."
        )

    # Detect level columns by prefix (case-insensitive)
    bp_cols = sorted(c for c in l2.columns if c.lower().startswith("bidprice"))
    ap_cols = sorted(c for c in l2.columns if c.lower().startswith("askprice"))
    bq_cols = sorted(c for c in l2.columns if c.lower().startswith("bidqty"))
    aq_cols = sorted(c for c in l2.columns if c.lower().startswith("askqty"))

    if len(bp_cols) < 2 or len(ap_cols) < 2:
        raise RuntimeError(
            f"BLOCKER: L2 depth file has fewer than 2 price levels per side. "
            f"bid_price cols={bp_cols}, ask_price cols={ap_cols}. "
            f"File: '{_L2_BOOK_PATH}'."
        )
    if len(bq_cols) < 2 or len(aq_cols) < 2:
        raise RuntimeError(
            f"BLOCKER: L2 depth file has fewer than 2 quantity levels per side. "
            f"bid_qty cols={bq_cols}, ask_qty cols={aq_cols}. "
            f"File: '{_L2_BOOK_PATH}'."
        )

    try:
        bp = l2[bp_cols[:2]].astype(float).to_numpy()
        ap = l2[ap_cols[:2]].astype(float).to_numpy()
        bq = l2[bq_cols[:2]].astype(float).to_numpy()
        aq = l2[aq_cols[:2]].astype(float).to_numpy()
        ts_raw = l2["timestamp"].astype(np.int64).to_numpy()
    except Exception as exc:
        raise RuntimeError(
            f"BLOCKER: L2 depth column parsing failed — {exc}."
        ) from exc

    total_bid = bq[:, 0] + bq[:, 1]
    total_ask = aq[:, 0] + aq[:, 1]

    if not np.all(np.isfinite(total_bid)) or not np.all(np.isfinite(total_ask)):
        raise RuntimeError(
            "BLOCKER: L2 depth qty columns contain non-finite values."
        )

    raw_ofi = total_bid - total_ask
    rs = pd.Series(raw_ofi)
    roll_mean = rs.rolling(60, min_periods=5).mean()
    roll_std  = rs.rolling(60, min_periods=5).std()
    ofi_vals  = ((rs - roll_mean) / (roll_std + 1e-12)).fillna(0.0).to_numpy(dtype=float)

    mid_arr    = (bp[:, 0] + ap[:, 0]) * 0.5
    spread_arr = ((ap[:, 0] - bp[:, 0]) / np.maximum(mid_arr, 1e-12)) * 1e4
    imb_arr    = raw_ofi / np.maximum(total_bid + total_ask, 1e-12)

    snaps = [
        _L1Snap(
            int(ts_raw[i]),
            float(bp[i, 0]),
            float(ap[i, 0]),
            float(total_bid[i]),
            float(total_ask[i]),
            float(spread_arr[i]),
            float(imb_arr[i]),
            float(ofi_vals[i]),
        )
        for i in range(len(l2))
    ]

    # Align OUTSIDE the loop
    try:
        snaps_5m = align_book_to_bars(bars_5m, snaps)
    except Exception as exc:
        raise RuntimeError(
            f"BLOCKER: L2 book alignment failed — {exc}."
        ) from exc

    if len(snaps_5m) < _MIN_5M_BARS:
        raise RuntimeError(
            f"BLOCKER: only {len(snaps_5m)} L2 snaps aligned to 5m bars; "
            f"need >= {_MIN_5M_BARS}. Verify bookDepth.csv covers Dec 2023."
        )
    if len(snaps_5m) != len(bars_5m):
        raise RuntimeError(
            f"BLOCKER: aligned snap count {len(snaps_5m)} != bar count "
            f"{len(bars_5m)}."
        )
    if any(s is None for s in snaps_5m):
        raise RuntimeError(
            "BLOCKER: alignment produced None entries — some bars have "
            "no L2 snapshot. Verify bookDepth.csv covers the full range."
        )

    ofi_z = np.asarray([_snapshot_ofi(s) for s in snaps_5m], dtype=float)

    if not np.all(np.isfinite(ofi_z)):
        n_bad = int(np.sum(~np.isfinite(ofi_z)))
        raise RuntimeError(
            f"BLOCKER: {n_bad} non-finite OFI values after L2 alignment."
        )

    X = np.column_stack([log_ret, ofi_z, vol_z])
    if X.shape[1] != 3 or not np.all(np.isfinite(X)):
        raise RuntimeError(
            "BLOCKER: non-finite feature matrix after L2 construction."
        )

    return X, "real_l2_depth", True


def calibrate_5m_artifacts(*, bars_1m: list[list], out_path: str = OUTPUT_PATH, meta_path: str = META_PATH, cal_slice: CalibrationSlice | None = None) -> dict:
    os.makedirs("weights", exist_ok=True)
    bars_5m = resample_bars(bars_1m, minutes=5)
    if len(bars_5m) < _MIN_5M_BARS:
        raise RuntimeError(
            f"BLOCKER: insufficient 5m bars: {len(bars_5m)} < {_MIN_5M_BARS}. "
            "Pass more 1m data covering at least Dec 2023 (8,910 bars)."
        )

    sl = cal_slice or CalibrationSlice(start_idx=0, end_idx=len(bars_5m))
    if sl.start_idx < 0 or sl.end_idx > len(bars_5m) or sl.start_idx >= sl.end_idx:
        raise RuntimeError("BLOCKER: invalid calibration slice")
    bars_5m_slice = bars_5m[sl.start_idx:sl.end_idx]
    if len(bars_5m_slice) < _MIN_TRAIN_BARS:
        raise RuntimeError(
            f"BLOCKER: calibration slice too small: "
            f"{len(bars_5m_slice)} < {_MIN_TRAIN_BARS} bars. "
            "Widen CalibrationSlice or provide more data."
        )

    X, feature_source, used_real_book = _build_5m_features(bars_5m_slice, bars_1m)
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
                f"BLOCKER: non-finite values in '{_k}' — "
                "calibration aborted. Check input data quality."
            )

    n_bars_ofi_nonzero = int(np.sum(np.abs(X[:, 1]) > 1e-9))
    if n_bars_ofi_nonzero == 0:
        raise RuntimeError(
            "BLOCKER: all OFI values are zero after L2 calibration. "
            "The L2 book data may be degenerate or constant."
        )

    np.savez(out_path, nhhmm_beta=nhhmm_beta.astype(np.float64), feature_mean=fmean.astype(np.float64), feature_std=fstd.astype(np.float64), sjm_centroids=sjm_centroids.astype(np.float64), sjm_feature_weights=sjm_feature_weights.astype(np.float64), nhhmm_mu=nhhmm_mu.astype(np.float64), nhhmm_sigma=nhhmm_sigma.astype(np.float64), garch_omega=np.asarray(g["omega"], dtype=np.float64), garch_alpha=np.asarray(g["alpha"], dtype=np.float64), garch_beta=np.asarray(g["beta_garch"], dtype=np.float64), garch_P=np.asarray(g["P"], dtype=np.float64), confirmation_ticks=np.asarray([conf_ticks], dtype=np.int64))

    saved = np.load(out_path)
    missing = sorted(REQUIRED_KEYS - set(saved.files))
    if missing:
        raise RuntimeError(f"BLOCKER: missing keys in {out_path}: {missing}")
    for key in REQUIRED_KEYS:
        arr = np.asarray(saved[key]).astype(float)
        if not np.all(np.isfinite(arr)):
            raise RuntimeError(f"BLOCKER: non-finite saved key: {key}")

    meta = {
        "timeframe": "5m",
        "feature_source": feature_source,
        "real_book_only": True,
        "ofi_source": feature_source,
        "blocker_on_missing_data": True,
        "source_files": {
            "ohlcv_1m": "data/ohlcv_1m.csv",
            "l2_book": _L2_BOOK_PATH,
        },
        "n_bars_total_5m": len(bars_5m),
        "n_bars_used": len(bars_5m_slice),
        "n_bars_ofi_nonzero": n_bars_ofi_nonzero,
        "train_range": {
            "start_ts": int(bars_5m_slice[0][0]),
            "end_ts":   int(bars_5m_slice[cutoff - 1][0]),
            "n_bars":   cutoff,
        },
        "val_range": {
            "start_ts": int(bars_5m_slice[min(cutoff, len(bars_5m_slice) - 1)][0]),
            "end_ts":   int(bars_5m_slice[-1][0]),
            "n_bars":   len(bars_5m_slice) - cutoff,
        },
        "label_method": "triple_barrier_labels",
        "calibration_status": "calibrated",
        "min_bars_required": _MIN_5M_BARS,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return {"output_path": out_path, "meta_path": meta_path, "bars_5m": len(bars_5m_slice), "n_bars_used": len(bars_5m_slice), "confirmation_ticks": conf_ticks}


def main() -> None:
    for p, label in [(_L2_BOOK_PATH, "L2-depth")]:
        if os.path.exists(p):
            try:
                cols = list(pd.read_csv(p, nrows=0).columns)
                nrows = sum(1 for _ in open(p)) - 1
            except Exception as exc:
                cols, nrows = [f"unreadable: {exc}"], -1
            print(f"[book-source] {label}: exists=True rows≈{nrows} columns={cols}")
        else:
            print(f"[book-source] {label}: exists=False — calibration will BLOCK")
    print("calibrate_regime_5m: loading full Dec 2023 1m OHLCV …")
    bars_1m = _load_1m_csv("data/ohlcv_1m.csv")
    print(f"  loaded {len(bars_1m)} 1m bars")
    out = calibrate_5m_artifacts(bars_1m=bars_1m)
    print(json.dumps(out, indent=2))
    print(f"n_bars_used = {out['n_bars_used']}  (must be >= {_MIN_5M_BARS})")


if __name__ == "__main__":
    main()

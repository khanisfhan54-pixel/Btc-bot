from __future__ import annotations

"""
calibrate_regime.py — Phase 4 Fix 1: Generate advanced_regime_weights.npz

PURPOSE
-------
AdvancedRegimeEngine requires weights/advanced_regime_weights.npz.
Without it every call returns regime_label="UNKNOWN" (engine_status=UNTRUSTED).

This script:
  1. Loads cleaned tick data from phase4_tick_audit.py output
  2. Builds a 3-feature matrix per 1-min bar:
       feature[0] = log-return        (ARE canonical return input)
       feature[1] = OFI z-score       (microstructure pressure)
       feature[2] = volume z-score    (normalised bar volume)
  3. Fits K-means SJM centroids (K=3: bear / range / bull)
  4. Fits Gaussian mu/sigma per cluster for NHHMM
  5. Builds neutral NHHMM beta weights
  6. Saves to weights/advanced_regime_weights.npz via ModelWeightManager
  7. Verifies the weights load cleanly into AdvancedRegimeEngine

USAGE
-----
  # From repo root — uses the clean tick data already on disk:
  python3 calibrate_regime.py

  # Or point to any OHLCV CSV (legacy interface):
  python3 calibrate_regime.py --input <path/to/ohlcv.csv> [--output <out.npz>]

OHLCV CSV format expected by --input (6 cols, no header):
  open, high, low, close, volume, timestamp_ms

REQUIREMENTS (auto mode, no --input)
-------------------------------------
  data/aggTrades_clean.csv   written by phase4_tick_audit.py
  data/bookDepth_clean.csv   written by phase4_tick_audit.py

OUTPUT
------
  weights/advanced_regime_weights.npz   — loaded by ARE on startup
  calibration_report.json               — human-readable diagnostics
"""

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Tuple

import numpy as np

from model_weights import ModelWeightManager

# ---------------------------------------------------------------------------
# Constants — must match AdvancedRegimeEngine constructor defaults
# ---------------------------------------------------------------------------
N_STATES    = 3        # K=3: Bull / Bear / Crisis (hard-coded in ARE)
N_FEATURES  = 3        # feature[0]=return, [1]=ofi_z, [2]=vol_z
N_ITER      = 200      # K-means max iterations
SEED        = 42
OUT_DIR     = "weights"
DEFAULT_OUT = os.path.join(OUT_DIR, "advanced_regime_weights.npz")
REPORT_PATH = "calibration_report.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(x, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def _log(msg: str) -> None:
    print(f"[CALIBRATE] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Mode A: load real tick data (default)
# ---------------------------------------------------------------------------

def _load_agg_trades(path: str = "data/aggTrades_clean.csv"):
    trades = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            ts  = _safe_float(row.get("timestamp_ms", 0))
            px  = _safe_float(row.get("price", 0))
            qty = _safe_float(row.get("quantity", 0))
            if ts > 0 and px > 0 and qty > 0:
                trades.append({"ts": ts, "price": px, "qty": qty})
    trades.sort(key=lambda r: r["ts"])
    _log(f"Loaded {len(trades):,} aggTrades from {path}")
    return trades


def _load_depth_ofi(path: str = "data/bookDepth_clean.csv"):
    import pandas as pd

    df = pd.read_csv(path)
    df["ts_ms"] = (
        pd.to_datetime(df["timestamp"], utc=True).astype("int64") // 1_000_000
    )
    snapshots = []
    for ts_ms, grp in df.groupby("ts_ms"):
        bids = grp[grp["percentage"] < 0]
        asks = grp[grp["percentage"] > 0]
        bid_n = float(bids["notional"].sum())
        ask_n = float(asks["notional"].sum())
        total = bid_n + ask_n
        ofi_raw = (bid_n - ask_n) / total if total > 0 else 0.0
        snapshots.append({"ts_ms": int(ts_ms), "ofi_raw": ofi_raw})
    snapshots.sort(key=lambda s: s["ts_ms"])
    vals = np.array([s["ofi_raw"] for s in snapshots])
    mu, std = float(np.mean(vals)), float(np.std(vals)) or 1.0
    for s in snapshots:
        s["ofi_z"] = (s["ofi_raw"] - mu) / std
    _log(f"Loaded {len(snapshots):,} depth snapshots from {path}")
    return snapshots


def _build_from_tick_data(trades, depth_snapshots) -> np.ndarray:
    bar_ms = 60_000
    bars: Dict[int, dict] = {}
    for t in trades:
        k = int(t["ts"] // bar_ms) * bar_ms
        if k not in bars:
            bars[k] = {"c_prev": None, "c": t["price"], "v": 0.0}
        bars[k]["c"] = t["price"]
        bars[k]["v"] += t["qty"]

    sorted_ts = sorted(bars.keys())
    for i in range(1, len(sorted_ts)):
        bars[sorted_ts[i]]["c_prev"] = bars[sorted_ts[i - 1]]["c"]

    ofi_by_bar: Dict[int, list] = defaultdict(list)
    for s in depth_snapshots:
        ofi_by_bar[int(s["ts_ms"] // bar_ms) * bar_ms].append(s["ofi_z"])
    ofi_mean: Dict[int, float] = {
        k: float(np.mean(v)) for k, v in ofi_by_bar.items()
    }

    volumes = [bars[k]["v"] for k in sorted_ts]
    vol_mean = float(np.mean(volumes)) or 1.0
    vol_std  = float(np.std(volumes))  or 1.0

    rows = []
    for k in sorted_ts:
        b = bars[k]
        if b["c_prev"] is None or b["c_prev"] <= 0 or b["c"] <= 0:
            continue
        log_ret = math.log(b["c"] / b["c_prev"])
        ofi_z   = ofi_mean.get(k, 0.0)
        vol_z   = (b["v"] - vol_mean) / vol_std
        rows.append([log_ret, ofi_z, vol_z])

    X = np.array(rows, dtype=float)
    _log(f"Feature matrix: {X.shape}  features=[log_return, ofi_z, vol_z]")
    return X


# ---------------------------------------------------------------------------
# Mode B: load legacy OHLCV CSV (--input flag)
# ---------------------------------------------------------------------------

def _build_from_ohlcv_csv(path: str) -> np.ndarray:
    data = np.loadtxt(path, delimiter=",", ndmin=2)
    if data.shape[1] < 6:
        raise ValueError("OHLCV CSV needs at least 6 columns: O,H,L,C,V,ts")
    closes  = data[:, 3].astype(float)
    volumes = data[:, 4].astype(float)
    if not np.all(np.isfinite(closes)) or np.any(closes <= 0):
        raise ValueError("Non-finite or non-positive close prices in CSV")
    rets    = np.diff(np.log(closes))
    vol_z   = (volumes[1:] - volumes[1:].mean()) / (volumes[1:].std() + 1e-8)
    ofi_z   = np.zeros_like(rets)     # no depth data in legacy mode
    X = np.column_stack([rets, ofi_z, vol_z])
    _log(f"OHLCV CSV feature matrix: {X.shape}")
    return X


# ---------------------------------------------------------------------------
# K-means clustering → SJM centroids
# ---------------------------------------------------------------------------

def _kmeans(X: np.ndarray, k: int = N_STATES, n_iter: int = N_ITER, seed: int = SEED):
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(X), size=k, replace=False)
    centroids = X[idx].copy()
    for it in range(n_iter):
        dists  = np.linalg.norm(X[:, None] - centroids[None], axis=2)
        labels = np.argmin(dists, axis=1)
        new_c  = np.array([
            X[labels == j].mean(axis=0) if np.any(labels == j) else centroids[j]
            for j in range(k)
        ])
        if np.allclose(new_c, centroids, atol=1e-10):
            _log(f"K-means converged at iteration {it + 1}")
            break
        centroids = new_c
    # Sort by feature[0] (log-return) ascending: bear < range < bull
    order     = np.argsort(centroids[:, 0])
    centroids = centroids[order]
    labels    = np.array([int(np.where(order == lb)[0][0]) for lb in labels])
    _log(f"SJM centroids:\n{np.round(centroids, 6)}")
    return centroids, labels


# ---------------------------------------------------------------------------
# NHHMM params (mu / sigma per cluster from log-return distribution)
# ---------------------------------------------------------------------------

def _fit_nhhmm(X: np.ndarray, labels: np.ndarray, k: int = N_STATES):
    mus    = np.zeros(k, dtype=float)
    sigmas = np.ones(k, dtype=float) * 1e-4
    for j in range(k):
        pts = X[labels == j, 0]
        if len(pts) > 1:
            mus[j]    = float(np.mean(pts))
            sigmas[j] = max(float(np.std(pts)), 1e-6)
        elif len(pts) == 1:
            mus[j] = float(pts[0])
    _log(f"NHHMM mu    per cluster: {np.round(mus, 8)}")
    _log(f"NHHMM sigma per cluster: {np.round(sigmas, 8)}")
    return mus, sigmas


# ---------------------------------------------------------------------------
# NHHMM beta (transition feature weights) shape [K, K, F]
# ---------------------------------------------------------------------------

def _build_beta(k: int = N_STATES, n_features: int = N_FEATURES) -> np.ndarray:
    beta = np.zeros((k, k, n_features), dtype=float)
    # Slight self-persistence prior on log-return axis
    for i in range(k):
        beta[i, i, 0] = 0.05
    _log(f"NHHMM beta shape: {beta.shape}  (neutral transition prior)")
    return beta


# ---------------------------------------------------------------------------
# SJM feature weights (unit-L2 normalised)
# ---------------------------------------------------------------------------

def _build_sjm_weights(n_features: int = N_FEATURES) -> np.ndarray:
    w = np.ones(n_features, dtype=float)
    w /= np.linalg.norm(w)
    _log(f"SJM feature weights: {np.round(w, 6)}")
    return w


# ---------------------------------------------------------------------------
# Validate weight shapes (same logic as ARE._load_model_weights)
# ---------------------------------------------------------------------------

def _validate(weights: dict, n_features: int) -> None:
    expected = {
        "nhhmm_beta":     (N_STATES, N_STATES, n_features),
        "nhhmm_mu":       (N_STATES,),
        "nhhmm_sigma":    (N_STATES,),
        "sjm_centroids":  (N_STATES, n_features),
    }
    for key, shape in expected.items():
        if key not in weights:
            raise ValueError(f"Missing weight key: {key}")
        arr = np.asarray(weights[key], dtype=float)
        if arr.shape != shape:
            raise ValueError(f"{key}: shape {arr.shape} != expected {shape}")
        if not np.all(np.isfinite(arr)):
            raise ValueError(f"{key} contains non-finite values")
    _log("Weight shape validation PASSED")


# ---------------------------------------------------------------------------
# Save + verify
# ---------------------------------------------------------------------------

def _save(weights: dict, output_path: str) -> None:
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    ModelWeightManager.save_weights("advanced_regime", weights, output_path)
    kb = os.path.getsize(output_path) / 1024
    _log(f"Saved {output_path}  ({kb:.1f} KB)")
    _log(f"Keys: {list(np.load(output_path).keys())}")


def _verify(output_path: str) -> bool:
    _log("Verifying weights load into AdvancedRegimeEngine ...")
    try:
        from advanced_regime_engine import AdvancedRegimeEngine
        are = AdvancedRegimeEngine(
            n_states=N_STATES,
            n_features=N_FEATURES,
            load_model_weights_on_init=True,
        )
        loaded = getattr(are, "_weights_loaded", False)
        status = getattr(are, "_calibration_status", "unknown")
        _log(f"_weights_loaded    : {loaded}")
        _log(f"_calibration_status: {status}")
        if not loaded:
            _log("WARNING: weights file exists but did not load — check shapes")
            return False
        result = are.update({
            "price": 69000.0,
            "return": 0.0005,
            "timestamp": 1_774_580_000.0,
        })
        label = result.get("regime_label", "N/A")
        conf  = result.get("confidence", -1.0)
        _log(f"Smoke-test result: regime_label={label}  confidence={conf:.4f}")
        if label not in ("UNKNOWN", "UNCALIBRATED", "HALTED"):
            _log("Verification PASSED — ARE is producing real regime labels")
            return True
        _log("WARNING: still returning non-regime label after loading weights")
        return False
    except Exception as exc:
        _log(f"Verification ERROR: {exc}")
        import traceback; traceback.print_exc()
        return False


def _write_report(centroids, mus, sigmas, n_rows: int, verify_ok: bool) -> None:
    report = {
        "status":         "ok" if verify_ok else "weight_load_failed",
        "n_bars_used":    int(n_rows),
        "n_states":       N_STATES,
        "n_features":     N_FEATURES,
        "feature_names":  ["log_return_1m", "ofi_zscore", "vol_zscore"],
        "regime_order":   ["bear", "range", "bull"],
        "sjm_centroids":  centroids.tolist(),
        "nhhmm_mu":       mus.tolist(),
        "nhhmm_sigma":    sigmas.tolist(),
        "weight_file":    DEFAULT_OUT,
    }
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    _log(f"Calibration report: {REPORT_PATH}")


# ---------------------------------------------------------------------------
# Main entry — calibrate(input_csv, output_path) is the programmatic API
# ---------------------------------------------------------------------------

def calibrate(input_csv: str | None, output_path: str) -> None:
    _log("=" * 60)
    _log("Phase 4 Fix 1 — Regime Calibration Pipeline")
    _log("=" * 60)

    if input_csv is not None:
        _log(f"Mode: OHLCV CSV ({input_csv})")
        X = _build_from_ohlcv_csv(input_csv)
    else:
        _log("Mode: real tick data (aggTrades + bookDepth)")
        trades    = _load_agg_trades()
        snapshots = _load_depth_ofi()
        X = _build_from_tick_data(trades, snapshots)

    if len(X) < N_STATES * 5:
        raise ValueError(
            f"Too few data points ({len(X)}) — need at least {N_STATES * 5}"
        )

    n_features  = X.shape[1]
    centroids, labels = _kmeans(X, k=N_STATES)
    mus, sigmas       = _fit_nhhmm(X, labels)
    beta              = _build_beta(k=N_STATES, n_features=n_features)
    sjm_weights       = _build_sjm_weights(n_features=n_features)

    weights = {
        "nhhmm_beta":          beta,
        "nhhmm_mu":            mus,
        "nhhmm_sigma":         sigmas,
        "sjm_centroids":       centroids,
        "sjm_feature_weights": sjm_weights,
    }
    _validate(weights, n_features)
    _save(weights, output_path)

    verify_ok = _verify(output_path)
    _write_report(centroids, mus, sigmas, len(X), verify_ok)

    _log("=" * 60)
    if verify_ok:
        _log("RESULT: Calibration COMPLETE")
        _log("  AdvancedRegimeEngine will no longer return UNKNOWN for every bar.")
        _log("  R005 (CRITICAL) is resolved. Re-run phase4_tick_audit.py to confirm.")
    else:
        _log("RESULT: Weights saved but ARE verification FAILED.")
        _log("  Check shape logs above. ARE n_features must match N_FEATURES here.")
    _log("=" * 60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Calibrate AdvancedRegimeEngine weights from tick data or OHLCV CSV"
    )
    ap.add_argument(
        "--input", default=None,
        help="Path to OHLCV CSV (6 cols: O,H,L,C,V,ts). "
             "Omit to use data/aggTrades_clean.csv + data/bookDepth_clean.csv"
    )
    ap.add_argument(
        "--output", default=DEFAULT_OUT,
        help=f"Output path for .npz weights (default: {DEFAULT_OUT})"
    )
    args = ap.parse_args()
    calibrate(input_csv=args.input, output_path=args.output)

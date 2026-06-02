from __future__ import annotations

import math
import os
from typing import Any, Dict, List

import numpy as np

from ..model.calibrator import brier_score, expected_calibration_error
from ..model.engine import StopHuntProbabilityEngine
from .feature_codec import record_to_fv
from .io import atomic_write_json, ensure_dir
from .target import DEFAULT_TARGET, TargetDefinition
from .trainer import align_samples


def _metrics(predictions: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not predictions:
        raise ValueError("walk-forward produced no predictions")
    probs = [float(p["probability"]) for p in predictions]
    labels = [int(p["label"]) for p in predictions]
    rets = [(1.0 if y == 1 else -1.0) * (p - 0.5) for p, y in zip(probs, labels) if p >= 0.55]
    n_trades = len(rets)
    wins = sum(1 for r in rets if r > 0)
    win_rate = wins / n_trades if n_trades else 0.0
    if n_trades > 1 and float(np.std(rets)) > 1e-12:
        sharpe = float(np.mean(rets) / np.std(rets) * math.sqrt(n_trades))
    else:
        sharpe = 0.0
    equity = 0.0; peak = 0.0; max_dd = 0.0
    for r in rets:
        equity += r; peak = max(peak, equity); max_dd = min(max_dd, equity - peak)
    return {"sharpe_ratio": sharpe, "max_drawdown": float(max_dd), "win_rate": float(win_rate), "number_of_trades": int(n_trades), "brier_score": brier_score(probs, labels), "expected_calibration_error": expected_calibration_error(probs, labels)}


def run_walk_forward(dataset: Dict[str, Any], labels_payload: Dict[str, Any], out_dir: str, *, target: TargetDefinition = DEFAULT_TARGET, min_train: int = 12, test_size: int = 4, model_version_prefix: str = "shpe.wf") -> Dict[str, Any]:
    samples, labels, regimes = align_samples(dataset, labels_payload)
    preds: List[Dict[str, Any]] = []
    folds: List[Dict[str, Any]] = []
    fold = 0
    start = min_train
    while start < len(samples):
        end = min(start + test_size, len(samples))
        if len(set(labels[:start])) < 2:
            start += test_size
            continue
        engine = StopHuntProbabilityEngine.train([record_to_fv(s) for s in samples[:start]], labels[:start], regimes[:start], calibrate_method="platt", calibration_holdout_frac=0.2, min_samples_per_regime=30, run_importance_audit=False, model_version=f"{model_version_prefix}.{fold}")
        fold_preds = []
        for j in range(start, end):
            pr = engine.predict(record_to_fv(samples[j]))
            row = {"fold": fold, "row_index": samples[j]["row_index"], "timestamp_ms": samples[j]["timestamp_ms"], "probability": pr.p_sweep, "raw_probability": pr.raw_p_sweep, "label": labels[j], "regime_used": pr.regime_used, "degraded": pr.degraded}
            preds.append(row); fold_preds.append(row)
        folds.append({"fold": fold, "train_rows": start, "test_rows": end - start, "train_range": [samples[0]["timestamp_utc"], samples[start - 1]["timestamp_utc"]], "test_range": [samples[start]["timestamp_utc"], samples[end - 1]["timestamp_utc"]]})
        fold += 1; start = end
    if not preds:
        raise RuntimeError("walk-forward validation failed: no valid folds")
    metrics = _metrics(preds)
    result = {"target_definition": target.to_dict(), "walk_forward_config": {"mode": "expanding_window", "min_train": min_train, "test_size": test_size}, "folds": folds, "predictions": preds, "metrics": metrics, "tested_date_range": [preds[0]["timestamp_ms"], preds[-1]["timestamp_ms"]]}
    base = ensure_dir(out_dir)
    path = os.path.join(base, "walk_forward.json")
    atomic_write_json(result, path)
    result["path"] = path
    return result

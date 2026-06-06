from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, List, Sequence

import numpy as np

from ..model.calibrator import brier_score, expected_calibration_error
from ..model.engine import StopHuntProbabilityEngine
from .feature_codec import record_to_fv
from .io import atomic_write_json, ensure_dir
from .target import DEFAULT_TARGET, TargetDefinition
from ..validation.purged_walk_forward import purged_walk_forward_splits
from ..validation.walk_forward import walk_forward_splits, walk_forward_splits_rolling
from .trainer import align_samples

log = logging.getLogger(__name__)


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


def _label_horizon_end_index(sample: Dict[str, Any], horizon_bars: int) -> int:
    return int(sample["row_index"]) + int(horizon_bars)


def _purged_train_end(samples: List[Dict[str, Any]], start: int, horizon_bars: int) -> int:
    if start <= 0:
        return 0
    first_test_index = int(samples[start]["row_index"])
    train_end = max(0, start - int(horizon_bars))
    while train_end > 0 and _label_horizon_end_index(samples[train_end - 1], horizon_bars) >= first_test_index:
        train_end -= 1
    return train_end


def _assert_no_train_label_horizon_overlap(samples: List[Dict[str, Any]], train_end: int, start: int, end: int, horizon_bars: int) -> None:
    if train_end <= 0 or start >= end:
        return
    first_test_index = int(samples[start]["row_index"])
    first_test_timestamp = int(samples[start]["timestamp_ms"])
    previous_train_timestamp = int(samples[train_end - 1]["timestamp_ms"])
    if previous_train_timestamp >= first_test_timestamp:
        raise RuntimeError("walk-forward validation failed closed: non-chronological fold boundary")
    leaking = [
        int(sample["row_index"])
        for sample in samples[:train_end]
        if _label_horizon_end_index(sample, horizon_bars) >= first_test_index
    ]
    if leaking:
        raise RuntimeError(
            "walk-forward validation failed closed: train label horizon overlaps test fold "
            f"(first_test_index={first_test_index}, leaking_train_row_indices={leaking})"
        )


def _assert_train_indices_do_not_leak(samples: List[Dict[str, Any]], train_indices: Sequence[int], test_indices: Sequence[int], horizon_bars: int) -> None:
    if not train_indices or not test_indices:
        return
    first_test_index = int(samples[test_indices[0]]["row_index"])
    first_test_timestamp = int(samples[test_indices[0]]["timestamp_ms"])
    if max(int(samples[i]["timestamp_ms"]) for i in train_indices) >= first_test_timestamp:
        raise RuntimeError("walk-forward validation failed closed: non-chronological fold boundary")
    leaking = [
        int(samples[i]["row_index"])
        for i in train_indices
        if _label_horizon_end_index(samples[i], horizon_bars) >= first_test_index
    ]
    if leaking:
        raise RuntimeError(
            "walk-forward validation failed closed: train label horizon overlaps test fold "
            f"(first_test_index={first_test_index}, leaking_train_row_indices={leaking})"
        )


def run_walk_forward(dataset: Dict[str, Any], labels_payload: Dict[str, Any], out_dir: str, *, target: TargetDefinition = DEFAULT_TARGET, min_train: int = 12, test_size: int = 4, model_version_prefix: str = "shpe.wf", validation_mode: str = "purged", embargo_size: int = 0) -> Dict[str, Any]:
    samples, labels, regimes = align_samples(dataset, labels_payload)
    horizon_bars = int(target.horizon_bars)
    if validation_mode not in {"expanding", "rolling", "purged"}:
        raise ValueError(f"validation_mode={validation_mode!r} must be one of: expanding, rolling, purged")
    if validation_mode == "expanding":
        split_iter = walk_forward_splits(len(samples), min_train, test_size, test_size)
    elif validation_mode == "rolling":
        split_iter = walk_forward_splits_rolling(len(samples), min_train, test_size, test_size)
    else:
        split_iter = purged_walk_forward_splits(len(samples), min_train, test_size, test_size, horizon_bars, embargo_size)
    preds: List[Dict[str, Any]] = []
    folds: List[Dict[str, Any]] = []
    fold = 0
    for train_range, test_range in split_iter:
        train_indices = list(train_range)
        test_indices = list(test_range)
        if validation_mode == "purged":
            log.info(
                "purged_walk_forward "
                "train=%d "
                "test=%d "
                "purge=%d "
                "embargo=%d",
                len(train_indices),
                len(test_indices),
                horizon_bars,
                embargo_size,
            )
        if validation_mode == "expanding":
            train_end = _purged_train_end(samples, test_indices[0], horizon_bars)
            train_indices = list(range(train_end))
        _assert_train_indices_do_not_leak(samples, train_indices, test_indices, horizon_bars)
        if not train_indices or len(set(labels[i] for i in train_indices)) < 2:
            continue
        engine = StopHuntProbabilityEngine.train([record_to_fv(samples[i]) for i in train_indices], [labels[i] for i in train_indices], [regimes[i] for i in train_indices], calibrate_method="platt", calibration_holdout_frac=0.2, min_samples_per_regime=30, run_importance_audit=False, model_version=f"{model_version_prefix}.{fold}")
        for j in test_indices:
            pr = engine.predict(record_to_fv(samples[j]))
            row = {"fold": fold, "row_index": samples[j]["row_index"], "timestamp_ms": samples[j]["timestamp_ms"], "probability": pr.p_sweep, "raw_probability": pr.raw_p_sweep, "label": labels[j], "regime_used": pr.regime_used, "degraded": pr.degraded}
            preds.append(row)
        purged_rows = test_indices[0] - max(train_indices) - 1
        folds.append({"fold": fold, "train_rows": len(train_indices), "test_rows": len(test_indices), "purged_rows": purged_rows, "purge_bars": horizon_bars, "embargo_bars": embargo_size, "validation_mode": validation_mode, "train_range": [samples[train_indices[0]]["timestamp_utc"], samples[train_indices[-1]]["timestamp_utc"]], "test_range": [samples[test_indices[0]]["timestamp_utc"], samples[test_indices[-1]]["timestamp_utc"]], "first_test_row_index": samples[test_indices[0]]["row_index"], "last_train_label_horizon_end_index": _label_horizon_end_index(samples[train_indices[-1]], horizon_bars)})
        fold += 1
    if not preds:
        raise RuntimeError("walk-forward validation failed: no valid folds")
    metrics = _metrics(preds)
    result = {"target_definition": target.to_dict(), "walk_forward_config": {"mode": validation_mode, "min_train": min_train, "test_size": test_size, "purge_bars": horizon_bars, "embargo_bars": embargo_size}, "folds": folds, "predictions": preds, "metrics": metrics, "tested_date_range": [preds[0]["timestamp_ms"], preds[-1]["timestamp_ms"]]}
    base = ensure_dir(out_dir)
    path = os.path.join(base, "walk_forward.json")
    atomic_write_json(result, path)
    result["path"] = path
    return result

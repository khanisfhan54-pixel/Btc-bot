from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

from ..model.engine import SHPE_FEATURE_NAMES, StopHuntProbabilityEngine
from .feature_codec import record_to_fv
from .io import atomic_write_json, ensure_dir
from .target import DEFAULT_TARGET, TargetDefinition

MODEL_SCHEMA_VERSION = "shpe-model-artifact.v1.0.0"


def align_samples(dataset: Dict[str, Any], labels_payload: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[int], List[str]]:
    labels_by_idx = {int(x["row_index"]): x for x in labels_payload.get("labels", [])}
    samples: List[Dict[str, Any]] = []
    labels: List[int] = []
    regimes: List[str] = []
    for sample in dataset.get("samples", []):
        idx = int(sample["row_index"])
        lab = labels_by_idx.get(idx)
        if lab is None or lab.get("label") is None:
            continue
        if int(lab["timestamp_ms"]) != int(sample["timestamp_ms"]):
            raise ValueError("label/sample timestamp mismatch")
        samples.append(sample)
        labels.append(int(lab["label"]))
        regimes.append(str(sample.get("regime_label") or "unknown"))
    if len(samples) < 4:
        raise ValueError("not enough labelled samples to train SHPE")
    if len(set(labels)) < 2:
        raise ValueError("SHPE training requires both positive and negative labels")
    return samples, labels, regimes


def train_and_save(dataset: Dict[str, Any], labels_payload: Dict[str, Any], out_dir: str, *, model_version: str = "shpe.v1.0.0-offline", target: TargetDefinition = DEFAULT_TARGET) -> Dict[str, Any]:
    if dataset.get("target_definition", {}).get("version") != target.version or labels_payload.get("target_definition", {}).get("version") != target.version:
        raise ValueError("target definition version mismatch")
    samples, labels, regimes = align_samples(dataset, labels_payload)
    fvs = [record_to_fv(s) for s in samples]
    engine = StopHuntProbabilityEngine.train(fvs, labels, regimes, calibrate_method="platt", calibration_holdout_frac=0.2, min_samples_per_regime=30, run_importance_audit=False, model_version=model_version)
    base = ensure_dir(os.path.join(out_dir, model_version))
    model_path = os.path.join(base, "shpe_model.pkl")
    engine.save(model_path)
    loaded = StopHuntProbabilityEngine.load(model_path)
    if tuple(loaded.feature_names) != tuple(SHPE_FEATURE_NAMES) or loaded.model_version != model_version:
        raise RuntimeError("saved SHPE model failed validation")
    cal_path = os.path.join(base, "calibrator.pkl")
    if engine.calibrator is not None:
        import pickle
        tmp = cal_path + ".tmp"
        with open(tmp, "wb") as fh:
            pickle.dump(engine.calibrator, fh, protocol=5)
        os.replace(tmp, cal_path)
    manifest = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "model_version": model_version,
        "model_path": model_path,
        "calibrator_path": cal_path if engine.calibrator is not None else None,
        "feature_schema": list(SHPE_FEATURE_NAMES),
        "target_definition_version": target.version,
        "regime_definitions": {"source": "dataset.regime_label", "train_counts": engine.classifier.train_counts()},
        "training_date_range": [samples[0]["timestamp_utc"], samples[-1]["timestamp_utc"]],
        "n_training_rows": len(samples),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(manifest, os.path.join(base, "manifest.json"))
    return {"engine": engine, "manifest": manifest, "model_path": model_path, "manifest_path": os.path.join(base, "manifest.json")}


def load_required_model(model_dir: str, *, expected_model_version: str, expected_target_version: str = DEFAULT_TARGET.version) -> StopHuntProbabilityEngine:
    from .io import read_json
    manifest_path = os.path.join(model_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        raise RuntimeError(f"required SHPE manifest missing: {manifest_path}")
    manifest = read_json(manifest_path)
    if manifest.get("model_version") != expected_model_version or manifest.get("target_definition_version") != expected_target_version:
        raise RuntimeError("required SHPE model manifest version mismatch")
    model_path = str(manifest.get("model_path") or "")
    if not os.path.exists(model_path):
        raise RuntimeError(f"required SHPE model missing: {model_path}")
    engine = StopHuntProbabilityEngine.load(model_path)
    if engine.model_version != expected_model_version or tuple(engine.feature_names) != tuple(SHPE_FEATURE_NAMES):
        raise RuntimeError("required SHPE model invalid after load")
    return engine

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .io import atomic_write_json, ensure_dir, read_json
from .target import DEFAULT_TARGET, TargetDefinition

LABEL_SCHEMA_VERSION = "shpe-labels.v1.0.0"


def _f(v: Any) -> float:
    return float(v)


def _event_timestamp(sample: Dict[str, Any]) -> int:
    return int(sample.get("feature_available_ts_ms", sample["timestamp_ms"]))


def _validate_positive_event_timestamp(row_index: int, feature_available_ts_ms: int, event_timestamp_ms: int) -> None:
    if event_timestamp_ms <= feature_available_ts_ms:
        raise ValueError(
            "label event timestamp ordering invalid: "
            f"row_index={row_index} event_timestamp_ms={event_timestamp_ms} "
            f"feature_available_ts_ms={feature_available_ts_ms}"
        )


def generate_labels(dataset: Dict[str, Any], out_dir: str, *, target: TargetDefinition = DEFAULT_TARGET, labels_version: str = "dev") -> Dict[str, Any]:
    if dataset.get("target_definition", {}).get("version") != target.version:
        raise ValueError("dataset target definition version mismatch")
    samples = list(dataset.get("samples", []))
    h = int(target.horizon_bars)
    lookback = int(target.pool_lookback_bars)
    buffer = float(target.sweep_buffer_bps) / 10000.0
    labels: List[Dict[str, Any]] = []
    for i, sample in enumerate(samples):
        if i < lookback - 1 or i + h >= len(samples):
            label: Optional[int] = None
            reason = "insufficient_past" if i < lookback - 1 else "insufficient_future"
        else:
            past = samples[i - lookback + 1: i + 1]
            high_pool = max(_f(s["ohlcv"]["high"]) for s in past)
            low_pool = min(_f(s["ohlcv"]["low"]) for s in past)
            event = False
            event_side = ""
            event_ts: Optional[int] = None
            for fut in samples[i + 1: i + h + 1]:
                hi = _f(fut["ohlcv"]["high"]); lo = _f(fut["ohlcv"]["low"]); close = _f(fut["ohlcv"]["close"])
                if hi >= high_pool * (1.0 + buffer) and close <= high_pool:
                    event = True; event_side = "high"; event_ts = _event_timestamp(fut); break
                if lo <= low_pool * (1.0 - buffer) and close >= low_pool:
                    event = True; event_side = "low"; event_ts = _event_timestamp(fut); break
            label = 1 if event else 0
            reason = event_side or "no_sweep"
        feature_available_ts_ms = int(sample["feature_available_ts_ms"])
        if label == 1:
            if event_ts is None:
                raise ValueError(f"positive label missing event_timestamp_ms: row_index={i}")
            _validate_positive_event_timestamp(i, feature_available_ts_ms, event_ts)
        labels.append({"row_index": i, "timestamp_ms": sample["timestamp_ms"], "label": label, "reason": reason, "horizon_bars": h, "feature_available_ts_ms": feature_available_ts_ms, "event_timestamp_ms": event_ts if label == 1 else None})
    base = ensure_dir(os.path.join(out_dir, labels_version))
    payload = {"schema_version": LABEL_SCHEMA_VERSION, "labels_version": labels_version, "target_definition": target.to_dict(), "metadata": {"horizon_bars": h, "pool_lookback_bars": lookback, "sweep_buffer_bps": target.sweep_buffer_bps, "exclusions": ["insufficient_past", "insufficient_future"]}, "labels": labels}
    path = os.path.join(base, "labels.json")
    atomic_write_json(payload, path)
    atomic_write_json({"labels_path": path, "rows": len(labels), "usable_rows": sum(1 for x in labels if x["label"] is not None)}, os.path.join(base, "manifest.json"))
    return {"path": path, "payload": payload}


def load_labels(path: str) -> Dict[str, Any]:
    return read_json(path)

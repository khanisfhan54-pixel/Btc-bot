from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import numpy as np

DEFAULT_TARGET_VOL_ARTIFACT_PATH = "weights/target_vol.json"
_REQUIRED_KEYS = {
    "calibrated_target_vol",
    "calibration_window",
    "sample_size",
    "percentile_used",
    "validation_metrics",
    "timestamp",
}


def _to_epoch_seconds(ts: Any) -> float:
    if isinstance(ts, np.datetime64):
        return float(ts.astype("datetime64[ns]").astype(np.int64) / 1_000_000_000.0)
    return float(ts)


def _iso_utc(ts: Any) -> str:
    try:
        sec = _to_epoch_seconds(ts)
        return datetime.fromtimestamp(sec, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return str(ts)


def _rolling_past_std(values: np.ndarray, window: int) -> np.ndarray:
    """Rolling std with each value computed from strictly prior observations."""
    n = int(values.size)
    out = np.full(n, np.nan, dtype=float)
    if n < 2:
        return out
    prefix = np.concatenate(([0.0], np.cumsum(values, dtype=float)))
    prefix2 = np.concatenate(([0.0], np.cumsum(values * values, dtype=float)))
    for i in range(1, n):
        start = max(0, i - int(window))
        count = i - start
        if count < 2:
            continue
        s = prefix[i] - prefix[start]
        s2 = prefix2[i] - prefix2[start]
        var = (s2 - (s * s) / count) / (count - 1)
        out[i] = float(np.sqrt(max(var, 0.0)))
    return out


def calibrate_target_vol(
    returns: np.ndarray,
    timestamps: np.ndarray,
    *,
    window_days: int = 30,
    percentile: float = 75.0,
    min_samples: int = 5000,
) -> dict:
    rets = np.asarray(returns, dtype=float).reshape(-1)
    ts = np.asarray(timestamps).reshape(-1)
    if rets.size != ts.size:
        raise ValueError("returns and timestamps must have identical length")
    finite = np.isfinite(rets)
    rets = rets[finite]
    ts = ts[finite]
    if rets.size < int(min_samples):
        raise ValueError(f"insufficient samples: {rets.size} < {min_samples}")

    window = max(int(window_days) * 24 * 60, 2)
    realized = _rolling_past_std(rets, window)
    realized = realized[np.isfinite(realized) & (realized > 0)]
    if realized.size < int(min_samples):
        raise ValueError(f"insufficient realized-vol samples: {realized.size} < {min_samples}")

    trailing = realized[-window:] if realized.size >= window else realized
    calibrated = float(np.percentile(trailing, float(percentile)))
    pct_names = {"p10": 10, "p25": 25, "p50": 50, "p75": 75, "p90": 90, "p95": 95, "p99": 99}
    metrics = {
        "mean": float(np.mean(trailing)),
        "median": float(np.median(trailing)),
        **{name: float(np.percentile(trailing, p)) for name, p in pct_names.items()},
        "low_vol_gate_activation_rate_at_calibrated_value": float(np.mean(trailing < calibrated)),
    }
    return {
        "calibrated_target_vol": calibrated,
        "calibration_window": {
            "start_ts": _iso_utc(ts[max(0, ts.size - trailing.size)]),
            "end_ts": _iso_utc(ts[-1]),
            "window_days": int(window_days),
        },
        "sample_size": int(trailing.size),
        "percentile_used": float(percentile),
        "validation_metrics": metrics,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def write_target_vol_artifact(result: dict, path: str = DEFAULT_TARGET_VOL_ARTIFACT_PATH) -> None:
    payload = dict(result)
    payload["schema_version"] = "1.0.0"
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")


def load_target_vol_artifact(path: str = DEFAULT_TARGET_VOL_ARTIFACT_PATH, *, min_samples: int = 5000) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict):
            return None
        if not _REQUIRED_KEYS.issubset(payload):
            return None
        val = float(payload.get("calibrated_target_vol"))
        if not np.isfinite(val) or val <= 0.0:
            return None
        if int(payload.get("sample_size", 0)) < int(min_samples):
            return None
        if not isinstance(payload.get("calibration_window"), dict):
            return None
        if not isinstance(payload.get("validation_metrics"), dict):
            return None
        return payload
    except Exception:
        return None

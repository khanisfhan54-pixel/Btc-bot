from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from ..model.engine import SHPE_FEATURE_NAMES
from .io import atomic_write_json, ensure_dir, read_json
from .target import DEFAULT_TARGET, TargetDefinition

DATASET_SCHEMA_VERSION = "shpe-dataset.v1.0.0"


def _f(row: Dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        value = float(row.get(key, default))
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _i(row: Dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(float(row.get(key, default)))
    except Exception:
        return default


def _date(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()


def validate_feature_rows(rows: Sequence[Dict[str, Any]], *, interval: str = "5m") -> None:
    if not rows:
        raise ValueError("SHPE dataset requires at least one feature row")
    required = ("symbol", "bar_interval", "timestamp_ms", "bar_start_ts_ms", "bar_end_ts_ms", "feature_available_ts_ms", "open", "high", "low", "close", "volume")
    missing = [c for c in required if c not in rows[0]]
    if missing:
        raise ValueError(f"feature rows missing required columns: {missing}")
    prev: Optional[int] = None
    for idx, row in enumerate(rows):
        ts = _i(row, "timestamp_ms")
        end = _i(row, "bar_end_ts_ms")
        avail = _i(row, "feature_available_ts_ms")
        if str(row.get("bar_interval")) != interval:
            raise ValueError(f"row {idx} interval {row.get('bar_interval')} != {interval}")
        if prev is not None and ts <= prev:
            raise ValueError(f"timestamps must be strictly increasing at row {idx}")
        prev = ts
        if avail != end:
            raise ValueError(f"row {idx} feature_available_ts_ms must equal bar_end_ts_ms")
        if _f(row, "open") <= 0 or _f(row, "high") <= 0 or _f(row, "low") <= 0 or _f(row, "close") <= 0:
            raise ValueError(f"row {idx} contains non-positive OHLC")
        if _f(row, "high") < _f(row, "low"):
            raise ValueError(f"row {idx} high < low")
        for k in ("last_trade_ts_ms", "last_book_event_ts_ms"):
            if row.get(k) not in (None, "") and _i(row, k) >= end:
                raise ValueError(f"row {idx} {k} lookahead leak past feature availability")


def derive_features(rows: Sequence[Dict[str, Any]], idx: int, target: TargetDefinition = DEFAULT_TARGET) -> Dict[str, float]:
    row = rows[idx]
    close = max(_f(row, "close"), 1e-9)
    start = max(0, idx - target.pool_lookback_bars + 1)
    window = rows[start: idx + 1]
    highs = [_f(r, "high") for r in window]
    lows = [_f(r, "low") for r in window]
    vols = [_f(r, "volume") for r in window]
    prior_high = max(highs) if highs else close
    prior_low = min(lows) if lows else close
    high_age = float(idx - (start + highs.index(prior_high))) if highs else 0.0
    low_age = float(idx - (start + lows.index(prior_low))) if lows else 0.0
    body = abs(_f(row, "close") - _f(row, "open"))
    rng = max(_f(row, "high") - _f(row, "low"), 1e-9)
    upper = max(0.0, _f(row, "high") - max(_f(row, "open"), _f(row, "close")))
    lower = max(0.0, min(_f(row, "open"), _f(row, "close")) - _f(row, "low"))
    mean_vol = sum(vols) / len(vols) if vols else 0.0
    sd_vol = math.sqrt(sum((v - mean_vol) ** 2 for v in vols) / max(len(vols) - 1, 1)) if len(vols) > 1 else 0.0
    vol_z = (_f(row, "volume") - mean_vol) / sd_vol if sd_vol > 1e-12 else _f(row, "vol_z")
    round_level = round(close / 1000.0) * 1000.0
    return {
        "pool_dist_to_high_pct": abs(close - prior_high) / close,
        "pool_dist_to_low_pct": abs(close - prior_low) / close,
        "pool_high_pool_age_bars": high_age,
        "pool_low_pool_age_bars": low_age,
        "pool_round_number_proximity_bps": abs(close - round_level) / close * 10000.0,
        "funding_rate_8h": _f(row, "funding_rate_8h"),
        "funding_z30d": _f(row, "funding_z30d"),
        "funding_oi_sign_divergence": _f(row, "funding_oi_sign_divergence"),
        "oi_delta_oi_velocity": _f(row, "delta_oi_velocity"),
        "oi_pct_change_1h": _f(row, "oi_pct_change_1h"),
        "oi_buildup_flag": _f(row, "oi_buildup_flag"),
        "oi_price_divergence_sign": _f(row, "oi_price_divergence_sign"),
        "volume_wick_to_body_ratio": (upper + lower) / max(body, 1e-9),
        "volume_upper_wick_pct": upper / rng,
        "volume_lower_wick_pct": lower / rng,
        "volume_zscore": vol_z,
        "volume_at_extreme_vs_close": abs((_f(row, "high") if _f(row, "close") >= _f(row, "open") else _f(row, "low")) - _f(row, "close")) / rng,
        "volume_exhaustion_candle_flag": 1.0 if vol_z > 1.5 and ((upper + lower) / max(body, 1e-9)) > 2.0 else 0.0,
        "lob_ofi_zscore": _f(row, "ofi_zscore", _f(row, "l1_order_flow_proxy_z")),
        "lob_queue_imbalance": _f(row, "book_imbalance", _f(row, "imbalance")),
        "lob_depth_replenishment_ratio": _f(row, "depth_replenishment_ratio", 1.0),
        "liq_nearest_long_cluster_dist_pct": _f(row, "nearest_long_cluster_dist_pct"),
        "liq_nearest_short_cluster_dist_pct": _f(row, "nearest_short_cluster_dist_pct"),
        "liq_cascade_amplification_flag": _f(row, "cascade_amplification_flag"),
        "regime_confidence": _f(row, "regime_confidence", 1.0 if row.get("regime") else 0.0),
        "regime_conviction": _f(row, "regime_conviction"),
        "regime_edge_score": _f(row, "regime_edge_score"),
        "regime_signal_valid": _f(row, "regime_signal_valid", 1.0 if row.get("regime") else 0.0),
        "regime_expected_volatility": _f(row, "regime_expected_volatility", _f(row, "volatility")),
    }


def build_dataset(rows: Sequence[Dict[str, Any]], out_dir: str, *, target: TargetDefinition = DEFAULT_TARGET, dataset_version: str = "dev") -> Dict[str, Any]:
    ordered = sorted([dict(r) for r in rows], key=lambda r: _i(r, "timestamp_ms"))
    validate_feature_rows(ordered, interval=target.bar_interval)
    samples: List[Dict[str, Any]] = []
    for idx, row in enumerate(ordered):
        feats = derive_features(ordered, idx, target)
        missing = [n for n in SHPE_FEATURE_NAMES if n not in feats]
        if missing:
            raise ValueError(f"derived feature schema missing: {missing}")
        ts = _i(row, "timestamp_ms")
        samples.append({
            "row_index": idx,
            "timestamp_ms": ts,
            "feature_available_ts_ms": _i(row, "feature_available_ts_ms"),
            "timestamp_utc": _date(ts),
            "symbol": str(row.get("symbol", target.symbol)),
            "bar_interval": str(row.get("bar_interval", target.bar_interval)),
            "raw_sample": {k: row.get(k) for k in ("open", "high", "low", "close", "volume", "regime")},
            "ohlcv": {k: _f(row, k) for k in ("open", "high", "low", "close", "volume")},
            "derived_features": {n: float(feats[n]) for n in SHPE_FEATURE_NAMES},
            "regime_label": str(row.get("regime") or row.get("regime_label") or "unknown"),
            "label": None,
        })
    base = ensure_dir(os.path.join(out_dir, dataset_version))
    payload = {"schema_version": DATASET_SCHEMA_VERSION, "dataset_version": dataset_version, "target_definition": target.to_dict(), "feature_schema": list(SHPE_FEATURE_NAMES), "samples": samples}
    path = os.path.join(base, "dataset.json")
    atomic_write_json(payload, path)
    manifest = {"dataset_path": path, "rows": len(samples), "date_range": [samples[0]["timestamp_utc"], samples[-1]["timestamp_utc"]] if samples else []}
    atomic_write_json(manifest, os.path.join(base, "manifest.json"))
    return {"path": path, "manifest_path": os.path.join(base, "manifest.json"), "payload": payload}


def load_feature_rows(path: str) -> List[Dict[str, Any]]:
    if path.endswith(".json"):
        data = read_json(path)
        rows = data.get("samples", data.get("rows"))
        if isinstance(rows, list):
            return [dict(r.get("raw_sample", r)) for r in rows]
    if path.endswith(".parquet"):
        try:
            import pyarrow.parquet as pq  # type: ignore
        except Exception as exc:
            raise RuntimeError("pyarrow is required to read parquet feature files") from exc
        return pq.read_table(path).to_pylist()
    raise ValueError(f"unsupported feature input: {path}")

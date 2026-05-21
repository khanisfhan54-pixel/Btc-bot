"""Signal adapter exposing SHPE probability only (no execution side-effects)."""
from __future__ import annotations

import logging
import os
import time as _time
from collections import defaultdict as _defaultdict
from typing import Optional, TypedDict

from ..model.engine import StopHuntProbabilityEngine
from .feature_pipeline import PipelineInput, build_feature_vector

_log = logging.getLogger("shpe.signal_adapter")

_WARN_COOLDOWN_SEC: float = 60.0
_warn_last_seen: dict = _defaultdict(float)


def _throttled_warn(key: str, msg: str) -> None:
    """Emit a warning at most once per _WARN_COOLDOWN_SEC for each unique key."""
    now = _time.monotonic()
    if now - _warn_last_seen[key] >= _WARN_COOLDOWN_SEC:
        _warn_last_seen[key] = now
        _log.error(msg)


class SHPEOutput(TypedDict):
    probability: float
    degraded: bool
    regime_used: str


def get_shpe_probability(engine: Optional[StopHuntProbabilityEngine], input_data: PipelineInput, bar_index: int) -> SHPEOutput:
    try:
        if os.getenv("SHPE_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"} or engine is None:
            return {"probability": 0.5, "degraded": True, "regime_used": "<disabled>"}
        feature_vector = build_feature_vector(input_data, bar_index)
        prediction = engine.predict(feature_vector)
        return {
            "probability": float(max(0.0, min(1.0, prediction.p_sweep))),
            "degraded": bool(prediction.degraded),
            "regime_used": str(prediction.regime_used),
        }
    except Exception as exc:
        _throttled_warn(
            f"shpe_error:{type(exc).__name__}",
            f"shpe_signal_adapter_error bar_index={bar_index} exc={exc!r}",
        )
        return {"probability": 0.5, "degraded": True, "regime_used": "<error>"}

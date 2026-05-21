"""Signal adapter exposing SHPE probability only (no execution side-effects)."""
from __future__ import annotations

import logging
import os
from typing import Optional, TypedDict

from ..model.engine import StopHuntProbabilityEngine
from .feature_pipeline import PipelineInput, build_feature_vector

_log = logging.getLogger("shpe.signal_adapter")


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
        _log.error("shpe_signal_adapter_error bar_index=%d exc=%r", bar_index, exc)
        return {"probability": 0.5, "degraded": True, "regime_used": "<error>"}

"""Signal adapter exposing SHPE probability only (no execution side-effects)."""
from __future__ import annotations

from typing import TypedDict

from ..model.engine import StopHuntProbabilityEngine
from .feature_pipeline import PipelineInput, build_feature_vector


class SHPEOutput(TypedDict):
    probability: float
    degraded: bool
    regime_used: str


def get_shpe_probability(engine: StopHuntProbabilityEngine, input_data: PipelineInput, bar_index: int) -> SHPEOutput:
    fv = build_feature_vector(input_data, bar_index)
    pred = engine.predict(fv)
    return {
        "probability": float(max(0.0, min(1.0, pred.p_sweep))),
        "degraded": bool(pred.degraded),
        "regime_used": str(pred.regime_used),
    }

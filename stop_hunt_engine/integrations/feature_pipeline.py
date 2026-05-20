"""Feature-pipeline adapter keeping ingestion separate from inference."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..data.candle_store import Candle
from ..data.derivatives import FundingPoint, LiquidationCluster, OpenInterestPoint
from ..data.l2_snapshot import L2Snapshot
from ..features.feature_vector import StopHuntFeatureVector, compute_feature_vector
from .regime_adapter import map_regime_output


@dataclass(frozen=True)
class PipelineInput:
    candles_5m: Sequence[Candle]
    l2_snapshots: Sequence[L2Snapshot]
    funding: Sequence[FundingPoint]
    open_interest: Sequence[OpenInterestPoint]
    liquidation_clusters: Sequence[LiquidationCluster]
    regime_output: dict


def build_feature_vector(input_data: PipelineInput, bar_index: int, *, max_clock_skew_sec: int = 3600) -> StopHuntFeatureVector:
    if not input_data.candles_5m:
        raise ValueError("candles_5m is empty")
    ts = input_data.candles_5m[bar_index].timestamp
    if input_data.l2_snapshots and abs(ts - input_data.l2_snapshots[-1].timestamp) > max_clock_skew_sec:
        raise ValueError("L2 snapshot timestamp mismatch")
    if input_data.open_interest and abs(ts - input_data.open_interest[-1].timestamp) > max_clock_skew_sec:
        raise ValueError("open_interest timestamp mismatch")
    return compute_feature_vector(
        bar_index,
        input_data.candles_5m,
        l2_snapshots=input_data.l2_snapshots,
        funding=input_data.funding,
        open_interest=input_data.open_interest,
        liquidation_clusters=input_data.liquidation_clusters,
        regime_output=map_regime_output(input_data.regime_output),
    )

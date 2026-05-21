"""Feature-pipeline adapter keeping ingestion separate from inference."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping, Sequence

from ..data.candle_store import Candle
from ..data.derivatives import FundingPoint, LiquidationCluster, OpenInterestPoint
from ..data.l2_snapshot import L2Snapshot
from ..features.feature_vector import StopHuntFeatureVector, compute_feature_vector
from .regime_adapter import map_regime_output

_log = logging.getLogger("shpe.feature_pipeline")
DEFAULT_MAX_CLOCK_SKEW_SEC: int = 3600


@dataclass(frozen=True)
class PipelineInput:
    candles_5m: Sequence[Candle]
    l2_snapshots: Sequence[L2Snapshot]
    funding: Sequence[FundingPoint]
    open_interest: Sequence[OpenInterestPoint]
    liquidation_clusters: Sequence[LiquidationCluster]
    regime_output: Mapping[str, object]


def build_feature_vector(
    input_data: PipelineInput,
    bar_index: int,
    *,
    max_clock_skew_sec: int = DEFAULT_MAX_CLOCK_SKEW_SEC,
) -> StopHuntFeatureVector:
    if not input_data.candles_5m:
        raise ValueError("candles_5m is empty")
    timestamp = input_data.candles_5m[bar_index].timestamp
    if input_data.l2_snapshots:
        l2_timestamp = input_data.l2_snapshots[-1].timestamp
        l2_delta = abs(timestamp - l2_timestamp)
        if l2_delta > max_clock_skew_sec:
            _log.warning("shpe_pipeline_clock_skew source=l2 candle_ts=%s source_ts=%s delta=%s", timestamp, l2_timestamp, l2_delta)
            raise ValueError("L2 snapshot timestamp mismatch")
    if input_data.open_interest:
        oi_timestamp = input_data.open_interest[-1].timestamp
        oi_delta = abs(timestamp - oi_timestamp)
        if oi_delta > max_clock_skew_sec:
            _log.warning("shpe_pipeline_clock_skew source=open_interest candle_ts=%s source_ts=%s delta=%s", timestamp, oi_timestamp, oi_delta)
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

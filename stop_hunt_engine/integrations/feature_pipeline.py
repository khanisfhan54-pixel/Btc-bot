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


def _nearest_past_snapshot(snapshots, candle_ts: float, max_skew_sec: int):
    """
    Return the snapshot with the largest timestamp that is still <= candle_ts.
    If none exists at or before candle_ts, fall back to the absolute-nearest
    within max_skew_sec (handles minor clock drift at sequence boundaries).
    Returns (snapshot, delta_seconds) or (None, None) if sequence is empty.
    Never returns a snapshot whose timestamp exceeds candle_ts by more than
    max_skew_sec (no lookahead leakage).
    """
    if not snapshots:
        return None, None
    past = [s for s in snapshots if s.timestamp <= candle_ts]
    if past:
        best = max(past, key=lambda s: s.timestamp)
        return best, abs(candle_ts - best.timestamp)
    nearest = min(snapshots, key=lambda s: abs(s.timestamp - candle_ts))
    delta = abs(nearest.timestamp - candle_ts)
    if nearest.timestamp > candle_ts and delta > max_skew_sec:
        return None, None
    return nearest, delta


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
        best_l2, l2_delta = _nearest_past_snapshot(
            input_data.l2_snapshots, timestamp, max_clock_skew_sec
        )
        if best_l2 is None or l2_delta is None or l2_delta > max_clock_skew_sec:
            _log.warning(
                "shpe_pipeline_clock_skew source=l2 candle_ts=%s best_ts=%s delta=%s",
                timestamp,
                getattr(best_l2, "timestamp", "none"),
                l2_delta,
            )
            raise ValueError("L2 snapshot timestamp mismatch")

    if input_data.open_interest:
        best_oi, oi_delta = _nearest_past_snapshot(
            input_data.open_interest, timestamp, max_clock_skew_sec
        )
        if best_oi is None or oi_delta is None or oi_delta > max_clock_skew_sec:
            _log.warning(
                "shpe_pipeline_clock_skew source=open_interest candle_ts=%s best_ts=%s delta=%s",
                timestamp,
                getattr(best_oi, "timestamp", "none"),
                oi_delta,
            )
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

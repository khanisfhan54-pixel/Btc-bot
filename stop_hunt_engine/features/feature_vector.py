from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, ClassVar, List, Mapping, Optional, Sequence
from ..data.candle_store import Candle
from ..data.derivatives import FundingPoint, LiquidationCluster, OpenInterestPoint
from ..data.l2_snapshot import L2Snapshot
from .funding_pressure import FundingPressureFeatures, compute_funding_pressure
from .liquidation_proximity import LiquidationProximityFeatures, compute_liquidation_proximity
from .lob_imbalance import LOBImbalanceFeatures, compute_lob_imbalance
from .oi_dynamics import OIDynamicsFeatures, compute_oi_dynamics
from .pool_distance import PoolDistanceFeatures, compute_pool_distance
from .regime_context import RegimeContextFeatures, project_regime_context
from .volume_trap import VolumeTrapFeatures, compute_volume_trap

@dataclass(frozen=True)
class StopHuntFeatureVector:
    bar_index: int
    timestamp: float
    pool: PoolDistanceFeatures
    funding: FundingPressureFeatures
    oi: OIDynamicsFeatures
    volume: VolumeTrapFeatures
    lob: LOBImbalanceFeatures
    liquidation: LiquidationProximityFeatures
    regime: RegimeContextFeatures
    stale_dimensions: List[str] = field(default_factory=list)
    UPDATE_FREQUENCY: ClassVar[str] = "5m"

_DIM_NAMES = ("pool", "funding", "oi", "volume", "lob", "liquidation", "regime")

def compute_feature_vector(i: int, candles: Sequence[Candle], *, l2_snapshots: Sequence[L2Snapshot] = (), funding: Sequence[FundingPoint] = (), open_interest: Sequence[OpenInterestPoint] = (), liquidation_clusters: Sequence[LiquidationCluster] = (), regime_output: Optional[Mapping[str, Any]] = None, liq_stale_seconds: int = 3600) -> StopHuntFeatureVector:
    if not candles or i < 0 or i >= len(candles):
        raise ValueError(f"bar_index {i} out of range for {len(candles)} candles")
    bar = candles[i]
    as_of_ts = bar.timestamp
    pool = compute_pool_distance(i, candles)
    fund = compute_funding_pressure(as_of_ts, funding, open_interest)
    oi = compute_oi_dynamics(as_of_ts, open_interest, candles)
    vol = compute_volume_trap(i, candles)
    lob = compute_lob_imbalance(as_of_ts, l2_snapshots)
    liq = compute_liquidation_proximity(as_of_ts, bar.close, liquidation_clusters, stale_seconds=liq_stale_seconds)
    reg = project_regime_context(regime_output, as_of_ts=as_of_ts)
    dims = (pool, fund, oi, vol, lob, liq, reg)
    stale = [name for name, d in zip(_DIM_NAMES, dims) if d.stale]
    return StopHuntFeatureVector(i, as_of_ts, pool, fund, oi, vol, lob, liq, reg, stale)

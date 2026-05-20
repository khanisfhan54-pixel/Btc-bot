from dataclasses import dataclass
from typing import Sequence
from ..data.derivatives import LiquidationCluster

@dataclass(frozen=True)
class LiquidationProximityFeatures:
    nearest_long_cluster_dist_pct: float = 0.0
    nearest_short_cluster_dist_pct: float = 0.0
    cascade_amplification_flag: bool = False
    stale: bool = True

def compute_liquidation_proximity(as_of_ts: float, price: float, liquidation_clusters: Sequence[LiquidationCluster]) -> LiquidationProximityFeatures:
    _ = (as_of_ts, price)
    return LiquidationProximityFeatures(stale=(len(liquidation_clusters)==0))

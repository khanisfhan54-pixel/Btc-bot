from dataclasses import dataclass
from typing import Sequence
from ..data.l2_snapshot import L2Snapshot

@dataclass(frozen=True)
class LOBImbalanceFeatures:
    ofi_zscore: float = 0.0
    queue_imbalance: float = 0.0
    depth_replenishment_ratio: float = 0.0
    stale: bool = True

def compute_lob_imbalance(as_of_ts: float, l2_snapshots: Sequence[L2Snapshot]) -> LOBImbalanceFeatures:
    return LOBImbalanceFeatures(stale=(len(l2_snapshots)==0))

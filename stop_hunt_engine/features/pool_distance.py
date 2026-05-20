from dataclasses import dataclass
from typing import Sequence
from ..data.candle_store import Candle

@dataclass(frozen=True)
class PoolDistanceFeatures:
    dist_to_high_pool_pct: float = 0.0
    dist_to_low_pool_pct: float = 0.0
    high_pool_age_bars: float = 0.0
    low_pool_age_bars: float = 0.0
    round_number_proximity_bps: float = 0.0
    stale: bool = False

def compute_pool_distance(i: int, candles: Sequence[Candle]) -> PoolDistanceFeatures:
    _ = (i, candles)
    return PoolDistanceFeatures()

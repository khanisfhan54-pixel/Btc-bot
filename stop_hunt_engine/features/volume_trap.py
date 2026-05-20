from dataclasses import dataclass
from typing import Sequence
from ..data.candle_store import Candle

@dataclass(frozen=True)
class VolumeTrapFeatures:
    wick_to_body_ratio: float = 0.0
    upper_wick_pct: float = 0.0
    lower_wick_pct: float = 0.0
    volume_zscore: float = 0.0
    volume_at_extreme_vs_close: float = 0.0
    exhaustion_candle_flag: bool = False
    stale: bool = False

def compute_volume_trap(i: int, candles: Sequence[Candle]) -> VolumeTrapFeatures:
    _ = (i, candles)
    return VolumeTrapFeatures()

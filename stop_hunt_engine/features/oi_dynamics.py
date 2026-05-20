from dataclasses import dataclass
from typing import Sequence
from ..data.derivatives import OpenInterestPoint
from ..data.candle_store import Candle

@dataclass(frozen=True)
class OIDynamicsFeatures:
    delta_oi_velocity: float = 0.0
    oi_pct_change_1h: float = 0.0
    oi_buildup_flag: bool = False
    oi_price_divergence_sign: float = 0.0
    stale: bool = True

def compute_oi_dynamics(as_of_ts: float, open_interest: Sequence[OpenInterestPoint], candles: Sequence[Candle]) -> OIDynamicsFeatures:
    return OIDynamicsFeatures(stale=(len(open_interest)==0 or len(candles)==0))

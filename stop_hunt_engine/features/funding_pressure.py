from dataclasses import dataclass
from typing import Sequence
from ..data.derivatives import FundingPoint, OpenInterestPoint

@dataclass(frozen=True)
class FundingPressureFeatures:
    funding_rate_8h: float = 0.0
    funding_z30d: float = 0.0
    funding_oi_sign_divergence: float = 0.0
    stale: bool = True

def compute_funding_pressure(as_of_ts: float, funding: Sequence[FundingPoint], open_interest: Sequence[OpenInterestPoint]) -> FundingPressureFeatures:
    return FundingPressureFeatures(stale=(len(funding)==0 or len(open_interest)==0))

from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class FundingPoint:
    timestamp: float
    rate_8h: float
    venue: str = ""


@dataclass(frozen=True)
class OpenInterestPoint:
    timestamp: float
    oi_usd: float
    venue: str = ""


@dataclass(frozen=True)
class LiquidationCluster:
    price: float
    size_usd: float
    side: str
    as_of: float


@dataclass(frozen=True)
class DerivativesSnapshot:
    funding: Tuple[FundingPoint, ...]
    open_interest: Tuple[OpenInterestPoint, ...]
    liquidations: Tuple[LiquidationCluster, ...]

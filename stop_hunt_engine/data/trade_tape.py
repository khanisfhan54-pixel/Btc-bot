from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Tuple


@dataclass(frozen=True)
class Trade:
    timestamp: float
    price: float
    size: float
    aggressor: Literal["buy", "sell"]


@dataclass(frozen=True)
class TradeTape:
    trades: Tuple[Trade, ...]
    source: str = ""

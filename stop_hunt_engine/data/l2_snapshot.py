from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class L2Snapshot:
    timestamp: float
    bids: Tuple[BookLevel, ...]
    asks: Tuple[BookLevel, ...]
    source: str = ""

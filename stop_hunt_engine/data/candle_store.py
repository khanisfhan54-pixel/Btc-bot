from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class Candle:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    atr: float = 0.0
    ema_fast: float = 0.0
    ema_slow: float = 0.0
    regime_label: str = ""


@dataclass(frozen=True)
class CandleStore:
    by_timeframe: Dict[str, Tuple[Candle, ...]]

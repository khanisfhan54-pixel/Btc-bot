from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict

TARGET_DEFINITION_VERSION = "shpe-target.v1.0.0-temporary"


@dataclass(frozen=True)
class TargetDefinition:
    version: str = TARGET_DEFINITION_VERSION
    event: str = "future stop-hunt sweep with rejection through prior high/low liquidity pool"
    symbol: str = "BTCUSDT"
    bar_interval: str = "5m"
    horizon_bars: int = 3
    pool_lookback_bars: int = 20
    sweep_buffer_bps: float = 1.0
    positive_condition: str = "future high sweeps prior high pool and closes back below it, or future low sweeps prior low pool and closes back above it"
    negative_condition: str = "full horizon exists and no positive sweep occurs"
    ignore_zone: str = "insufficient past pool bars, missing full future horizon, or invalid prices"
    regime_conditioning: str = "existing regime label used for model routing only; not used to create labels"
    no_lookahead_rule: str = "features/pools use bars <= t; labels use bars t+1..t+horizon after features are fixed"

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


DEFAULT_TARGET = TargetDefinition()

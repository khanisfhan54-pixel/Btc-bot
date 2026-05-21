"""Liquidity-pool distance features from swing highs/lows."""
from __future__ import annotations

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


def compute_pool_distance(i: int, candles: Sequence[Candle], *, lookback: int = 50, round_step: float = 1000.0) -> PoolDistanceFeatures:
    if not candles or i < 0 or i >= len(candles):
        return PoolDistanceFeatures(stale=True)
    window_start = max(0, i - lookback + 1)
    window = candles[window_start: i + 1]
    close = max(float(candles[i].close), 1e-9)

    highs = [float(c.high) for c in window]
    lows = [float(c.low) for c in window]
    h = max(highs)
    l = min(lows)
    hi_idx = window_start + highs.index(h)
    lo_idx = window_start + lows.index(l)

    dist_hi = abs(close - h) / close
    dist_lo = abs(close - l) / close

    nearest_round = round(close / round_step) * round_step
    round_bps = abs(close - nearest_round) / close * 10000.0

    return PoolDistanceFeatures(
        dist_to_high_pool_pct=float(dist_hi),
        dist_to_low_pool_pct=float(dist_lo),
        high_pool_age_bars=float(i - hi_idx),
        low_pool_age_bars=float(i - lo_idx),
        round_number_proximity_bps=float(round_bps),
        stale=False,
    )

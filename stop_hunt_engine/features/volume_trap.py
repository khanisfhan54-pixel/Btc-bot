"""Candle/volume trap features."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

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


def compute_volume_trap(i: int, candles: Sequence[Candle], *, z_window: int = 50) -> VolumeTrapFeatures:
    if not candles or i < 0 or i >= len(candles):
        return VolumeTrapFeatures(stale=True)
    c = candles[i]
    body = abs(c.close - c.open)
    rng = max(c.high - c.low, 1e-9)
    upper_wick = max(c.high - max(c.open, c.close), 0.0)
    lower_wick = max(min(c.open, c.close) - c.low, 0.0)
    wick_to_body = float((upper_wick + lower_wick) / max(body, 1e-9))

    vols = np.asarray([float(x.volume) for x in candles[max(0, i - z_window + 1): i + 1]], dtype=float)
    if vols.size >= 5 and float(np.std(vols)) > 0.0:
        v_z = float((vols[-1] - float(np.mean(vols))) / float(np.std(vols)))
    else:
        v_z = 0.0

    extreme_ref = c.high if c.close >= c.open else c.low
    vol_pos = float(abs(extreme_ref - c.close) / rng)
    exhaustion = bool(v_z > 1.5 and wick_to_body > 2.0)

    return VolumeTrapFeatures(
        wick_to_body_ratio=wick_to_body,
        upper_wick_pct=float(upper_wick / rng),
        lower_wick_pct=float(lower_wick / rng),
        volume_zscore=v_z,
        volume_at_extreme_vs_close=vol_pos,
        exhaustion_candle_flag=exhaustion,
        stale=False,
    )

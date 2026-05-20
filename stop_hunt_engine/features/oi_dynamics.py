"""Open-interest dynamics features."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from ..data.candle_store import Candle
from ..data.derivatives import OpenInterestPoint


@dataclass(frozen=True)
class OIDynamicsFeatures:
    delta_oi_velocity: float = 0.0
    oi_pct_change_1h: float = 0.0
    oi_buildup_flag: bool = False
    oi_price_divergence_sign: float = 0.0
    stale: bool = True


def compute_oi_dynamics(
    as_of_ts: float,
    open_interest: Sequence[OpenInterestPoint],
    candles: Sequence[Candle],
    *,
    stale_seconds: int = 20 * 60,
) -> OIDynamicsFeatures:
    oi_hist = [p for p in open_interest if p.timestamp <= as_of_ts]
    c_hist = [c for c in candles if c.timestamp <= as_of_ts]
    if len(oi_hist) < 2 or len(c_hist) < 2:
        return OIDynamicsFeatures(stale=True)

    curr, prev = oi_hist[-1], oi_hist[-2]
    dt = max(curr.timestamp - prev.timestamp, 1.0)
    delta_oi = float(curr.oi_usd - prev.oi_usd)
    velocity = delta_oi / dt

    one_hour_ts = as_of_ts - 3600
    base = next((p for p in reversed(oi_hist) if p.timestamp <= one_hour_ts), oi_hist[0])
    denom = max(abs(base.oi_usd), 1.0)
    pct_1h = float((curr.oi_usd - base.oi_usd) / denom)

    price_delta = float(c_hist[-1].close - c_hist[-2].close)
    oi_sign = np.sign(delta_oi)
    px_sign = np.sign(price_delta)
    divergence = float(0.0 if oi_sign == 0 or px_sign == 0 else (-1.0 if oi_sign != px_sign else 1.0))
    buildup = bool(delta_oi > 0 and price_delta > 0)

    stale = (as_of_ts - curr.timestamp) > stale_seconds
    return OIDynamicsFeatures(velocity, pct_1h, buildup, divergence, stale)

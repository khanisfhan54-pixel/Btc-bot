# impact_decay.py
"""
impact_decay.py — Market Impact Decay Model

Tracks and estimates the decay of market impact after order entry.
Used post-entry to monitor how much our order's price impact is dissipating,
which informs trailing-stop management and partial profit targets.

Based on exponential decay:
  impact(t) = impact_0 * exp(-lambda * t)

Where:
  impact_0 : initial impact at entry (function of order size vs book depth)
  lambda   : decay rate (faster in liquid markets, slower in thin books)
  t        : time elapsed since entry in seconds

Output from update():
  {
    "impact_bps":      float — current estimated impact in basis points
    "decay_factor":    float — fraction of initial impact remaining [0, 1]
    "residual_impact": float — absolute price offset due to residual impact
    "half_life_s":     float — time for impact to halve (seconds)
    "fully_decayed":   bool  — True when impact < DECAY_THRESHOLD_BPS
    "elapsed_s":       float — seconds since entry
    "price_move_bps":  float — actual price movement since entry in bps
    "direction":       str   — "LONG" or "SHORT"
  }
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, Optional


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


class ImpactDecay:
    """
    Tracks a single active position's market impact decay.

    Usage:
        tracker = ImpactDecay()
        tracker.record_entry(entry_price, position_size, features_payload, direction)
        status = tracker.update(current_price, features_payload)   # each cycle
        tracker.reset()                                             # on position close
    """

    DECAY_THRESHOLD_BPS: float = 0.1

    def __init__(
        self,
        base_decay_rate: float = 0.015,
        liquidity_boost: float = 2.0,
        size_scale: float = 5.0,
        impact_scale_bps: float = 3.0,
    ) -> None:
        self.base_decay_rate = base_decay_rate
        self.liquidity_boost = liquidity_boost
        self.size_scale = size_scale
        self.impact_scale_bps = impact_scale_bps

        self._entry_time: Optional[float] = None
        self._entry_price: Optional[float] = None
        self._initial_impact_bps: float = 0.0
        self._decay_rate: float = base_decay_rate
        self._direction: str = "LONG"

    def record_entry(
        self,
        entry_price: float,
        position_size: float,
        features_payload: Dict[str, Any],
        direction: str = "LONG",
    ) -> None:
        """Call immediately after order fill."""
        feat = features_payload.get("features", features_payload) if isinstance(features_payload, dict) else {}

        liq_score   = _safe_float(feat.get("liquidity_score", 0.5))
        total_depth = _safe_float(feat.get("total_depth_n", self.size_scale))

        size_ratio           = _clamp(position_size / max(total_depth, self.size_scale), 0.0, 2.0)
        initial_impact_bps   = self.impact_scale_bps * math.sqrt(size_ratio)
        decay_rate           = self.base_decay_rate * (1.0 + self.liquidity_boost * liq_score)

        self._entry_time         = time.time()
        self._entry_price        = float(entry_price)
        self._initial_impact_bps = initial_impact_bps
        self._decay_rate         = decay_rate
        self._direction          = str(direction).upper()

    def update(
        self,
        current_price: float,
        features_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Call each cycle after entry to track impact decay. Safe to call even before entry."""
        if self._entry_time is None or self._entry_price is None:
            return self._null_state()

        elapsed_s    = time.time() - self._entry_time
        decay_factor = math.exp(-self._decay_rate * elapsed_s)
        impact_bps   = self._initial_impact_bps * decay_factor

        entry          = self._entry_price
        mid            = _safe_float(current_price)
        price_move_bps = abs(mid - entry) / max(entry, 1e-9) * 10_000.0
        half_life_s    = math.log(2.0) / max(self._decay_rate, 1e-9)
        fully_decayed  = impact_bps < self.DECAY_THRESHOLD_BPS
        residual_impact = entry * (impact_bps / 10_000.0)

        return {
            "impact_bps":      round(impact_bps, 4),
            "decay_factor":    round(_clamp(decay_factor, 0.0, 1.0), 4),
            "residual_impact": round(residual_impact, 4),
            "half_life_s":     round(half_life_s, 2),
            "fully_decayed":   fully_decayed,
            "elapsed_s":       round(elapsed_s, 1),
            "price_move_bps":  round(price_move_bps, 4),
            "direction":       self._direction,
        }

    def reset(self) -> None:
        """Call when position is closed."""
        self._entry_time         = None
        self._entry_price        = None
        self._initial_impact_bps = 0.0
        self._direction          = "LONG"

    def _null_state(self) -> Dict[str, Any]:
        return {
            "impact_bps":      0.0,
            "decay_factor":    0.0,
            "residual_impact": 0.0,
            "half_life_s":     0.0,
            "fully_decayed":   True,
            "elapsed_s":       0.0,
            "price_move_bps":  0.0,
            "direction":       self._direction,
        }
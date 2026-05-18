"""
Risk Model (U-09)
=================
ATR-based stop-loss + fixed-fractional position sizing.
Used by the backtest harness; never depended on by the predictor itself.
"""
from __future__ import annotations

import math
from typing import Dict


class RiskModel:
    """Compute position size, stop price, and per-trade risk."""

    def __init__(self,
                 atr_stop_mult: float = 1.5,
                 max_risk_frac: float = 0.01,
                 max_position_frac: float = 0.1):
        if not (atr_stop_mult > 0):
            raise ValueError("atr_stop_mult must be > 0")
        if not (0.0 < max_risk_frac < 1.0):
            raise ValueError("max_risk_frac must be in (0, 1)")
        if not (0.0 < max_position_frac <= 1.0):
            raise ValueError("max_position_frac must be in (0, 1]")
        self.atr_stop_mult = float(atr_stop_mult)
        self.max_risk_frac = float(max_risk_frac)
        self.max_position_frac = float(max_position_frac)

    def compute_position_size(self,
                              equity: float,
                              entry_price: float,
                              atr: float,
                              confidence: float,
                              side: str = "BUY") -> Dict[str, float]:
        equity = max(0.0, float(equity))
        entry_price = float(entry_price)
        atr = max(1e-8, float(atr))
        confidence = max(0.0, min(1.0, float(confidence)))
        side = str(side).upper()

        stop_distance = self.atr_stop_mult * atr
        if side == "BUY":
            stop_price = entry_price - stop_distance
        elif side == "SELL":
            stop_price = entry_price + stop_distance
        else:
            stop_price = entry_price - stop_distance  # default long stop

        risk_per_trade = equity * self.max_risk_frac
        size_by_risk = risk_per_trade / (stop_distance + 1e-8)

        max_size_by_capital = (
            (equity * self.max_position_frac) / max(entry_price, 1e-8)
        )

        position_size = min(size_by_risk, max_size_by_capital)
        position_size *= confidence
        if not math.isfinite(position_size) or position_size < 0.0:
            position_size = 0.0
        if not math.isfinite(stop_price) or stop_price <= 0.0:
            stop_price = max(entry_price * 0.5, 1e-6)

        assert position_size >= 0.0, "position_size must be non-negative"
        assert stop_price > 0.0, "stop_price must be positive"

        return {
            "position_size": position_size,
            "stop_price": stop_price,
            "risk_per_trade": risk_per_trade,
            "stop_distance_atr": self.atr_stop_mult,
            "side": side,
        }

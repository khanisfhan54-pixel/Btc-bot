# execution_liquidation_engine.py
"""
Unified Execution + Liquidation Engine.
Based on Exponential Decay Limit Placement + Stochastic Inventory Control.
Fail-safe: every public method catches exceptions and returns sensible defaults.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class ExecutionLiquidationEngine:
    """
    Unified Execution + Liquidation Engine.

    Pressure tiers
    ──────────────
    High   (>0.8) → AGGRESSIVE: single market order
    Medium (>0.4) → ADAPTIVE:   fewer, tighter limit levels
    Low          → PASSIVE:    multi-level exponential decay limits
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config or {}
        self.alpha        = _safe_float(cfg.get("decay_alpha", 0.5))
        self.risk_aversion = _safe_float(cfg.get("gamma", 0.1))
        self.max_impact   = _safe_float(cfg.get("max_impact", 0.05))
        self.liq_buffer   = max(1e-6, _safe_float(cfg.get("liq_buffer", 0.02)))

    # ------------------------------------------------------------------
    # Core computations
    # ------------------------------------------------------------------

    def compute_liquidation_pressure(
        self,
        mid_price: float,
        liq_price: float,
        vol_24h: float,  # kept for future vol-weighted scaling
    ) -> float:
        """Return 0 (no pressure) → 1 (at liquidation boundary)."""
        try:
            mid  = _safe_float(mid_price)
            liq  = _safe_float(liq_price)
            if mid <= 0 or liq <= 0:
                return 0.0
            distance = abs(mid - liq) / mid
            pressure = math.exp(-distance / self.liq_buffer)
            return _clamp(pressure, 0.0, 1.0)
        except Exception as exc:
            logger.debug("[LIQ_ENGINE] compute_liquidation_pressure error: %s", exc)
            return 0.0

    def compute_execution_style(
        self,
        inventory: float,
        liq_pressure: float,
        book_depth: float,
    ) -> str:
        """Return 'AGGRESSIVE' | 'ADAPTIVE' | 'PASSIVE'."""
        try:
            inv   = abs(_safe_float(inventory))
            press = _clamp(_safe_float(liq_pressure), 0.0, 1.0)
            depth = _safe_float(book_depth, 1.0)
            urgency = (inv * self.risk_aversion) + (press * 2.0)
            if urgency > 0.8 or (depth > 0 and depth < inv * 0.5):
                return "AGGRESSIVE"
            elif urgency > 0.4:
                return "ADAPTIVE"
            return "PASSIVE"
        except Exception as exc:
            logger.debug("[LIQ_ENGINE] compute_execution_style error: %s", exc)
            return "PASSIVE"

    def optimize_order_placement(
        self,
        total_v: float,
        mid: float,
        spread: float,
        n_levels: int = 5,
    ) -> List[Dict[str, Any]]:
        """Exponential-decay allocation across *n_levels* limit orders."""
        try:
            total_v = _safe_float(total_v)
            mid     = _safe_float(mid)
            spread  = max(0.0, _safe_float(spread))
            tick    = 0.5
            levels  = max(1, min(n_levels, 10))

            weights = [math.exp(-self.alpha * i) for i in range(levels)]
            total_w = sum(weights) or 1.0
            norm    = [w / total_w for w in weights]

            allocation: List[Dict[str, Any]] = []
            for i, weight in enumerate(norm):
                price = mid - (spread / 2.0) - (i * tick)
                allocation.append({
                    "price": round(price, 2),
                    "size":  round(total_v * weight, 8),
                    "type":  "LIMIT",
                })
            return allocation
        except Exception as exc:
            logger.debug("[LIQ_ENGINE] optimize_order_placement error: %s", exc)
            return [{"price": _safe_float(mid), "size": _safe_float(total_v), "type": "LIMIT"}]

    def adaptive_inventory_control(
        self,
        inventory: float,
        liq_pressure: float,
    ) -> float:
        """
        Return a target inventory after applying pressure-driven reduction.
        High pressure → reduce faster (multiplier approaches 0).
        """
        try:
            inv   = _safe_float(inventory)
            press = _clamp(_safe_float(liq_pressure), 0.0, 1.0)
            reduction = 1.0 - (press ** 2)
            return inv * _clamp(reduction, 0.0, 1.0)
        except Exception as exc:
            logger.debug("[LIQ_ENGINE] adaptive_inventory_control error: %s", exc)
            return _safe_float(inventory)

    def execute_step(
        self,
        market_data: Dict[str, Any],
        account_data: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Full step: compute pressure → style → order plan.

        market_data keys: mid, spread, vol_24h, bid_depth
        account_data keys: inventory, liq_price
        """
        try:
            mid       = _safe_float(market_data.get("mid", 0.0))
            spread    = _safe_float(market_data.get("spread", 0.0))
            vol_24h   = _safe_float(market_data.get("vol_24h", 1_000_000.0))
            bid_depth = _safe_float(market_data.get("bid_depth", 5.0))
            inventory = _safe_float(account_data.get("inventory", 0.0))
            liq_price = _safe_float(account_data.get("liq_price", mid * 0.97 if mid > 0 else 0.0))

            liq_p = self.compute_liquidation_pressure(mid, liq_price, vol_24h)
            style = self.compute_execution_style(inventory, liq_p, bid_depth)

            if style == "AGGRESSIVE":
                return [{"price": mid, "size": inventory, "type": "MARKET"}]

            n_levels = 3 if style == "ADAPTIVE" else 5
            return self.optimize_order_placement(inventory, mid, spread, n_levels)
        except Exception as exc:
            logger.warning("[LIQ_ENGINE] execute_step error (non-fatal): %s", exc)
            return []

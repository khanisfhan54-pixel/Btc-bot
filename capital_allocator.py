from __future__ import annotations

import math
from typing import Any, Dict


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert to finite float safely; return default on invalid values."""
    try:
        if value is None:
            return default
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def _clamp(x: Any, lo: float, hi: float) -> float:
    """Clamp a value to [lo, hi] with safe coercion."""
    xv = _safe_float(x, lo)
    return max(lo, min(hi, xv))


class CapitalAllocator:
    """
    Adaptive capital allocation and regime-aware risk control.

    Output contract:
        {
            "capital_scale": float,      # 0.0 -> 1.5
            "risk_per_trade": float,     # adjusted fraction
            "max_exposure": float,       # absolute cap in account currency
            "allow_trading": bool,
            "reason": str,
        }
    """

    def __init__(self, base_risk: float = 0.01) -> None:
        self.base_risk = _clamp(base_risk, 0.0, 0.05)

    def _safe_defaults(self, reason: str, account_equity: float) -> Dict[str, Any]:
        scale = 0.5
        risk_per_trade = self.base_risk * scale
        max_exposure = max(0.0, account_equity) * 0.20 * scale
        return {
            "capital_scale": float(scale),
            "risk_per_trade": float(risk_per_trade),
            "max_exposure": float(max_exposure),
            "allow_trading": False,
            "reason": reason,
        }

    def allocate(
        self,
        learning_params: Dict[str, Any],
        meta_result: Dict[str, Any],
        features: Dict[str, Any],
        current_drawdown: float,
        account_equity: float,
    ) -> Dict[str, Any]:
        learning_params = learning_params if isinstance(learning_params, dict) else {}
        meta_result = meta_result if isinstance(meta_result, dict) else {}
        features = features if isinstance(features, dict) else {}

        equity = _safe_float(account_equity, -1.0)
        drawdown = _safe_float(current_drawdown, -1.0)

        if equity < 0.0 or drawdown < 0.0 or drawdown > 1.0:
            return self._safe_defaults("failsafe: invalid equity/drawdown", max(0.0, equity))

        # Input extraction (defensive)
        win_rate = _safe_float(learning_params.get("win_rate"), 0.5)
        expectancy = _safe_float(learning_params.get("expectancy"), 0.0)

        execution_quality = _safe_float(learning_params.get("execution_quality"), 0.5)
        execution_fill_rate = _safe_float(learning_params.get("execution_fill_rate"), 1.0)
        execution_slippage = _safe_float(learning_params.get("execution_slippage"), 0.0)
        execution_latency = _safe_float(learning_params.get("execution_latency"), 0.0)
        execution_samples = _safe_float(learning_params.get("execution_samples", 0))
        has_execution_data = execution_samples > 0

        meta_scale = _safe_float(meta_result.get("risk_scale"), 1.0)
        regime = str(features.get("regime", "")).strip().lower()

        # Extreme value failsafe checks
        extreme_invalid = (
            not (0.0 <= win_rate <= 1.0)
            or not (0.0 <= execution_quality <= 1.0)
            or not (0.0 <= execution_fill_rate <= 1.0)
            or execution_slippage < 0.0
            or execution_slippage > 10_000.0
            or execution_latency < 0.0
            or execution_latency > 1_000_000.0
            or meta_scale < 0.0
            or meta_scale > 3.0
        )
        if extreme_invalid:
            return self._safe_defaults("failsafe: missing/NaN/extreme inputs", equity)

        # 1) Baseline
        scale = 1.0
        allow_trading = True
        reason_parts = ["baseline"]

        # 2) Performance factor
        if win_rate > 0.60:
            scale += 0.10
            reason_parts.append("win_rate>0.60:+0.10")
        elif win_rate < 0.40:
            scale -= 0.15
            reason_parts.append("win_rate<0.40:-0.15")

        if expectancy < -0.01:
            scale *= 0.70
            reason_parts.append("expectancy<-0.01:*0.70")

        # 3) Execution factor
        if has_execution_data:
            if execution_quality < 0.40:
                scale *= 0.70
                reason_parts.append("exec_quality<0.40:*0.70")

            if execution_fill_rate < 0.50:
                scale *= 0.80
                reason_parts.append("fill_rate<0.50:*0.80")

            if execution_slippage > 10.0:
                scale *= 0.85
                reason_parts.append("slippage>10:*0.85")

            if execution_latency > 2000.0:
                scale *= 0.85
                reason_parts.append("latency>2000:*0.85")

        # 4) Drawdown control
        if drawdown > 0.10:
            scale *= 0.70
            reason_parts.append("drawdown>10%:*0.70")

        if drawdown > 0.20:
            scale *= 0.40
            reason_parts.append("drawdown>20%:*0.40")

        if drawdown > 0.25:
            allow_trading = False
            reason_parts.append("drawdown>25%:halt")

        # 5) Regime switching
        if regime == "trend":
            scale *= 1.10
            reason_parts.append("regime=trend:*1.10")
        elif regime == "range":
            scale *= 0.90
            reason_parts.append("regime=range:*0.90")
        elif regime == "toxic":
            scale *= 0.50
            reason_parts.append("regime=toxic:*0.50")
            if has_execution_data and execution_quality < 0.60:
                allow_trading = False
                reason_parts.append("toxic+exec_quality<0.60:halt")

        if has_execution_data:
            combined_quality = (
                0.6 * _clamp(execution_quality, 0.0, 1.0)
                + 0.4 * _clamp(win_rate, 0.0, 1.0)
            )
            quality_scale = _clamp(0.8 + 0.4 * combined_quality, 0.7, 1.2)
            scale *= quality_scale
            reason_parts.append("combined_quality_scale_applied")

        # 6) Meta filter integration
        scale *= meta_scale
        reason_parts.append("meta_scale_applied")

        # 7) Final clamp
        scale = _clamp(scale, 0.0, 1.5)

        # 8) Risk per trade
        risk_per_trade = self.base_risk * scale

        # 9) Max exposure
        max_exposure = equity * 0.20 * scale

        # Final NaN/inf guard
        if any(
            math.isnan(v) or math.isinf(v)
            for v in (scale, risk_per_trade, max_exposure)
        ):
            return self._safe_defaults("failsafe: numerical instability", equity)

        return {
            "capital_scale": float(scale),
            "risk_per_trade": float(risk_per_trade),
            "max_exposure": float(max_exposure),
            "allow_trading": bool(allow_trading),
            "reason": " | ".join(reason_parts),
        }

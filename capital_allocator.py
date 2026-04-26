from __future__ import annotations

import os
from typing import Optional

import numpy as np


class CapitalAllocator:
    def __init__(self) -> None:
        self.max_risk_pct = float(os.environ.get("MAX_RISK_PCT", "0.005"))

    def allocate(
        self,
        signal_confidence: float,
        regime_context: dict,
        current_equity: float,
        max_risk_pct: Optional[float] = None,
        **_: object,
    ) -> dict:
        vals = [signal_confidence, current_equity]
        if not np.all(np.isfinite(np.asarray(vals, dtype=float))):
            raise ValueError("allocate inputs must be finite")
        effective_risk_pct = float(self.max_risk_pct if max_risk_pct is None else max_risk_pct)
        regime = str((regime_context or {}).get("regime", "RANGE")).upper()
        mult = {"TREND": 1.0, "RANGE": 0.6, "BEAR": 0.4, "TOXIC": 0.0, "HALTED": 0.0, "UNCALIBRATED": 0.0, "STALE_FALLBACK": 0.0}.get(regime, 0.5)
        risk_amount = float(current_equity) * effective_risk_pct * mult * float(signal_confidence)
        denom = max(float(current_equity) * effective_risk_pct, 1e-9)
        capital_scale = min(1.0, max(0.0, risk_amount / denom))
        return {
            "capital_scale": capital_scale,
            "position_size_usd": risk_amount,
            "risk_amount_usd": risk_amount,
            "allow_trading": capital_scale > 0.0,
            "reason": "kelly_fixed_fractional",
        }

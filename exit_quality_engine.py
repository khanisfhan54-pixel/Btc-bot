# exit_quality_engine.py
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple

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


class ExitQualityEngine:
    """
    Tracks open trade excursions (MFE / MAE) and scores each exit
    from 0.0 (worst) to 1.0 (optimal). Results feed back into
    learning_engine.py so future trades improve over time.
    """

    def __init__(self) -> None:
        self._active: bool = False
        self._side: str = "LONG"
        self._entry_price: float = 0.0
        self._entry_time: float = 0.0
        self._confidence: float = 0.0
        self._regime: str = "unknown"
        self._size: float = 0.0

        self._highest_price: float = 0.0
        self._lowest_price: float = float("inf")

        self.history: Deque[Dict[str, Any]] = deque(maxlen=250)

    def on_entry(
        self,
        side: str,
        entry_price: float,
        confidence: float = 0.0,
        regime: str = "unknown",
        size: float = 0.0,
    ) -> None:
        side = str(side or "LONG").upper()
        entry_price = _safe_float(entry_price)
        if entry_price <= 0:
            return

        self._active = True
        self._side = side
        self._entry_price = entry_price
        self._entry_time = time.time()
        self._confidence = _safe_float(confidence)
        self._regime = str(regime or "unknown").lower()
        self._size = _safe_float(size)
        self._highest_price = entry_price
        self._lowest_price = entry_price

    def update(self, current_price: float) -> None:
        if not self._active:
            return
        price = _safe_float(current_price)
        if price <= 0:
            return
        if price > self._highest_price:
            self._highest_price = price
        if price < self._lowest_price:
            self._lowest_price = price

    def on_exit(
        self,
        exit_price: float,
        reason: str = "unknown",
        regime: str = "",
        features: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self._active:
            return {}

        features = features if isinstance(features, dict) else {}
        exit_price = _safe_float(exit_price)
        reason = str(reason or "unknown").lower()
        regime = str(regime or self._regime or features.get("regime", "unknown")).lower()

        metrics = self.compute_metrics(exit_price=exit_price, reason=reason, regime=regime)
        self.history.append(metrics)
        self._active = False
        return metrics

    def compute_metrics(
        self,
        exit_price: float,
        reason: str = "unknown",
        regime: str = "unknown",
    ) -> Dict[str, Any]:
        entry = self._entry_price
        highest = self._highest_price
        lowest = self._lowest_price
        side = self._side
        holding_seconds = max(0.0, time.time() - self._entry_time)

        if entry <= 0:
            return self._empty_metrics(reason, regime, holding_seconds)

        if side == "LONG":
            mfe_price = max(0.0, highest - entry)
            mae_price = max(0.0, entry - lowest)
            realized_pnl = exit_price - entry
            peak_pnl = mfe_price
        else:
            mfe_price = max(0.0, entry - lowest)
            mae_price = max(0.0, highest - entry)
            realized_pnl = entry - exit_price
            peak_pnl = mfe_price

        mfe_pct = mfe_price / entry if entry > 0 else 0.0
        mae_pct = mae_price / entry if entry > 0 else 0.0

        if peak_pnl > 0:
            exit_efficiency = _clamp(realized_pnl / peak_pnl, -1.0, 1.0)
        else:
            exit_efficiency = 0.0 if realized_pnl >= 0 else -1.0

        exit_quality_score, exit_classification = self.score_exit(
            realized_pnl=realized_pnl,
            mfe_price=mfe_price,
            mae_price=mae_price,
            peak_pnl=peak_pnl,
            exit_efficiency=exit_efficiency,
        )

        return {
            "mfe_price": round(mfe_price, 8),
            "mfe_pct": round(mfe_pct, 8),
            "mae_price": round(mae_price, 8),
            "mae_pct": round(mae_pct, 8),
            "realized_pnl": round(realized_pnl, 8),
            "peak_pnl": round(peak_pnl, 8),
            "exit_efficiency": round(exit_efficiency, 6),
            "exit_quality_score": round(exit_quality_score, 6),
            "exit_classification": exit_classification,
            "holding_seconds": round(holding_seconds, 3),
            "side": side,
            "reason": reason,
            "regime": regime,
            "entry_price": round(entry, 8),
            "exit_price": round(exit_price, 8),
            "confidence": round(self._confidence, 6),
            "size": round(self._size, 8),
        }

    def score_exit(
        self,
        realized_pnl: float,
        mfe_price: float,
        mae_price: float,
        peak_pnl: float,
        exit_efficiency: float,
    ) -> Tuple[float, str]:
        """
        Returns (exit_quality_score: float 0..1, exit_classification: str).

        Components:
          40% — how much of the MFE move was captured (efficiency)
          30% — MFE dominance over MAE (risk quality)
          30% — sign of realized PnL
        """
        eff_score = _clamp((exit_efficiency + 1.0) / 2.0, 0.0, 1.0) * 0.40

        if mfe_price + mae_price > 0:
            mfe_fraction = mfe_price / (mfe_price + mae_price)
        else:
            mfe_fraction = 0.5
        risk_ratio_score = _clamp(mfe_fraction, 0.0, 1.0) * 0.30

        pnl_score = 0.30 if realized_pnl >= 0 else 0.0

        exit_quality_score = _clamp(eff_score + risk_ratio_score + pnl_score, 0.0, 1.0)

        if peak_pnl <= 0:
            classification = "optimal" if realized_pnl >= 0 else "poor"
        elif exit_efficiency >= 0.75:
            classification = "optimal"
        elif exit_efficiency >= 0.40:
            classification = "early" if realized_pnl < peak_pnl * 0.85 else "optimal"
        elif exit_efficiency >= 0.0:
            classification = "late" if mae_price > mfe_price * 0.5 else "early"
        else:
            classification = "poor"

        return exit_quality_score, classification

    def get_state(self) -> Dict[str, Any]:
        return {
            "active": self._active,
            "side": self._side,
            "entry_price": self._entry_price,
            "entry_time": self._entry_time,
            "confidence": self._confidence,
            "regime": self._regime,
            "size": self._size,
            "highest_price": self._highest_price,
            "lowest_price": self._lowest_price,
        }

    def _empty_metrics(
        self,
        reason: str,
        regime: str,
        holding_seconds: float,
    ) -> Dict[str, Any]:
        return {
            "mfe_price": 0.0,
            "mfe_pct": 0.0,
            "mae_price": 0.0,
            "mae_pct": 0.0,
            "realized_pnl": 0.0,
            "peak_pnl": 0.0,
            "exit_efficiency": 0.0,
            "exit_quality_score": 0.0,
            "exit_classification": "poor",
            "holding_seconds": round(holding_seconds, 3),
            "side": self._side,
            "reason": reason,
            "regime": regime,
            "entry_price": 0.0,
            "exit_price": 0.0,
            "confidence": round(self._confidence, 6),
            "size": round(self._size, 8),
        }


EXIT_QUALITY_ENGINE = ExitQualityEngine()

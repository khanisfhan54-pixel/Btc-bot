# signal_engine.py
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def _safe_get(d: Any, key: str, default: Any = None) -> Any:
    return d.get(key, default) if isinstance(d, dict) else default


def _clamp(x: float, low: float, high: float) -> float:
    return max(low, min(high, x))


class SignalEngine:

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}

    # ------------------------------------------------------------------
    # Primary entry point (new production API)
    # ------------------------------------------------------------------
    def generate_signal(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        features = kwargs.get("features") or (args[0] if args else {})
        if not isinstance(features, dict):
            return {"action": "HOLD", "signal": "HOLD", "confidence": 0.0, "score": 0, "reasons": []}

        liquidity = features.get("liquidity", {})
        regime    = features.get("regime", {})
        candles   = features.get("candles", [])
        volume    = features.get("volume", 0)

        if not candles or len(candles) < 3:
            return {"action": "HOLD", "signal": "HOLD", "confidence": 0.0, "score": 0, "reasons": []}

        last = candles[-1]
        prev = candles[-2]

        last_close = last.get("close", 0)
        prev_close = prev.get("close", 0)

        # ── 1. Liquidity sweep signal ──────────────────────────────────
        stop_hunt  = liquidity.get("stop_hunt", False)
        sweep_side = liquidity.get("stop_hunt_side")

        # ── 2. Displacement ────────────────────────────────────────────
        body   = abs(last_close - last.get("open", last_close))
        range_ = abs(last.get("high", last_close) - last.get("low", last_close))

        displacement = body / range_ if range_ > 0 else 0.0
        strong_displacement = displacement > 0.6

        # ── 3. Volume confirmation ─────────────────────────────────────
        avg_vol = sum(c.get("volume", 0) for c in candles[-10:]) / 10
        vol_score = _clamp(volume / avg_vol if avg_vol > 0 else 1.0, 0.0, 2.0)
        volume_confirmed = vol_score > 1.2

        # ── 4. Regime ──────────────────────────────────────────────────
        regime_type = regime.get("regime", "range") if isinstance(regime, dict) else str(regime)

        # ── Signal logic ───────────────────────────────────────────────
        reasons: List[str] = []
        base: Dict[str, Any] = {}

        if stop_hunt and strong_displacement:
            # Liquidity sweep reversal
            if sweep_side == "sell":
                side = "buy"
            elif sweep_side == "buy":
                side = "sell"
            else:
                side = "buy"

            base = {
                "type":       "liquidity_sweep_reversal",
                "side":       side,
                "entry_type": "reversal",
            }
            reasons += ["stop_hunt", "displacement"]

        elif regime_type == "trend" and strong_displacement and volume_confirmed:
            # Momentum breakout
            side = "buy" if last_close > prev_close else "sell"
            base = {
                "type":       "momentum_breakout",
                "side":       side,
                "entry_type": "continuation",
            }
            reasons += ["trend", "momentum"]

        else:
            return {"action": "HOLD", "signal": "HOLD", "confidence": 0.0, "score": 0, "reasons": []}

        # ── 5. Confidence model ────────────────────────────────────────
        liquidity_score    = 1.0 if stop_hunt else 0.4
        displacement_score = displacement
        volume_score_      = vol_score / 2.0
        regime_score       = 1.0 if regime_type == "trend" else 0.7

        confidence = (
            0.30 * liquidity_score
            + 0.30 * displacement_score
            + 0.20 * volume_score_
            + 0.20 * regime_score
        )
        confidence = _clamp(confidence, 0.05, 0.95)

        # ── Final return ───────────────────────────────────────────────
        signal_str = "LONG" if base.get("side") == "buy" else "SHORT"
        return {
            **base,
            "action":     signal_str,
            "signal":     signal_str,
            "confidence": confidence,
            "score":      int(confidence * 100),
            "reasons":    reasons,
        }

    # ------------------------------------------------------------------
    # Backward-compatible shim — main.py calls .generate(feat_dict)
    # and expects {"signal": "LONG"/"SHORT"/"HOLD", "confidence": float,
    #              "reason": str}
    # ------------------------------------------------------------------
    def generate(self, features_payload: Any = None, **kwargs: Any) -> Dict[str, Any]:
        features = (
            features_payload.get("features", features_payload)
            if isinstance(features_payload, dict)
            else (kwargs.get("features") or {})
        )
        result = self.generate_signal(features=features)

        signal = result.get("signal", "HOLD")
        if signal not in ("LONG", "SHORT"):
            signal = "HOLD"

        return {
            **result,
            "signal":     signal,
            "confidence": result.get("confidence", 0.0),
            "reason":     ", ".join(result.get("reasons", [])) or "HOLD",
        }

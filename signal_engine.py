# signal_engine.py
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _safe_get(d: Any, key: str, default: Any = None) -> Any:
    return d.get(key, default) if isinstance(d, dict) else default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _clamp(x: float, low: float, high: float) -> float:
    return max(low, min(high, x))


def _normalize_candle(candle: Any) -> Optional[Dict[str, float]]:
    if isinstance(candle, dict):
        nested = _safe_get(candle, "candle") or _safe_get(candle, "kline")
        if isinstance(nested, dict):
            candle = nested

        o = _safe_float(candle.get("open", candle.get("o", candle.get("Open", 0.0))), 0.0)
        h = _safe_float(candle.get("high", candle.get("h", candle.get("High", 0.0))), 0.0)
        l = _safe_float(candle.get("low", candle.get("l", candle.get("Low", 0.0))), 0.0)
        c = _safe_float(candle.get("close", candle.get("c", candle.get("Close", 0.0))), 0.0)
        v = _safe_float(candle.get("volume", candle.get("v", candle.get("Volume", 0.0))), 0.0)
        return {"open": o, "high": h, "low": l, "close": c, "volume": v}

    if isinstance(candle, (list, tuple)) and len(candle) >= 6:
        return {
            "open": _safe_float(candle[1], 0.0),
            "high": _safe_float(candle[2], 0.0),
            "low": _safe_float(candle[3], 0.0),
            "close": _safe_float(candle[4], 0.0),
            "volume": _safe_float(candle[5], 0.0),
        }

    return None


def _normalize_candles(raw_candles: Any) -> List[Dict[str, float]]:
    source = raw_candles

    if isinstance(source, dict):
        for key in ("candles", "ohlcv", "rows", "data", "klines", "1m", "15m", "primary"):
            candidate = source.get(key)
            if isinstance(candidate, (list, tuple)) and candidate:
                source = candidate
                break
        else:
            source = []

    if not isinstance(source, (list, tuple)):
        return []

    normalized: List[Dict[str, float]] = []
    for c in source:
        nc = _normalize_candle(c)
        if nc is not None:
            normalized.append(nc)
    return normalized


def _extract_regime_type(features: Dict[str, Any]) -> str:
    regime = _safe_get(features, "regime", {})
    market_state = _safe_get(features, "market_state", {})

    if isinstance(regime, dict):
        value = regime.get("regime")
        if value is None:
            value = regime.get("type")
        if value is None:
            value = regime.get("state")
        if value is not None:
            return str(value).lower()
    elif regime not in (None, ""):
        return str(regime).lower()

    if isinstance(market_state, dict):
        value = market_state.get("type")
        if value is None:
            value = market_state.get("state")
        if value is not None:
            return str(value).lower()
    elif market_state not in (None, ""):
        return str(market_state).lower()

    return "range"


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

        liquidity = _safe_get(features, "liquidity", {})
        raw_candles = _safe_get(features, "candles", _safe_get(features, "ohlcv", []))
        candles = _normalize_candles(raw_candles)

        if len(candles) < 3:
            return {"action": "HOLD", "signal": "HOLD", "confidence": 0.0, "score": 0, "reasons": []}

        last = candles[-1]
        prev = candles[-2]

        last_close = _safe_float(last.get("close", 0.0), 0.0)
        prev_close = _safe_float(prev.get("close", 0.0), 0.0)

        # ── 1. Liquidity sweep signal ──────────────────────────────────
        stop_hunt_raw = _safe_get(liquidity, "stop_hunt", None)
        if stop_hunt_raw is None:
            stop_hunt_raw = _safe_get(features, "stop_hunt", None)
        if isinstance(stop_hunt_raw, dict):
            stop_hunt = bool(_safe_get(stop_hunt_raw, "stop_hunt", _safe_get(stop_hunt_raw, "detected", False)))
        elif stop_hunt_raw is None:
            stop_hunt = bool(_safe_get(features, "stop_hunt_detected", False))
        else:
            stop_hunt = bool(stop_hunt_raw)

        sweep_side = _safe_get(liquidity, "stop_hunt_side", None)
        if sweep_side is None:
            sweep_side = _safe_get(features, "stop_hunt_side", None)
        if sweep_side is None:
            sweep_side = _safe_get(_safe_get(features, "liquidity_sweep", {}), "side", None)
        if sweep_side is None:
            stop_hunt_payload = _safe_get(features, "stop_hunt", None)
            if isinstance(stop_hunt_payload, dict):
                sweep_side = _safe_get(stop_hunt_payload, "side", None)
                if sweep_side is None:
                    sweep_side = _safe_get(stop_hunt_payload, "direction", None)

        # ── 2. Displacement ────────────────────────────────────────────
        body = abs(last_close - _safe_float(last.get("open", last_close), last_close))
        range_ = abs(_safe_float(last.get("high", last_close), last_close) - _safe_float(last.get("low", last_close), last_close))

        displacement = body / range_ if range_ > 0 else 0.0
        strong_displacement = displacement > 0.6

        # ── 3. Volume confirmation ─────────────────────────────────────
        recent = candles[-10:]
        sample_count = len(recent)
        avg_vol = (sum(_safe_float(c.get("volume", 0.0), 0.0) for c in recent) / sample_count) if sample_count > 0 else 0.0

        feature_volume = _safe_float(_safe_get(features, "volume", 0.0), 0.0)
        if feature_volume <= 0:
            feature_volume = _safe_float(last.get("volume", 0.0), 0.0)

        vol_score = _clamp(feature_volume / avg_vol if avg_vol > 0 else 1.0, 0.0, 2.0)
        volume_confirmed = vol_score > 1.2

        # ── 4. Regime ──────────────────────────────────────────────────
        regime_type = _extract_regime_type(features)

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
                "type": "liquidity_sweep_reversal",
                "side": side,
                "entry_type": "reversal",
            }
            reasons += ["stop_hunt", "displacement"]

        elif regime_type == "trend" and strong_displacement and volume_confirmed:
            # Momentum breakout
            side = "buy" if last_close > prev_close else "sell"
            base = {
                "type": "momentum_breakout",
                "side": side,
                "entry_type": "continuation",
            }
            reasons += ["trend", "momentum"]

        else:
            return {"action": "HOLD", "signal": "HOLD", "confidence": 0.0, "score": 0, "reasons": []}

        # ── 5. Confidence model ────────────────────────────────────────
        liquidity_score = 1.0 if stop_hunt else 0.4
        displacement_score = displacement
        volume_score_ = vol_score / 2.0
        regime_score = 1.0 if regime_type == "trend" else 0.7

        confidence = (
            0.30 * liquidity_score
            + 0.30 * displacement_score
            + 0.20 * volume_score_
            + 0.20 * regime_score
        )

        confluence_score = _clamp(_safe_float(_safe_get(features, "confluence_score", 0.0), 0.0), 0.0, 10.0)
        institutional_score = _clamp(_safe_float(_safe_get(features, "institutional_score", 0.0), 0.0), 0.0, 10.0)

        confluence_mod = 0.90 + 0.20 * (confluence_score / 10.0)
        institutional_mod = 0.90 + 0.20 * (institutional_score / 10.0)
        confidence *= confluence_mod * institutional_mod

        confidence = _clamp(confidence, 0.05, 0.95)

        # ── Final return ───────────────────────────────────────────────
        signal_str = "LONG" if base.get("side") == "buy" else "SHORT"
        return {
            **base,
            "action": signal_str,
            "signal": signal_str,
            "confidence": confidence,
            "score": int(confidence * 100),
            "reasons": reasons,
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
            "signal": signal,
            "confidence": result.get("confidence", 0.0),
            "reason": ", ".join(result.get("reasons", [])) or "HOLD",
        }

# liquidity_hunting_engine.py
"""
LiquidityHuntingEngine — detects stop hunts, liquidity zones, spoofing, and OFI.
Accepts a plain features dict. Returns a plain dict. Never crashes.
"""
from __future__ import annotations

import logging
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


class LiquidityHuntingEngine:
    """
    Detects institutional liquidity-hunting patterns and generates
    entry timing signals.

    hunt_signal values
    ──────────────────
    "LONG_HUNT"  — stop hunt below key lows; expect long bounce
    "SHORT_HUNT" — stop hunt above key highs; expect short reversal
    "NONE"       — no actionable hunt pattern detected
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config or {}
        self.wick_ratio_threshold  = _safe_float(cfg.get("wick_ratio_threshold", 0.60))
        self.volume_spike_ratio    = _safe_float(cfg.get("volume_spike_ratio",   2.5))
        self.ofi_threshold         = _safe_float(cfg.get("ofi_threshold",        0.30))
        self.spoof_ratio           = _safe_float(cfg.get("spoof_ratio",          3.0))

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def detect_liquidity_zones(self, features: Dict[str, Any]) -> Dict[str, Any]:
        """Approximate equal-high / equal-low liquidity clusters."""
        try:
            high  = _safe_float(features.get("high"))
            low   = _safe_float(features.get("low"))
            close = _safe_float(features.get("close"))
            swing_highs = features.get("swing_highs") or []
            swing_lows  = features.get("swing_lows")  or []

            ask_wall = _safe_float(features.get("liquidity_ask_wall") or
                                   (swing_highs[0] if swing_highs else high))
            bid_wall = _safe_float(features.get("liquidity_bid_wall") or
                                   (swing_lows[0]  if swing_lows  else low))

            near_ask = high > 0 and close > 0 and abs(ask_wall - close) / close < 0.003
            near_bid = low  > 0 and close > 0 and abs(bid_wall - close) / close < 0.003

            return {
                "ask_wall": ask_wall,
                "bid_wall": bid_wall,
                "near_ask_liquidity": near_ask,
                "near_bid_liquidity": near_bid,
            }
        except Exception as exc:
            logger.debug("[HUNT] detect_liquidity_zones error: %s", exc)
            return {"ask_wall": 0.0, "bid_wall": 0.0,
                    "near_ask_liquidity": False, "near_bid_liquidity": False}

    def detect_stop_hunt(self, features: Dict[str, Any]) -> Dict[str, Any]:
        """
        Stop hunt = large wick + volume spike + fast reversal.
        Returns direction ('long', 'short', 'none') and a strength score.
        """
        try:
            open_  = _safe_float(features.get("open"))
            high   = _safe_float(features.get("high"))
            low    = _safe_float(features.get("low"))
            close  = _safe_float(features.get("close"))
            volume = _safe_float(features.get("volume"))
            avg_vol = _safe_float(features.get("avg_volume") or
                                  features.get("baseline_volume"), 1.0)

            if open_ <= 0 or high <= low:
                return {"detected": False, "direction": "none", "strength": 0.0}

            candle_range = high - low
            body_size    = abs(close - open_)
            body_ratio   = body_size / candle_range if candle_range > 0 else 1.0

            lower_wick = min(open_, close) - low
            upper_wick = high - max(open_, close)

            lower_wick_ratio = lower_wick / candle_range if candle_range > 0 else 0.0
            upper_wick_ratio = upper_wick / candle_range if candle_range > 0 else 0.0

            vol_spike = volume / avg_vol if avg_vol > 0 else 1.0
            has_vol_spike = vol_spike >= self.volume_spike_ratio

            # Long stop hunt: big lower wick (swept lows) + reversal upward
            long_hunt = (
                lower_wick_ratio >= self.wick_ratio_threshold
                and close > open_
                and has_vol_spike
            )
            # Short stop hunt: big upper wick (swept highs) + reversal downward
            short_hunt = (
                upper_wick_ratio >= self.wick_ratio_threshold
                and close < open_
                and has_vol_spike
            )

            if long_hunt:
                strength = _clamp(lower_wick_ratio * (vol_spike / self.volume_spike_ratio), 0.0, 1.0)
                return {"detected": True, "direction": "long", "strength": round(strength, 4)}
            if short_hunt:
                strength = _clamp(upper_wick_ratio * (vol_spike / self.volume_spike_ratio), 0.0, 1.0)
                return {"detected": True, "direction": "short", "strength": round(strength, 4)}

            return {"detected": False, "direction": "none", "strength": 0.0}
        except Exception as exc:
            logger.debug("[HUNT] detect_stop_hunt error: %s", exc)
            return {"detected": False, "direction": "none", "strength": 0.0}

    def compute_ofi(self, features: Dict[str, Any]) -> float:
        """Order Flow Imbalance: bid_volume - ask_volume, normalised to [-1, 1]."""
        try:
            bid_vol = _safe_float(features.get("bid_volume") or
                                  features.get("buy_notional") or
                                  features.get("aggressive_buy_usd"))
            ask_vol = _safe_float(features.get("ask_volume") or
                                  features.get("sell_notional") or
                                  features.get("aggressive_sell_usd"))
            total = bid_vol + ask_vol
            if total <= 0:
                return 0.0
            return _clamp((bid_vol - ask_vol) / total, -1.0, 1.0)
        except Exception as exc:
            logger.debug("[HUNT] compute_ofi error: %s", exc)
            return 0.0

    def detect_spoofing(self, features: Dict[str, Any]) -> bool:
        """
        Spoofing heuristic: uses the explicit flag from the feature engine
        (which has access to real order book cancellation data),
        OR detects a large imbalance in RESTING orderbook quantities
        (bids_vol / asks_vol) which signals potential layering.
        Trade volume (buy_notional / sell_notional) is intentionally
        excluded — a high buy/sell volume ratio is bullish pressure, not spoofing.
        """
        try:
            if bool(features.get("spoof_detected") or features.get("spoofing_detected")):
                return True
            # Only use resting orderbook quantities (not trade volumes)
            bid_vol = _safe_float(features.get("bids_vol") or features.get("bid_volume"))
            ask_vol = _safe_float(features.get("asks_vol") or features.get("ask_volume"))
            if bid_vol > 0 and ask_vol > 0:
                ratio = max(bid_vol, ask_vol) / min(bid_vol, ask_vol)
                return ratio >= self.spoof_ratio
            return False
        except Exception as exc:
            logger.debug("[HUNT] detect_spoofing error: %s", exc)
            return False

    def detect_liquidation_cascade(self, features: Dict[str, Any]) -> Dict[str, Any]:
        """
        Score proximity to liquidation zones + volatility + volume spike.
        cascade_score > 0.6 → cascade_detected.
        """
        try:
            price      = _safe_float(features.get("price") or features.get("close"))
            liq_levels = features.get("liquidation_levels") or []
            volatility = _safe_float(features.get("volatility"))
            volume     = _safe_float(features.get("volume"))
            avg_vol    = _safe_float(features.get("avg_volume") or
                                     features.get("baseline_volume"), 1.0)

            cascade_score = 0.0
            for lvl in liq_levels:
                distance = abs(price - _safe_float(lvl)) / max(price, 1.0)
                if distance < 0.003:
                    cascade_score += 0.4

            if volatility > 0.02:
                cascade_score += 0.3
            if avg_vol > 0 and volume > avg_vol * 2:
                cascade_score += 0.3

            cascade_score = _clamp(cascade_score, 0.0, 1.0)
            return {
                "cascade_detected": cascade_score > 0.6,
                "cascade_score":    round(cascade_score, 6),
            }
        except Exception as exc:
            logger.debug("[HUNT] detect_liquidation_cascade error: %s", exc)
            return {"cascade_detected": False, "cascade_score": 0.0}

    def detect_stop_run_stack(self, features: Dict[str, Any]) -> Dict[str, Any]:
        """
        Count how many liquidity levels are within 0.2% of current price.
        hits >= 2 → stacked stop run detected.
        """
        try:
            price  = _safe_float(features.get("price") or features.get("close"))
            levels = features.get("liquidity_levels") or []
            hits   = 0
            for lvl in levels:
                if abs(price - _safe_float(lvl)) / max(price, 1.0) < 0.002:
                    hits += 1
            return {
                "stack_detected": hits >= 2,
                "stack_count":    hits,
            }
        except Exception as exc:
            logger.debug("[HUNT] detect_stop_run_stack error: %s", exc)
            return {"stack_detected": False, "stack_count": 0}

    def generate_hunt_signal(self, features: Dict[str, Any]) -> Dict[str, Any]:
        """
        Master method: combine all sub-detectors into one hunt signal.

        Returns
        -------
        {
            "hunt_signal": "LONG_HUNT" | "SHORT_HUNT" | "NONE",
            "liquidity_zones": dict,
            "stop_hunt_detected": bool,
            "spoofing": bool,
            "ofi": float,
            "confidence": float,
        }
        """
        try:
            zones    = self.detect_liquidity_zones(features)
            hunt     = self.detect_stop_hunt(features)
            ofi      = self.compute_ofi(features)
            spoofing = self.detect_spoofing(features)
            cascade  = self.detect_liquidation_cascade(features)
            stack    = self.detect_stop_run_stack(features)

            hunt_signal = "NONE"
            confidence  = 0.0

            if hunt["detected"] and not spoofing:
                if hunt["direction"] == "long":
                    hunt_signal = "LONG_HUNT"
                    confidence  = _clamp(
                        hunt["strength"] * 0.60
                        + _clamp(ofi, 0.0, 1.0) * 0.25
                        + (0.15 if zones["near_bid_liquidity"] else 0.0),
                        0.0, 1.0,
                    )
                elif hunt["direction"] == "short":
                    hunt_signal = "SHORT_HUNT"
                    confidence  = _clamp(
                        hunt["strength"] * 0.60
                        + _clamp(-ofi, 0.0, 1.0) * 0.25
                        + (0.15 if zones["near_ask_liquidity"] else 0.0),
                        0.0, 1.0,
                    )

            # Cascade confirmation lifts confidence
            confidence = _clamp(
                confidence + (0.3 if cascade["cascade_detected"] else 0.0),
                0.0, 1.0,
            )

            logger.debug(
                "[HUNT] signal=%s confidence=%.3f stop_hunt=%s ofi=%.3f spoof=%s "
                "cascade=%.2f stack=%d",
                hunt_signal, confidence, hunt["detected"], ofi, spoofing,
                cascade["cascade_score"], stack["stack_count"],
            )

            return {
                "hunt_signal":        hunt_signal,
                "liquidity_zones":    zones,
                "stop_hunt_detected": bool(hunt["detected"]),
                "spoofing":           bool(spoofing),
                "ofi":                round(ofi, 6),
                "confidence":         round(confidence, 6),
                "cascade":            cascade,
                "stack":              stack,
            }
        except Exception as exc:
            logger.warning("[HUNT] generate_hunt_signal error (non-fatal): %s", exc)
            return {
                "hunt_signal": "NONE", "liquidity_zones": {},
                "stop_hunt_detected": False, "spoofing": False,
                "ofi": 0.0, "confidence": 0.0,
                "cascade": {"cascade_detected": False, "cascade_score": 0.0},
                "stack":   {"stack_detected": False, "stack_count": 0},
            }

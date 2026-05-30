from __future__ import annotations

import logging
import math
import copy
from collections import deque
from typing import Any, Dict, List, Optional, Tuple, TypedDict, Union

logger = logging.getLogger(__name__)

class ZoneCandidate(TypedDict):
    price: float
    side: str
    type: str
    score: float
    age: float
    distance: float
    memory_effect: float
    volume_effect: float
    regime_effect: float

class MagnetPrediction(TypedDict):
    zone_side: str
    target_price: float
    zone_type: str
    score: float
    confidence: float
    probability: float
    components: Dict[str, float]
    memory_state: Dict[str, Any]
    horizon_bars: int
    should_trade: bool
    warnings: List[str]
    candidate_zones: List[ZoneCandidate]


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        f = float(x)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default

class LiquidityMagnetPredictor:
    """
    Predictive module that forecasts likely liquidity magnet zones before
    the sweep happens. Uses time decay, volume weighting, sweep history memory,
    and regime context.
    """
    def __init__(
        self,
        memory_maxlen: int = 100,
        decay_half_life: float = 24.0, # bars
        atr_distance_scale: float = 2.0,
    ):
        self.memory_maxlen = memory_maxlen
        self.decay_half_life = _safe_float(decay_half_life, 24.0)
        self.atr_distance_scale = _safe_float(atr_distance_scale, 2.0)

        # Track historical zone touches, sweeps, rejections
        # key: str (hash or price/side string)
        # value: dict of state
        self.zone_memory: Dict[str, Dict[str, Any]] = {}
        # Keeps bounded size for zone keys
        self._zone_keys: deque = deque(maxlen=self.memory_maxlen)

    def _get_zone_key(self, price: float, side: str, zone_type: str) -> str:
        return f"{side}_{zone_type}_{price:.2f}"

    def update_memory(self, price: float, side: str, zone_type: str, interaction: str, time: float) -> None:
        """
        Interaction types: "touch", "rejection", "sweep", "breakout"
        """
        key = self._get_zone_key(price, side, zone_type)
        if key not in self.zone_memory:
            self.zone_memory[key] = {
                "touches": 0,
                "rejections": 0,
                "sweeps": 0,
                "breakouts": 0,
                "last_interaction_time": time,
                "last_outcome": "none"
            }
            if len(self.zone_memory) >= self.memory_maxlen:
                oldest = self._zone_keys.popleft()
                self.zone_memory.pop(oldest, None)
            self._zone_keys.append(key)

        state = self.zone_memory[key]
        if interaction in ["touch", "rejection", "sweep", "breakout"]:
            key_name = interaction + ("es" if interaction == "touch" else "s")
            state[key_name] += 1
            state["last_interaction_time"] = time
            state["last_outcome"] = interaction

    def get_memory_state(self, price: float, side: str, zone_type: str) -> Dict[str, Any]:
        key = self._get_zone_key(price, side, zone_type)
        if key in self.zone_memory:
            return copy.deepcopy(self.zone_memory[key])
        return {
            "touches": 0,
            "rejections": 0,
            "sweeps": 0,
            "breakouts": 0,
            "last_interaction_time": 0.0,
            "last_outcome": "none"
        }

    def _score_distance(self, price: float, current_price: float, atr: float) -> float:
        dist = abs(price - current_price)
        if atr <= 0.0:
            # Fallback to % distance if no ATR
            dist_pct = dist / max(current_price, 1e-8)
            # If dist_pct is 0, score is 1.0. If dist is 5%, score is ~e^-5
            return math.exp(-dist_pct * 100.0)

        scaled_dist = dist / (atr * self.atr_distance_scale)
        return math.exp(-scaled_dist)

    def _score_time_decay(self, age_bars: float) -> float:
        if age_bars <= 0:
            return 1.0
        return math.exp(-math.log(2) * age_bars / max(self.decay_half_life, 1e-8))

    def _score_volume(self, candidate: Dict[str, Any], volume_intel: Dict[str, Any]) -> float:
        vol_node_strength = _safe_float(candidate.get("volume_node_strength", 0.0))
        absorption = _safe_float(candidate.get("absorption", 0.0))
        liq_score = _safe_float(volume_intel.get("liquidity_score", 1.0))

        # Base weight 1.0, boosted by volume properties
        weight = 1.0 + (vol_node_strength * 0.5) + (absorption * 0.3)
        weight *= max(0.5, min(2.0, liq_score))
        return min(weight, 3.0)

    def _score_memory(self, price: float, side: str, zone_type: str) -> float:
        mem = self.get_memory_state(price, side, zone_type)
        score = 1.0

        # Multiple touches typically weaken a level, making it more likely to break (attract)
        score += mem["touches"] * 0.1

        # Prior sweeps make it slightly less attractive to sweep again immediately
        score -= mem["sweeps"] * 0.2

        # Rejections make it a stronger barrier
        score += mem["rejections"] * 0.15

        # Ensure it doesn't go negative
        return max(0.1, min(score, 2.0))

    def _score_regime(self, market_state: Dict[str, Any], side: str) -> float:
        regime = market_state.get("regime", "normal").lower()
        volatility = _safe_float(market_state.get("volatility", 1.0))

        weight = 1.0
        if "toxic" in regime or "illiquid" in regime:
            weight = 0.5
        elif "trending" in regime:
            trend_dir = market_state.get("trend_direction", "none")
            if (trend_dir == "up" and side == "above") or (trend_dir == "down" and side == "below"):
                weight = 1.2 # Continuation sweeps
            else:
                weight = 0.8

        if volatility > 2.0:
            weight *= 0.8 # Highly volatile, less predictable

        return max(0.1, min(weight, 2.0))

    def _score_stop_hunt(self, stop_hunt_data: Dict[str, Any], side: str) -> float:
        p_sweep = _safe_float(stop_hunt_data.get("probability", 0.5))
        # Adjust weight based on probability
        # 0.5 -> 1.0, 1.0 -> 1.5, 0.0 -> 0.5
        weight = 0.5 + p_sweep
        return max(0.5, min(weight, 1.5))

    def predict(
        self,
        candidates: List[Dict[str, Any]],
        current_price: float,
        current_time: float,
        market_state: Dict[str, Any],
        stop_hunt_data: Optional[Dict[str, Any]] = None,
        volume_intel: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate candidates and predict the most likely liquidity magnet.
        """
        safe_candidates = candidates if isinstance(candidates, list) else []
        safe_market_state = market_state if isinstance(market_state, dict) else {}
        safe_stop_hunt = stop_hunt_data if isinstance(stop_hunt_data, dict) else {}
        safe_vol = volume_intel if isinstance(volume_intel, dict) else {}

        if not safe_candidates:
            return self._empty_prediction()

        atr = _safe_float(safe_market_state.get("atr", 0.0))

        scored_candidates: List[ZoneCandidate] = []
        for cand in safe_candidates:
            if not isinstance(cand, dict):
                continue

            price = _safe_float(cand.get("price", 0.0))
            if price <= 0.0:
                continue

            side = cand.get("side", "unknown")
            zone_type = cand.get("type", "unknown")
            age_bars = _safe_float(cand.get("age_bars", 0.0))
            base_strength = _safe_float(cand.get("base_strength", 1.0))

            dist_wt = self._score_distance(price, current_price, atr)
            time_wt = self._score_time_decay(age_bars)
            vol_wt = self._score_volume(cand, safe_vol)
            mem_wt = self._score_memory(price, side, zone_type)
            regime_wt = self._score_regime(safe_market_state, side)
            sh_wt = self._score_stop_hunt(safe_stop_hunt, side)

            # Stop hunt engine data quality weight
            degraded = safe_stop_hunt.get("degraded", False)
            dq_wt = 0.5 if degraded else 1.0

            final_score = base_strength * dist_wt * time_wt * vol_wt * mem_wt * regime_wt * sh_wt * dq_wt
            if math.isnan(final_score) or math.isinf(final_score):
                final_score = 0.0

            scored_candidates.append({
                "price": price,
                "side": side,
                "type": zone_type,
                "score": final_score,
                "age": age_bars,
                "distance": abs(price - current_price),
                "memory_effect": mem_wt,
                "volume_effect": vol_wt,
                "regime_effect": regime_wt,
            })

        if not scored_candidates:
            return self._empty_prediction()

        scored_candidates.sort(key=lambda x: x["score"], reverse=True)
        top_cand = scored_candidates[0]

        score = top_cand["score"]
        confidence = min(1.0, score / 10.0) # Arbitrary scaling, max 1.0
        should_trade = confidence > 0.4 and not safe_stop_hunt.get("degraded", False)

        # Calculate roughly how many bars away this might be based on ATR
        horizon_bars = 0
        if atr > 0:
            horizon_bars = int(top_cand["distance"] / (atr * 0.5))

        return {
            "zone_side": top_cand["side"],
            "target_price": top_cand["price"],
            "zone_type": top_cand["type"],
            "score": score,
            "confidence": confidence,
            "probability": min(1.0, confidence * 0.8),
            "components": {
                "memory_effect": top_cand["memory_effect"],
                "volume_effect": top_cand["volume_effect"],
                "regime_effect": top_cand["regime_effect"],
            },
            "memory_state": self.get_memory_state(top_cand["price"], top_cand["side"], top_cand["type"]),
            "horizon_bars": max(1, horizon_bars),
            "should_trade": should_trade,
            "warnings": ["degraded_inputs"] if safe_stop_hunt.get("degraded", False) else [],
            "candidate_zones": scored_candidates
        }

    def _empty_prediction(self) -> Dict[str, Any]:
        return {
            "zone_side": "none",
            "target_price": 0.0,
            "zone_type": "none",
            "score": 0.0,
            "confidence": 0.0,
            "probability": 0.0,
            "components": {},
            "memory_state": {},
            "horizon_bars": 0,
            "should_trade": False,
            "warnings": ["empty_candidates"],
            "candidate_zones": []
        }


def predict_liquidity_magnet(
    candidates: List[Dict[str, Any]],
    current_price: float,
    current_time: float,
    market_state: Dict[str, Any],
    stop_hunt_data: Optional[Dict[str, Any]] = None,
    volume_intel: Optional[Dict[str, Any]] = None,
    predictor_instance: Optional[LiquidityMagnetPredictor] = None,
) -> Dict[str, Any]:
    """
    Predict which liquidity magnet is likely to attract price next.
    Returns a dictionary matching MagnetPrediction TypedDict keys.
    """
    inst = predictor_instance if predictor_instance is not None else LiquidityMagnetPredictor()
    return inst.predict(
        candidates=candidates,
        current_price=current_price,
        current_time=current_time,
        market_state=market_state,
        stop_hunt_data=stop_hunt_data,
        volume_intel=volume_intel
    )

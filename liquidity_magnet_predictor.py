from __future__ import annotations

import copy
import logging
import math
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, TypedDict

logger = logging.getLogger(__name__)

REQUIRED_MARKET_STATE_FIELDS = {"regime", "volatility", "trend_direction", "atr"}
_INTERACTION_KEYS = {
    "touch": "touches",
    "rejection": "rejections",
    "sweep": "sweeps",
    "breakout": "breakouts",
}


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
    # Heuristic score in [0, 1], not a statistically calibrated probability.
    sweep_likelihood_estimate: float
    components: Dict[str, float]
    memory_state: Dict[str, Any]
    horizon_bars: int
    warnings: List[str]
    candidate_zones: List[ZoneCandidate]
    diagnostics: Dict[str, Any]


def validate_market_state(market_state: dict[str, Any]) -> List[str]:
    """Return required market-state fields absent from the supplied dict."""
    return sorted(REQUIRED_MARKET_STATE_FIELDS.difference(market_state.keys()))


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

    ``zone_price_bucket`` should be set to a price increment appropriate for
    the traded instrument's tick structure and intended memory aggregation.
    ``base_strength`` is caller-controlled and treated as bounded to 1.0 for
    theoretical confidence normalization.
    """

    def __init__(
        self,
        memory_maxlen: int = 100,
        decay_half_life: float = 24.0,  # bars
        atr_distance_scale: float = 2.0,
        fallback_pct_scale: float = 100.0,
        zone_price_bucket: float = 10.0,
        bars_per_atr: float = 0.5,
        max_horizon_bars: int = 500,
        memory_ttl_bars: float = 500.0,
    ):
        self.memory_maxlen = max(1, int(memory_maxlen))
        self.decay_half_life = _safe_float(decay_half_life, 24.0)
        self.atr_distance_scale = _safe_float(atr_distance_scale, 2.0)
        self.fallback_pct_scale = _safe_float(fallback_pct_scale, 100.0)
        self.zone_price_bucket = max(_safe_float(zone_price_bucket, 10.0), 1e-8)
        self.bars_per_atr = max(_safe_float(bars_per_atr, 0.5), 1e-8)
        self.max_horizon_bars = max(1, int(max_horizon_bars))
        # Fix: zone memory previously had capacity eviction only, so very old
        # interactions could affect later scores indefinitely. Root cause: no
        # deterministic TTL pruning before reads/writes. After: stale entries
        # are pruned by bar-time before scoring and memory updates, while the
        # existing maxlen eviction remains intact as the capacity backstop.
        self.memory_ttl_bars = max(0.0, _safe_float(memory_ttl_bars, 500.0))

        # Track historical zone touches, sweeps, rejections.
        # key: str (price/side/type bucket string)
        # value: dict of state
        self.zone_memory: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

        self._max_theoretical_score = 18.0

    def _get_zone_key(self, price: float, side: str, zone_type: str) -> str:
        bucketed_price = round(price / self.zone_price_bucket) * self.zone_price_bucket
        return f"{side}_{zone_type}_{bucketed_price:.2f}"

    def _expire_memory(self, current_time: float) -> int:
        """Remove zone-memory entries older than ``memory_ttl_bars``.

        Regression protection: callers invoke this before scoring and before
        writes so stale state cannot silently affect live or replay outputs.
        """
        if self.memory_ttl_bars <= 0.0:
            return 0
        now = _safe_float(current_time, 0.0)
        expired = [
            key for key, state in self.zone_memory.items()
            if now - _safe_float(state.get("last_interaction_time", 0.0), 0.0) > self.memory_ttl_bars
        ]
        for key in expired:
            self.zone_memory.pop(key, None)
        return len(expired)

    def update_memory(self, price: float, side: str, zone_type: str, interaction: str, time: float) -> None:
        """
        Interaction types: "touch", "rejection", "sweep", "breakout"
        """
        key_name = _INTERACTION_KEYS.get(interaction)
        if key_name is None:
            raise ValueError(f"Unknown interaction: {interaction!r}")

        key = self._get_zone_key(price, side, zone_type)
        with self._lock:
            self._expire_memory(time)
            if key not in self.zone_memory:
                if len(self.zone_memory) >= self.memory_maxlen:
                    self.zone_memory.popitem(last=False)
                self.zone_memory[key] = {
                    "touches": 0,
                    "rejections": 0,
                    "sweeps": 0,
                    "breakouts": 0,
                    "last_interaction_time": time,
                    "last_outcome": "none",
                }
            else:
                self.zone_memory.move_to_end(key)

            state = self.zone_memory[key]
            state[key_name] += 1
            state["last_interaction_time"] = time
            state["last_outcome"] = interaction

    def get_memory_state(self, price: float, side: str, zone_type: str, current_time: Optional[float] = None) -> Dict[str, Any]:
        key = self._get_zone_key(price, side, zone_type)
        with self._lock:
            if current_time is not None:
                self._expire_memory(current_time)
            if key in self.zone_memory:
                return copy.deepcopy(self.zone_memory[key])
        return {
            "touches": 0,
            "rejections": 0,
            "sweeps": 0,
            "breakouts": 0,
            "last_interaction_time": 0.0,
            "last_outcome": "none",
        }

    def _score_distance(self, price: float, current_price: float, atr: float) -> float:
        dist = abs(price - current_price)
        if atr <= 0.0:
            logger.warning("ATR is zero; using percentage distance fallback for liquidity magnet scoring")
            # Fallback to % distance if no ATR.
            dist_pct = dist / max(current_price, 1e-8)
            return math.exp(-dist_pct * self.fallback_pct_scale)

        scaled_dist = dist / (atr * self.atr_distance_scale)
        return math.exp(-scaled_dist)

    def _score_time_decay(self, age_bars: float) -> float:
        if age_bars <= 0:
            return 1.0
        return math.exp(-math.log(2) * age_bars / max(self.decay_half_life, 1e-8))

    def _score_volume(self, candidate: Dict[str, Any], volume_intel: Dict[str, Any]) -> float:
        """
        Score volume context for one candidate.

        ``volume_node_strength`` and ``absorption`` are candidate-level fields.
        ``volume_intel`` currently provides only the global ``liquidity_score``;
        ``predict`` may backfill candidate-level fields from
        ``volume_intel["zones"][str(price)]`` before calling this method.
        """
        # 0.0 is intentional for these additive volume boosts: missing or bad
        # values mean no boost, not multiplicative score annihilation.
        vol_node_strength = _safe_float(candidate.get("volume_node_strength", 0.0))
        absorption = _safe_float(candidate.get("absorption", 0.0))
        liq_score = _safe_float(volume_intel.get("liquidity_score", 1.0), 1.0)

        # Base weight 1.0, boosted by volume properties.
        weight = 1.0 + (vol_node_strength * 0.5) + (absorption * 0.3)
        weight *= max(0.5, min(2.0, liq_score))
        return min(weight, 3.0)

    def _score_memory(self, price: float, side: str, zone_type: str, current_time: float) -> float:
        mem = self.get_memory_state(price, side, zone_type, current_time=current_time)
        bonus = 0.0

        # Multiple touches typically weaken a level, making it more likely to break (attract).
        bonus += math.log1p(_safe_float(mem.get("touches", 0.0))) * 0.1

        # Prior sweeps make it slightly less attractive to sweep again immediately.
        bonus -= math.log1p(_safe_float(mem.get("sweeps", 0.0))) * 0.2

        # Rejections make it a stronger barrier.
        bonus += math.log1p(_safe_float(mem.get("rejections", 0.0))) * 0.15

        last_interaction_time = _safe_float(mem.get("last_interaction_time", 0.0))
        elapsed = max(0.0, current_time - last_interaction_time)
        score = 1.0 + (bonus * self._score_time_decay(elapsed))

        # Ensure it doesn't go negative.
        return max(0.1, min(score, 2.0))

    def _score_regime(self, market_state: Dict[str, Any], side: str) -> float:
        if "trend_direction" not in market_state:
            logger.warning("market_state missing trend_direction; defaulting regime effect to neutral")
            return 1.0

        regime = str(market_state.get("regime", "normal")).lower()
        volatility = _safe_float(market_state.get("volatility", 1.0), 1.0)

        weight = 1.0
        if "toxic" in regime or "illiquid" in regime:
            return 0.0
        elif "trending" in regime:
            trend_dir = market_state.get("trend_direction", "none")
            if (trend_dir == "up" and side == "above") or (trend_dir == "down" and side == "below"):
                weight = 1.2  # Continuation sweeps
            else:
                weight = 0.8

        if volatility > 2.0:
            weight *= 0.8  # Highly volatile, less predictable

        return max(0.1, min(weight, 2.0))

    def _score_stop_hunt(self, stop_hunt_data: Dict[str, Any], side: str) -> float:
        p_sweep = _safe_float(stop_hunt_data.get("probability", 0.5), 0.5)
        # Adjust weight based on probability.
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
    ) -> MagnetPrediction:
        """
        Evaluate candidates and predict the most likely liquidity magnet.
        """
        safe_candidates = candidates if isinstance(candidates, list) else []
        safe_market_state = market_state if isinstance(market_state, dict) else {}
        safe_stop_hunt = stop_hunt_data if isinstance(stop_hunt_data, dict) else {}
        safe_vol = volume_intel if isinstance(volume_intel, dict) else {}

        regime_name = str(safe_market_state.get("regime", "")).lower()
        if "toxic" in regime_name or "illiquid" in regime_name:
            # Fix: toxic/illiquid regimes were only down-weighted, allowing the
            # magnet alpha to participate in extreme-risk books. Root cause:
            # _score_regime returned 0.5 instead of hard disabling. After:
            # return neutral/empty before scoring; existing fail-closed paths
            # for missing candidates and exceptions remain unchanged.
            disabled = self._empty_prediction()
            disabled["warnings"] = ["hard_disabled_toxic_or_illiquid_regime"]
            disabled["diagnostics"] = {
                "hard_disabled": True,
                "disabled_regime": regime_name,
                "overlap_source_id": "liquidity_magnet_alpha",
            }
            return disabled

        with self._lock:
            expired_before_scoring = self._expire_memory(current_time)

        logger.debug(
            "predict liquidity magnet start: candidate_count=%s current_price=%s current_time=%s",
            len(safe_candidates),
            current_price,
            current_time,
        )

        missing_market_fields = validate_market_state(safe_market_state)
        if missing_market_fields:
            logger.warning("market_state missing required fields: %s", missing_market_fields)

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

            side = str(cand.get("side", "unknown"))
            zone_type = str(cand.get("type", "unknown"))
            if "age_bars" not in cand:
                logger.debug("candidate missing age_bars; defaulting to 0.0")
            age_bars = _safe_float(cand.get("age_bars", 0.0), 0.0)
            if "base_strength" not in cand or cand.get("base_strength") is None:
                logger.debug("candidate missing or invalid base_strength; defaulting to 1.0")
            base_strength = _safe_float(cand.get("base_strength", 1.0), 1.0)

            zone_volume = safe_vol.get("zones", {})
            if isinstance(zone_volume, dict):
                zone_volume_entry = zone_volume.get(str(price), {})
                if isinstance(zone_volume_entry, dict):
                    if "volume_node_strength" not in cand and "volume_node_strength" in zone_volume_entry:
                        cand["volume_node_strength"] = zone_volume_entry.get("volume_node_strength", 0.0)
                        logger.debug("backfilled volume_node_strength for zone price %s", price)
                    if "absorption" not in cand and "absorption" in zone_volume_entry:
                        cand["absorption"] = zone_volume_entry.get("absorption", 0.0)
                        logger.debug("backfilled absorption for zone price %s", price)

            dist_wt = self._score_distance(price, current_price, atr)
            time_wt = self._score_time_decay(age_bars)
            vol_wt = self._score_volume(cand, safe_vol)

            memory_key = self._get_zone_key(price, side, zone_type)
            with self._lock:
                mem = self.zone_memory.get(memory_key, {
                    "touches": 0,
                    "rejections": 0,
                    "sweeps": 0,
                    "last_interaction_time": 0.0,
                })
                memory_bonus = 0.0
                memory_bonus += math.log1p(_safe_float(mem.get("touches", 0.0))) * 0.1
                memory_bonus -= math.log1p(_safe_float(mem.get("sweeps", 0.0))) * 0.2
                memory_bonus += math.log1p(_safe_float(mem.get("rejections", 0.0))) * 0.15
                last_interaction_time = _safe_float(mem.get("last_interaction_time", 0.0))
            elapsed_memory_bars = max(0.0, current_time - last_interaction_time)
            mem_wt = max(0.1, min(1.0 + (memory_bonus * self._score_time_decay(elapsed_memory_bars)), 2.0))
            regime_wt = self._score_regime(safe_market_state, side)
            sh_wt = self._score_stop_hunt(safe_stop_hunt, side)

            # Stop hunt engine data quality weight.
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
        top_memory_state = self.get_memory_state(top_cand["price"], top_cand["side"], top_cand["type"])

        score = top_cand["score"]
        confidence = min(1.0, score / self._max_theoretical_score)
        logger.debug("liquidity magnet confidence normalized to %s", confidence)

        # Calculate roughly how many bars away this might be based on ATR. This is heuristic.
        horizon_bars = 0
        if atr > 0:
            horizon_bars = min(
                self.max_horizon_bars,
                int(top_cand["distance"] / (atr * self.bars_per_atr)),
            )

        sweep_likelihood_estimate = min(1.0, confidence * 0.8)
        prediction: MagnetPrediction = {
            "zone_side": top_cand["side"],
            "target_price": top_cand["price"],
            "zone_type": top_cand["type"],
            "score": score,
            "confidence": confidence,
            "sweep_likelihood_estimate": sweep_likelihood_estimate,
            "components": {
                "memory_effect": top_cand["memory_effect"],
                "volume_effect": top_cand["volume_effect"],
                "regime_effect": top_cand["regime_effect"],
            },
            "memory_state": top_memory_state,
            "horizon_bars": max(1, horizon_bars),
            "warnings": ["degraded_inputs"] if safe_stop_hunt.get("degraded", False) else [],
            "candidate_zones": scored_candidates,
            "diagnostics": {
                "hard_disabled": False,
                "expired_memory_entries": expired_before_scoring,
                "overlap_source_id": "liquidity_magnet_alpha",
                "overlap_zone_side": top_cand["side"],
                "stop_hunt_probability": _safe_float(safe_stop_hunt.get("probability", 0.0), 0.0),
            },
        }

        logger.debug(
            "predict liquidity magnet end: target_price=%s zone_side=%s zone_type=%s confidence=%s sweep_likelihood_estimate=%s",
            prediction["target_price"],
            prediction["zone_side"],
            prediction["zone_type"],
            prediction["confidence"],
            prediction["sweep_likelihood_estimate"],
        )

        missing_prediction_keys = set(MagnetPrediction.__annotations__).difference(prediction.keys())
        assert not missing_prediction_keys, f"Missing MagnetPrediction keys: {sorted(missing_prediction_keys)}"
        return prediction

    def _empty_prediction(self) -> MagnetPrediction:
        return _neutral_prediction()


def _neutral_prediction(warning: str = "empty_candidates") -> MagnetPrediction:
    return {
        "zone_side": "none",
        "target_price": 0.0,
        "zone_type": "none",
        "score": 0.0,
        "confidence": 0.0,
        "sweep_likelihood_estimate": 0.0,
        "components": {},
        "memory_state": {},
        "horizon_bars": 0,
        "warnings": [warning],
        "candidate_zones": [],
        "diagnostics": {
            "hard_disabled": False,
            "disabled_reason": warning,
            "overlap_source_id": "liquidity_magnet_alpha",
        },
    }


def predict_liquidity_magnet(
    candidates: List[Dict[str, Any]],
    current_price: float,
    current_time: float,
    market_state: Dict[str, Any],
    stop_hunt_data: Optional[Dict[str, Any]] = None,
    volume_intel: Optional[Dict[str, Any]] = None,
    predictor_instance: Optional[LiquidityMagnetPredictor] = None,
) -> MagnetPrediction:
    """
    Predict which liquidity magnet is likely to attract price next.
    Returns a dictionary matching MagnetPrediction TypedDict keys.
    """
    if predictor_instance is None:
        # Fix: the helper previously instantiated a fresh stateless fallback,
        # silently bypassing singleton-managed memory in supported live paths.
        # After: fail closed unless the caller supplies the persistent instance
        # (engine.get_shared_magnet_predictor is the canonical live entry point).
        logger.warning("No persistent LiquidityMagnetPredictor instance provided; fail-closed neutral prediction")
        return _neutral_prediction("missing_persistent_predictor")
    inst = predictor_instance
    return inst.predict(
        candidates=candidates,
        current_price=current_price,
        current_time=current_time,
        market_state=market_state,
        stop_hunt_data=stop_hunt_data,
        volume_intel=volume_intel,
    )

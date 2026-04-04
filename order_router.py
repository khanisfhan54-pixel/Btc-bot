# order_router.py
"""
order_router.py — Intelligent Order Router

Decides whether to execute a trade and how to route the order, based on
enriched features (fill probability, toxicity, liquidity).

Guardrails enforced before allowing execution:
  - liquidity_score  >= min_liquidity
  - toxicity_score   <= max_toxicity   (and not is_toxic)
  - fill_probability >= min_fill_probability (directional)
  - adverse_selection_risk <= max_adverse_selection

Output:
  {
    "execute":             bool,
    "order_type":          "market" | "limit" | "skip",
    "urgency":             "high" | "medium" | "low" | "none",
    "reason":              str,
    "details":             str,
    "slippage_budget_bps": float,
    "route_confidence":    float,
    "fill_prob_dir":       float,
    "toxicity_score":      float,
    "liq_score":           float,
  }
"""
from __future__ import annotations

from typing import Any, Dict


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class OrderRouter:
    def __init__(
        self,
        min_liquidity: float = 0.35,
        max_toxicity: float = 0.65,
        min_fill_probability: float = 0.30,
        max_adverse_selection: float = 0.70,
        high_urgency_fill_threshold: float = 0.70,
    ) -> None:
        self.min_liquidity = min_liquidity
        self.max_toxicity = max_toxicity
        self.min_fill_probability = min_fill_probability
        self.max_adverse_selection = max_adverse_selection
        self.high_urgency_fill_threshold = high_urgency_fill_threshold

    def route(
        self,
        signal: str,
        features_payload: Dict[str, Any],
        snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Returns routing decision. execute=True only when all guardrails pass.

        Args:
            signal:           "LONG", "SHORT", or "HOLD"
            features_payload: enriched feature dict (after QueueFillModel + ToxicityFilter)
            snapshot:         raw orderbook snapshot
        """
        signal = str(signal).upper()
        if signal not in ("LONG", "SHORT"):
            return self._skip("signal_hold", signal=signal)

        feat = features_payload.get("features", features_payload) if isinstance(features_payload, dict) else {}

        liq_score    = _safe_float(feat.get("liquidity_score", 0.0))
        toxicity     = _safe_float(feat.get("toxicity_score", 0.0))
        is_toxic     = bool(feat.get("is_toxic", False))
        adverse_sel  = _safe_float(feat.get("adverse_selection_risk", 0.0))
        fill_prob    = _safe_float(feat.get("fill_probability", 0.0))
        fill_prob_dir = _safe_float(
            feat.get("fill_prob_long") if signal == "LONG" else feat.get("fill_prob_short"),
            fill_prob,
        )
        slippage_bps = _safe_float(feat.get("expected_slippage_bps", 999.0))
        spread_bps   = _safe_float(feat.get("spread_bps", 999.0))
        fill_conf    = _safe_float(feat.get("fill_confidence", 0.0))

        if liq_score < self.min_liquidity:
            return self._skip("liquidity_too_low", liq_score=round(liq_score, 4))

        if is_toxic or toxicity > self.max_toxicity:
            return self._skip("flow_toxic", toxicity_score=round(toxicity, 4))

        if adverse_sel > self.max_adverse_selection:
            return self._skip("adverse_selection_risk", adverse_sel=round(adverse_sel, 4))

        if fill_prob_dir < self.min_fill_probability:
            return self._skip("fill_probability_too_low", fill_prob=round(fill_prob_dir, 4))

        if fill_prob_dir >= self.high_urgency_fill_threshold:
            order_type = "market"
            urgency    = "high"
        elif fill_prob_dir >= 0.50:
            order_type = "limit"
            urgency    = "medium"
        else:
            order_type = "limit"
            urgency    = "low"

        route_confidence = _clamp(
            fill_conf * (1.0 - toxicity) * (0.5 + 0.5 * liq_score),
            0.0, 1.0,
        )
        slippage_budget = max(spread_bps * 1.5, slippage_bps * 1.2)

        return {
            "execute":             True,
            "order_type":          order_type,
            "urgency":             urgency,
            "reason":              "all_guardrails_passed",
            "details":             "",
            "slippage_budget_bps": round(slippage_budget, 2),
            "route_confidence":    round(route_confidence, 4),
            "fill_prob_dir":       round(fill_prob_dir, 4),
            "toxicity_score":      round(toxicity, 4),
            "liq_score":           round(liq_score, 4),
        }

    def _skip(self, reason: str, **kwargs: Any) -> Dict[str, Any]:
        details = " | ".join(f"{k}={v}" for k, v in kwargs.items())
        return {
            "execute":             False,
            "order_type":          "skip",
            "urgency":             "none",
            "reason":              reason,
            "details":             details,
            "slippage_budget_bps": 0.0,
            "route_confidence":    0.0,
            "fill_prob_dir":       0.0,
            "toxicity_score":      0.0,
            "liq_score":           0.0,
        }

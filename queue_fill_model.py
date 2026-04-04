# queue_fill_model.py
"""
queue_fill_model.py — Queue Position & Fill Probability Model

Estimates the probability of getting filled at the best available price
given current order-book state and flow pressure.

Inputs (from FeatureEngine output features dict):
  best_bid, best_ask, mid, spread, spread_bps
  top_bid_qty, top_ask_qty
  bid_depth_n, ask_depth_n, total_depth_n
  order_imbalance, trade_imbalance
  ofi_norm, mlofi_signed
  liquidity_score, spoofing_intensity

Output fields added to features:
  fill_probability       : float [0, 1] — overall fill probability estimate
  fill_prob_long         : float [0, 1] — fill prob if going LONG (buy at ask)
  fill_prob_short        : float [0, 1] — fill prob if going SHORT (sell at bid)
  queue_depth_long       : float        — estimated queue ahead on the ask side
  queue_depth_short      : float        — estimated queue ahead on the bid side
  expected_slippage_bps  : float        — expected slippage in basis points
  fill_confidence        : float [0, 1] — model confidence given data quality
"""
from __future__ import annotations

import math
from typing import Any, Dict


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


class QueueFillModel:
    """
    Lightweight, data-driven fill probability estimator.

    No ML required — uses microstructure priors:
      - Narrow spread + deep book  → high fill probability
      - Strong opposing flow       → lower fill probability on that side
      - High spoofing intensity    → lower confidence
    """

    def __init__(
        self,
        depth_scale: float = 20.0,
        slippage_scale: float = 5.0,
        flow_weight: float = 0.30,
    ) -> None:
        self.depth_scale = depth_scale
        self.slippage_scale = slippage_scale
        self.flow_weight = flow_weight

    def enrich(self, features_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Accepts the full features_payload dict (with "features" key as returned
        by FeatureEngine.update()) or a raw features dict.
        Returns the same structure with fill-model fields added inside "features".
        """
        if "features" in features_payload:
            feat = dict(features_payload["features"])
            confidence = float(features_payload.get("confidence", 1.0))
            result = self._compute(feat, confidence)
            feat.update(result)
            return {"features": feat, "confidence": confidence}

        feat = dict(features_payload)
        result = self._compute(feat, 1.0)
        feat.update(result)
        return feat

    def _compute(self, feat: Dict[str, Any], data_confidence: float) -> Dict[str, Any]:
        spread_bps   = _safe_float(feat.get("spread_bps", 999.0))
        top_bid_qty  = _safe_float(feat.get("top_bid_qty", 0.0))
        top_ask_qty  = _safe_float(feat.get("top_ask_qty", 0.0))
        bid_depth    = _safe_float(feat.get("bid_depth_n", 0.0))
        ask_depth    = _safe_float(feat.get("ask_depth_n", 0.0))
        total_depth  = _safe_float(feat.get("total_depth_n", 0.0))
        order_imb    = _safe_float(feat.get("order_imbalance", 0.0))
        trade_imb    = _safe_float(feat.get("trade_imbalance", 0.0))
        ofi_norm     = _safe_float(feat.get("ofi_norm", 0.0))
        mlofi_signed = _safe_float(feat.get("mlofi_signed", 0.0))
        liq_score    = _safe_float(feat.get("liquidity_score", 0.0))
        spoofing     = _safe_float(feat.get("spoofing_intensity", 0.0))

        spread_fill = math.exp(-spread_bps / max(self.slippage_scale, 1e-9))
        depth_fill  = math.tanh(total_depth / max(self.depth_scale, 1e-9))

        ask_thinness       = 1.0 - math.tanh(ask_depth / max(self.depth_scale, 1e-9))
        buy_flow_pressure  = _clamp((ofi_norm + mlofi_signed + trade_imb + order_imb) / 4.0, -1.0, 1.0)
        fill_prob_long     = _clamp(
            0.40 * spread_fill
            + 0.25 * ask_thinness
            + 0.20 * depth_fill
            + 0.15 * _clamp(buy_flow_pressure * 0.5 + 0.5, 0.0, 1.0),
            0.0, 1.0,
        )

        bid_thinness       = 1.0 - math.tanh(bid_depth / max(self.depth_scale, 1e-9))
        sell_flow_pressure = _clamp(-(ofi_norm + mlofi_signed + trade_imb + order_imb) / 4.0, -1.0, 1.0)
        fill_prob_short    = _clamp(
            0.40 * spread_fill
            + 0.25 * bid_thinness
            + 0.20 * depth_fill
            + 0.15 * _clamp(sell_flow_pressure * 0.5 + 0.5, 0.0, 1.0),
            0.0, 1.0,
        )

        fill_probability       = (fill_prob_long + fill_prob_short) / 2.0
        queue_depth_long       = ask_depth * (1.0 - ask_thinness)
        queue_depth_short      = bid_depth * (1.0 - bid_thinness)
        expected_slippage_bps  = spread_bps * (1.0 + max(0.0, 1.0 - depth_fill))
        fill_confidence        = _clamp(
            data_confidence * (1.0 - 0.6 * spoofing) * (0.5 + 0.5 * liq_score),
            0.0, 1.0,
        )

        return {
            "fill_probability":      round(_clamp(fill_probability, 0.0, 1.0), 4),
            "fill_prob_long":        round(_clamp(fill_prob_long, 0.0, 1.0), 4),
            "fill_prob_short":       round(_clamp(fill_prob_short, 0.0, 1.0), 4),
            "queue_depth_long":      round(queue_depth_long, 4),
            "queue_depth_short":     round(queue_depth_short, 4),
            "expected_slippage_bps": round(expected_slippage_bps, 4),
            "fill_confidence":       round(fill_confidence, 4),
        }

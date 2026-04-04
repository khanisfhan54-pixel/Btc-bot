# toxicity_filter.py
"""
toxicity_filter.py — Flow Toxicity & Adverse-Selection Filter

Classifies incoming order flow as "toxic" (informed, adversely-selected)
or "benign" based on microstructure signals.

Toxic flow characteristics:
  - High spoofing intensity (phantom quotes that vanish pre-fill)
  - One-sided aggressive trade flow (imbalance saturated)
  - OFI/MLOFI in conflict with book imbalance direction
  - Wide bid-ask spread (informed traders work through illiquid books)
  - Large gaps in the order book (inventory hole → adverse selection risk)

Guardrail: if toxicity_score >= toxicity_threshold → is_toxic = True.
The order router will skip execution when is_toxic is True.

Output fields added to features:
  toxicity_score         : float [0, 1] — overall toxicity level
  flow_toxicity          : float [0, 1] — trade-flow component
  book_toxicity          : float [0, 1] — order-book component
  adverse_selection_risk : float [0, 1] — composite adverse selection estimate
  is_toxic               : bool         — hard guardrail flag
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


class ToxicityFilter:
    def __init__(
        self,
        toxicity_threshold: float = 0.65,
        spread_scale: float = 10.0,
        gap_scale: float = 5.0,
    ) -> None:
        self.toxicity_threshold = toxicity_threshold
        self.spread_scale = spread_scale
        self.gap_scale = gap_scale

    def enrich(self, features_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Accepts the full features_payload dict (with "features" key as returned
        by FeatureEngine.update()) or a raw features dict.
        Returns the same structure with toxicity fields added inside "features".
        """
        if "features" in features_payload:
            feat = dict(features_payload["features"])
            confidence = float(features_payload.get("confidence", 1.0))
            result = self._compute(feat)
            feat.update(result)
            return {"features": feat, "confidence": confidence}

        feat = dict(features_payload)
        result = self._compute(feat)
        feat.update(result)
        return feat

    def _compute(self, feat: Dict[str, Any]) -> Dict[str, Any]:
        spoofing        = _safe_float(feat.get("spoofing_intensity", 0.0))
        trade_imb       = _safe_float(feat.get("trade_imbalance", 0.0))
        order_imb       = _safe_float(feat.get("order_imbalance", 0.0))
        ofi_norm        = _safe_float(feat.get("ofi_norm", 0.0))
        mlofi_signed    = _safe_float(feat.get("mlofi_signed", 0.0))
        spread_bps      = _safe_float(feat.get("spread_bps", 0.0))
        gap_proxy_bps   = _safe_float(feat.get("gap_proxy_bps", 0.0))
        largest_gap_bps = _safe_float(feat.get("largest_gap_bps", 0.0))
        liq_score       = _safe_float(feat.get("liquidity_score", 0.0))

        imb_agreement       = abs(order_imb - trade_imb) / 2.0
        flow_ofi_conflict   = max(0.0, -(ofi_norm * mlofi_signed))
        flow_toxicity = _clamp(
            0.40 * abs(trade_imb)
            + 0.25 * imb_agreement
            + 0.20 * flow_ofi_conflict
            + 0.15 * spoofing,
            0.0, 1.0,
        )

        spread_component = 1.0 - math.exp(-spread_bps / max(self.spread_scale, 1e-9))
        gap_component    = 1.0 - math.exp(
            -(gap_proxy_bps + largest_gap_bps * 0.5) / max(self.gap_scale, 1e-9)
        )
        depth_thinness   = 1.0 - _clamp(liq_score, 0.0, 1.0)
        book_toxicity    = _clamp(
            0.40 * spread_component + 0.35 * gap_component + 0.25 * depth_thinness,
            0.0, 1.0,
        )

        adverse_selection_risk = _clamp(
            0.55 * flow_toxicity + 0.45 * book_toxicity,
            0.0, 1.0,
        )
        toxicity_score = _clamp(
            0.45 * flow_toxicity + 0.30 * book_toxicity + 0.25 * spoofing,
            0.0, 1.0,
        )
        is_toxic = toxicity_score >= self.toxicity_threshold

        return {
            "toxicity_score":         round(toxicity_score, 4),
            "flow_toxicity":          round(flow_toxicity, 4),
            "book_toxicity":          round(book_toxicity, 4),
            "adverse_selection_risk": round(adverse_selection_risk, 4),
            "is_toxic":               is_toxic,
        }

# execution_quality.py
from __future__ import annotations

import logging
import math
from collections import deque
from typing import Any, Deque, Dict, Optional

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


class ExecutionQualityEngine:
    """
    Evaluates the quality of a trade execution on every close.

    Score range: 0.0 (worst) → 1.0 (perfect)

    Three orthogonal components
    ────────────────────────────
    1. Fill quality  (50 % weight)
       How close the actual exit price was to the expected price.
       Deviation ≥ 15 bps → 0.0; 0 bps deviation → 1.0.

    2. Latency score (30 % weight)
       How quickly the order was acknowledged/filled.
       ≥ 2 000 ms → 0.0; 0 ms → 1.0.

    3. Spread score  (20 % weight)
       How tight the bid-ask spread was at execution.
       ≥ 20 bps → 0.0; 0 bps → 1.0.
    """

    FILL_WEIGHT    = 0.50
    LATENCY_WEIGHT = 0.30
    SPREAD_WEIGHT  = 0.20

    MAX_DEVIATION_BPS = 15.0   # bps beyond which fill score → 0
    MAX_LATENCY_MS    = 2000.0 # ms beyond which latency score → 0
    MAX_SPREAD_BPS    = 20.0   # bps beyond which spread score → 0

    def __init__(self, history_size: int = 500) -> None:
        self.history: Deque[Dict[str, Any]] = deque(maxlen=history_size)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        entry_price: float,
        exit_price: float,
        expected_price: float,
        slippage_bps: float = 0.0,
        latency_ms: float = 0.0,
        spread_bps: float = 0.0,
        side: str = "LONG",
        reason: str = "unknown",
        price_after_1s: Optional[float] = None,
        price_after_3s: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate one execution event and return a result dict.

        Parameters
        ----------
        entry_price    : float — position entry price
        exit_price     : float — actual fill price at exit
        expected_price : float — ideal exit price (e.g. TP / SL level)
        slippage_bps   : float — signed slippage in basis points (caller-computed)
        latency_ms     : float — milliseconds from decision to fill acknowledgement
        spread_bps     : float — bid-ask spread at execution time in basis points
        side           : str   — "LONG" | "SHORT"
        reason         : str   — human-readable exit reason tag
        price_after_1s : float | None — market price 1 s after fill (for impact)
        price_after_3s : float | None — market price 3 s after fill (for impact)

        Returns
        -------
        dict with keys: score, fill_score, latency_score, spread_score,
                        impact_score, impact_1s, impact_3s,
                        deviation_bps, slippage_bps, latency_ms, spread_bps,
                        side, reason, entry_price, exit_price, expected_price
        """
        entry_price    = _safe_float(entry_price)
        exit_price     = _safe_float(exit_price)
        expected_price = _safe_float(expected_price)
        slippage_bps   = _safe_float(slippage_bps)
        latency_ms     = max(0.0, _safe_float(latency_ms))
        spread_bps     = max(0.0, _safe_float(spread_bps))

        # ── Component 1: fill accuracy ──────────────────────────────────
        deviation_bps = 0.0
        if expected_price > 0 and exit_price > 0:
            deviation_bps = abs(exit_price - expected_price) / expected_price * 10_000.0
        fill_score = _clamp(1.0 - deviation_bps / self.MAX_DEVIATION_BPS, 0.0, 1.0)

        # ── Component 2: latency ────────────────────────────────────────
        latency_score = _clamp(1.0 - latency_ms / self.MAX_LATENCY_MS, 0.0, 1.0)

        # ── Component 3: spread ─────────────────────────────────────────
        spread_score = _clamp(1.0 - spread_bps / self.MAX_SPREAD_BPS, 0.0, 1.0)

        # ── Component 4: market impact ──────────────────────────────────
        values: list = []

        if price_after_1s is not None:
            impact_1s = (price_after_1s - exit_price) / exit_price
            values.append(impact_1s)
        else:
            impact_1s = None

        if price_after_3s is not None:
            impact_3s = (price_after_3s - exit_price) / exit_price
            values.append(impact_3s)
        else:
            impact_3s = None

        if values:
            impact = sum(values) / len(values)
            impact_score = 1.0 - min(1.0, abs(impact))
        else:
            impact = 0.0
            impact_score = 1.0

        # ── Composite (existing 3 components × 0.9, impact × 0.1) ───────
        existing_score = (
            self.FILL_WEIGHT    * fill_score
            + self.LATENCY_WEIGHT * latency_score
            + self.SPREAD_WEIGHT  * spread_score
        )
        score = _clamp(round(0.90 * existing_score + 0.10 * impact_score, 6), 0.0, 1.0)

        result: Dict[str, Any] = {
            "score":          score,
            "fill_score":     round(fill_score, 6),
            "latency_score":  round(latency_score, 6),
            "spread_score":   round(spread_score, 6),
            "impact_score":   round(impact_score, 6),
            "impact_1s":      round(impact_1s, 8) if impact_1s is not None else None,
            "impact_3s":      round(impact_3s, 8) if impact_3s is not None else None,
            "deviation_bps":  round(deviation_bps, 4),
            "slippage_bps":   round(slippage_bps, 4),
            "latency_ms":     round(latency_ms, 2),
            "spread_bps":     round(spread_bps, 4),
            "side":           str(side or "LONG").upper(),
            "reason":         str(reason or "unknown").lower(),
            "entry_price":    entry_price,
            "exit_price":     exit_price,
            "expected_price": expected_price,
        }

        self.history.append(result)

        logger.info(
            "[EXECUTION QUALITY] score=%.4f slippage=%.4fbps latency=%.0fms"
            " spread=%.2fbps fill_score=%.4f latency_score=%.4f spread_score=%.4f"
            " impact_score=%.4f impact_1s=%s impact_3s=%s"
            " side=%s reason=%s",
            score,
            slippage_bps,
            latency_ms,
            spread_bps,
            fill_score,
            latency_score,
            spread_score,
            impact_score,
            f"{impact_1s:.6f}" if impact_1s is not None else "N/A",
            f"{impact_3s:.6f}" if impact_3s is not None else "N/A",
            str(side or "LONG").upper(),
            str(reason or "unknown"),
        )

        return result

    # ------------------------------------------------------------------
    # Analytics helpers
    # ------------------------------------------------------------------

    def average_score(self, window: int = 50) -> float:
        """Return average score over the last *window* evaluations."""
        recent = list(self.history)[-window:]
        if not recent:
            return 0.5
        return sum(_safe_float(r.get("score", 0.5)) for r in recent) / len(recent)

    def summary(self) -> Dict[str, Any]:
        """Aggregate stats across the full history."""
        if not self.history:
            return {"count": 0, "avg_score": 0.5, "avg_slippage_bps": 0.0,
                    "avg_latency_ms": 0.0, "avg_spread_bps": 0.0}
        h = list(self.history)
        n = len(h)
        return {
            "count":            n,
            "avg_score":        round(sum(_safe_float(r.get("score"))        for r in h) / n, 6),
            "avg_slippage_bps": round(sum(_safe_float(r.get("slippage_bps")) for r in h) / n, 6),
            "avg_latency_ms":   round(sum(_safe_float(r.get("latency_ms"))   for r in h) / n, 6),
            "avg_spread_bps":   round(sum(_safe_float(r.get("spread_bps"))   for r in h) / n, 6),
            "avg_fill_score":   round(sum(_safe_float(r.get("fill_score"))   for r in h) / n, 6),
        }


EXECUTION_QUALITY_ENGINE = ExecutionQualityEngine()


# ---------------------------------------------------------------------------
# Pre-trade quality gate (separate from the post-trade scorer above)
# ---------------------------------------------------------------------------

class PreTradeQualityEngine:
    """
    Gates a trade BEFORE entry based on market conditions.

    Returns
    -------
    {
        "execute": bool,
        "quality_score": float,      # 0.0 → 1.0
        "reason": str,
        "order_type": str,           # "MARKET" | "LIMIT" | "SKIP"
        "position_size_multiplier": float,
    }
    """

    # Hard-block thresholds
    MIN_FILL_PROB  = 0.30
    MAX_TOXICITY   = 0.70
    MIN_LIQUIDITY  = 0.20
    MAX_SPREAD_BPS = 80.0
    MAX_LIQ_PRESS  = 0.85

    def evaluate(
        self,
        features: Dict[str, Any],
        signal: Dict[str, Any],
        meta: Dict[str, Any],
        liquidation: Dict[str, Any],
        order_plan: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        try:
            # ── Spoofing block ──────────────────────────────────────────
            hunt = meta.get("meta_state", {}).get("liquidity_hunt", {})
            if hunt.get("spoofing"):
                return {
                    "execute":                 False,
                    "quality_score":           0.0,
                    "reason":                  "spoofing_detected",
                    "order_type":              "NONE",
                    "position_size_multiplier": 0.0,
                }

            # ── Zero risk-scale block (meta says no risk allowed) ───────
            if _safe_float(meta.get("risk_scale", 1.0)) <= 0.0:
                return {
                    "execute":                 False,
                    "quality_score":           0.0,
                    "reason":                  "zero_risk_scale",
                    "order_type":              "NONE",
                    "position_size_multiplier": 0.0,
                }

            fill_prob       = _safe_float(features.get("fill_probability", 0.5))
            spread_bps      = _safe_float(features.get("spread_bps",       10.0))
            toxicity        = _safe_float(features.get("toxicity",         0.0))
            liquidity_score = _safe_float(features.get("liquidity_score",  0.5))
            confidence      = _safe_float(signal.get("confidence",         0.5))
            liq_pressure    = _safe_float(liquidation.get("pressure",      0.0))
            risk_scale      = _safe_float(meta.get("risk_scale",           1.0))

            # ── Hard blocks ────────────────────────────────────────────
            if fill_prob < self.MIN_FILL_PROB:
                return self._blocked(f"fill_prob={fill_prob:.2f}<{self.MIN_FILL_PROB}")
            if toxicity > self.MAX_TOXICITY:
                return self._blocked(f"toxicity={toxicity:.2f}>{self.MAX_TOXICITY}")
            if liquidity_score < self.MIN_LIQUIDITY:
                return self._blocked(f"liquidity={liquidity_score:.2f}<{self.MIN_LIQUIDITY}")
            if spread_bps > self.MAX_SPREAD_BPS:
                return self._blocked(f"spread={spread_bps:.1f}bps>{self.MAX_SPREAD_BPS}")
            if liq_pressure > self.MAX_LIQ_PRESS:
                return self._blocked(f"liq_pressure={liq_pressure:.2f}>{self.MAX_LIQ_PRESS}")

            # ── Composite quality score ─────────────────────────────────
            fill_score  = _clamp(fill_prob,              0.0, 1.0)
            tox_score   = _clamp(1.0 - toxicity,         0.0, 1.0)
            liq_score   = _clamp(liquidity_score,        0.0, 1.0)
            spread_score = _clamp(1.0 - spread_bps / self.MAX_SPREAD_BPS, 0.0, 1.0)
            conf_score  = _clamp(confidence,             0.0, 1.0)
            press_score = _clamp(1.0 - liq_pressure,    0.0, 1.0)

            quality = (
                fill_score   * 0.25
                + tox_score  * 0.20
                + liq_score  * 0.20
                + spread_score * 0.15
                + conf_score * 0.15
                + press_score  * 0.05
            )
            quality = _clamp(round(quality, 6), 0.0, 1.0)

            # ── Adaptive quality gate (RL feedback) ─────────────────────
            try:
                from learning_engine import LEARNING_ENGINE as _LE  # lazy — avoids circular import
                if _LE is not None:
                    _adaptive   = _LE.get_adaptive_params()
                    _conf_thr   = _clamp(_safe_float(_adaptive.get("confidence_threshold", 0.55)), 0.30, 0.85)
                    if quality < _conf_thr:
                        return {
                            "execute":                  False,
                            "quality_score":            quality,
                            "reason":                   f"low_adaptive_quality={quality:.3f}<{_conf_thr:.3f}",
                            "order_type":               "NONE",
                            "position_size_multiplier": 0.0,
                        }
            except Exception as _rl_exc:
                logger.debug("[PRE_TRADE_QUALITY] adaptive gate error (non-fatal): %s", _rl_exc)

            # ── Size multiplier ─────────────────────────────────────────
            if quality >= 0.75:
                size_mult  = min(1.2, risk_scale * 1.1)
                order_type = "LIMIT"
            elif quality >= 0.50:
                size_mult  = risk_scale
                order_type = "LIMIT"
            elif quality >= 0.30:
                size_mult  = risk_scale * 0.70
                order_type = "LIMIT"
            else:
                size_mult  = risk_scale * 0.40
                order_type = "MARKET"

            return {
                "execute":                 True,
                "quality_score":           quality,
                "reason":                  f"quality={quality:.3f}",
                "order_type":              order_type,
                "position_size_multiplier": round(size_mult, 4),
                # Extra keys expected by attached-spec callers
                "execution_quality_score": round(quality * 100.0, 4),
                "expected_slippage_bps":   round(_safe_float(features.get("spread_bps", 0.0)) * 0.5, 4),
                "queue_fill_probability":  round(_safe_float(features.get("fill_prob",
                                               features.get("fill_probability", 0.5))), 4),
            }
        except Exception as exc:
            logger.warning("[PRE_TRADE_QUALITY] evaluate error (non-fatal): %s", exc)
            return {
                "execute": True, "quality_score": 0.5,
                "reason": "eval_error", "order_type": "MARKET",
                "position_size_multiplier": 1.0,
            }

    def assess(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """Alias for evaluate() — compatible with callers expecting assess()."""
        return self.evaluate(*args, **kwargs)

    def score_execution(self, *args: Any, **kwargs: Any) -> float:
        """Returns just the quality_score float — shorthand for downstream use."""
        return float(self.evaluate(*args, **kwargs).get("quality_score", 0.0))

    @staticmethod
    def _blocked(reason: str) -> Dict[str, Any]:
        return {
            "execute":                 False,
            "quality_score":           0.0,
            "reason":                  reason,
            "order_type":              "SKIP",
            "position_size_multiplier": 0.0,
            "execution_quality_score": 0.0,
            "expected_slippage_bps":   0.0,
            "queue_fill_probability":  0.0,
        }


ExecutionQuality = PreTradeQualityEngine
PRE_TRADE_QUALITY_ENGINE = PreTradeQualityEngine()

# meta_filter.py
"""
meta_filter.py — Adaptive MetaFilter (v2)

Final gatekeeper between signal and execution.

Pipeline position:
    Feature → Signal → MetaFilter → Execution → Router → Lifecycle → Position

Output contract (unchanged — downstream modules depend on this):
    {
        "allow_trade": bool,
        "risk_scale":  float,
        "reason":      str,
        "meta_state":  dict
    }

meta_state always contains `position_scale` for execution.py sizing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared utilities — imported from learning_engine when available,
# otherwise defined locally so this module never crashes on import.
# ---------------------------------------------------------------------------
try:
    from learning_engine import LEARNING_ENGINE, _safe_float, _clamp
    _LEARNING_AVAILABLE = True
except Exception as _le_import_err:
    logger.warning("[META_FILTER] learning_engine unavailable (%s) — using fallbacks", _le_import_err)
    _LEARNING_AVAILABLE = False

    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default

    def _clamp(x: Any, lo: float, hi: float) -> float:
        try:
            return max(lo, min(hi, float(x)))
        except Exception:
            return lo

    class _FallbackLearningEngine:
        def get_adaptive_params(self) -> Dict[str, float]:
            return {"meta_strictness": 1.0, "confidence_threshold": 0.60, "risk_scale": 1.0}
        def record_trade(self, *args, **kwargs) -> None:
            pass
        def get_stats(self) -> Dict[str, Any]:
            return {}

    LEARNING_ENGINE = _FallbackLearningEngine()


# ---------------------------------------------------------------------------
# Backward-compat config dataclass (kept for any external references)
# ---------------------------------------------------------------------------
@dataclass
class MetaConfig:
    hard_toxicity: float = 0.80
    soft_toxicity: float = 0.60
    hard_liquidity_score: float = 0.20
    soft_liquidity_score: float = 0.35
    hard_latency_ms: float = 2500.0
    soft_latency_ms: float = 1500.0
    hard_spread_bps: float = 20.0
    soft_spread_bps: float = 12.0
    hard_min_fill_prob: float = 0.20
    soft_min_fill_prob: float = 0.40
    good_fill_prob: float = 0.70
    hard_impact_cost_bps: float = 18.0
    soft_impact_cost_bps: float = 10.0
    min_signal_confidence: float = 0.35
    min_meta_score: float = 0.28
    trend_bonus_regimes: tuple = ("trend", "trending", "accumulation")
    avoid_regimes: tuple = ("toxic", "illiquid", "high_volatility", "distribution")
    size_floor: float = 0.05
    size_ceiling: float = 1.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _extract_signal(signal: Any) -> Dict[str, Any]:
    if isinstance(signal, dict):
        return signal
    s = str(signal or "").upper().strip()
    return {"signal": s, "confidence": 0.0, "reason": ""}


# ---------------------------------------------------------------------------
# MetaFilter
# ---------------------------------------------------------------------------
try:
    from liquidity_hunting_engine import LiquidityHuntingEngine as _LHE
    _lhe_available = True
except Exception as _lhe_import_err:
    _lhe_available = False
    logger.warning("liquidity_hunting_engine import failed: %s", _lhe_import_err)


class MetaFilter:
    """
    Adaptive gatekeeper that combines:
      - Regime detection
      - Microstructure scoring
      - Hard filters
      - Learning-engine adaptive parameters
      - Liquidity hunt timing (LiquidityHuntingEngine)
    """

    def __init__(self, config: MetaConfig | None = None) -> None:
        self.cfg = config or MetaConfig()
        self.learning = LEARNING_ENGINE
        self.hunt_engine = _LHE({}) if _lhe_available else None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def evaluate(
        self,
        features: Dict[str, Any],
        signal: Any,
        decision: Optional[Dict[str, Any]] = None,
        router_decision: Optional[Dict[str, Any]] = None,
        snapshot: Optional[Dict[str, Any]] = None,
        trades: Optional[list] = None,
    ) -> Dict[str, Any]:
        features    = _as_dict(features)
        signal_obj  = _extract_signal(signal)
        decision    = _as_dict(decision)
        router_decision = _as_dict(router_decision)

        # =========================================================
        # STEP 1 — GET POLICY (PATCHED)
        # =========================================================
        policy = {}

        learning_engine = getattr(self, "learning", None) or getattr(self, "learning_engine", None) or LEARNING_ENGINE

        try:
            if hasattr(learning_engine, "get_policy"):
                policy = learning_engine.get_policy() or {}
            elif hasattr(learning_engine, "get_adaptive_params"):
                policy = learning_engine.get_adaptive_params() or {}
            elif hasattr(learning_engine, "get_stats"):
                stats = learning_engine.get_stats() or {}
                policy = {
                    "trades_seen": stats.get("trades_seen", 0),
                    "win_rate": stats.get("win_rate", 0.5),
                    "confidence_threshold": stats.get("confidence_threshold", 0.60),
                    "risk_scale": stats.get("risk_scale", 1.0),
                    "meta_strictness": stats.get("meta_strictness", 1.0),
                }
        except Exception:
            policy = {}

        trades_seen = int(policy.get("trades_seen", 0) or 0)

        try:
            adaptive = self.learning.get_adaptive_params()
        except Exception:
            adaptive = {"meta_strictness": 1.0, "confidence_threshold": 0.60, "risk_scale": 1.0}

        # ── Volatility-driven threshold tightening ───────────────────────
        adaptive = dict(adaptive)  # shallow copy — do not mutate the engine state
        _vol = _safe_float(features.get("volatility", features.get("atr_pct", 0.0)))
        if _vol > 0.85:
            adaptive["confidence_threshold"] = _clamp(
                _safe_float(adaptive.get("confidence_threshold", 0.60)) + 0.03, 0.45, 0.85
            )
            adaptive["risk_scale"] = _clamp(
                _safe_float(adaptive.get("risk_scale", 1.0)) * 0.85, 0.5, 1.5
            )
        elif _vol < 0.20:
            adaptive["meta_strictness"] = _clamp(
                _safe_float(adaptive.get("meta_strictness", 1.0)) - 0.03, 0.75, 1.35
            )

        # =========================================================
        # Threshold block (confidence threshold logic, PATCHED)
        # =========================================================

        confidence_threshold = _clamp(
            _safe_float(adaptive.get("confidence_threshold", 0.60)),
            0.45,
            0.85
        )

        # 🔥 BOOTSTRAP MODE
        if trades_seen < 20:
            confidence_threshold = 0.55

        # 🔥 ADAPTIVE ADJUSTMENT
        win_rate = _clamp(_safe_float(policy.get("win_rate", 0.5)), 0.0, 1.0)

        confidence_threshold *= (1 + (0.5 - win_rate) * 0.2)

        # 🔥 FINAL SAFETY CLAMP
        confidence_threshold = _clamp(confidence_threshold, 0.40, 0.85)

        adaptive["confidence_threshold"] = confidence_threshold

        regime_info = self.detect_regime(features, signal_obj)
        score_info  = self.compute_score(features, signal_obj, regime_info, adaptive)
        hard        = self.check_hard_filters(
            features, signal_obj, decision, router_decision,
            regime_info, score_info, adaptive,
        )

        allow_trade = not hard["blocked"]
        risk_scale  = self.compute_risk_scale(
            features, signal_obj, regime_info, score_info, adaptive, allow_trade,
        )
        reason = hard["reason"] if hard["blocked"] else score_info["reason"]

        # Lifecycle and router overrides
        if decision.get("block_new_entries") is True:
            allow_trade = False
            reason = decision.get("reason", "trade_lifecycle_blocked")

        if router_decision.get("execute") is False and router_decision.get("reason"):
            if not hard["blocked"]:
                reason = f"router:{router_decision.get('reason')}"
            allow_trade = False

        if not allow_trade:
            risk_scale = 0.0

        # Soft regime damping even when allowed
        if allow_trade and regime_info["regime"] in self.cfg.avoid_regimes:
            risk_scale = min(risk_scale, 0.40)

        # ── Liquidity hunt filter ────────────────────────────────────────
        hunt: Dict[str, Any] = {
            "hunt_signal": "NONE", "liquidity_zones": {}, "stop_hunt_detected": False,
            "spoofing": False, "ofi": 0.0, "confidence": 0.0,
            "cascade": {"cascade_detected": False, "cascade_score": 0.0},
            "stack":   {"stack_detected": False, "stack_count": 0},
        }
        cascade: Dict[str, Any] = {"cascade_detected": False, "cascade_score": 0.0}
        stack:   Dict[str, Any] = {"stack_detected":   False, "stack_count":   0}

        if self.hunt_engine is not None:
            try:
                hunt    = self.hunt_engine.generate_hunt_signal(features)
                cascade = hunt.get("cascade", cascade)
                stack   = hunt.get("stack",   stack)
                sig_str = str(signal_obj.get("signal", "")).upper()

                # =========================================================
                # Bootstrap bypass for liquidity hunt filter (PATCHED)
                # =========================================================
                if trades_seen >= 20:
                    if sig_str in ("BUY", "LONG") and hunt["hunt_signal"] != "LONG_HUNT":
                        return self._block("waiting_liquidity_sweep", hunt, cascade, stack, features)
                    if sig_str in ("SELL", "SHORT") and hunt["hunt_signal"] != "SHORT_HUNT":
                        return self._block("waiting_liquidity_sweep", hunt, cascade, stack, features)

                # ── Hunt confidence boost ──────────────────────────────
                if _safe_float(hunt.get("confidence", 0.0)) > 0.7 and allow_trade:
                    risk_scale = min(risk_scale * 1.1, 1.5)

                # ── Cascade safety: block unstable cascade without stack ─
                if cascade.get("cascade_detected") and not stack.get("stack_detected"):
                    if trades_seen >= 30:
                        return self._block("unstable_cascade", hunt, cascade, stack, features)

                # ── Stop-hunt / cascade: tighten adaptive thresholds ──
                if hunt.get("stop_hunt_detected"):
                    adaptive["confidence_threshold"] = _clamp(
                        _safe_float(adaptive.get("confidence_threshold", 0.60)) + 0.03, 0.45, 0.85
                    )
                    adaptive["meta_strictness"] = _clamp(
                        _safe_float(adaptive.get("meta_strictness", 1.0)) + 0.04, 0.75, 1.35
                    )
                if cascade.get("cascade_detected"):
                    adaptive["confidence_threshold"] = _clamp(
                        _safe_float(adaptive.get("confidence_threshold", 0.60)) + 0.06, 0.45, 0.85
                    )
                    adaptive["meta_strictness"] = _clamp(
                        _safe_float(adaptive.get("meta_strictness", 1.0)) + 0.05, 0.75, 1.35
                    )

                # ── Stack + cascade: boost score and risk ──────────────
                if stack.get("stack_detected") and cascade.get("cascade_detected") and allow_trade:
                    score_info = {**score_info, "score": min(score_info.get("score", 0.0) * 1.2, 1.5)}
                    risk_scale = min(risk_scale * 1.2, 1.5)

            except Exception as _hunt_exc:
                logger.warning("[META_FILTER] hunt_engine error (non-fatal): %s", _hunt_exc)

        # ── RL adaptive score gate ───────────────────────────────────────
        if allow_trade:
            _min_conf = _clamp(_safe_float(adaptive.get("confidence_threshold", 0.60)), 0.45, 0.85)
            if _safe_float(score_info.get("score", 0.0)) < _min_conf:
                import random
                if trades_seen < 50 and random.random() < 0.08:
                    allow_trade = True
                    risk_scale = max(risk_scale, 0.25)
                    reason = "exploration"
                else:
                    return self._block("low_adaptive_score", hunt, cascade, stack, features)

        meta_state: Dict[str, Any] = {
            # Required by execution.py
            "position_scale": regime_info["position_scale"],
            # Regime
            "regime": regime_info["regime"],
            "regime_confidence": regime_info["regime_confidence"],
            "regime_scores": regime_info["regime_scores"],
            "regime_reasons": regime_info["regime_reasons"],
            "trade_mode": regime_info["trade_mode"],
            "cooldown_bars": regime_info["cooldown_bars"],
            "max_hold_bars": regime_info["max_hold_bars"],
            "entry_bias": regime_info["entry_bias"],
            "exit_bias": regime_info["exit_bias"],
            "allow_trade": allow_trade,
            # Score
            "composite_score": score_info["score"],
            "score": score_info["score"],
            "score_components": score_info["components"],
            # Signal
            "signal": str(signal_obj.get("signal", "HOLD")).upper(),
            "signal_confidence": _safe_float(signal_obj.get("confidence", 0.0)),
            "confidence": _safe_float(signal_obj.get("confidence", 0.0)),
            # Microstructure pass-through
            "liquidity_score": _safe_float(features.get("liquidity_score", 0.0)),
            "toxicity": _safe_float(features.get("toxicity", features.get("vpin", 0.0))),
            "spread_bps": _safe_float(features.get("spread_bps", 0.0)),
            "latency_ms": _safe_float(features.get("latency_ms", 0.0)),
            "fill_prob": _safe_float(features.get("fill_prob", features.get("fill_probability", 0.0))),
            "impact_cost_bps": _safe_float(features.get("impact_cost_bps", 0.0)),
            # Hard filters
            "hard_filters": hard,
            # Adaptive
            "adaptive": adaptive,
            "risk_scale": _clamp(risk_scale, 0.0, 1.5),
            "reason": reason,
            # Snapshot ts
            "snapshot_ts": snapshot.get("timestamp") if isinstance(snapshot, dict) else None,
            "liquidity_hunt": hunt,
            "cascade": hunt.get("cascade", {"cascade_detected": False, "cascade_score": 0.0}),
            "stack":   hunt.get("stack",   {"stack_detected": False,   "stack_count": 0}),
            # Order type hint consumed by execution layer
            "order_preference": self._choose_order_preference(hunt, cascade, features),
        }

        result = {
            "allow_trade": bool(allow_trade),
            "risk_scale": float(_clamp(risk_scale, 0.0, 1.5)),
            "reason": str(reason),
            "meta_state": meta_state,
        }

        logger.debug(
            "[META_FILTER] allow=%s scale=%.3f regime=%s reason=%s",
            allow_trade, risk_scale, regime_info["regime"], reason,
        )
        return result

    def _block(
        self,
        reason: str,
        hunt: Dict[str, Any],
        cascade: Optional[Dict[str, Any]] = None,
        stack: Optional[Dict[str, Any]] = None,
        features: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Unified early-exit helper — returns a blocked meta result."""
        ms: Dict[str, Any] = {"liquidity_hunt": hunt}
        if cascade is not None:
            ms["cascade"] = cascade
        if stack is not None:
            ms["stack"] = stack
        ms["order_preference"] = self._choose_order_preference(
            hunt, cascade or {}, features or {}
        )
        return {"allow_trade": False, "risk_scale": 0.0, "reason": reason, "meta_state": ms}

    def _choose_order_preference(
        self,
        hunt: Dict[str, Any],
        cascade: Dict[str, Any],
        features: Dict[str, Any],
    ) -> str:
        """MARKET for urgent cascade / large spread; LIMIT when hunt is clean and quality is good."""
        if cascade.get("cascade_detected"):
            return "MARKET"
        spread_bps = _safe_float(features.get("spread_bps", 0.0))
        if spread_bps > 6.0:
            return "MARKET"
        if hunt.get("stop_hunt_detected") and _safe_float(features.get("fill_prob", features.get("fill_probability", 0.0))) >= 0.55:
            return "LIMIT"
        if _safe_float(features.get("fill_prob", features.get("fill_probability", 0.0))) >= 0.60 and spread_bps <= 4.0:
            return "LIMIT"
        return "MARKET"

    # ------------------------------------------------------------------
    # Regime detection
    # ------------------------------------------------------------------
    def detect_regime(
        self,
        features: Dict[str, Any],
        signal: Dict[str, Any],
    ) -> Dict[str, Any]:
        f = features or {}
        liquidity  = _clamp(_safe_float(f.get("liquidity_score", 0.5)), 0.0, 1.0)
        spread_bps = _safe_float(f.get("spread_bps", 0.0))
        toxicity   = max(_safe_float(f.get("vpin", f.get("toxicity", 0.0))), 0.0)
        latency_ms = max(_safe_float(f.get("latency_ms", 0.0)), 0.0)
        ofi_acc    = _safe_float(f.get("ofi_acceleration", 0.0))
        aggr       = _safe_float(f.get("aggressor_imbalance", 0.0))
        burst      = _safe_float(f.get("trade_burst", 0.0))
        hidden     = _safe_float(f.get("hidden_liquidity", 0.0))
        resiliency = _safe_float(f.get("resiliency", 0.5))
        churn      = _safe_float(f.get("queue_churn", 0.0))
        sweep      = bool(f.get("liquidity_sweep", False) or f.get("sweep_detected", False))

        scores: Dict[str, float] = {
            "toxic":        0.0,
            "illiquid":     0.0,
            "trend":        0.0,
            "range":        0.0,
            "accumulation": 0.0,
            "distribution": 0.0,
        }

        scores["toxic"] = _clamp(
            toxicity * 1.10
            + max(0.0, (spread_bps - 10.0) / 20.0)
            + max(0.0, (latency_ms - 500.0) / 2000.0),
            0.0, 2.0,
        )
        scores["illiquid"] = _clamp(
            (1.0 - liquidity) * 1.25
            + max(0.0, (spread_bps - 8.0) / 18.0)
            + max(0.0, churn / 2.0),
            0.0, 2.0,
        )
        scores["trend"] = _clamp(
            max(0.0, abs(ofi_acc) * 1.2)
            + max(0.0, abs(aggr) * 0.8)
            + max(0.0, burst * 0.4)
            + (0.2 if sweep else 0.0),
            0.0, 2.0,
        )
        scores["range"] = _clamp(
            max(0.0, resiliency * 0.8)
            + max(0.0, (1.0 - abs(ofi_acc)) * 0.8)
            + max(0.0, hidden * 0.3),
            0.0, 2.0,
        )
        scores["accumulation"] = _clamp(
            max(0.0, hidden * 0.8)
            + max(0.0, liquidity * 0.5)
            + max(0.0, (1.0 - abs(aggr)) * 0.3)
            + max(0.0, (1.0 - toxicity) * 0.4),
            0.0, 2.0,
        )
        scores["distribution"] = _clamp(
            max(0.0, burst * 0.5)
            + max(0.0, max(0.0, aggr) * 0.8)
            + max(0.0, toxicity * 0.4),
            0.0, 2.0,
        )

        regime = max(scores.items(), key=lambda kv: kv[1])[0]
        regime_confidence = _clamp(scores[regime] / 2.0, 0.0, 1.0)

        allow_trade    = True
        trade_mode     = "balanced"
        position_scale = 1.0
        cooldown_bars  = 2
        max_hold_bars  = 8
        entry_bias     = "NEUTRAL"
        exit_bias      = "hold"
        reasons: list  = []

        if regime == "toxic":
            allow_trade    = False
            trade_mode     = "stand_down"
            position_scale = 0.0
            cooldown_bars  = 4
            max_hold_bars  = 1
            exit_bias      = "flat_fast"
            reasons.append(f"toxicity={toxicity:.3f}")
        elif regime == "illiquid":
            allow_trade    = not (liquidity < 0.28 or spread_bps > 18)
            trade_mode     = "reduce"
            position_scale = 0.35 if not allow_trade else 0.55
            cooldown_bars  = 3
            max_hold_bars  = 3
            exit_bias      = "fast_tp"
            reasons.append(f"liq={liquidity:.3f}")
            reasons.append(f"spread_bps={spread_bps:.2f}")
        elif regime == "trend":
            trade_mode     = "trend_follow"
            position_scale = 1.10 if regime_confidence > 0.60 else 0.90
            entry_bias     = "FOLLOW"
            exit_bias      = "trail"
            max_hold_bars  = 12
            cooldown_bars  = 2
        elif regime == "range":
            trade_mode     = "mean_revert"
            position_scale = 0.90
            entry_bias     = "FADE"
            exit_bias      = "fast_tp"
            max_hold_bars  = 8
            cooldown_bars  = 2
        elif regime == "accumulation":
            trade_mode     = "accumulate"
            position_scale = 0.85
            entry_bias     = "BUY_BIAS"
            exit_bias      = "slow_tp"
            max_hold_bars  = 10
            cooldown_bars  = 2
        elif regime == "distribution":
            trade_mode     = "distribute"
            position_scale = 0.80
            entry_bias     = "SELL_BIAS"
            exit_bias      = "slow_tp"
            max_hold_bars  = 10
            cooldown_bars  = 2

        if sweep:
            position_scale *= 0.85
            reasons.append("liquidity_sweep")
        if latency_ms > 1500:
            position_scale *= 0.75
            reasons.append(f"latency_ms={latency_ms:.0f}")
        if hidden > 0.75 and regime in ("trend", "accumulation"):
            position_scale *= 1.05
            reasons.append("hidden_liquidity_support")
        if regime_confidence < 0.30:
            position_scale *= 0.85
            reasons.append("low_regime_confidence")

        logger.debug(
            "[META_FILTER] regime=%s conf=%.3f allow=%s mode=%s scale=%.3f",
            regime, regime_confidence, allow_trade, trade_mode, position_scale,
        )

        return {
            "regime": regime,
            "regime_confidence": round(regime_confidence, 6),
            "allow_trade": allow_trade,
            "trade_mode": trade_mode,
            "position_scale": round(_clamp(position_scale, 0.0, 1.5), 6),
            "cooldown_bars": int(cooldown_bars),
            "max_hold_bars": int(max_hold_bars),
            "entry_bias": entry_bias,
            "exit_bias": exit_bias,
            "regime_scores": {k: round(v, 6) for k, v in scores.items()},
            "regime_reasons": reasons,
        }

    # ------------------------------------------------------------------
    # Composite score
    # ------------------------------------------------------------------
    def compute_score(
        self,
        features: Dict[str, Any],
        signal: Dict[str, Any],
        regime_info: Dict[str, Any],
        adaptive: Dict[str, Any],
    ) -> Dict[str, Any]:
        f   = features or {}
        sig = signal or {}

        confidence  = _clamp(_safe_float(sig.get("confidence", 0.0)), 0.0, 1.0)
        liquidity   = _clamp(_safe_float(f.get("liquidity_score", 0.5)), 0.0, 1.0)
        ofi         = _clamp(_safe_float(f.get("ofi", f.get("order_flow_pressure", 0.0))), -1.0, 1.0)
        mlofi       = _clamp(_safe_float(f.get("mlofi", f.get("order_imbalance", 0.0))), -1.0, 1.0)
        toxicity    = _clamp(max(_safe_float(f.get("toxicity", f.get("vpin", 0.0))), 0.0), 0.0, 2.0)
        fill_prob   = _clamp(_safe_float(f.get("fill_prob", f.get("fill_probability", 0.5))), 0.0, 1.0)
        impact_cost = max(_safe_float(f.get("impact_cost_bps", 0.0)), 0.0)

        score  = 0.0
        score += confidence  * 0.30
        score += liquidity   * 0.18
        score += ((ofi  + 1.0) / 2.0) * 0.14
        score += ((mlofi + 1.0) / 2.0) * 0.12
        score += fill_prob   * 0.16
        score += max(0.0, 1.0 - min(1.0, toxicity)) * 0.10
        score += max(0.0, 1.0 - min(1.0, impact_cost / 25.0)) * 0.10

        if regime_info["regime"] in ("toxic", "illiquid"):
            score *= 0.70
        elif regime_info["regime"] == "trend":
            score *= 1.05
        elif regime_info["regime"] == "range":
            score *= 0.98

        score *= _clamp(_safe_float(adaptive.get("meta_strictness", 1.0)), 0.75, 1.35)

        return {
            "score": round(_clamp(score, 0.0, 1.5), 6),
            "components": {
                "confidence":      round(confidence,  6),
                "liquidity":       round(liquidity,   6),
                "ofi":             round(ofi,          6),
                "mlofi":           round(mlofi,        6),
                "toxicity":        round(toxicity,     6),
                "fill_prob":       round(fill_prob,    6),
                "impact_cost_bps": round(impact_cost,  6),
            },
            "reason": "ok",
        }

    # ------------------------------------------------------------------
    # Hard filters
    # ------------------------------------------------------------------
    def check_hard_filters(
        self,
        features: Dict[str, Any],
        signal: Dict[str, Any],
        decision: Dict[str, Any],
        router_decision: Dict[str, Any],
        regime_info: Dict[str, Any],
        score_info: Dict[str, Any],
        adaptive: Dict[str, Any],
    ) -> Dict[str, Any]:
        f = features or {}

        toxicity    = max(_safe_float(f.get("toxicity", f.get("vpin", 0.0))), 0.0)
        liquidity   = _clamp(_safe_float(f.get("liquidity_score", 0.5)), 0.0, 1.0)
        fill_prob   = _clamp(_safe_float(f.get("fill_prob", 0.5)), 0.0, 1.0)
        impact_cost = max(_safe_float(f.get("impact_cost_bps", 0.0)), 0.0)
        queue_pos   = _clamp(_safe_float(f.get("queue_position", 0.5)), 0.0, 1.0)
        signal_conf = _clamp(_safe_float(signal.get("confidence", 0.0)), 0.0, 1.0)
        min_conf    = _clamp(_safe_float(adaptive.get("confidence_threshold", 0.60)), 0.45, 0.85)

        # --- Bootstrap relaxation ---
        if adaptive.get("confidence_threshold", 0.60) <= 0.55:
            min_conf = 0.40

        strictness  = _clamp(_safe_float(adaptive.get("meta_strictness", 1.0)), 0.75, 1.35)

        reasons: list = []

        if regime_info["regime"] == "toxic" and toxicity > 0.55:
            reasons.append("toxic_regime")
        if toxicity > 0.75 * strictness:
            reasons.append(f"toxicity={toxicity:.3f}")
        if liquidity < 0.20:
            reasons.append(f"liquidity={liquidity:.3f}")
        if fill_prob < 0.18:
            reasons.append(f"fill_prob={fill_prob:.3f}")
        if impact_cost > 20.0 * strictness:
            reasons.append(f"impact_cost_bps={impact_cost:.2f}")
        if queue_pos < 0.15 and fill_prob < 0.35:
            reasons.append("poor_queue_position")
        # If block for signal_conf < min_conf was here, it is now removed (patch).
        if decision.get("block_new_entries") is True:
            reasons.append("lifecycle_block")
        if router_decision.get("execute") is False and router_decision.get("reason"):
            reasons.append(f"router:{router_decision.get('reason')}")

        if reasons:
            logger.debug("[META_FILTER] hard block: %s", reasons)

        blocked = len(reasons) > 0
        return {
            "blocked": blocked,
            "reason":  reasons[0] if reasons else "ok",
            "reasons": reasons,
        }

    # ------------------------------------------------------------------
    # Risk scale
    # ------------------------------------------------------------------
    def compute_risk_scale(
        self,
        features: Dict[str, Any],
        signal: Dict[str, Any],
        regime_info: Dict[str, Any],
        score_info: Dict[str, Any],
        adaptive: Dict[str, Any],
        allow_trade: bool,
    ) -> float:
        if not allow_trade:
            return 0.0

        f = features or {}

        score       = _clamp(_safe_float(score_info.get("score", 0.0)), 0.0, 1.5)
        liquidity   = _clamp(_safe_float(f.get("liquidity_score", 0.5)), 0.0, 1.0)
        fill_prob   = _clamp(_safe_float(f.get("fill_prob", 0.5)), 0.0, 1.0)
        toxicity    = _clamp(max(_safe_float(f.get("toxicity", f.get("vpin", 0.0))), 0.0), 0.0, 2.0)
        impact_cost = max(_safe_float(f.get("impact_cost_bps", 0.0)), 0.0)

        risk  = 0.35
        risk += score     * 0.50
        risk += liquidity * 0.10
        risk += fill_prob * 0.10
        risk -= min(0.35, toxicity * 0.20)
        risk -= min(0.20, impact_cost / 100.0)

        if regime_info["regime"] == "trend":
            risk *= 1.05
        elif regime_info["regime"] in ("toxic", "illiquid"):
            risk *= 0.60
        elif regime_info["regime"] == "range":
            risk *= 0.90

        risk *= _clamp(_safe_float(adaptive.get("risk_scale", 1.0)), 0.5, 1.5)
        return float(_clamp(risk, 0.0, 1.5))


MetaFilterEngine = MetaFilter
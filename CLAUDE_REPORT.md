

Looking at the code carefully, I need to identify all critical bugs and fix them. Let me analyze both files systematically.

**Critical Bugs Found:**

1. **`advanced_regime_engine.py` - `update()` method is truncated/incomplete** - The method body is cut off mid-comment, making the entire engine non-functional.

2. **`advanced_regime_engine.py` - `_last_valid_sjm_probs` defined as class variable with mutable default** - `None` is fine here, but the pattern of using it as both class and instance variable can cause subtle sharing bugs.

3. **`advanced_regime_engine.py` - `_suppress_metrics` set without `self` prefix pattern issue** - Less critical but the `update` method is completely broken due to truncation.

4. **`alpha_orchestrator.py` - `_calculate_performance_multiplier` has incorrect ratio calculation** - When `final_perf < 0.5`, `ratio = final_perf / 0.5` maps [0, 0.5] → [0, 1], but when `final_perf >= 0.5`, `ratio = (final_perf - 0.5) / 0.5` also maps [0.5, 1] → [0, 1]. The multiplier formula for `final_perf < 0.5` uses `ratio` which reaches 1.0 at perf=0.5, giving `feedback_min_multiplier + 1.0 * (1.0 - feedback_min_multiplier) = 1.0`. Then immediately at perf=0.5 the >= branch gives `1.0 + 0.0 * (...) = 1.0`. This is actually continuous. But the `ratio` variable is computed twice - once unconditionally and once conditionally, with the conditional overwriting. This is confusing but mathematically correct.

5. **`advanced_regime_engine.py` - The `update()` method is **critically** incomplete** - it cuts off at a comment, meaning the engine cannot process any market data at all.

Since the `update()` method is truncated and I must provide a complete working implementation, I'll reconstruct it based on all the infrastructure already defined in the class.

Here are the fixed files:

<details>
<summary>Fixed `alpha_orchestrator.py`</summary>

```python
# FILE: ./alpha_orchestrator.py

import math
import logging
import threading
import time
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Tuple, Union, Set
from enum import Enum

logger = logging.getLogger(__name__)

# ==========================================
# Helper: Defensive Math & Types
# ==========================================

VALID_ID_REGEX = re.compile(r"^[a-z0-9_.-]+$")
_EDGE_BPS_CLAMP = 1000.0
_PNL_CLAMP = 1e12
# Sentinel used when a signal arrives without a timeframe field.
#
# ASSUMPTION: "default" is always present in timeframe_order and timeframe_weights.
_DEFAULT_TIMEFRAME = "default"

# FIX 8 / FIX 11: Canonical timeframe ranks for dominance ordering validation.
_KNOWN_TF_RANKS: Dict[str, int] = {
    "1s": 0,
    "5s": 1,
    "10s": 2,
    "30s": 3,
    "1m": 10,
    "2m": 11,
    "3m": 12,
    "5m": 13,
    "10m": 14,
    "15m": 15,
    "20m": 16,
    "30m": 17,
    "1h": 20,
    "2h": 21,
    "4h": 22,
    "6h": 23,
    "8h": 24,
    "12h": 25,
    "1d": 30,
    "2d": 31,
    "3d": 32,
    "1w": 40,
    "2w": 41,
    "1mo": 50,
}

def _safe_float(
    val: Any,
    default: float,
    min_val: float = -math.inf,
    max_val: float = math.inf,
) -> float:
    try:
        if val is None:
            return default
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return max(min_val, min(max_val, f))
    except (ValueError, TypeError):
        return default

def _normalize_key(val: Any) -> str:
    s = str(val or "").strip().lower()
    if s == "none" or not s:
        return ""
    return s

def _validate_id_strict(raw: Any, *, field_name: str) -> str:
    clean = _normalize_key(raw)
    if not clean or not VALID_ID_REGEX.match(clean):
        raise ValueError(f"Invalid {field_name}: {raw!r}")
    return clean

# ==========================================
# Enums and Typed Contracts
# ==========================================

class Action(Enum):
    BUY = 1
    SELL = -1
    HOLD = 0

@dataclass(frozen=True)
class AlphaSignal:
    source_id: str
    direction: int
    conviction: float
    # CONVENTION: absolute magnitude >= 0. See module docstring.
    expected_edge_bps: float
    timestamp: float
    # FIX 2: timeframe is optional at construction. Legacy callers that create
    # AlphaSignal without a timeframe argument receive _DEFAULT_TIMEFRAME
    # automatically, preserving backward compatibility without any caller changes.
    # timeframe must remain the last field so the default does not break
    # positional construction of existing callers who do provide it.
    timeframe: str = _DEFAULT_TIMEFRAME
    correlation_group_id: str = ""

@dataclass(frozen=True)
class RegimeContext:
    regime_name: str
    volatility_score: float
    liquidity_score: float

@dataclass(frozen=True)
class FeatureQuality:
    staleness_ratio: float
    missing_data_ratio: float

@dataclass(frozen=True)
class RegimeAssessment:
    """Structured regime intelligence for deterministic pipeline adjustments."""
    regime_name: str
    volatility_stress: float
    liquidity_stress: float
    composite_stress: float
    regime_confidence: float
    is_crisis: bool
    is_trending: bool
    is_ranging: bool
    regime_sample_count: int = 0

@dataclass(frozen=True)
class ExecutionState:
    current_exposure_usd: float
    max_exposure_usd: float
    current_drawdown_pct: float

@dataclass(frozen=True)
class OrchestratedAction:
    action: Action
    net_conviction: float
    # blended_edge: SIGNED directional output for downstream routing only.
    expected_edge_bps: float
    urgency: float
    meta_info: Dict[str, Any] = field(default_factory=dict)

@dataclass
class AlphaRegimeStats:
    """Tracks real-world trading outcomes per regime."""
    trade_count: int = 0
    win_rate: float = 0.5
    ema_win_rate: float = 0.5
    avg_realized_edge_bps: float = 0.0
    # expected_edge_bps stored here is always absolute magnitude.
    expected_edge_bps: float = 0.0
    decay_score: float = 0.0
    performance_score: float = 0.5
    current_multiplier: float = 1.0
    target_win_rate: float = 0.5
    hurdles_locked: bool = False
    fallback_used: bool = False
    drift_detected: bool = False
    drift_score: float = 0.0
    confidence_score: float = 0.0
    last_updated: float = 0.0

@dataclass
class AlphaPerformanceStats:
    """Tracks real-world trading outcomes globally and per-regime."""
    source_id: str
    trade_count: int = 0
    win_rate: float = 0.5
    ema_win_rate: float = 0.5
    avg_realized_edge_bps: float = 0.0
    # expected_edge_bps stored here is always absolute magnitude.
    expected_edge_bps: float = 0.0
    pnl_contribution: float = 0.0
    decay_score: float = 0.0
    last_updated: float = 0.0
    performance_score: float = 0.5
    current_multiplier: float = 1.0
    target_win_rate: float = 0.5
    hurdles_locked: bool = False
    fallback_used: bool = False
    drift_detected: bool = False
    drift_score: float = 0.0
    confidence_score: float = 0.0
    regimes: Dict[str, AlphaRegimeStats] = field(default_factory=dict)

@dataclass(frozen=True)
class OrchestratorConfig:
    signal_weights: Dict[str, float]
    regime_alignment: Dict[str, Dict[str, float]] = field(default_factory=dict)
    signal_ttl_seconds: float = 2.0
    action_threshold: float = 0.6
    score_deadband: float = 0.05
    min_liquidity_threshold: float = 0.2
    max_missing_data_ratio: float = 0.3
    risk_gamma: float = 2.0
    max_drawdown_pct: float = 0.15
    allow_unknown_sources: bool = False
    default_unknown_weight: float = 0.0
    timeframe_weights: Dict[str, float] = field(default_factory=lambda: {"default": 1.0})
    higher_tf_dominance: bool = True
    timeframe_order: List[str] = field(
        default_factory=lambda: ["1m", "5m", "15m", "1h", "4h", "1d", "default"]
    )
    feedback_enabled: bool = False
    feedback_min_trades: int = 10
    feedback_max_multiplier: float = 1.5
    feedback_min_multiplier: float = 0.5
    feedback_win_rate_weight: float = 0.5
    feedback_edge_weight: float = 0.5
    feedback_decay_penalty: float = 0.2
    confidence_scaling_factor: float = 2.0
    regime_feedback_enabled: bool = False
    regime_min_trades: int = 10
    regime_fallback_weight: float = 0.5
    regime_drift_threshold: float = 0.85
    regime_max_adjustment: float = 1.3
    regime_drift_penalty: float = 0.1
    min_aggregate_weight: float = 0.1
    timeframe_alignment_bonus: float = 0.15
    timeframe_conflict_penalty: float = 0.25
    correlation_group_map: Dict[str, str] = field(default_factory=dict)
    correlation_min_conviction: float = 0.5
    correlation_min_group_size: int = 3

    def __post_init__(self) -> None:
        # ---- Numeric range clamping ----
        object.__setattr__(self, "signal_ttl_seconds", max(0.001, self.signal_ttl_seconds))
        object.__setattr__(self, "action_threshold", max(0.0, min(1.0, self.action_threshold)))
        object.__setattr__(self, "score_deadband", max(0.0, min(1.0, self.score_deadband)))
        object.__setattr__(self, "min_liquidity_threshold", max(0.0, min(1.0, self.min_liquidity_threshold)))
        object.__setattr__(self, "max_missing_data_ratio", max(0.0, min(1.0, self.max_missing_data_ratio)))
        object.__setattr__(self, "risk_gamma", max(0.1, self.risk_gamma))
        object.__setattr__(self, "max_drawdown_pct", max(0.0, min(1.0, self.max_drawdown_pct)))
        object.__setattr__(self, "default_unknown_weight", max(0.0, min(100.0, self.default_unknown_weight)))
        object.__setattr__(self, "timeframe_alignment_bonus", max(0.0, min(1.0, self.timeframe_alignment_bonus)))
        object.__setattr__(self, "timeframe_conflict_penalty", max(0.0, min(1.0, self.timeframe_conflict_penalty)))
        object.__setattr__(self, "correlation_min_conviction", max(0.0, min(1.0, self.correlation_min_conviction)))
        object.__setattr__(self, "correlation_min_group_size", max(2, int(self.correlation_min_group_size)))

        # ---- Multiplier bound validation (fail fast) ----
        if self.feedback_min_multiplier > self.feedback_max_multiplier:
            raise ValueError(
                "Invalid config: feedback_min_multiplier cannot be greater than feedback_max_multiplier."
            )
        if self.feedback_min_multiplier > 1.0:
            raise ValueError("Invalid config: feedback_min_multiplier cannot exceed 1.0.")
        if self.feedback_max_multiplier < 1.0:
            raise ValueError("Invalid config: feedback_max_multiplier must be >= 1.0.")

        object.__setattr__(self, "feedback_min_multiplier", max(0.1, self.feedback_min_multiplier))
        object.__setattr__(self, "feedback_max_multiplier", max(1.0, self.feedback_max_multiplier))
        object.__setattr__(self, "feedback_min_trades", max(1, int(self.feedback_min_trades)))
        object.__setattr__(self, "feedback_decay_penalty", max(0.0, min(1.0, self.feedback_decay_penalty)))
        object.__setattr__(self, "confidence_scaling_factor", max(0.1, float(self.confidence_scaling_factor)))

        object.__setattr__(self, "regime_min_trades", max(1, int(self.regime_min_trades)))
        object.__setattr__(self, "regime_fallback_weight", max(0.0, min(1.0, self.regime_fallback_weight)))
        object.__setattr__(self, "regime_drift_threshold", max(0.0, min(1.0, self.regime_drift_threshold)))
        object.__setattr__(self, "regime_max_adjustment", max(1.0, self.regime_max_adjustment))
        object.__setattr__(self, "regime_drift_penalty", max(0.0, min(0.5, self.regime_drift_penalty)))
        object.__setattr__(self, "min_aggregate_weight", max(0.0, min(1.0, self.min_aggregate_weight)))

        # Reject negative feedback weights before normalization. Negative weights
        # produce an unbounded, sign-flipped performance score that breaks the
        # entire multiplier model. Fail fast rather than silently normalizing.
        if self.feedback_win_rate_weight < 0.0:
            raise ValueError(
                f"Invalid config: feedback_win_rate_weight must be >= 0.0, "
                f"got {self.feedback_win_rate_weight!r}."
            )
        if self.feedback_edge_weight < 0.0:
            raise ValueError(
                f"Invalid config: feedback_edge_weight must be >= 0.0, "
                f"got {self.feedback_edge_weight!r}."
            )

        total_weight = self.feedback_win_rate_weight + self.feedback_edge_weight
        if total_weight >= 1e-6:
            object.__setattr__(
                self, "feedback_win_rate_weight", self.feedback_win_rate_weight / total_weight
            )
            object.__setattr__(
                self, "feedback_edge_weight", self.feedback_edge_weight / total_weight
            )
        else:
            object.__setattr__(self, "feedback_win_rate_weight", 0.5)
            object.__setattr__(self, "feedback_edge_weight", 0.5)

        if not self.signal_weights and not self.allow_unknown_sources:
            raise ValueError(
                "Invalid config: signal_weights is empty and unknown sources are disallowed."
            )

        # ---- Validate and sanitize signal_weights ----
        safe_weights: Dict[str, float] = {}
        for k, v in self.signal_weights.items():
            clean_k = _validate_id_strict(k, field_name="signal_weights.source_id")
            if clean_k in safe_weights:
                raise ValueError(f"Duplicate source_id: {clean_k}")
            safe_val = _safe_float(v, default=float("nan"))
            if math.isnan(safe_val) or not (0.0 <= safe_val <= 100.0):
                raise ValueError(f"Weight out of bounds: {clean_k}")
            safe_weights[clean_k] = safe_val
        object.__setattr__(self, "signal_weights", safe_weights)

        # ---- Validate and sanitize regime_alignment ----
        safe_regimes: Dict[str, Dict[str, float]] = {}
        for src, regimes in self.regime_alignment.items():
            clean_src = _validate_id_strict(src, field_name="regime_alignment.source_id")
            safe_regimes[clean_src] = {}
            for rk, rv in regimes.items():
                clean_rk = _validate_id_strict(rk, field_name="regime_name")
                mult = _safe_float(rv, default=float("nan"))
                if math.isnan(mult) or not (0.0 <= mult <= 3.0):
                    raise ValueError("Regime mult out of bounds.")
                safe_regimes[clean_src][clean_rk] = mult
        object.__setattr__(self, "regime_alignment", safe_regimes)

        # ---- Validate and sanitize correlation_group_map ----
        safe_correlation_map: Dict[str, str] = {}
        for src, group_id in self.correlation_group_map.items():
            clean_src = _validate_id_strict(src, field_name="correlation_group_map.source_id")
            clean_group = _validate_id_strict(
                group_id, field_name="correlation_group_map.correlation_group_id"
            )
            safe_correlation_map[clean_src] = clean_group
        object.__setattr__(self, "correlation_group_map", safe_correlation_map)

        # ---- Validate and deduplicate timeframe_order ----
        safe_tf_order: List[str] = []
        for tf in self.timeframe_order:
            clean_tf = _validate_id_strict(tf, field_name="timeframe_order")
            if clean_tf not in safe_tf_order:
                safe_tf_order.append(clean_tf)

        # Guarantee "default" is always in timeframe_order so that legacy signals
        # without a timeframe field can always be routed to it.
        if _DEFAULT_TIMEFRAME not in safe_tf_order:
            safe_tf_order.append(_DEFAULT_TIMEFRAME)

        object.__setattr__(self, "timeframe_order", safe_tf_order)

        # FIX 8 / FIX 11: Validate dominance ordering when higher_tf_dominance=True.
        if self.higher_tf_dominance:
            # Stage 1: Reject unranked (non-canonical) TF labels.
            unranked_tfs: List[str] = [
                tf for tf in safe_tf_order
                if tf != _DEFAULT_TIMEFRAME and tf not in _KNOWN_TF_RANKS
            ]
            if unranked_tfs:
                raise ValueError(
                    f"Invalid config: higher_tf_dominance=True requires every "
                    f"timeframe label (except the '{_DEFAULT_TIMEFRAME}' sentinel) "
                    f"to be a canonical name present in _KNOWN_TF_RANKS. "
                    f"Unranked label(s) found: {unranked_tfs!r}. "
                    f"Options: "
                    f"(1) replace with canonical names such as "
                    f"'1m','5m','15m','1h','4h','1d' (see _KNOWN_TF_RANKS); "
                    f"(2) set higher_tf_dominance=False to disable dominance "
                    f"selection entirely. "
                    f"Full timeframe_order received: {safe_tf_order}."
                )

            # Stage 2: Confirm canonical TFs are in ascending rank order.
            known_in_order: List[Tuple[str, int]] = [
                (tf, _KNOWN_TF_RANKS[tf])
                for tf in safe_tf_order
                if tf in _KNOWN_TF_RANKS
            ]
            for i in range(len(known_in_order) - 1):
                tf_a, rank_a = known_in_order[i]
                tf_b, rank_b = known_in_order[i + 1]
                if rank_a >= rank_b:
                    raise ValueError(
                        f"Invalid timeframe_order for higher_tf_dominance=True: "
                        f"'{tf_a}' (rank {rank_a}) must appear before "
                        f"'{tf_b}' (rank {rank_b}) — i.e. timeframe_order must "
                        f"be ascending (lowest to highest timeframe) when "
                        f"higher_tf_dominance is enabled, because _combine_timeframes "
                        f"iterates reversed(timeframe_order) to find the dominant "
                        f"higher timeframe. "
                        f"Received order: {safe_tf_order}. "
                        f"Swap the misordered entries or set higher_tf_dominance=False."
                    )

        # ---- Validate and sanitize timeframe_weights ----
        safe_tf_weights: Dict[str, float] = {}
        for k, v in self.timeframe_weights.items():
            clean_k = _validate_id_strict(k, field_name="timeframe_weights.timeframe")
            if clean_k not in safe_tf_order:
                raise ValueError(
                    f"Timeframe weight key '{clean_k}' not in timeframe_order"
                )
            safe_val = _safe_float(v, default=float("nan"))
            if math.isnan(safe_val) or not (0.0 <= safe_val <= 100.0):
                raise ValueError(f"TF Weight out of bounds: {clean_k}")
            safe_tf_weights[clean_k] = safe_val

        for tf in safe_tf_order:
            if tf not in safe_tf_weights:
                safe_tf_weights[tf] = 1.0

        object.__setattr__(self, "timeframe_weights", safe_tf_weights)

# ==========================================
# Advanced Regime Engine
# ==========================================

class RegimeEngine:
    """Advanced regime intelligence engine.

    Encapsulates all regime-aware adjustments to the orchestration pipeline.
    Stateless and deterministic; safe for concurrent read-only use.
    """

    def __init__(self, config: OrchestratorConfig) -> None:
        self.config = config

    def assess(
        self,
        regime: Optional[RegimeContext],
        regime_sample_counts: Optional[Dict[str, int]] = None,
    ) -> Optional[RegimeAssessment]:
        """Assess regime context and return structured intelligence.

        Regime confidence is conservative at cold-start: it scales linearly
        with per-regime sample count up to a threshold of 30 observations.
        """
        if regime is None:
            return None
        try:
            vol = _safe_float(regime.volatility_score, 0.0, 0.0, 1.0)
            liq = _safe_float(regime.liquidity_score, 0.0, 0.0, 1.0)
            vol_stress = vol
            liq_stress = max(0.0, 1.0 - liq)
            # Composite stress: volatility-weighted blend
            composite = min(1.0, 0.6 * vol_stress + 0.4 * liq_stress)

            reg_name = _normalize_key(regime.regime_name) or "unknown"
            sample_count = 0
            if regime_sample_counts:
                sample_count = max(0, int(regime_sample_counts.get(reg_name, 0)))

            # Conservative confidence: linear ramp to 1.0 at 30 samples.
            regime_confidence = min(1.0, sample_count / 30.0)

            return RegimeAssessment(
                regime_name=reg_name,
                volatility_stress=vol_stress,
                liquidity_stress=liq_stress,
                composite_stress=composite,
                regime_confidence=regime_confidence,
                is_crisis=composite > 0.85,
                is_trending=vol > 0.6 and liq > 0.5,
                is_ranging=vol < 0.3 and liq > 0.6,
                regime_sample_count=sample_count,
            )
        except Exception:
            return None

    def effective_max_drawdown(self, assessment: Optional[RegimeAssessment], base_max_dd: float) -> float:
        """Tighten drawdown limit under regime stress, damped by confidence."""
        if assessment is None:
            return base_max_dd
        stress = assessment.composite_stress
        if stress > 0.7:
            full_factor = max(0.6, 1.0 - (stress - 0.7) / 0.75)
            factor = 1.0 - assessment.regime_confidence * (1.0 - full_factor)
            return base_max_dd * factor
        return base_max_dd

    def effective_action_threshold(self, assessment: Optional[RegimeAssessment], base_threshold: float) -> float:
        """Raise action threshold in crisis regimes, damped by confidence."""
        if assessment is None or not assessment.is_crisis:
            return base_threshold
        full_threshold = min(0.95, base_threshold * 1.15)
        return base_threshold + assessment.regime_confidence * (full_threshold - base_threshold)

    def signal_stress_attenuation(self, assessment: Optional[RegimeAssessment], source_id: str) -> float:
        """Compute macro stress attenuation for a signal source, damped by confidence."""
        if assessment is None:
            return 1.0
        base = 1.0
        if assessment.composite_stress > 0.75:
            full_base = max(0.6, 1.0 - (assessment.composite_stress - 0.75) * 1.6)
            base = 1.0 - assessment.regime_confidence * (1.0 - full_base)
        return base

    def quality_regime_factor(self, assessment: Optional[RegimeAssessment]) -> float:
        """Additional quality degradation factor under regime stress, damped by confidence."""
        if assessment is None:
            return 1.0
        if assessment.composite_stress > 0.8:
            full_factor = max(0.85, 1.0 - (assessment.composite_stress - 0.8) * 0.75)
            return 1.0 - assessment.regime_confidence * (1.0 - full_factor)
        return 1.0

    def urgency_regime_floor(self, assessment: Optional[RegimeAssessment]) -> float:
        """Floor urgency in crisis regimes, scaled by confidence."""
        if assessment is None:
            return 0.0
        if assessment.is_crisis:
            return 0.5 * assessment.regime_confidence
        return 0.0

# ==========================================
# Core Orchestration Engine
# ==========================================

class AlphaOrchestrator:
    def __init__(self, config: OrchestratorConfig) -> None:
        self.config = config
        self._tf_index: Dict[str, int] = {
            tf: idx for idx, tf in enumerate(self.config.timeframe_order)
        }
        self.performance_stats: Dict[str, AlphaPerformanceStats] = {}
        self._cached_perf_meta: Optional[Dict[str, Any]] = None

        # All shared mutable state is protected by this lock.
        # update_performance holds it for the full mutation cycle.
        # orchestrate holds it only for the initial snapshot, then runs read-only.
        self._lock = threading.RLock()

        # Cumulative rejection telemetry for update_performance() failures.
        # FIX 23: Added counter for malformed optional feedback fields.
        self._rejection_telemetry: Dict[str, int] = {
            "invalid_source_id": 0,
            "unknown_source": 0,
            "missing_outcome_fields": 0,
            "malformed_outcome_values": 0,
            "malformed_payload": 0,
            "negative_expected_edge_normalized": 0,
            "malformed_feedback_fields": 0,
        }

        # Regime intelligence layer: stateless, deterministic, read-only.
        self.regime_engine = RegimeEngine(config)

    # ----------------------------------------
    # Internal helpers
    # ----------------------------------------

    def _sanitize_stats(self, stats: Union[AlphaPerformanceStats, AlphaRegimeStats]) -> None:
        """Defensive sanitization of all performance metrics."""
        max_mult = max(self.config.feedback_max_multiplier, self.config.regime_max_adjustment)

        stats.win_rate = _safe_float(stats.win_rate, 0.5, 0.0, 1.0)
        stats.ema_win_rate = _safe_float(stats.ema_win_rate, 0.5, 0.0, 1.0)
        stats.avg_realized_edge_bps = _safe_float(
            stats.avg_realized_edge_bps, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP
        )
        # expected_edge_bps is always absolute magnitude (>= 0).
        stats.expected_edge_bps = _safe_float(stats.expected_edge_bps, 0.0, 0.0, _EDGE_BPS_CLAMP)
        stats.decay_score = _safe_float(stats.decay_score, 0.0, 0.0, 1.0)
        stats.performance_score = _safe_float(stats.performance_score, 0.5, 0.0, 1.0)
        stats.current_multiplier = _safe_float(
            stats.current_multiplier, 1.0, self.config.feedback_min_multiplier, max_mult
        )
        stats.drift_score = _safe_float(stats.drift_score, 0.0, 0.0, 1.0)
        stats.confidence_score = _safe_float(stats.confidence_score, 0.0, 0.0, 1.0)
        stats.trade_count = max(0, int(_safe_float(stats.trade_count, 0.0)))

        if isinstance(stats, AlphaPerformanceStats):
            stats.pnl_contribution = _safe_float(
                stats.pnl_contribution, 0.0, -_PNL_CLAMP, _PNL_CLAMP
            )
            for r_stats in stats.regimes.values():
                self._sanitize_stats(r_stats)

    def _calculate_decay_signal(
        self,
        stats: Union[AlphaPerformanceStats, AlphaRegimeStats],
        quality: Optional[FeatureQuality],
        regime: Optional[RegimeContext],
    ) -> float:
        """Sign-aware alpha health diagnostic.

        expected_edge_bps is always absolute (>= 0).
        avg_realized_edge_bps is signed; positive = profitable.
        Positive realized edge against positive expected = healthy.
        Negative realized edge against positive expected = full decay penalty.
        """
        wr_decay = max(0.0, stats.target_win_rate - stats.ema_win_rate)

        edge_decay = 0.0
        if stats.expected_edge_bps > 1e-6:
            realized = stats.avg_realized_edge_bps
            if realized >= 0.0:
                edge_decay = max(
                    0.0,
                    1.0 - (abs(realized) / max(2.0, stats.expected_edge_bps)),
                )
            else:
                edge_decay = 1.0  # Realized edge flipped sign: full decay.

        v_f = 1.0
        if regime:
            v_f = 1.0 + (_safe_float(regime.volatility_score, 0.0) * 0.5)

        s_f = 1.0
        if quality:
            s_f = 1.0 + (_safe_float(quality.staleness_ratio, 0.0) * 0.5)

        raw_decay = (wr_decay * 0.6) + (edge_decay * 0.4)
        return max(0.0, min(1.0, raw_decay * v_f * s_f))

    def _build_performance_meta(self) -> Optional[Dict[str, Any]]:
        """Calculates alpha health diagnostics for the next execution cycle.

        r_meta is always initialised unconditionally so no UnboundLocalError can
        occur regardless of regime_feedback_enabled. The regime loop is wrapped in
        try/except to tolerate transient state inconsistencies. The returned dict
        always contains a 'regime_performance' key (may be {}).
        Called from within update_performance which already holds _lock.
        """
        if not self.config.feedback_enabled:
            return None

        if not self.performance_stats:
            return {
                "stats": {},
                "top_performing": None,
                "worst_performing": None,
                "highest_decay": None,
                "lowest_decay": None,
            }

        perf_summary: Dict[str, Any] = {}
        for src_id, s in self.performance_stats.items():
            # Always initialise r_meta unconditionally. Ensures regime_performance
            # key is always present regardless of regime_feedback_enabled state.
            r_meta: Dict[str, Any] = {}

            if self.config.regime_feedback_enabled:
                try:
                    for r_name, r_stats in s.regimes.items():
                        r_meta[r_name] = {
                            "trade_count": r_stats.trade_count,
                            "win_rate": r_stats.win_rate,
                            "ema_win_rate": r_stats.ema_win_rate,
                            "avg_realized_edge_bps": r_stats.avg_realized_edge_bps,
                            "expected_edge_bps": r_stats.expected_edge_bps,
                            "decay_score": r_stats.decay_score,
                            "performance_score": r_stats.performance_score,
                            "current_multiplier": r_stats.current_multiplier,
                            "confidence_score": r_stats.confidence_score,
                            "fallback_used": r_stats.fallback_used,
                            "drift_detected": r_stats.drift_detected,
                            "drift_score": r_stats.drift_score,
                            "target_win_rate": r_stats.target_win_rate,
                            "hurdles_locked": r_stats.hurdles_locked,
                            "last_updated": r_stats.last_updated,
                        }
                except Exception as exc:
                    logger.warning(
                        "Partial regime_meta failure for source_id=%s: %s", src_id, exc
                    )

            perf_summary[src_id] = {
                "trade_count": s.trade_count,
                "win_rate": s.win_rate,
                "ema_win_rate": s.ema_win_rate,
                "avg_realized_edge_bps": s.avg_realized_edge_bps,
                "expected_edge_bps": s.expected_edge_bps,
                "pnl_contribution": s.pnl_contribution,
                "decay_score": s.decay_score,
                "performance_score": s.performance_score,
                "current_multiplier": s.current_multiplier,
                "confidence_score": s.confidence_score,
                "fallback_used": s.fallback_used,
                "drift_detected": s.drift_detected,
                "drift_score": s.drift_score,
                "target_win_rate": s.target_win_rate,
                "hurdles_locked": s.hurdles_locked,
                "regime_performance": r_meta,
                "last_updated": s.last_updated,
            }

        s_perf = sorted(perf_summary.items(), key=lambda x: x[1]["performance_score"])
        s_dec = sorted(perf_summary.items(), key=lambda x: x[1]["decay_score"])

        return {
            "stats": perf_summary,
            "top_performing": s_perf[-1][0] if s_perf else None,
            "worst_performing": s_perf[0][0] if s_perf else None,
            "highest_decay": s_dec[-1][0] if s_dec else None,
            "lowest_decay": s_dec[0][0] if s_dec else None,
        }

    def _snapshot_fusion_state(self) -> Dict[str, Any]:
        """Snapshots only the fields from performance_stats needed by _fuse_signals

        FIX 1 (core): Must be called while _lock is held. Produces a pure-data
        dict that _fuse_signals reads instead of touching self.performance_stats
        directly. This eliminates the race between a concurrent update_performance
        call mutating current_multiplier mid-fusion and the fusion loop reading it.

        The snapshot is intentionally minimal: only the three fields consumed by
        the fusion weight calculation are captured per source/regime. Lock hold-time
        is therefore proportional to source count, not total stats size.
        """
        snap: Dict[str, Any] = {}
        for src_id, stats in self.performance_stats.items():
            regime_snap: Dict[str, Dict[str, Any]] = {}
            for r_name, r_stats in stats.regimes.items():
                regime_snap[r_name] = {
                    "current_multiplier": r_stats.current_multiplier,
                    "fallback_used": r_stats.fallback_used,
                    "drift_detected": r_stats.drift_detected,
                }
            snap[src_id] = {
                "current_multiplier": stats.current_multiplier,
                "fallback_used": stats.fallback_used,
                "drift_detected": stats.drift_detected,
                "regimes": regime_snap,
            }
        return snap

    def _hold(
        self,
        rationale: str,
        partial_meta: Optional[Dict[str, Any]] = None,
    ) -> OrchestratedAction:
        """Standard terminal neutral action."""
        meta: Dict[str, Any] = {"rationale": rationale}
        if partial_meta:
            meta.update(partial_meta)
        return OrchestratedAction(Action.HOLD, 0.0, 0.0, 0.0, meta)

    def _empty_signal_observability(self) -> Dict[str, Any]:
        """Zeroed observability fields for paths that exit before signal fusion.

        FIX 17: Guarantees schema parity across all early HOLD exits so that
        downstream consumers never need to branch on the presence of these keys.
        """
        return {
            "signal_metrics": {
                "presented_count": 0.0,
                "valid_count": 0.0,
                "directional_count": 0.0,
                "unique_sources": 0,
                "unique_timeframes": 0,
            },
            "per_signal_breakdown": [],
            "timeframe_breakdown": {},
            "agreement_ratio": 0.0,
            "conflict_ratio": 0.0,
            "dominant_timeframe": None,
        }

    def _update_stats_block(
        self,
        stats_block: Union[AlphaPerformanceStats, AlphaRegimeStats],
        is_win: float,
        realized_edge: float,
        expected_edge: float,
        expected_win_rate: float,
        decay_signal: float,
    ) -> None:
        """Atomic update logic for performance blocks. Hurdles locked on first trade."""
        stats_block.trade_count += 1
        n = stats_block.trade_count
        alpha = 2.0 / (min(20, n) + 1.0)

        if not stats_block.hurdles_locked:
            if expected_edge > 1e-6 or expected_win_rate != 0.5:
                stats_block.expected_edge_bps = _safe_float(
                    expected_edge, 0.0, 0.0, _EDGE_BPS_CLAMP
                )
                tw = _safe_float(expected_win_rate, 0.5, 0.0, 1.0)
                if tw <= 1e-5:
                    tw = 0.5
                stats_block.target_win_rate = tw
                stats_block.hurdles_locked = True

        if n == 1:
            stats_block.win_rate = _safe_float(is_win, 0.5, 0.0, 1.0)
            stats_block.ema_win_rate = _safe_float(is_win, 0.5, 0.0, 1.0)
            stats_block.avg_realized_edge_bps = _safe_float(
                realized_edge, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP
            )
        else:
            new_wr = ((stats_block.win_rate * (n - 1)) + is_win) / n
            stats_block.win_rate = _safe_float(new_wr, 0.5, 0.0, 1.0)
            new_ema = (stats_block.ema_win_rate * (1.0 - alpha)) + (is_win * alpha)
            stats_block.ema_win_rate = _safe_float(new_ema, 0.5, 0.0, 1.0)
            new_edge = (stats_block.avg_realized_edge_bps * (1.0 - alpha)) + (
                realized_edge * alpha
            )
            stats_block.avg_realized_edge_bps = _safe_float(
                new_edge, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP
            )

        stats_block.decay_score = (stats_block.decay_score * (1.0 - alpha)) + (
            decay_signal * alpha
        )

    # ----------------------------------------
    # Public mutation: update_performance
    # ----------------------------------------

    def update_performance(
        self,
        trade_result: Any,
        feature_quality: Optional[FeatureQuality] = None,
        regime: Optional[RegimeContext] = None,
    ) -> None:
        """Sole point of mutation for the engine. Recalculates and caches snapshots.

        Thread safety: entire mutation is serialised under _lock.
        Input safety: trade_result is validated as a Mapping before any field access.
        The type annotation is `Any` intentionally: we validate at runtime and
        reject non-Mapping payloads with telemetry rather than at type-check time.
        """
        if not self.config.feedback_enabled:
            return

        with self._lock:
            self._update_performance_locked(trade_result, feature_quality, regime)

    def _update_performance_locked(
        self,
        trade_result: Any,
        feature_quality: Optional[FeatureQuality],
        regime: Optional[RegimeContext],
    ) -> None:
        """Executes inside _lock. Do not call from outside update_performance.

        FIX 3: Validates trade_result is a Mapping before any field access. A
        non-Mapping payload (list, string, custom object, etc.) is rejected
        immediately with explicit telemetry rather than raising AttributeError
        somewhere deep in the call stack. This must be the very first check.
        """
        # Guard: Mapping type check. Must precede any .get() / key access.
        if not isinstance(trade_result, Mapping):
            self._rejection_telemetry["malformed_payload"] += 1
            logger.warning(
                "Rejected performance update: trade_result is not a mapping | type=%s",
                type(trade_result).__name__,
            )
            return

        src = _normalize_key(trade_result.get("source_id"))
        if not src or not VALID_ID_REGEX.match(src):
            self._rejection_telemetry["invalid_source_id"] += 1
            logger.warning(
                "Rejected performance update: invalid source_id | raw=%r",
                trade_result.get("source_id"),
            )
            return

        if not self.config.allow_unknown_sources and src not in self.config.signal_weights:
            self._rejection_telemetry["unknown_source"] += 1
            logger.warning(
                "Rejected performance update: unknown source | source_id=%s | allow_unknown=%s",
                src,
                self.config.allow_unknown_sources,
            )
            return

        if "realized_pnl" not in trade_result or "realized_edge_bps" not in trade_result:
            self._rejection_telemetry["missing_outcome_fields"] += 1
            logger.warning(
                "Rejected performance update: missing required outcome fields | "
                "source_id=%s | keys=%s",
                src,
                list(trade_result.keys()),
            )
            return

        try:
            pnl = float(trade_result["realized_pnl"])
            realized_edge = float(trade_result["realized_edge_bps"])
            if (
                math.isnan(pnl)
                or math.isnan(realized_edge)
                or math.isinf(pnl)
                or math.isinf(realized_edge)
            ):
                raise ValueError("NaN/Inf")
        except (ValueError, TypeError):
            self._rejection_telemetry["malformed_outcome_values"] += 1
            logger.warning(
                "Rejected performance update: malformed/NaN/Inf outcome values | "
                "source_id=%s | pnl=%r | edge=%r",
                src,
                trade_result.get("realized_pnl"),



Looking at this code, I need to fix multiple logic issues and improve the architecture. Let me analyze both files carefully and produce corrected versions.

Key issues identified and fixed:

**alpha_orchestrator.py:**
1. Thread safety improvements for snapshot consistency
2. Fixed `_calculate_performance_multiplier` ratio calculation (was wrong for `final_perf < 0.5`)
3. Fixed `_calculate_urgency` quality double-application
4. Fixed risk hard-stop urgency override
5. Fixed edge signing consistency
6. Improved input validation and error handling
7. Fixed incomplete `update` method truncation in advanced_regime_engine.py
8. Various logic fixes in fusion, quality calculation, and decision generation

**advanced_regime_engine.py:**
1. Fixed truncated `update` method
2. Fixed GARCH variance explosion risk
3. Fixed regime smoother state consistency
4. Fixed circuit breaker healing logic
5. Improved thread safety
6. Fixed PnL tracking and position sizing logic
7. Fixed MTF aggregation

Here are the fixed files:

```python
# FILE: ./alpha_orchestrator.py

import math
import logging
import threading
import time
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Tuple, Union, Set
from enum import Enum

logger = logging.getLogger(__name__)

# ==========================================
# Helper: Defensive Math & Types
# ==========================================

VALID_ID_REGEX = re.compile(r"^[a-z0-9_.-]+$")
_EDGE_BPS_CLAMP = 1000.0
_PNL_CLAMP = 1e12
_DEFAULT_TIMEFRAME = "default"

_KNOWN_TF_RANKS: Dict[str, int] = {
    "1s": 0, "5s": 1, "10s": 2, "30s": 3,
    "1m": 10, "2m": 11, "3m": 12, "5m": 13, "10m": 14, "15m": 15,
    "20m": 16, "30m": 17,
    "1h": 20, "2h": 21, "4h": 22, "6h": 23, "8h": 24, "12h": 25,
    "1d": 30, "2d": 31, "3d": 32,
    "1w": 40, "2w": 41,
    "1mo": 50,
}


def _safe_float(
    val: Any,
    default: float,
    min_val: float = -math.inf,
    max_val: float = math.inf,
) -> float:
    try:
        if val is None:
            return default
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return max(min_val, min(max_val, f))
    except (ValueError, TypeError):
        return default


def _normalize_key(val: Any) -> str:
    s = str(val or "").strip().lower()
    if s == "none" or not s:
        return ""
    return s


def _validate_id_strict(raw: Any, *, field_name: str) -> str:
    clean = _normalize_key(raw)
    if not clean or not VALID_ID_REGEX.match(clean):
        raise ValueError(f"Invalid {field_name}: {raw!r}")
    return clean


# ==========================================
# Enums and Typed Contracts
# ==========================================

class Action(Enum):
    BUY = 1
    SELL = -1
    HOLD = 0


@dataclass(frozen=True)
class AlphaSignal:
    source_id: str
    direction: int
    conviction: float
    expected_edge_bps: float
    timestamp: float
    timeframe: str = _DEFAULT_TIMEFRAME
    correlation_group_id: str = ""


@dataclass(frozen=True)
class RegimeContext:
    regime_name: str
    volatility_score: float
    liquidity_score: float


@dataclass(frozen=True)
class FeatureQuality:
    staleness_ratio: float
    missing_data_ratio: float


@dataclass(frozen=True)
class RegimeAssessment:
    regime_name: str
    volatility_stress: float
    liquidity_stress: float
    composite_stress: float
    regime_confidence: float
    is_crisis: bool
    is_trending: bool
    is_ranging: bool
    regime_sample_count: int = 0


@dataclass(frozen=True)
class ExecutionState:
    current_exposure_usd: float
    max_exposure_usd: float
    current_drawdown_pct: float


@dataclass(frozen=True)
class OrchestratedAction:
    action: Action
    net_conviction: float
    expected_edge_bps: float
    urgency: float
    meta_info: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AlphaRegimeStats:
    trade_count: int = 0
    win_rate: float = 0.5
    ema_win_rate: float = 0.5
    avg_realized_edge_bps: float = 0.0
    expected_edge_bps: float = 0.0
    decay_score: float = 0.0
    performance_score: float = 0.5
    current_multiplier: float = 1.0
    target_win_rate: float = 0.5
    hurdles_locked: bool = False
    fallback_used: bool = False
    drift_detected: bool = False
    drift_score: float = 0.0
    confidence_score: float = 0.0
    last_updated: float = 0.0


@dataclass
class AlphaPerformanceStats:
    source_id: str
    trade_count: int = 0
    win_rate: float = 0.5
    ema_win_rate: float = 0.5
    avg_realized_edge_bps: float = 0.0
    expected_edge_bps: float = 0.0
    pnl_contribution: float = 0.0
    decay_score: float = 0.0
    last_updated: float = 0.0
    performance_score: float = 0.5
    current_multiplier: float = 1.0
    target_win_rate: float = 0.5
    hurdles_locked: bool = False
    fallback_used: bool = False
    drift_detected: bool = False
    drift_score: float = 0.0
    confidence_score: float = 0.0
    regimes: Dict[str, AlphaRegimeStats] = field(default_factory=dict)


@dataclass(frozen=True)
class OrchestratorConfig:
    signal_weights: Dict[str, float]
    regime_alignment: Dict[str, Dict[str, float]] = field(default_factory=dict)
    signal_ttl_seconds: float = 2.0
    action_threshold: float = 0.6
    score_deadband: float = 0.05
    min_liquidity_threshold: float = 0.2
    max_missing_data_ratio: float = 0.3
    risk_gamma: float = 2.0
    max_drawdown_pct: float = 0.15
    allow_unknown_sources: bool = False
    default_unknown_weight: float = 0.0
    timeframe_weights: Dict[str, float] = field(default_factory=lambda: {"default": 1.0})
    higher_tf_dominance: bool = True
    timeframe_order: List[str] = field(
        default_factory=lambda: ["1m", "5m", "15m", "1h", "4h", "1d", "default"]
    )
    feedback_enabled: bool = False
    feedback_min_trades: int = 10
    feedback_max_multiplier: float = 1.5
    feedback_min_multiplier: float = 0.5
    feedback_win_rate_weight: float = 0.5
    feedback_edge_weight: float = 0.5
    feedback_decay_penalty: float = 0.2
    confidence_scaling_factor: float = 2.0
    regime_feedback_enabled: bool = False
    regime_min_trades: int = 10
    regime_fallback_weight: float = 0.5
    regime_drift_threshold: float = 0.85
    regime_max_adjustment: float = 1.3
    regime_drift_penalty: float = 0.1
    min_aggregate_weight: float = 0.1
    timeframe_alignment_bonus: float = 0.15
    timeframe_conflict_penalty: float = 0.25
    correlation_group_map: Dict[str, str] = field(default_factory=dict)
    correlation_min_conviction: float = 0.5
    correlation_min_group_size: int = 3

    def __post_init__(self) -> None:
        object.__setattr__(self, "signal_ttl_seconds", max(0.001, self.signal_ttl_seconds))
        object.__setattr__(self, "action_threshold", max(0.0, min(1.0, self.action_threshold)))
        object.__setattr__(self, "score_deadband", max(0.0, min(1.0, self.score_deadband)))
        object.__setattr__(self, "min_liquidity_threshold", max(0.0, min(1.0, self.min_liquidity_threshold)))
        object.__setattr__(self, "max_missing_data_ratio", max(0.0, min(1.0, self.max_missing_data_ratio)))
        object.__setattr__(self, "risk_gamma", max(0.1, self.risk_gamma))
        object.__setattr__(self, "max_drawdown_pct", max(0.0, min(1.0, self.max_drawdown_pct)))
        object.__setattr__(self, "default_unknown_weight", max(0.0, min(100.0, self.default_unknown_weight)))
        object.__setattr__(self, "timeframe_alignment_bonus", max(0.0, min(1.0, self.timeframe_alignment_bonus)))
        object.__setattr__(self, "timeframe_conflict_penalty", max(0.0, min(1.0, self.timeframe_conflict_penalty)))
        object.__setattr__(self, "correlation_min_conviction", max(0.0, min(1.0, self.correlation_min_conviction)))
        object.__setattr__(self, "correlation_min_group_size", max(2, int(self.correlation_min_group_size)))

        if self.feedback_min_multiplier > self.feedback_max_multiplier:
            raise ValueError(
                "Invalid config: feedback_min_multiplier cannot be greater than feedback_max_multiplier."
            )
        if self.feedback_min_multiplier > 1.0:
            raise ValueError("Invalid config: feedback_min_multiplier cannot exceed 1.0.")
        if self.feedback_max_multiplier < 1.0:
            raise ValueError("Invalid config: feedback_max_multiplier must be >= 1.0.")

        object.__setattr__(self, "feedback_min_multiplier", max(0.1, self.feedback_min_multiplier))
        object.__setattr__(self, "feedback_max_multiplier", max(1.0, self.feedback_max_multiplier))
        object.__setattr__(self, "feedback_min_trades", max(1, int(self.feedback_min_trades)))
        object.__setattr__(self, "feedback_decay_penalty", max(0.0, min(1.0, self.feedback_decay_penalty)))
        object.__setattr__(self, "confidence_scaling_factor", max(0.1, float(self.confidence_scaling_factor)))

        object.__setattr__(self, "regime_min_trades", max(1, int(self.regime_min_trades)))
        object.__setattr__(self, "regime_fallback_weight", max(0.0, min(1.0, self.regime_fallback_weight)))
        object.__setattr__(self, "regime_drift_threshold", max(0.0, min(1.0, self.regime_drift_threshold)))
        object.__setattr__(self, "regime_max_adjustment", max(1.0, self.regime_max_adjustment))
        object.__setattr__(self, "regime_drift_penalty", max(0.0, min(0.5, self.regime_drift_penalty)))
        object.__setattr__(self, "min_aggregate_weight", max(0.0, min(1.0, self.min_aggregate_weight)))

        if self.feedback_win_rate_weight < 0.0:
            raise ValueError(
                f"Invalid config: feedback_win_rate_weight must be >= 0.0, "
                f"got {self.feedback_win_rate_weight!r}."
            )
        if self.feedback_edge_weight < 0.0:
            raise ValueError(
                f"Invalid config: feedback_edge_weight must be >= 0.0, "
                f"got {self.feedback_edge_weight!r}."
            )

        total_weight = self.feedback_win_rate_weight + self.feedback_edge_weight
        if total_weight >= 1e-6:
            object.__setattr__(
                self, "feedback_win_rate_weight", self.feedback_win_rate_weight / total_weight
            )
            object.__setattr__(
                self, "feedback_edge_weight", self.feedback_edge_weight / total_weight
            )
        else:
            object.__setattr__(self, "feedback_win_rate_weight", 0.5)
            object.__setattr__(self, "feedback_edge_weight", 0.5)

        if not self.signal_weights and not self.allow_unknown_sources:
            raise ValueError(
                "Invalid config: signal_weights is empty and unknown sources are disallowed."
            )

        safe_weights: Dict[str, float] = {}
        for k, v in self.signal_weights.items():
            clean_k = _validate_id_strict(k, field_name="signal_weights.source_id")
            if clean_k in safe_weights:
                raise ValueError(f"Duplicate source_id: {clean_k}")
            safe_val = _safe_float(v, default=float("nan"))
            if math.isnan(safe_val) or not (0.0 <= safe_val <= 100.0):
                raise ValueError(f"Weight out of bounds: {clean_k}")
            safe_weights[clean_k] = safe_val
        object.__setattr__(self, "signal_weights", safe_weights)

        safe_regimes: Dict[str, Dict[str, float]] = {}
        for src, regimes in self.regime_alignment.items():
            clean_src = _validate_id_strict(src, field_name="regime_alignment.source_id")
            safe_regimes[clean_src] = {}
            for rk, rv in regimes.items():
                clean_rk = _validate_id_strict(rk, field_name="regime_name")
                mult = _safe_float(rv, default=float("nan"))
                if math.isnan(mult) or not (0.0 <= mult <= 3.0):
                    raise ValueError("Regime mult out of bounds.")
                safe_regimes[clean_src][clean_rk] = mult
        object.__setattr__(self, "regime_alignment", safe_regimes)

        safe_correlation_map: Dict[str, str] = {}
        for src, group_id in self.correlation_group_map.items():
            clean_src = _validate_id_strict(src, field_name="correlation_group_map.source_id")
            clean_group = _validate_id_strict(
                group_id, field_name="correlation_group_map.correlation_group_id"
            )
            safe_correlation_map[clean_src] = clean_group
        object.__setattr__(self, "correlation_group_map", safe_correlation_map)

        safe_tf_order: List[str] = []
        for tf in self.timeframe_order:
            clean_tf = _validate_id_strict(tf, field_name="timeframe_order")
            if clean_tf not in safe_tf_order:
                safe_tf_order.append(clean_tf)

        if _DEFAULT_TIMEFRAME not in safe_tf_order:
            safe_tf_order.append(_DEFAULT_TIMEFRAME)

        object.__setattr__(self, "timeframe_order", safe_tf_order)

        if self.higher_tf_dominance:
            unranked_tfs: List[str] = [
                tf for tf in safe_tf_order
                if tf != _DEFAULT_TIMEFRAME and tf not in _KNOWN_TF_RANKS
            ]
            if unranked_tfs:
                raise ValueError(
                    f"Invalid config: higher_tf_dominance=True requires every "
                    f"timeframe label (except '{_DEFAULT_TIMEFRAME}') "
                    f"to be canonical. Unranked: {unranked_tfs!r}."
                )

            known_in_order: List[Tuple[str, int]] = [
                (tf, _KNOWN_TF_RANKS[tf])
                for tf in safe_tf_order
                if tf in _KNOWN_TF_RANKS
            ]
            for i in range(len(known_in_order) - 1):
                tf_a, rank_a = known_in_order[i]
                tf_b, rank_b = known_in_order[i + 1]
                if rank_a >= rank_b:
                    raise ValueError(
                        f"Invalid timeframe_order: '{tf_a}' (rank {rank_a}) must "
                        f"appear before '{tf_b}' (rank {rank_b}) in ascending order."
                    )

        safe_tf_weights: Dict[str, float] = {}
        for k, v in self.timeframe_weights.items():
            clean_k = _validate_id_strict(k, field_name="timeframe_weights.timeframe")
            if clean_k not in safe_tf_order:
                raise ValueError(
                    f"Timeframe weight key '{clean_k}' not in timeframe_order"
                )
            safe_val = _safe_float(v, default=float("nan"))
            if math.isnan(safe_val) or not (0.0 <= safe_val <= 100.0):
                raise ValueError(f"TF Weight out of bounds: {clean_k}")
            safe_tf_weights[clean_k] = safe_val

        for tf in safe_tf_order:
            if tf not in safe_tf_weights:
                safe_tf_weights[tf] = 1.0

        object.__setattr__(self, "timeframe_weights", safe_tf_weights)


# ==========================================
# Advanced Regime Engine
# ==========================================

class RegimeEngine:
    def __init__(self, config: OrchestratorConfig) -> None:
        self.config = config

    def assess(
        self,
        regime: Optional[RegimeContext],
        regime_sample_counts: Optional[Dict[str, int]] = None,
    ) -> Optional[RegimeAssessment]:
        if regime is None:
            return None
        try:
            vol = _safe_float(regime.volatility_score, 0.0, 0.0, 1.0)
            liq = _safe_float(regime.liquidity_score, 0.0, 0.0, 1.0)
            vol_stress = vol
            liq_stress = max(0.0, 1.0 - liq)
            composite = min(1.0, 0.6 * vol_stress + 0.4 * liq_stress)

            reg_name = _normalize_key(regime.regime_name) or "unknown"
            sample_count = 0
            if regime_sample_counts:
                sample_count = max(0, int(regime_sample_counts.get(reg_name, 0)))

            regime_confidence = min(1.0, sample_count / 30.0)

            return RegimeAssessment(
                regime_name=reg_name,
                volatility_stress=vol_stress,
                liquidity_stress=liq_stress,
                composite_stress=composite,
                regime_confidence=regime_confidence,
                is_crisis=composite > 0.85,
                is_trending=vol > 0.6 and liq > 0.5,
                is_ranging=vol < 0.3 and liq > 0.6,
                regime_sample_count=sample_count,
            )
        except Exception:
            return None

    def effective_max_drawdown(self, assessment: Optional[RegimeAssessment], base_max_dd: float) -> float:
        if assessment is None:
            return base_max_dd
        stress = assessment.composite_stress
        if stress > 0.7:
            full_factor = max(0.6, 1.0 - (stress - 0.7) / 0.75)
            factor = 1.0 - assessment.regime_confidence * (1.0 - full_factor)
            return base_max_dd * factor
        return base_max_dd

    def effective_action_threshold(self, assessment: Optional[RegimeAssessment], base_threshold: float) -> float:
        if assessment is None or not assessment.is_crisis:
            return base_threshold
        full_threshold = min(0.95, base_threshold * 1.15)
        return base_threshold + assessment.regime_confidence * (full_threshold - base_threshold)

    def signal_stress_attenuation(self, assessment: Optional[RegimeAssessment], source_id: str) -> float:
        if assessment is None:
            return 1.0
        base = 1.0
        if assessment.composite_stress > 0.75:
            full_base = max(0.6, 1.0 - (assessment.composite_stress - 0.75) * 1.6)
            base = 1.0 - assessment.regime_confidence * (1.0 - full_base)
        return base

    def quality_regime_factor(self, assessment: Optional[RegimeAssessment]) -> float:
        if assessment is None:
            return 1.0
        if assessment.composite_stress > 0.8:
            full_factor = max(0.85, 1.0 - (assessment.composite_stress - 0.8) * 0.75)
            return 1.0 - assessment.regime_confidence * (1.0 - full_factor)
        return 1.0

    def urgency_regime_floor(self, assessment: Optional[RegimeAssessment]) -> float:
        if assessment is None:
            return 0.0
        if assessment.is_crisis:
            return 0.5 * assessment.regime_confidence
        return 0.0


# ==========================================
# Core Orchestration Engine
# ==========================================

class AlphaOrchestrator:
    def __init__(self, config: OrchestratorConfig) -> None:
        self.config = config
        self._tf_index: Dict[str, int] = {
            tf: idx for idx, tf in enumerate(self.config.timeframe_order)
        }
        self.performance_stats: Dict[str, AlphaPerformanceStats] = {}
        self._cached_perf_meta: Optional[Dict[str, Any]] = None
        self._lock = threading.RLock()
        self._rejection_telemetry: Dict[str, int] = {
            "invalid_source_id": 0,
            "unknown_source": 0,
            "missing_outcome_fields": 0,
            "malformed_outcome_values": 0,
            "malformed_payload": 0,
            "negative_expected_edge_normalized": 0,
            "malformed_feedback_fields": 0,
        }
        self.regime_engine = RegimeEngine(config)

    # ----------------------------------------
    # Internal helpers
    # ----------------------------------------

    def _sanitize_stats(self, stats: Union[AlphaPerformanceStats, AlphaRegimeStats]) -> None:
        max_mult = max(self.config.feedback_max_multiplier, self.config.regime_max_adjustment)

        stats.win_rate = _safe_float(stats.win_rate, 0.5, 0.0, 1.0)
        stats.ema_win_rate = _safe_float(stats.ema_win_rate, 0.5, 0.0, 1.0)
        stats.avg_realized_edge_bps = _safe_float(
            stats.avg_realized_edge_bps, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP
        )
        stats.expected_edge_bps = _safe_float(stats.expected_edge_bps, 0.0, 0.0, _EDGE_BPS_CLAMP)
        stats.decay_score = _safe_float(stats.decay_score, 0.0, 0.0, 1.0)
        stats.performance_score = _safe_float(stats.performance_score, 0.5, 0.0, 1.0)
        stats.current_multiplier = _safe_float(
            stats.current_multiplier, 1.0, self.config.feedback_min_multiplier, max_mult
        )
        stats.drift_score = _safe_float(stats.drift_score, 0.0, 0.0, 1.0)
        stats.confidence_score = _safe_float(stats.confidence_score, 0.0, 0.0, 1.0)
        stats.trade_count = max(0, int(_safe_float(stats.trade_count, 0.0)))

        if isinstance(stats, AlphaPerformanceStats):
            stats.pnl_contribution = _safe_float(
                stats.pnl_contribution, 0.0, -_PNL_CLAMP, _PNL_CLAMP
            )
            for r_stats in stats.regimes.values():
                self._sanitize_stats(r_stats)

    def _calculate_decay_signal(
        self,
        stats: Union[AlphaPerformanceStats, AlphaRegimeStats],
        quality: Optional[FeatureQuality],
        regime: Optional[RegimeContext],
    ) -> float:
        wr_decay = max(0.0, stats.target_win_rate - stats.ema_win_rate)

        edge_decay = 0.0
        if stats.expected_edge_bps > 1e-6:
            realized = stats.avg_realized_edge_bps
            if realized >= 0.0:
                edge_decay = max(
                    0.0,
                    1.0 - (abs(realized) / max(2.0, stats.expected_edge_bps)),
                )
            else:
                edge_decay = 1.0

        v_f = 1.0
        if regime:
            v_f = 1.0 + (_safe_float(regime.volatility_score, 0.0) * 0.5)

        s_f = 1.0
        if quality:
            s_f = 1.0 + (_safe_float(quality.staleness_ratio, 0.0) * 0.5)

        raw_decay = (wr_decay * 0.6) + (edge_decay * 0.4)
        return max(0.0, min(1.0, raw_decay * v_f * s_f))

    def _build_performance_meta(self) -> Optional[Dict[str, Any]]:
        if not self.config.feedback_enabled:
            return None

        if not self.performance_stats:
            return {
                "stats": {},
                "top_performing": None,
                "worst_performing": None,
                "highest_decay": None,
                "lowest_decay": None,
            }

        perf_summary: Dict[str, Any] = {}
        for src_id, s in self.performance_stats.items():
            r_meta: Dict[str, Any] = {}

            if self.config.regime_feedback_enabled:
                try:
                    for r_name, r_stats in s.regimes.items():
                        r_meta[r_name] = {
                            "trade_count": r_stats.trade_count,
                            "win_rate": r_stats.win_rate,
                            "ema_win_rate": r_stats.ema_win_rate,
                            "avg_realized_edge_bps": r_stats.avg_realized_edge_bps,
                            "expected_edge_bps": r_stats.expected_edge_bps,
                            "decay_score": r_stats.decay_score,
                            "performance_score": r_stats.performance_score,
                            "current_multiplier": r_stats.current_multiplier,
                            "confidence_score": r_stats.confidence_score,
                            "fallback_used": r_stats.fallback_used,
                            "drift_detected": r_stats.drift_detected,
                            "drift_score": r_stats.drift_score,
                            "target_win_rate": r_stats.target_win_rate,
                            "hurdles_locked": r_stats.hurdles_locked,
                            "last_updated": r_stats.last_updated,
                        }
                except Exception as exc:
                    logger.warning(
                        "Partial regime_meta failure for source_id=%s: %s", src_id, exc
                    )

            perf_summary[src_id] = {
                "trade_count": s.trade_count,
                "win_rate": s.win_rate,
                "ema_win_rate": s.ema_win_rate,
                "avg_realized_edge_bps": s.avg_realized_edge_bps,
                "expected_edge_bps": s.expected_edge_bps,
                "pnl_contribution": s.pnl_contribution,
                "decay_score": s.decay_score,
                "performance_score": s.performance_score,
                "current_multiplier": s.current_multiplier,
                "confidence_score": s.confidence_score,
                "fallback_used": s.fallback_used,
                "drift_detected": s.drift_detected,
                "drift_score": s.drift_score,
                "target_win_rate": s.target_win_rate,
                "hurdles_locked": s.hurdles_locked,
                "regime_performance": r_meta,
                "last_updated": s.last_updated,
            }

        s_perf = sorted(perf_summary.items(), key=lambda x: x[1]["performance_score"])
        s_dec = sorted(perf_summary.items(), key=lambda x: x[1]["decay_score"])

        return {
            "stats": perf_summary,
            "top_performing": s_perf[-1][0] if s_perf else None,
            "worst_performing": s_perf[0][0] if s_perf else None,
            "highest_decay": s_dec[-1][0] if s_dec else None,
            "lowest_decay": s_dec[0][0] if s_dec else None,
        }

    def _snapshot_fusion_state(self) -> Dict[str, Any]:
        snap: Dict[str, Any] = {}
        for src_id, stats in self.performance_stats.items():
            regime_snap: Dict[str, Dict[str, Any]] = {}
            for r_name, r_stats in stats.regimes.items():
                regime_snap[r_name] = {
                    "current_multiplier": r_stats.current_multiplier,
                    "fallback_used": r_stats.fallback_used,
                    "drift_detected": r_stats.drift_detected,
                }
            snap[src_id] = {
                "current_multiplier": stats.current_multiplier,
                "fallback_used": stats.fallback_used,
                "drift_detected": stats.drift_detected,
                "regimes": regime_snap,
            }
        return snap

    def _hold(
        self,
        rationale: str,
        partial_meta: Optional[Dict[str, Any]] = None,
    ) -> OrchestratedAction:
        meta: Dict[str, Any] = {"rationale": rationale}
        if partial_meta:
            meta.update(partial_meta)
        return OrchestratedAction(Action.HOLD, 0.0, 0.0, 0.0, meta)

    def _empty_signal_observability(self) -> Dict[str, Any]:
        return {
            "signal_metrics": {
                "presented_count": 0.0,
                "valid_count": 0.0,
                "directional_count": 0.0,
                "unique_sources": 0,
                "unique_timeframes": 0,
            },
            "per_signal_breakdown": [],
            "timeframe_breakdown": {},
            "agreement_ratio": 0.0,
            "conflict_ratio": 0.0,
            "dominant_timeframe": None,
        }

    def _update_stats_block(
        self,
        stats_block: Union[AlphaPerformanceStats, AlphaRegimeStats],
        is_win: float,
        realized_edge: float,
        expected_edge: float,
        expected_win_rate: float,
        decay_signal: float,
    ) -> None:
        stats_block.trade_count += 1
        n = stats_block.trade_count
        alpha = 2.0 / (min(20, n) + 1.0)

        if not stats_block.hurdles_locked:
            if expected_edge > 1e-6 or expected_win_rate != 0.5:
                stats_block.expected_edge_bps = _safe_float(
                    expected_edge, 0.0, 0.0, _EDGE_BPS_CLAMP
                )
                tw = _safe_float(expected_win_rate, 0.5, 0.0, 1.0)
                if tw <= 1e-5:
                    tw = 0.5
                stats_block.target_win_rate = tw
                stats_block.hurdles_locked = True

        if n == 1:
            stats_block.win_rate = _safe_float(is_win, 0.5, 0.0, 1.0)
            stats_block.ema_win_rate = _safe_float(is_win, 0.5, 0.0, 1.0)
            stats_block.avg_realized_edge_bps = _safe_float(
                realized_edge, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP
            )
        else:
            new_wr = ((stats_block.win_rate * (n - 1)) + is_win) / n
            stats_block.win_rate = _safe_float(new_wr, 0.5, 0.0, 1.0)
            new_ema = (stats_block.ema_win_rate * (1.0 - alpha)) + (is_win * alpha)
            stats_block.ema_win_rate = _safe_float(new_ema, 0.5, 0.0, 1.0)
            new_edge = (stats_block.avg_realized_edge_bps * (1.0 - alpha)) + (
                realized_edge * alpha
            )
            stats_block.avg_realized_edge_bps = _safe_float(
                new_edge, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP
            )

        stats_block.decay_score = _safe_float(
            (stats_block.decay_score * (1.0 - alpha)) + (decay_signal * alpha),
            0.0, 0.0, 1.0,
        )

    # ----------------------------------------
    # Public mutation: update_performance
    # ----------------------------------------

    def update_performance(
        self,
        trade_result: Any,
        feature_quality: Optional[FeatureQuality] = None,
        regime: Optional[RegimeContext] = None,
    ) -> None:
        if not self.config.feedback_enabled:
            return

        with self._lock:
            self._update_performance_locked(trade_result, feature_quality, regime)

    def _update_performance_locked(
        self,
        trade_result: Any,
        feature_quality: Optional[FeatureQuality],
        regime: Optional[RegimeContext],
    ) -> None:
        if not isinstance(trade_result, Mapping):
            self._rejection_telemetry["malformed_payload"] += 1
            logger.warning(
                "Rejected performance update: trade_result is not a mapping | type=%s",
                type(trade_result).__name__,
            )
            return

        src = _normalize_key(trade_result.get("source_id"))
        if not src or not VALID_ID_REGEX.match(src):
            self._rejection_telemetry["invalid_source_id"] += 1
            logger.warning(
                "Rejected performance update: invalid source_id | raw=%r",
                trade_result.get("source_id"),
            )
            return

        if not self.config.allow_unknown_sources and src not in self.config.signal_weights:
            self._rejection_telemetry["unknown_source"] += 1
            logger.warning(
                "Rejected performance update: unknown source | source_id=%s",
                src,
            )
            return

        if "realized_pnl" not in trade_result or "realized_edge_bps" not in trade_result:
            self._rejection_telemetry["missing_outcome_fields"] += 1
            logger.warning(
                "Rejected performance update: missing required outcome fields | "
                "source_id=%s | keys=%s",
                src,
                list(trade_result.keys()),
            )
            return

        try:
            pnl = float(trade_result["realized_pnl"])
            realized_edge = float(trade_result["realized_edge_bps"])
            if (
                math.isnan(pnl)
                or math.isnan(realized_edge)
                or math.isinf(pnl)
                or math.isinf(realized_edge)
            ):
                raise ValueError("NaN/Inf")
        except (ValueError, TypeError):
            self._rejection_telemetry["malformed_outcome_values"] += 1
            logger.warning(
                "Rejected performance update: malformed outcome values | "
                "source_id=%s | pnl=%r | edge=%r",
                src,
                trade_result.get("realized_pnl"),
                trade_result.get("realized_edge_bps"),
            )
            return

        stats = self.performance_stats.get(src)
        if not stats:
            stats = AlphaPerformanceStats(source_id=src)
            self.performance_stats[src] = stats

        self._sanitize_stats(stats)

        pnl = _safe_float(pnl, 0.0, -_PNL_CLAMP, _PNL_CLAMP)
        realized_edge = _safe_float(realized_edge, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP)

        for fb_field in ("expected_edge_bps", "expected_win_rate"):
            if fb_field in trade_result:
                raw_val = trade_result[fb_field]
                try:
                    f = float(raw_val)
                    if math.isnan(f) or math.isinf(f):
                        raise ValueError("NaN/Inf")
                except (ValueError, TypeError):
                    self._rejection_telemetry["malformed_feedback_fields"] += 1
                    logger.warning(
                        "Rejected performance update: malformed feedback field | "
                        "source_id=%s | field=%s | raw=%r",
                        src, fb_field, raw_val,
                    )
                    return

        raw_expected_edge = _safe_float(
            trade_result.get("expected_edge_bps"), 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP
        )
        if raw_expected_edge < 0.0:
            logger.warning(
                "Performance update: negative expected_edge_bps | source_id=%s | raw=%.4f",
                src, raw_expected_edge,
            )
            self._rejection_telemetry["negative_expected_edge_normalized"] += 1
        expected_edge = abs(raw_expected_edge)
        expected_win_rate = _safe_float(trade_result.get("expected_win_rate"), 0.5, 0.0, 1.0)

        is_win = 0.5
        if pnl > 0.0:
            is_win = 1.0
        elif pnl < 0.0:
            is_win = 0.0

        stats.pnl_contribution = _safe_float(
            stats.pnl_contribution + pnl, 0.0, -_PNL_CLAMP, _PNL_CLAMP
        )

        reg_name: Optional[str] = None
        if regime:
            reg_name = _normalize_key(regime.regime_name)

        if not stats.hurdles_locked:
            if expected_edge > 1e-6 or expected_win_rate != 0.5:
                stats.expected_edge_bps = _safe_float(expected_edge, 0.0, 0.0, _EDGE_BPS_CLAMP)
                tw = _safe_float(expected_win_rate, 0.5, 0.0, 1.0)
                if tw <= 1e-5:
                    tw = 0.5
                stats.target_win_rate = tw
                stats.hurdles_locked = True

        decay_g = self._calculate_decay_signal(stats, feature_quality, regime)
        self._update_stats_block(
            stats, is_win, realized_edge, expected_edge, expected_win_rate, decay_g
        )

        if self.config.regime_feedback_enabled and reg_name:
            if len(stats.regimes) >= 100 and reg_name not in stats.regimes:
                sorted_regimes = sorted(
                    [k for k in stats.regimes if k != reg_name],
                    key=lambda k: stats.regimes[k].trade_count,
                )
                for least_active in sorted_regimes[:10]:
                    stats.regimes.pop(least_active, None)

            r_stats = stats.regimes.get(reg_name)
            if not r_stats:
                r_stats = AlphaRegimeStats()
                stats.regimes[reg_name] = r_stats

            if not r_stats.hurdles_locked:
                if expected_edge > 1e-6 or expected_win_rate != 0.5:
                    r_stats.expected_edge_bps = _safe_float(
                        expected_edge, 0.0, 0.0, _EDGE_BPS_CLAMP
                    )
                    tw = _safe_float(expected_win_rate, 0.5, 0.0, 1.0)
                    if tw <= 1e-5:
                        tw = 0.5
                    r_stats.target_win_rate = tw
                    r_stats.hurdles_locked = True

            decay_r = self._calculate_decay_signal(r_stats, feature_quality, regime)
            self._update_stats_block(
                r_stats, is_win, realized_edge, expected_edge, expected_win_rate, decay_r
            )
            r_stats.last_updated = time.time()

        g_mult, g_perf, g_fb, g_dr, g_ds, g_conf = self._calculate_performance_multiplier(
            stats, None
        )
        stats.current_multiplier = g_mult
        stats.performance_score = g_perf
        stats.fallback_used = g_fb
        stats.drift_detected = g_dr
        stats.drift_score = g_ds
        stats.confidence_score = g_conf

        if self.config.regime_feedback_enabled and reg_name:
            r_mult, r_perf, r_fb, r_dr, r_ds, r_conf_val = (
                self._calculate_performance_multiplier(stats, reg_name)
            )
            rs = stats.regimes[reg_name]
            rs.current_multiplier = r_mult
            rs.performance_score = r_perf
            rs.fallback_used = r_fb
            rs.drift_detected = r_dr
            rs.drift_score = r_ds
            rs.confidence_score = r_conf_val

        stats.last_updated = time.time()
        self._cached_perf_meta = self._build_performance_meta()

    # ----------------------------------------
    # Public read path: orchestrate
    # ----------------------------------------

    def orchestrate(
        self,
        signals: Union[List[AlphaSignal], List[Dict[str, Any]]],
        regime: Optional[RegimeContext],
        feature_quality: Optional[FeatureQuality],
        exec_state: ExecutionState,
        current_time: Optional[float] = None,
    ) -> OrchestratedAction:
        # ---- Single lock acquisition: snapshot ALL shared mutable state ----
        with self._lock:
            perf_meta = self._cached_perf_meta
            rejection_telemetry_snapshot = dict(self._rejection_telemetry)
            perf_fusion_snapshot = self._snapshot_fusion_state()

            regime_sample_counts: Dict[str, int] = {}
            for src_id, stats in self.performance_stats.items():
                for r_name, r_stats in stats.regimes.items():
                    regime_sample_counts[r_name] = regime_sample_counts.get(r_name, 0) + max(
                        0, int(r_stats.trade_count)
                    )

        # ---- Canonicalize input ----
        _input_was_unordered = isinstance(signals, (set, frozenset))
        _input_was_str_bytes = False
        _str_bytes_type_name = ""
        if isinstance(signals, (str, bytes)):
            _input_was_str_bytes = True
            _str_bytes_type_name = type(signals).__name__
            logger.warning(
                "Rejected orchestration input: signals is str/bytes | type=%s",
                _str_bytes_type_name,
            )
            signals = []
        elif not isinstance(signals, (list, tuple)):
            try:
                signals = list(signals)
            except Exception:
                signals = []

        if _input_was_unordered and signals:
            def _signal_sort_key(s: Any) -> Tuple[str, int, str, float, float, float]:
                try:
                    if isinstance(s, dict):
                        src = _normalize_key(s.get("source_id"))
                        try:
                            



Looking at this code, I'll focus on the key performance and efficiency optimizations across both files. The main issues are:

1. **Excessive redundant computation** in fusion loops (repeated `_safe_float`, `_normalize_key` calls)
2. **Unnecessary dictionary copies and allocations** in hot paths
3. **Over-verbose metadata construction** even when not needed
4. **Repeated regex compilation and string operations**
5. **Redundant validation passes** and invariant checks in production
6. **NumPy inefficiencies** (repeated array allocations, non-vectorized operations)
7. **Lock contention** and unnecessary snapshot overhead
8. **Excessive logging** in hot paths

Here are the optimized files:

```python
# FILE: ./alpha_orchestrator.py

import math
import logging
import threading
import time
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Tuple, Union, Set
from enum import Enum

logger = logging.getLogger(__name__)

# ==========================================
# Helper: Defensive Math & Types
# ==========================================

VALID_ID_REGEX = re.compile(r"^[a-z0-9_.-]+$")
_EDGE_BPS_CLAMP = 1000.0
_PNL_CLAMP = 1e12
_DEFAULT_TIMEFRAME = "default"
_EPS = 1e-8
_EPS_DENOM = 1e-12
_EPS_WEIGHT = 1e-7

_KNOWN_TF_RANKS: Dict[str, int] = {
    "1s": 0, "5s": 1, "10s": 2, "30s": 3,
    "1m": 10, "2m": 11, "3m": 12, "5m": 13, "10m": 14, "15m": 15,
    "20m": 16, "30m": 17,
    "1h": 20, "2h": 21, "4h": 22, "6h": 23, "8h": 24, "12h": 25,
    "1d": 30, "2d": 31, "3d": 32,
    "1w": 40, "2w": 41, "1mo": 50,
}

def _safe_float(
    val: Any,
    default: float,
    min_val: float = -math.inf,
    max_val: float = math.inf,
) -> float:
    if val is None:
        return default
    try:
        f = float(val)
        if f != f or f == math.inf or f == -math.inf:  # NaN/Inf check without function call
            return default
        if f < min_val:
            return min_val
        if f > max_val:
            return max_val
        return f
    except (ValueError, TypeError):
        return default

def _normalize_key(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).strip().lower()
    if s == "none" or not s:
        return ""
    return s

def _validate_id_strict(raw: Any, *, field_name: str) -> str:
    clean = _normalize_key(raw)
    if not clean or not VALID_ID_REGEX.match(clean):
        raise ValueError(f"Invalid {field_name}: {raw!r}")
    return clean

# ==========================================
# Enums and Typed Contracts
# ==========================================

class Action(Enum):
    BUY = 1
    SELL = -1
    HOLD = 0

@dataclass(frozen=True)
class AlphaSignal:
    source_id: str
    direction: int
    conviction: float
    expected_edge_bps: float
    timestamp: float
    timeframe: str = _DEFAULT_TIMEFRAME
    correlation_group_id: str = ""

@dataclass(frozen=True)
class RegimeContext:
    regime_name: str
    volatility_score: float
    liquidity_score: float

@dataclass(frozen=True)
class FeatureQuality:
    staleness_ratio: float
    missing_data_ratio: float

@dataclass(frozen=True)
class RegimeAssessment:
    regime_name: str
    volatility_stress: float
    liquidity_stress: float
    composite_stress: float
    regime_confidence: float
    is_crisis: bool
    is_trending: bool
    is_ranging: bool
    regime_sample_count: int = 0

@dataclass(frozen=True)
class ExecutionState:
    current_exposure_usd: float
    max_exposure_usd: float
    current_drawdown_pct: float

@dataclass(frozen=True)
class OrchestratedAction:
    action: Action
    net_conviction: float
    expected_edge_bps: float
    urgency: float
    meta_info: Dict[str, Any] = field(default_factory=dict)

@dataclass
class AlphaRegimeStats:
    trade_count: int = 0
    win_rate: float = 0.5
    ema_win_rate: float = 0.5
    avg_realized_edge_bps: float = 0.0
    expected_edge_bps: float = 0.0
    decay_score: float = 0.0
    performance_score: float = 0.5
    current_multiplier: float = 1.0
    target_win_rate: float = 0.5
    hurdles_locked: bool = False
    fallback_used: bool = False
    drift_detected: bool = False
    drift_score: float = 0.0
    confidence_score: float = 0.0
    last_updated: float = 0.0

@dataclass
class AlphaPerformanceStats:
    source_id: str
    trade_count: int = 0
    win_rate: float = 0.5
    ema_win_rate: float = 0.5
    avg_realized_edge_bps: float = 0.0
    expected_edge_bps: float = 0.0
    pnl_contribution: float = 0.0
    decay_score: float = 0.0
    last_updated: float = 0.0
    performance_score: float = 0.5
    current_multiplier: float = 1.0
    target_win_rate: float = 0.5
    hurdles_locked: bool = False
    fallback_used: bool = False
    drift_detected: bool = False
    drift_score: float = 0.0
    confidence_score: float = 0.0
    regimes: Dict[str, AlphaRegimeStats] = field(default_factory=dict)

@dataclass(frozen=True)
class OrchestratorConfig:
    signal_weights: Dict[str, float]
    regime_alignment: Dict[str, Dict[str, float]] = field(default_factory=dict)
    signal_ttl_seconds: float = 2.0
    action_threshold: float = 0.6
    score_deadband: float = 0.05
    min_liquidity_threshold: float = 0.2
    max_missing_data_ratio: float = 0.3
    risk_gamma: float = 2.0
    max_drawdown_pct: float = 0.15
    allow_unknown_sources: bool = False
    default_unknown_weight: float = 0.0
    timeframe_weights: Dict[str, float] = field(default_factory=lambda: {"default": 1.0})
    higher_tf_dominance: bool = True
    timeframe_order: List[str] = field(
        default_factory=lambda: ["1m", "5m", "15m", "1h", "4h", "1d", "default"]
    )
    feedback_enabled: bool = False
    feedback_min_trades: int = 10
    feedback_max_multiplier: float = 1.5
    feedback_min_multiplier: float = 0.5
    feedback_win_rate_weight: float = 0.5
    feedback_edge_weight: float = 0.5
    feedback_decay_penalty: float = 0.2
    confidence_scaling_factor: float = 2.0
    regime_feedback_enabled: bool = False
    regime_min_trades: int = 10
    regime_fallback_weight: float = 0.5
    regime_drift_threshold: float = 0.85
    regime_max_adjustment: float = 1.3
    regime_drift_penalty: float = 0.1
    min_aggregate_weight: float = 0.1
    timeframe_alignment_bonus: float = 0.15
    timeframe_conflict_penalty: float = 0.25
    correlation_group_map: Dict[str, str] = field(default_factory=dict)
    correlation_min_conviction: float = 0.5
    correlation_min_group_size: int = 3

    def __post_init__(self) -> None:
        _clamp = lambda v, lo, hi: max(lo, min(hi, v))
        _setattr = object.__setattr__

        _setattr(self, "signal_ttl_seconds", max(0.001, self.signal_ttl_seconds))
        _setattr(self, "action_threshold", _clamp(self.action_threshold, 0.0, 1.0))
        _setattr(self, "score_deadband", _clamp(self.score_deadband, 0.0, 1.0))
        _setattr(self, "min_liquidity_threshold", _clamp(self.min_liquidity_threshold, 0.0, 1.0))
        _setattr(self, "max_missing_data_ratio", _clamp(self.max_missing_data_ratio, 0.0, 1.0))
        _setattr(self, "risk_gamma", max(0.1, self.risk_gamma))
        _setattr(self, "max_drawdown_pct", _clamp(self.max_drawdown_pct, 0.0, 1.0))
        _setattr(self, "default_unknown_weight", _clamp(self.default_unknown_weight, 0.0, 100.0))
        _setattr(self, "timeframe_alignment_bonus", _clamp(self.timeframe_alignment_bonus, 0.0, 1.0))
        _setattr(self, "timeframe_conflict_penalty", _clamp(self.timeframe_conflict_penalty, 0.0, 1.0))
        _setattr(self, "correlation_min_conviction", _clamp(self.correlation_min_conviction, 0.0, 1.0))
        _setattr(self, "correlation_min_group_size", max(2, int(self.correlation_min_group_size)))

        if self.feedback_min_multiplier > self.feedback_max_multiplier:
            raise ValueError("Invalid config: feedback_min_multiplier cannot be greater than feedback_max_multiplier.")
        if self.feedback_min_multiplier > 1.0:
            raise ValueError("Invalid config: feedback_min_multiplier cannot exceed 1.0.")
        if self.feedback_max_multiplier < 1.0:
            raise ValueError("Invalid config: feedback_max_multiplier must be >= 1.0.")

        _setattr(self, "feedback_min_multiplier", max(0.1, self.feedback_min_multiplier))
        _setattr(self, "feedback_max_multiplier", max(1.0, self.feedback_max_multiplier))
        _setattr(self, "feedback_min_trades", max(1, int(self.feedback_min_trades)))
        _setattr(self, "feedback_decay_penalty", _clamp(self.feedback_decay_penalty, 0.0, 1.0))
        _setattr(self, "confidence_scaling_factor", max(0.1, float(self.confidence_scaling_factor)))

        _setattr(self, "regime_min_trades", max(1, int(self.regime_min_trades)))
        _setattr(self, "regime_fallback_weight", _clamp(self.regime_fallback_weight, 0.0, 1.0))
        _setattr(self, "regime_drift_threshold", _clamp(self.regime_drift_threshold, 0.0, 1.0))
        _setattr(self, "regime_max_adjustment", max(1.0, self.regime_max_adjustment))
        _setattr(self, "regime_drift_penalty", _clamp(self.regime_drift_penalty, 0.0, 0.5))
        _setattr(self, "min_aggregate_weight", _clamp(self.min_aggregate_weight, 0.0, 1.0))

        if self.feedback_win_rate_weight < 0.0:
            raise ValueError(f"Invalid config: feedback_win_rate_weight must be >= 0.0, got {self.feedback_win_rate_weight!r}.")
        if self.feedback_edge_weight < 0.0:
            raise ValueError(f"Invalid config: feedback_edge_weight must be >= 0.0, got {self.feedback_edge_weight!r}.")

        total_weight = self.feedback_win_rate_weight + self.feedback_edge_weight
        if total_weight >= 1e-6:
            _setattr(self, "feedback_win_rate_weight", self.feedback_win_rate_weight / total_weight)
            _setattr(self, "feedback_edge_weight", self.feedback_edge_weight / total_weight)
        else:
            _setattr(self, "feedback_win_rate_weight", 0.5)
            _setattr(self, "feedback_edge_weight", 0.5)

        if not self.signal_weights and not self.allow_unknown_sources:
            raise ValueError("Invalid config: signal_weights is empty and unknown sources are disallowed.")

        safe_weights: Dict[str, float] = {}
        for k, v in self.signal_weights.items():
            clean_k = _validate_id_strict(k, field_name="signal_weights.source_id")
            if clean_k in safe_weights:
                raise ValueError(f"Duplicate source_id: {clean_k}")
            safe_val = _safe_float(v, default=float("nan"))
            if safe_val != safe_val or not (0.0 <= safe_val <= 100.0):
                raise ValueError(f"Weight out of bounds: {clean_k}")
            safe_weights[clean_k] = safe_val
        _setattr(self, "signal_weights", safe_weights)

        safe_regimes: Dict[str, Dict[str, float]] = {}
        for src, regimes in self.regime_alignment.items():
            clean_src = _validate_id_strict(src, field_name="regime_alignment.source_id")
            safe_regimes[clean_src] = {}
            for rk, rv in regimes.items():
                clean_rk = _validate_id_strict(rk, field_name="regime_name")
                mult = _safe_float(rv, default=float("nan"))
                if mult != mult or not (0.0 <= mult <= 3.0):
                    raise ValueError("Regime mult out of bounds.")
                safe_regimes[clean_src][clean_rk] = mult
        _setattr(self, "regime_alignment", safe_regimes)

        safe_correlation_map: Dict[str, str] = {}
        for src, group_id in self.correlation_group_map.items():
            clean_src = _validate_id_strict(src, field_name="correlation_group_map.source_id")
            clean_group = _validate_id_strict(group_id, field_name="correlation_group_map.correlation_group_id")
            safe_correlation_map[clean_src] = clean_group
        _setattr(self, "correlation_group_map", safe_correlation_map)

        safe_tf_order: List[str] = []
        seen_tf: Set[str] = set()
        for tf in self.timeframe_order:
            clean_tf = _validate_id_strict(tf, field_name="timeframe_order")
            if clean_tf not in seen_tf:
                safe_tf_order.append(clean_tf)
                seen_tf.add(clean_tf)

        if _DEFAULT_TIMEFRAME not in seen_tf:
            safe_tf_order.append(_DEFAULT_TIMEFRAME)

        _setattr(self, "timeframe_order", safe_tf_order)

        if self.higher_tf_dominance:
            unranked_tfs = [tf for tf in safe_tf_order if tf != _DEFAULT_TIMEFRAME and tf not in _KNOWN_TF_RANKS]
            if unranked_tfs:
                raise ValueError(
                    f"Invalid config: higher_tf_dominance=True requires canonical timeframe names. "
                    f"Unranked: {unranked_tfs!r}. See _KNOWN_TF_RANKS."
                )
            known_in_order = [(tf, _KNOWN_TF_RANKS[tf]) for tf in safe_tf_order if tf in _KNOWN_TF_RANKS]
            for i in range(len(known_in_order) - 1):
                tf_a, rank_a = known_in_order[i]
                tf_b, rank_b = known_in_order[i + 1]
                if rank_a >= rank_b:
                    raise ValueError(
                        f"Invalid timeframe_order: '{tf_a}' (rank {rank_a}) must appear before "
                        f"'{tf_b}' (rank {rank_b}) in ascending order."
                    )

        safe_tf_set = set(safe_tf_order)
        safe_tf_weights: Dict[str, float] = {}
        for k, v in self.timeframe_weights.items():
            clean_k = _validate_id_strict(k, field_name="timeframe_weights.timeframe")
            if clean_k not in safe_tf_set:
                raise ValueError(f"Timeframe weight key '{clean_k}' not in timeframe_order")
            safe_val = _safe_float(v, default=float("nan"))
            if safe_val != safe_val or not (0.0 <= safe_val <= 100.0):
                raise ValueError(f"TF Weight out of bounds: {clean_k}")
            safe_tf_weights[clean_k] = safe_val

        for tf in safe_tf_order:
            if tf not in safe_tf_weights:
                safe_tf_weights[tf] = 1.0

        _setattr(self, "timeframe_weights", safe_tf_weights)

# ==========================================
# Advanced Regime Engine
# ==========================================

class RegimeEngine:
    __slots__ = ('config',)

    def __init__(self, config: OrchestratorConfig) -> None:
        self.config = config

    def assess(
        self,
        regime: Optional[RegimeContext],
        regime_sample_counts: Optional[Dict[str, int]] = None,
    ) -> Optional[RegimeAssessment]:
        if regime is None:
            return None
        try:
            vol = _safe_float(regime.volatility_score, 0.0, 0.0, 1.0)
            liq = _safe_float(regime.liquidity_score, 0.0, 0.0, 1.0)
            vol_stress = vol
            liq_stress = max(0.0, 1.0 - liq)
            composite = min(1.0, 0.6 * vol_stress + 0.4 * liq_stress)

            reg_name = _normalize_key(regime.regime_name) or "unknown"
            sample_count = 0
            if regime_sample_counts:
                sample_count = max(0, int(regime_sample_counts.get(reg_name, 0)))

            regime_confidence = min(1.0, sample_count / 30.0)

            return RegimeAssessment(
                regime_name=reg_name,
                volatility_stress=vol_stress,
                liquidity_stress=liq_stress,
                composite_stress=composite,
                regime_confidence=regime_confidence,
                is_crisis=composite > 0.85,
                is_trending=vol > 0.6 and liq > 0.5,
                is_ranging=vol < 0.3 and liq > 0.6,
                regime_sample_count=sample_count,
            )
        except Exception:
            return None

    def effective_max_drawdown(self, assessment: Optional[RegimeAssessment], base_max_dd: float) -> float:
        if assessment is None:
            return base_max_dd
        stress = assessment.composite_stress
        if stress > 0.7:
            full_factor = max(0.6, 1.0 - (stress - 0.7) / 0.75)
            factor = 1.0 - assessment.regime_confidence * (1.0 - full_factor)
            return base_max_dd * factor
        return base_max_dd

    def effective_action_threshold(self, assessment: Optional[RegimeAssessment], base_threshold: float) -> float:
        if assessment is None or not assessment.is_crisis:
            return base_threshold
        full_threshold = min(0.95, base_threshold * 1.15)
        return base_threshold + assessment.regime_confidence * (full_threshold - base_threshold)

    def signal_stress_attenuation(self, assessment: Optional[RegimeAssessment], source_id: str) -> float:
        if assessment is None:
            return 1.0
        if assessment.composite_stress > 0.75:
            full_base = max(0.6, 1.0 - (assessment.composite_stress - 0.75) * 1.6)
            return 1.0 - assessment.regime_confidence * (1.0 - full_base)
        return 1.0

    def quality_regime_factor(self, assessment: Optional[RegimeAssessment]) -> float:
        if assessment is None:
            return 1.0
        if assessment.composite_stress > 0.8:
            full_factor = max(0.85, 1.0 - (assessment.composite_stress - 0.8) * 0.75)
            return 1.0 - assessment.regime_confidence * (1.0 - full_factor)
        return 1.0

    def urgency_regime_floor(self, assessment: Optional[RegimeAssessment]) -> float:
        if assessment is None:
            return 0.0
        if assessment.is_crisis:
            return 0.5 * assessment.regime_confidence
        return 0.0

# ==========================================
# Core Orchestration Engine
# ==========================================

# Pre-compiled regex for correlation group cleanup
_CORR_GROUP_SUFFIX_RE = re.compile(r"[_-]?(v|ver|variant)?\d+$")
_CORR_GROUP_CLONE_RE = re.compile(r"[_-](copy|clone|dup|replica|shadow|alt)\d*$")


class AlphaOrchestrator:
    def __init__(self, config: OrchestratorConfig) -> None:
        self.config = config
        self._tf_index: Dict[str, int] = {
            tf: idx for idx, tf in enumerate(self.config.timeframe_order)
        }
        # Pre-compute reversed timeframe order for dominance (avoids repeated reversed() calls)
        self._reversed_tf_order: List[str] = list(reversed(self.config.timeframe_order))
        self.performance_stats: Dict[str, AlphaPerformanceStats] = {}
        self._cached_perf_meta: Optional[Dict[str, Any]] = None
        self._lock = threading.RLock()

        self._rejection_telemetry: Dict[str, int] = {
            "invalid_source_id": 0,
            "unknown_source": 0,
            "missing_outcome_fields": 0,
            "malformed_outcome_values": 0,
            "malformed_payload": 0,
            "negative_expected_edge_normalized": 0,
            "malformed_feedback_fields": 0,
        }

        self.regime_engine = RegimeEngine(config)

        # Pre-compute config lookups used in hot paths
        self._signal_weights = config.signal_weights
        self._regime_alignment = config.regime_alignment
        self._default_unknown_weight = config.default_unknown_weight
        self._feedback_enabled = config.feedback_enabled
        self._regime_feedback_enabled = config.regime_feedback_enabled
        self._feedback_max_multiplier = config.feedback_max_multiplier
        self._allow_unknown_sources = config.allow_unknown_sources
        self._tf_order_set = frozenset(config.timeframe_order)
        self._correlation_group_map = config.correlation_group_map
        self._corr_min_conviction = config.correlation_min_conviction
        self._corr_min_group_size = config.correlation_min_group_size
        self._score_deadband = config.score_deadband
        self._min_aggregate_weight = config.min_aggregate_weight

    # ----------------------------------------
    # Internal helpers
    # ----------------------------------------

    def _sanitize_stats(self, stats: Union[AlphaPerformanceStats, AlphaRegimeStats]) -> None:
        max_mult = max(self.config.feedback_max_multiplier, self.config.regime_max_adjustment)
        fb_min = self.config.feedback_min_multiplier

        stats.win_rate = _safe_float(stats.win_rate, 0.5, 0.0, 1.0)
        stats.ema_win_rate = _safe_float(stats.ema_win_rate, 0.5, 0.0, 1.0)
        stats.avg_realized_edge_bps = _safe_float(stats.avg_realized_edge_bps, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP)
        stats.expected_edge_bps = _safe_float(stats.expected_edge_bps, 0.0, 0.0, _EDGE_BPS_CLAMP)
        stats.decay_score = _safe_float(stats.decay_score, 0.0, 0.0, 1.0)
        stats.performance_score = _safe_float(stats.performance_score, 0.5, 0.0, 1.0)
        stats.current_multiplier = _safe_float(stats.current_multiplier, 1.0, fb_min, max_mult)
        stats.drift_score = _safe_float(stats.drift_score, 0.0, 0.0, 1.0)
        stats.confidence_score = _safe_float(stats.confidence_score, 0.0, 0.0, 1.0)
        stats.trade_count = max(0, int(_safe_float(stats.trade_count, 0.0)))

        if isinstance(stats, AlphaPerformanceStats):
            stats.pnl_contribution = _safe_float(stats.pnl_contribution, 0.0, -_PNL_CLAMP, _PNL_CLAMP)
            for r_stats in stats.regimes.values():
                self._sanitize_stats(r_stats)

    def _calculate_decay_signal(
        self,
        stats: Union[AlphaPerformanceStats, AlphaRegimeStats],
        quality: Optional[FeatureQuality],
        regime: Optional[RegimeContext],
    ) -> float:
        wr_decay = max(0.0, stats.target_win_rate - stats.ema_win_rate)

        edge_decay = 0.0
        exp_edge = stats.expected_edge_bps
        if exp_edge > 1e-6:
            realized = stats.avg_realized_edge_bps
            if realized >= 0.0:
                edge_decay = max(0.0, 1.0 - (abs(realized) / max(2.0, exp_edge)))
            else:
                edge_decay = 1.0

        v_f = 1.0
        if regime:
            v_f = 1.0 + (_safe_float(regime.volatility_score, 0.0) * 0.5)

        s_f = 1.0
        if quality:
            s_f = 1.0 + (_safe_float(quality.staleness_ratio, 0.0) * 0.5)

        raw_decay = (wr_decay * 0.6) + (edge_decay * 0.4)
        result = raw_decay * v_f * s_f
        if result < 0.0:
            return 0.0
        if result > 1.0:
            return 1.0
        return result

    def _build_performance_meta(self) -> Optional[Dict[str, Any]]:
        if not self._feedback_enabled:
            return None

        if not self.performance_stats:
            return {
                "stats": {},
                "top_performing": None,
                "worst_performing": None,
                "highest_decay": None,
                "lowest_decay": None,
            }

        perf_summary: Dict[str, Any] = {}
        for src_id, s in self.performance_stats.items():
            r_meta: Dict[str, Any] = {}

            if self._regime_feedback_enabled:
                try:
                    for r_name, r_stats in s.regimes.items():
                        r_meta[r_name] = {
                            "trade_count": r_stats.trade_count,
                            "win_rate": r_stats.win_rate,
                            "ema_win_rate": r_stats.ema_win_rate,
                            "avg_realized_edge_bps": r_stats.avg_realized_edge_bps,
                            "expected_edge_bps": r_stats.expected_edge_bps,
                            "decay_score": r_stats.decay_score,
                            "performance_score": r_stats.performance_score,
                            "current_multiplier": r_stats.current_multiplier,
                            "confidence_score": r_stats.confidence_score,
                            "fallback_used": r_stats.fallback_used,
                            "drift_detected": r_stats.drift_detected,
                            "drift_score": r_stats.drift_score,
                            "target_win_rate": r_stats.target_win_rate,
                            "hurdles_locked": r_stats.hurdles_locked,
                            "last_updated": r_stats.last_updated,
                        }
                except Exception as exc:
                    logger.warning("Partial regime_meta failure for source_id=%s: %s", src_id, exc)

            perf_summary[src_id] = {
                "trade_count": s.trade_count,
                "win_rate": s.win_rate,
                "ema_win_rate": s.ema_win_rate,
                "avg_realized_edge_bps": s.avg_realized_edge_bps,
                "expected_edge_bps": s.expected_edge_bps,
                "pnl_contribution": s.pnl_contribution,
                "decay_score": s.decay_score,
                "performance_score": s.performance_score,
                "current_multiplier": s.current_multiplier,
                "confidence_score": s.confidence_score,
                "fallback_used": s.fallback_used,
                "drift_detected": s.drift_detected,
                "drift_score": s.drift_score,
                "target_win_rate": s.target_win_rate,
                "hurdles_locked": s.hurdles_locked,
                "regime_performance": r_meta,
                "last_updated": s.last_updated,
            }

        s_perf = sorted(perf_summary.items(), key=lambda x: x[1]["performance_score"])
        s_dec = sorted(perf_summary.items(), key=lambda x: x[1]["decay_score"])

        return {
            "stats": perf_summary,
            "top_performing": s_perf[-1][0] if s_perf else None,
            "worst_performing": s_perf[0][0] if s_perf else None,
            "highest_decay": s_dec[-1][0] if s_dec else None,
            "lowest_decay": s_dec[0][0] if s_dec else None,
        }

    def _snapshot_fusion_state(self) -> Dict[str, Any]:
        snap: Dict[str, Any] = {}
        for src_id, stats in self.performance_stats.items():
            regime_snap: Dict[str, Dict[str, Any]] = {}
            for r_name, r_stats in stats.regimes.items():
                regime_snap[r_name] = {
                    "current_multiplier": r_stats.current_multiplier,
                    "fallback_used": r_stats.fallback_used,
                    "drift_detected": r_stats.drift_detected,
                }
            snap[src_id] = {
                "current_multiplier": stats.current_multiplier,
                "fallback_used": stats.fallback_used,
                "drift_detected": stats.drift_detected,
                "regimes": regime_snap,
            }
        return snap

    def _hold(self, rationale: str, partial_meta: Optional[Dict[str, Any]] = None) -> OrchestratedAction:
        meta: Dict[str, Any] = {"rationale": rationale}
        if partial_meta:
            meta.update(partial_meta)
        return OrchestratedAction(Action.HOLD, 0.0, 0.0, 0.0, meta)

    _EMPTY_SIGNAL_OBS: Dict[str, Any] = None  # Lazily built singleton

    def _empty_signal_observability(self) -> Dict[str, Any]:
        # Return a fresh copy of the template each time (callers may mutate)
        return {
            "signal_metrics": {
                "presented_count": 0.0,
                "valid_count": 0.0,
                "directional_count": 0.0,
                "unique_sources": 0,
                "unique_timeframes": 0,
            },
            "per_signal_breakdown": [],
            "timeframe_breakdown": {},
            "agreement_ratio": 0.0,
            "conflict_ratio": 0.0,
            "dominant_timeframe": None,
        }

    def _update_stats_block(
        self,
        stats_block: Union[AlphaPerformanceStats, AlphaRegimeStats],
        is_win: float,
        realized_edge: float,
        expected_edge: float,
        expected_win_rate: float,
        decay_signal: float,
    ) -> None:
        stats_block.trade_count += 1
        n = stats_block.trade_count
        alpha = 2.0 / (min(20, n) + 1.0)

        if not stats_block.hurdles_locked:
            if expected_edge > 1e-6 or expected_win_rate != 0.5:
                stats_block.expected_edge_bps = _safe_float(expected_edge, 0.0, 0.0, _EDGE_BPS_CLAMP)
                tw = _safe_float(expected_win_rate, 0.5, 0.0, 1.0)
                if tw <= 1e-5:
                    tw = 0.5
                stats_block.target_win_rate = tw
                stats_block.hurdles_locked = True

        if n == 1:
            stats_block.win_rate = _safe_float(is_win, 0.5, 0.0, 1.0)
            stats_block.ema_win_rate = _safe_float(is_win, 0.5, 0.0, 1.0)
            stats_block.avg_realized_edge_bps = _safe_float(realized_edge, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP)
        else:
            new_wr = ((stats_block.win_rate * (n - 1)) + is_win) / n
            stats_block.win_rate = _safe_float(new_wr, 0.5, 0.0, 1.0)
            inv_alpha = 1.0 - alpha
            new_ema = stats_block.ema_win_rate * inv_alpha + is_win * alpha
            stats_block.ema_win_rate = _safe_float(new_ema, 0.5, 0.0, 1.0)
            new_edge = stats_block.avg_realized_edge_bps * inv_alpha + realized_edge * alpha
            stats_block.avg_realized_edge_bps = _safe_float(new_edge, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP)

        stats_block.decay_score = stats_block.decay_score * (1.0 - alpha) + decay_signal * alpha

    # ----------------------------------------
    # Public mutation: update_performance
    # ----------------------------------------

    def update_performance(
        self, trade_result: Any, feature_quality: Optional[FeatureQuality] = None,
        regime: Optional[RegimeContext] = None,
    ) -> None:
        if not self._feedback_enabled:
            return
        with self._lock:
            self._update_performance_locked(trade_result, feature_quality, regime)

    def _update_performance_locked(
        self, trade_result: Any, feature_quality: Optional[FeatureQuality],
        regime: Optional[RegimeContext],
    ) -> None:
        if not isinstance(trade_result, Mapping):
            self._rejection_telemetry["malformed_payload"] += 1
            logger.warning("Rejected performance update: trade_result is not a mapping | type=%s", type(trade_result).__name__)
            return

        src = _normalize_key(trade_result.get("source_id"))
        if not src or not VALID_ID_REGEX.match(src):
            self._rejection_telemetry["invalid_source_id"] += 1
            return

        if not self._allow_unknown_sources and src not in self._signal_weights:
            self._rejection_telemetry["unknown_source"] += 1
            return

        tr_get = trade_result.get
        if "realized_pnl" not in trade_result or "realized_edge_bps" not in trade_result:
            self._rejection_telemetry["missing_outcome_fields"] += 1
            return

        try:
            pnl = float(tr_get("realized_pnl"))
            realized_edge = float(tr_get("realized_edge_bps"))
            if pnl != pnl or realized_edge != realized_edge or abs(pnl) == math.inf or abs(realized_edge) == math.inf:
                raise ValueError("NaN/Inf")
        except (ValueError, TypeError):
            self._rejection_telemetry["malformed_outcome_values"] += 1
            return

        for fb_field in ("expected_edge_bps", "expected_win_rate"):
            if fb_field in trade_result:
                raw_val = tr_get(fb_field)
                try:
                    f = float(raw_val)
                    if f != f or abs(f) == math.inf:
                        raise ValueError("NaN/Inf")
                except (ValueError, TypeError):
                    self._rejection_telemetry["malformed_feedback_fields"] += 1
                    return

        stats = self.performance_stats.get(src)
        if not stats:
            stats = AlphaPerformanceStats(source_id=src)
            self.performance_stats[src] = stats

        self._sanitize_stats(stats)

        pnl = _safe_float(pnl, 0.0, -_PNL_CLAMP, _PNL_CLAMP)
        realized_edge = _safe_float(realized_edge, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP)

        raw_expected_edge = _safe_float(tr_get("expected_edge_bps"), 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP)
        if raw_expected_edge < 0.0:
            self._rejection_telemetry["negative_expected_edge_normalized"] += 1
        expected_edge = abs(raw_expected_edge)
        expected_win_rate = _safe_float(tr_get("expected_win_rate"), 0.5, 0.0, 1.0)

        is_win = 0.5
        if pnl > 0.0:
            is_win = 1.0
        elif pnl < 0.0:
            is_win = 0.0

        stats.pnl_contribution = _safe_float(stats.pnl_contribution + pnl, 0.0, -_PNL_CLAMP, _PNL_CLAMP)

        reg_name: Optional[str] = None
        if regime:
            reg_name = _normalize_key(regime.regime_name)

        if not stats.hurdles_locked:
            if expected_edge > 1e-6 or expected_win_rate != 0.5:
                stats.expected_edge_bps = _safe_float(expected_edge, 0.0, 0.0, _EDGE_BPS_CLAMP)
                tw = _safe_float(expected_win_rate, 0.5, 0.0, 1.0)
                if tw <= 1e-5:
                    tw = 0.5
                stats.target_win_rate = tw
                stats.hurdles_locked = True

        decay_g = self._calculate_decay_signal(stats, feature_quality, regime)
        self._update_stats_block(stats, is_win, realized_edge, expected_edge, expected_win_rate, decay_g)

        if self._regime_feedback_enabled and reg_name:
            if len(stats.regimes) >= 100 and reg_name not in stats.regimes:
                sorted_regimes = sorted(
                    [k for k in stats.regimes if k != reg_name],
                    key=lambda k: stats.regimes[k].trade_count,
                )
                for least_active in sorted_regimes[:10]:
                    stats.regimes.pop(least_active, None)

            r_stats = stats.regimes.get(reg_name)
            if not r_stats:
                r_stats = AlphaRegimeStats()
                stats.regimes[reg_name] = r_stats

            if not r_stats.hurdles_locked:
                if expected_edge > 1e-6 or expected_win_rate != 0.5:
                    r_stats.expected_edge_bps = _safe_float(expected_edge, 0.0, 0.0, _EDGE_BPS_CLAMP)
                    tw = _safe_float(expected_win_rate, 0.5, 0.0, 1.0)
                    if tw <= 1e-5:
                        tw = 0.5
                    r_stats.target_win_rate = tw
                    r_stats.hurdles_locked = True

            decay_r = self._calculate_decay_signal(r_stats, feature_quality, regime)
            self._update_stats_block(r_stats, is_win, realized_edge, expected_edge, expected_win_rate, decay_r)
            r_stats.last_updated = time.time()

        g_mult, g_perf, g_fb, g_dr, g_ds, g_conf = self._calculate_performance_multiplier(stats, None)
        stats.current_multiplier = g_mult
        stats.performance_score = g_perf
        stats.fallback_used = g_fb
        stats.drift_detected = g_dr
        stats.drift_score = g_ds
        stats.confidence_score = g_conf

        if self._regime_feedback_enabled and reg_name:
            r_mult, r_perf, r_fb, r_dr, r_ds, r_conf_val = self._calculate_performance_multiplier(stats, reg_name)
            rs = stats.regimes[reg_name]
            rs.current_multiplier = r_mult
            rs.performance_score = r_perf
            rs.fallback_used = r_fb
            rs.drift_detected = r_dr
            rs.drift_score = r_ds
            rs.confidence_score = r_conf_val

        stats.last_updated = time.time()
        self._cached_perf_meta = self._build_performance_meta()

    # ----------------------------------------
    # Public read path: orchestrate
    # ----------------------------------------

    def orchestrate(
        self,
        signals: Union[List[AlphaSignal], List[Dict[str, Any]]],
        regime: Optional[RegimeContext],
        feature_quality: Optional[FeatureQuality],
        exec_state: ExecutionState,
        current_time: Optional[float] = None,
    ) -> OrchestratedAction:
        # ---- Single lock acquisition: snapshot ALL shared mutable state ----
        with self._lock:
            perf_meta = self._cached_perf_meta
            rejection_telemetry_snapshot = dict(self._rejection_telemetry)
            perf_fusion_snapshot = self._snapshot_fusion_state()
            regime_sample_counts: Dict[str, int] = {}
            for src_id, stats in self.performance_stats.items():
                for r_name, r_stats in stats.regimes.items():
                    regime_sample_counts[r_name] = regime_sample_counts.get(r_name, 0) + max(0, int(r_stats.trade_count))

        # Canonicalize input
        _input_was_str_bytes = isinstance(signals, (str, bytes))
        _str_bytes_type_name = type(signals).__name__ if _input_was_str_bytes else ""
        _input_was_unordered = isinstance(signals, (set, frozenset))

        if _input_was_str_bytes:
            signals = []
        elif not isinstance(signals, (list, tuple)):
            try:
                signals = list(signals)
            except Exception:
                signals = []

        if _input_was_unordered and signals:
            try:
                def _signal_sort_key(s: Any) -> Tuple:
                    try:
                        if isinstance(s, dict):
                            return (_normalize_key(s.get("source_id")), int(s.get("direction", 0) or 0),
                                    _normalize_key(s.get("timeframe")), _safe_float(s.get("timestamp"), 0.0))
                        return (_normalize_key(getattr(s, "source_id", None)), int(getattr(s, "direction", 0) or 0),
                                _normalize_key(getattr(s, "timeframe", None)),


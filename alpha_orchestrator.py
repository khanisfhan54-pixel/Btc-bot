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
                trade_result.get("realized_edge_bps"),
            )
            return

        stats = self.performance_stats.get(src)
        if not stats:
            stats = AlphaPerformanceStats(source_id=src)
            self.performance_stats[src] = stats

        self._sanitize_stats(stats)

        pnl = _safe_float(pnl, 0.0, -_PNL_CLAMP, _PNL_CLAMP)
        # realized_edge_bps: signed (positive = profitable trade, negative = loss).
        realized_edge = _safe_float(realized_edge, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP)

        # FIX 23: Explicit validation for optional feedback fields.
        # If present, they must be finite numeric. NaN/Inf/non-numeric rejects the
        # entire update so garbage cannot enter the learning loop.
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
                        src,
                        fb_field,
                        raw_val,
                    )
                    return

        # expected_edge_bps from trade results normalised to absolute magnitude,
        # matching the signal-level convention enforced in _validate_and_prune.
        # FIX 18: Detect and log contract violations before normalising.
        raw_expected_edge = _safe_float(
            trade_result.get("expected_edge_bps"), 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP
        )
        if raw_expected_edge < 0.0:
            logger.warning(
                "Performance update: negative expected_edge_bps — contract violation | "
                "source_id=%s | raw=%.4f | normalizing to abs().",
                src,
                raw_expected_edge,
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

        # Update Global Tier
        decay_g = self._calculate_decay_signal(stats, feature_quality, regime)
        self._update_stats_block(
            stats, is_win, realized_edge, expected_edge, expected_win_rate, decay_g
        )

        # Update Regime Tier
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

        # Finalise Multipliers
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
        """High-performance pure execution path. Deterministic and side-effect free.

        FIX 1 (complete): ONE lock acquisition at entry snapshots ALL shared mutable
        state: perf_meta, rejection_telemetry, and the fusion-state snapshot consumed
        by _fuse_signals. After the lock is released, every downstream operation reads
        only from immutable local variables. No shared state is accessed again.

        FIX 7: All environmental quantities (stale_ratio, missing_ratio, reg_vol,
        reg_liq, exec_dd) are extracted before any guard so that every early HOLD
        path emits a fully instrumented environmental_context block. A
        decision_telemetry sub-dict isolates per-call signal counts from the
        cumulative rejection_telemetry snapshot.

        FIX 10 (invalid_current_time observability): Environmental quantities,
        environmental_context, and a partial source_policy_summary are computed
        BEFORE the time-resolution guard. The invalid_current_time HOLD path
        therefore returns the same rich metadata shape as every other early exit.
        orchestration_ts is None in this path (no valid timestamp was available).
        Signal counts in decision_telemetry are 0 with time_resolution_failed=True
        marking that _validate_and_prune did not run.

        FIX 12 (invalid_current_time metrics schema parity): The `metrics` field
        inside _invalid_time_exit_meta now uses the full canonical zero-state schema
        that mirrors _validate_and_prune's return dict exactly, replacing the
        previous empty dict `{}`. Downstream consumers can now rely on a consistent
        schema for meta_info["metrics"] across all orchestrate return paths.

        FIX 13 (urgency double quality): final_conviction is already
        conviction_pre_quality * quality_multiplier. _calculate_urgency is called
        with 1.0 as the quality argument so it does not re-apply quality_factor,
        keeping quality's effect on urgency exactly once via the conviction input.

        FIX 14 (risk_pressure rename): meta_info["risk_metrics"]["risk_penalty"]
        is now "risk_pressure" to match the actual semantics of the value
        (util^gamma — utilisation-proportional execution pressure, not a haircut).
        See also _apply_risk_overlay docstring.

        FIX 15 (backward-compatible alias): "risk_penalty" is retained as an alias
        inside meta_info["risk_metrics"] for consumers that have not yet migrated.

        FIX 17 (observability parity): All early HOLD paths (pre-validation and
        post-validation) include zeroed defaults for signal_metrics,
        per_signal_breakdown, timeframe_breakdown, agreement_ratio, conflict_ratio,
        and dominant_timeframe via _empty_signal_observability().

        FIX 19: per_signal_breakdown entries now carry a "timeframe" key for
        self-contained MTF forensics.

        FIX 20: Smooth bounded volatility attenuation replaces the hard step
        at 0.8 to prevent threshold-flapping when vol oscillates around the boundary.

        FIX 21: Accepts any finite iterable; normalised to list early.

        FIX 22: Drawdown-based risk gating removed from _fuse_signals(). Risk is
        applied exclusively post-fusion via _apply_risk_overlay, preserving the
        required architecture and fail-closed semantics.

        FIX 25: str/bytes rejected before list() conversion; unordered containers
        (set, frozenset) are deterministically sorted by signal fields so
        observability output is reproducible without changing fusion math.

        FIX 25b: Unordered-iterable sort key uses only safe normalization helpers
        and never raises on malformed field types.  Sorting itself is wrapped in a
        try/except so any unexpected failure falls back to unsorted materialization.
        str/bytes inputs emit an explicit warning and are surfaced in metadata as
        invalid_input_type rather than being silently indistinguishable from an
        empty batch.

        FIX 26: When both current_time is invalid AND signals is str/bytes, the
        early time-resolution guard now carries the invalid_input_type marker in
        decision_telemetry and rejection_details, ensuring malformed string payloads
        are identifiable regardless of which guard fires first.

        FIX 27 (risk hard-stop urgency override): When the risk overlay has
        triggered a full stop (dd_breach or zero_exp), final_conviction is already
        0.0 and action will be HOLD. The crisis urgency floor in _calculate_urgency
        must not override this: a non-zero urgency paired with action=HOLD sends
        contradictory signals to downstream OMS routers and violates the requirement
        that crisis must respect the risk overlay. urgency is forced to 0.0 when
        risk_rat is a hard-stop sentinel.

        REGIME INTEGRATION:
        - RegimeAssessment is computed once at entry and propagated read-only.
        - Regime stress attenuates signal weights in fusion.
        - Regime stress tightens drawdown limits dynamically.
        - Regime stress degrades quality multiplier further.
        - Crisis regimes raise action threshold and cap urgency.
        - All regime adjustments are visible in meta_info.
        """
        # ---- Single lock acquisition: snapshot ALL shared mutable state ----
        with self._lock:
            perf_meta = self._cached_perf_meta
            rejection_telemetry_snapshot = dict(self._rejection_telemetry)
            # Fusion-state snapshot: pure-data copy of fields needed by _fuse_signals.
            # This prevents a race where update_performance mutates current_multiplier
            # while _fuse_signals is mid-iteration over signals.
            perf_fusion_snapshot = self._snapshot_fusion_state()

            # Build per-regime sample counts for conservative confidence estimation.
            regime_sample_counts: Dict[str, int] = {}
            for src_id, stats in self.performance_stats.items():
                for r_name, r_stats in stats.regimes.items():
                    regime_sample_counts[r_name] = regime_sample_counts.get(r_name, 0) + max(
                        0, int(r_stats.trade_count)
                    )
        # Lock released. All code below is read-only w.r.t. shared state.

        # FIX 25: Canonicalize input: reject str/bytes explicitly (they are iterable
        # but invalid payloads), materialize other non-sequence iterables once into a
        # list, and enforce deterministic ordering for unordered containers so
        # observability output is auditable and reproducible across runs.
        _input_was_unordered = isinstance(signals, (set, frozenset))
        _input_was_str_bytes = False
        _str_bytes_type_name = ""
        if isinstance(signals, (str, bytes)):
            _input_was_str_bytes = True
            _str_bytes_type_name = type(signals).__name__
            logger.warning(
                "Rejected orchestration input: signals is str/bytes, not a sequence of signals | type=%s",
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
                        direction = 0
                        try:
                            direction = int(s.get("direction", 0) or 0)
                        except (ValueError, TypeError):
                            direction = 0
                        tf = _normalize_key(s.get("timeframe"))
                        ts = _safe_float(s.get("timestamp"), 0.0)
                        conv = _safe_float(s.get("conviction"), 0.0, 0.0, 1.0)
                        edge = _safe_float(s.get("expected_edge_bps"), 0.0, 0.0, _EDGE_BPS_CLAMP)
                    else:
                        src = _normalize_key(getattr(s, "source_id", None))
                        direction = 0
                        try:
                            direction = int(getattr(s, "direction", 0) or 0)
                        except (ValueError, TypeError):
                            direction = 0
                        tf = _normalize_key(getattr(s, "timeframe", None))
                        ts = _safe_float(getattr(s, "timestamp", None), 0.0)
                        conv = _safe_float(getattr(s, "conviction", None), 0.0, 0.0, 1.0)
                        edge = _safe_float(getattr(s, "expected_edge_bps", None), 0.0, 0.0, _EDGE_BPS_CLAMP)
                    return (src, direction, tf, ts, conv, edge)
                except Exception:
                    return ("", 0, "", 0.0, 0.0, 0.0)

            try:
                signals = sorted(signals, key=_signal_sort_key)
            except Exception:
                # Fallback: keep unsorted materialized list and let validation reject bad items
                pass

        # FIX 10: Extract ALL environmental quantities from input parameters before
        # the time-resolution guard. None of these values depend on `now`, so they
        # are available even when current_time is invalid. Moving this block above
        # the time check ensures the invalid_current_time HOLD path has a full
        # environmental_context and source_policy_summary, matching every other
        # early exit path.
        exec_dd = _safe_float(exec_state.current_drawdown_pct, 0.0, 0.0, 1.0)
        reg_vol = _safe_float(regime.volatility_score, 1.0, 0.0, 1.0) if regime else 1.0
        reg_liq = _safe_float(regime.liquidity_score, 0.0, 0.0, 1.0) if regime else 0.0

        stale_ratio = 0.0
        missing_ratio = 0.0
        if feature_quality:
            try:
                stale_ratio = _safe_float(feature_quality.staleness_ratio, 0.0, 0.0, 1.0)
                missing_ratio = _safe_float(feature_quality.missing_data_ratio, 0.0, 0.0, 1.0)
            except Exception:
                pass

        reg_name: str = "unknown"
        if regime:
            rn = _normalize_key(regime.regime_name)
            if rn:
                reg_name = rn

        # ---- Advanced Regime Assessment ----
        regime_assessment = self.regime_engine.assess(regime, regime_sample_counts)
        effective_max_dd = self.config.max_drawdown_pct
        if self.regime_engine:
            effective_max_dd = self.regime_engine.effective_max_drawdown(regime_assessment, self.config.max_drawdown_pct)

        # environmental_context: fully populated from parameters, no `now` needed.
        environmental_context: Dict[str, Any] = {
            "stale_ratio": stale_ratio,
            "missing_ratio": missing_ratio,
            "reg_vol": reg_vol,
            "reg_liq": reg_liq,
            "exec_drawdown_pct": exec_dd,
            "regime_name": reg_name,
            "liquidity_threshold": self.config.min_liquidity_threshold,
            "missing_data_threshold": self.config.max_missing_data_ratio,
            "max_drawdown_threshold": self.config.max_drawdown_pct,
            "regime_adjusted_max_dd": effective_max_dd,
            "signal_ttl_seconds": self.config.signal_ttl_seconds,
            "regime_assessment": regime_assessment.__dict__ if regime_assessment else None,
        }

        # FIX 10: Raw presented count is available before signal validation.
        _presented_raw: int
        if _input_was_str_bytes:
            _presented_raw = 1
        else:
            _presented_raw = len(signals)

        # FIX 10: source_policy_summary config fields are available before signal
        # validation. Dynamic fields (known_sources_active, unknown_sources_accepted)
        # require validated signals and are populated with empty lists here; they are
        # overwritten with real data after _validate_and_prune runs below.
        # The pre-validation instance is used only by the invalid_current_time path.
        _pre_val_source_policy: Dict[str, Any] = {
            "allow_unknown_sources": self.config.allow_unknown_sources,
            "known_sources_active": [],
            "unknown_sources_accepted": [],
            "default_unknown_weight": self.config.default_unknown_weight
            if self.config.allow_unknown_sources
            else None,
        }

        # ---- Time resolution ----
        # FIX 10 / FIX 12: _invalid_time_exit_meta is built before the resolution
        # attempt so both the NaN/Inf branch and the TypeError/ValueError branch
        # share a single metadata dict without duplication.
        #
        # FIX 12: `metrics` now uses the full canonical zero-state schema that
        # mirrors _validate_and_prune's return dict exactly. All integer counters
        # default to 0; unknown_sources_accepted is an empty list to match the
        # type contract. orchestration_ts is None (honest: no valid timestamp).
        # time_resolution_failed=True in decision_telemetry marks that
        # _validate_and_prune did not run.
        if current_time is not None:
            _invalid_time_exit_meta: Dict[str, Any] = {
                "orchestration_ts": None,
                # FIX 12: Full canonical zero-state metrics schema (was `{}`).
                "metrics": {
                    "accepted": 0,
                    "stale": 0,
                    "invalid": 0,
                    "future_timestamp": 0,
                    "negative_edge_normalized": 0,
                    "unknown_sources_accepted": [],
                    "duplicates_removed": 0,
                },
                "rejection_details": [],
                "fusion_stats": {
                    "presented_count": float(_presented_raw),
                    "valid_count": 0.0,
                    "directional_count": 0.0,
                },
                "alpha_performance": perf_meta,
                "rejection_telemetry": rejection_telemetry_snapshot,
                "environmental_context": environmental_context,
                "decision_telemetry": {
                    "signals_presented": _presented_raw,
                    "signals_accepted": 0,
                    "signals_stale": 0,
                    "signals_invalid": 0,
                    "signals_future_ts": 0,
                    "negative_edge_normalized": 0,
                    "unknown_sources_accepted_count": 0,
                    "time_resolution_failed": True,
                    "cumulative_rejection_telemetry": rejection_telemetry_snapshot,
                },
                "source_policy_summary": _pre_val_source_policy,
                # FIX 17: Zeroed observability defaults for schema parity.
                **self._empty_signal_observability(),
            }
            # FIX 26: Surface invalid_input_type in the early time-resolution guard
            # so that malformed str/bytes payloads are identifiable even when the
            # time check fires first. Rationale remains "invalid_current_time".
            if _input_was_str_bytes:
                _invalid_time_exit_meta["metrics"]["invalid"] = 1
                _invalid_time_exit_meta["rejection_details"].append(
                    {"reason": "invalid_input_type", "type": _str_bytes_type_name}
                )
                _invalid_time_exit_meta["decision_telemetry"]["signals_invalid"] = 1
            try:
                now = float(current_time)
                if math.isnan(now) or math.isinf(now):
                    return self._hold("invalid_current_time", _invalid_time_exit_meta)
            except (ValueError, TypeError):
                return self._hold("invalid_current_time", _invalid_time_exit_meta)
        else:
            now = time.time()

        # ---- Signal Validation (pure; no shared state access) ----
        # FIX 9: _validate_and_prune now returns unknown_sources_accepted IDs in metrics.
        valid, metrics, rejection_details = self._validate_and_prune(signals, now)

        # FIX 25b: Preserve explicit invalid-input observability for str/bytes.
        if _input_was_str_bytes:
            metrics["invalid"] = metrics.get("invalid", 0) + 1
            rejection_details.append(
                {"reason": "invalid_input_type", "type": _str_bytes_type_name}
            )

        # FIX 9: Extract unknown accepted sources from per-call metrics.
        unknown_accepted: List[str] = metrics.get("unknown_sources_accepted", [])

        active_source_ids = {s.source_id for s in valid}

        presented_count = float(_presented_raw)
        valid_count = float(len(valid))
        active_count = float(sum(1 for s in valid if s.direction != 0))

        fusion_stats = {
            "presented_count": presented_count,
            "valid_count": valid_count,
            "directional_count": active_count,
        }

        # FIX 7: decision_telemetry groups all per-call diagnostic counts so that
        # a single orchestration call is self-explaining without needing the
        # cumulative counters. Cumulative snapshot is kept separately for trend
        # analysis. Both are always present; neither mutates shared state.
        decision_telemetry: Dict[str, Any] = {
            "signals_presented": int(presented_count),
            "signals_accepted": metrics.get("accepted", 0),
            "signals_stale": metrics.get("stale", 0),
            "signals_invalid": metrics.get("invalid", 0),
            "signals_future_ts": metrics.get("future_timestamp", 0),
            "negative_edge_normalized": metrics.get("negative_edge_normalized", 0),
            "unknown_sources_accepted_count": len(unknown_accepted),
            "cumulative_rejection_telemetry": rejection_telemetry_snapshot,
        }

        # FIX 9: Source policy summary — makes allow_unknown_sources config drift
        # and unknown-signal acceptance immediately visible per decision.
        # This is the fully populated version (replaces _pre_val_source_policy).
        known_source_ids = sorted(active_source_ids & set(self.config.signal_weights))
        source_policy_summary: Dict[str, Any] = {
            "allow_unknown_sources": self.config.allow_unknown_sources,
            "known_sources_active": known_source_ids,
            "unknown_sources_accepted": sorted(set(unknown_accepted)),
            "default_unknown_weight": self.config.default_unknown_weight
            if self.config.allow_unknown_sources
            else None,
        }

        # FIX 6: Use the snapshotted telemetry (state at orchestration entry) so
        # concurrent update_performance calls cannot mutate base_meta mid-flight.
        # Per-call signal metrics (accepted/stale/invalid/negative_edge_normalized)
        # are in `metrics` and accurately reflect this call in isolation.
        base_meta: Dict[str, Any] = {
            "orchestration_ts": now,
            "metrics": metrics,
            "rejection_details": rejection_details,
            "fusion_stats": fusion_stats,
            "alpha_performance": perf_meta,
            "rejection_telemetry": rejection_telemetry_snapshot,
            # FIX 7: Always present in every HOLD path.
            "environmental_context": environmental_context,
            # FIX 7: Per-call isolated telemetry.
            "decision_telemetry": decision_telemetry,
            # FIX 9: Per-call source policy visibility.
            "source_policy_summary": source_policy_summary,
            # FIX 17: Zeroed observability defaults for schema parity.
            **self._empty_signal_observability(),
        }

        # ---- Guard: Performance Failure (Source) — scoped to active sources ----
        if perf_meta and perf_meta.get("highest_decay"):
            worst_src = perf_meta["highest_decay"]
            if worst_src in active_source_ids:
                worst_val = perf_meta["stats"].get(worst_src, {}).get("decay_score", 0.0)
                worst_stats = perf_meta["stats"].get(worst_src, {})
                if (
                    worst_val > 0.85
                    and worst_stats.get("trade_count", 0)
                    > self.config.feedback_min_trades
                ):
                    meta_payload = base_meta.copy()
                    meta_payload.update(
                        {"source_id": worst_src, "decay_score": worst_val}
                    )
                    return self._hold("decay_drift_limit_exceeded", meta_payload)

        # ---- Guard: Regime Drift Safety — scoped to current regime and active sources ----
        if self.config.regime_feedback_enabled and perf_meta and perf_meta.get("stats"):
            for src_id in active_source_ids:
                src_stats = perf_meta["stats"].get(src_id)
                if not src_stats or not isinstance(src_stats, dict):
                    continue
                reg_perf = src_stats.get("regime_performance")
                if not reg_perf or not isinstance(reg_perf, dict):
                    continue
                reg_data = reg_perf.get(reg_name)
                if not reg_data or not isinstance(reg_data, dict):
                    continue
                if (
                    reg_data.get("drift_detected")
                    and reg_data.get("trade_count", 0) > self.config.regime_min_trades
                ):
                    meta_payload = base_meta.copy()
                    meta_payload.update(
                        {
                            "source_id": src_id,
                            "regime": reg_name,
                            "drift_detected": True,
                            "regime_trade_count": reg_data.get("trade_count", 0),
                            "regime_drift_score": reg_data.get("drift_score", 0.0),
                            "rationale": "regime_drift_safety_brake",
                        }
                    )
                    return self._hold("regime_drift_safety_brake", meta_payload)

        # ---- Guard: Data Quality ----
        # FIX 7: stale_ratio and missing_ratio are already in environmental_context
        # inside base_meta; no additional enrichment needed here.
        if missing_ratio > self.config.max_missing_data_ratio:
            return self._hold("poor_feature_quality", base_meta)

        # ---- Guard: Market Stress ----
        if reg_liq < self.config.min_liquidity_threshold:
            return self._hold("insufficient_liquidity", base_meta)

        if not valid:
            if _input_was_str_bytes:
                return self._hold("invalid_input_type", base_meta)
            return self._hold("no_valid_signals", base_meta)

        # ---- Environmental Multipliers ----
        # FIX 4: _calculate_quality_multipliers returns a full breakdown so that
        # each sub-component (stale, missing, vol amplifier) is independently
        # surfaced in meta_info.quality_metrics for forensic debugging.
        fq_multipliers = self._calculate_quality_multipliers(stale_ratio, missing_ratio, reg_vol, regime_assessment=regime_assessment)
        quality_multiplier = fq_multipliers["combined_multiplier"]

        signals_by_tf: Dict[str, List[AlphaSignal]] = {}
        for s in valid:
            signals_by_tf.setdefault(s.timeframe, []).append(s)

        # ---- Signal Fusion (uses immutable perf_fusion_snapshot, not live stats) ----
        tf_results: Dict[str, Dict[str, Any]] = {}
        per_signal_breakdown: List[Dict[str, Any]] = []
        for tf, tf_sigs in signals_by_tf.items():
            score, edge, meta_fusion = self._fuse_signals(
                tf_sigs, reg_name, exec_dd, perf_fusion_snapshot, regime_assessment=regime_assessment
            )
            tf_results[tf] = {
                "net_score": score,
                "blended_edge": edge,
                "fusion_meta": meta_fusion,
            }
            if meta_fusion and "breakdown" in meta_fusion:
                # FIX 19: Stamp timeframe on every row so MTF forensics are self-contained.
                for entry in meta_fusion["breakdown"]:
                    entry["timeframe"] = tf
                per_signal_breakdown.extend(meta_fusion["breakdown"])

        net_score, blended_edge, mtf_meta = self._combine_timeframes(tf_results)

        # ---- Agreement / conflict ratios ----
        agreement_ratio = 0.0
        conflict_ratio = 0.0
        dominant_timeframe = mtf_meta.get("dominant") if mtf_meta else None

        if len(tf_results) > 1:
            tf_dirs: List[int] = []
            for tf, res in tf_results.items():
                s = res["net_score"]
                if s > self.config.score_deadband:
                    tf_dirs.append(1)
                elif s < -self.config.score_deadband:
                    tf_dirs.append(-1)
                else:
                    tf_dirs.append(0)

            non_zero_dirs = [d for d in tf_dirs if d != 0]
            if len(non_zero_dirs) >= 2:
                agreements = 0
                conflicts = 0
                pairs = 0
                for i in range(len(non_zero_dirs)):
                    for j in range(i + 1, len(non_zero_dirs)):
                        pairs += 1
                        if non_zero_dirs[i] == non_zero_dirs[j]:
                            agreements += 1
                        else:
                            conflicts += 1
                if pairs > 0:
                    agreement_ratio = agreements / pairs
                    conflict_ratio = conflicts / pairs

        # ---- Risk overlay ----
        risk_scaler, risk_pen, util, risk_rat = self._apply_risk_overlay(
            1.0, exec_state, exec_dd, max_drawdown_pct=effective_max_dd
        )

        # FIX 20: Smooth bounded volatility attenuation. Replaces previous hard step
        # at 0.8 to prevent threshold-flapping when vol oscillates around the boundary.
        # Ramps linearly from 1.0 at vol=0.7 down to 0.9 at vol=0.9, clamped flat
        # beyond that. Bounded to [0.9, 1.0]; deterministic; fail-closed unchanged.
        vol_scaler = 1.0
        if reg_vol > 0.7:
            vol_scaler = max(0.9, 1.0 - (reg_vol - 0.7) * 0.5)

        # FIX 4: Capture conviction before and after quality scaling so the meta
        # shows exactly how much conviction was lost to data quality degradation.
        conviction_pre_quality = max(0.0, min(1.0, abs(net_score) * risk_scaler * vol_scaler))
        final_conviction = max(0.0, min(1.0, conviction_pre_quality * quality_multiplier))

        # FIX 13: final_conviction already incorporates quality_multiplier once
        # (= conviction_pre_quality * quality_multiplier). Passing 1.0 as the
        # quality argument prevents _calculate_urgency from applying quality a
        # second time. Quality reaches urgency exactly once via final_conviction.
        urgency = self._calculate_urgency(
            final_conviction, reg_vol, reg_liq, 1.0, agreement_ratio, conflict_ratio, regime_assessment=regime_assessment
        )

        # FIX 27: Risk hard-stop urgency override.
        # When the risk overlay has triggered a full stop (drawdown breach or zero
        # exposure capacity), final_conviction is already 0.0 and the action will be
        # HOLD. The crisis urgency floor in _calculate_urgency must NOT override this:
        # a non-zero urgency paired with action=HOLD sends contradictory signals to
        # downstream OMS routers and violates the requirement that crisis regimes must
        # still respect the risk overlay and drawdown shutdown.
        # Urgency must be zero whenever risk has hard-stopped all activity.
        if risk_rat in ("dd_breach", "zero_exp"):
            urgency = 0.0

        timeframe_breakdown: Dict[str, Any] = {
            tf: {"net_score": res["net_score"], "blended_edge": res["blended_edge"]}
            for tf, res in tf_results.items()
        }

        signal_metrics = {
            "presented_count": presented_count,
            "valid_count": valid_count,
            "directional_count": active_count,
            "unique_sources": len(active_source_ids),
            "unique_timeframes": len(signals_by_tf),
        }

        meta_payload = base_meta.copy()
        meta_payload.update(
            {
                "final_conviction": final_conviction,
                "mtf_metrics": mtf_meta,
                "tf_fusion_breakdown": tf_results,
                # FIX 14: Renamed "risk_penalty" → "risk_pressure".
                # risk_pen = util^gamma: utilisation-proportional execution pressure
                # (0.0 = no pressure, 1.0 = drawdown / full utilisation ceiling hit).
                # "risk_penalty" implied a signed haircut; "risk_pressure" matches
                # the actual semantics. See also _apply_risk_overlay docstring.
                # FIX 15: Backward-compatible alias preserved for this revision.
                "risk_metrics": {
                    "scaler": risk_scaler,
                    "utilization": util,
                    "risk_pressure": risk_pen,
                    "risk_penalty": risk_pen,
                    "regime_adjusted_max_dd": effective_max_dd,
                },
                # FIX 4: Full quality breakdown. Explains exactly how conviction was
                # degraded: which sub-factor (staleness vs missing data vs vol) drove
                # the combined_multiplier and by how much. conviction_pre_quality vs
                # conviction_post_quality shows the absolute conviction loss.
                "quality_metrics": {
                    "stale_ratio": stale_ratio,
                    "missing_ratio": missing_ratio,
                    "vol_amplifier": fq_multipliers.get("vol_amplifier"),
                    "stale_multiplier": fq_multipliers.get("stale_multiplier"),
                    "missing_multiplier": fq_multipliers.get("missing_multiplier"),
                    "regime_factor": fq_multipliers.get("regime_factor"),
                    "combined_multiplier": quality_multiplier,
                    "conviction_pre_quality": conviction_pre_quality,
                    "conviction_post_quality": final_conviction,
                },
                "signal_metrics": signal_metrics,
                "per_signal_breakdown": per_signal_breakdown,
                "timeframe_breakdown": timeframe_breakdown,
                "agreement_ratio": agreement_ratio,
                "conflict_ratio": conflict_ratio,
                "dominant_timeframe": dominant_timeframe,
                "regime_assessment": regime_assessment.__dict__ if regime_assessment else None,
            }
        )

        return self._generate_decision(
            net_score, final_conviction, blended_edge, urgency, risk_rat, meta_payload, regime_assessment=regime_assessment
        )

    # ----------------------------------------
    # Signal fusion
    # ----------------------------------------

    def _fuse_signals(
        self,
        signals: List[AlphaSignal],
        regime_name: str,
        safe_dd: float,
        perf_snapshot: Dict[str, Any],
        regime_assessment: Optional[RegimeAssessment] = None,
    ) -> Tuple[float, float, Dict[str, Any]]:
        """Normalized directional weighted average with two-pass dominance cap.

        FIX 1: Uses perf_snapshot (a pure-data dict captured under _lock at the
        start of orchestrate) instead of self.performance_stats directly. This
        eliminates the race where a concurrent update_performance call could mutate
        current_multiplier while this loop is mid-iteration. The snapshot is
        structurally identical to what self.performance_stats would have provided,
        so the math is unchanged.

        FIX 5: expected_edge_bps on each AlphaSignal is absolute magnitude (>= 0)
        as enforced at ingress by _validate_and_prune. Signed blended edge is
        computed by multiplying by direction here, preventing double-signing.

        FIX 9: source_policy ("known" / "unknown_defaulted") is stamped on every
        breakdown entry so downstream consumers can distinguish signals that were
        routed through allow_unknown_sources fallback from those with explicit weights.

        FIX 22: Drawdown-based risk gating removed from this method. Risk is applied
        exclusively post-fusion via _apply_risk_overlay in orchestrate().
        """
        weighted_sum = 0.0
        weighted_edge = 0.0
        denom = 0.0
        raw_weighted_sum = 0.0
        raw_weighted_edge = 0.0
        raw_denom = 0.0
        adjusted_total_weight = 0.0
        breakdown: List[Dict[str, Any]] = []
        signal_count = len(signals)
        correlation_group_sizes: Dict[Tuple[int, str, str], int] = {}

        def _resolve_correlation_group_id(signal: AlphaSignal) -> str:
            explicit_group_id = _normalize_key(getattr(signal, "correlation_group_id", ""))
            if explicit_group_id:
                return explicit_group_id
            mapped_group_id = _normalize_key(self.config.correlation_group_map.get(signal.source_id, ""))
            if mapped_group_id:
                return mapped_group_id
            sid = _normalize_key(signal.source_id)
            sid = re.sub(r"[_-]?(v|ver|variant)?\d+$", "", sid)
            sid = re.sub(r"[_-](copy|clone|dup|replica|shadow|alt)\d*$", "", sid)
            return sid or _normalize_key(signal.source_id)

        # Pre-pass: build correlation bucket sizes.
        for s in signals:
            direction = 0
            if s.direction > 0:
                direction = 1
            elif s.direction < 0:
                direction = -1
            if direction == 0:
                continue
            group_key = (
                direction,
                _normalize_key(s.timeframe),
                _resolve_correlation_group_id(s),
            )
            correlation_group_sizes[group_key] = correlation_group_sizes.get(group_key, 0) + 1

        # Pass 1: Sum adjusted weights for dynamic dominance capping.
        for s in signals:
            base_w = _safe_float(
                self.config.signal_weights.get(s.source_id, self.config.default_unknown_weight),
                0.0,
            )
            perf_mult = 1.0

            # FIX 1: Read from snapshot, not live self.performance_stats.
            st_snap = perf_snapshot.get(s.source_id) if perf_snapshot else None
            if self.config.feedback_enabled and st_snap:
                perf_mult = st_snap["current_multiplier"]
                if self.config.regime_feedback_enabled:
                    r_snap = st_snap["regimes"].get(regime_name)
                    if r_snap:
                        perf_mult = r_snap["current_multiplier"]

            eff_w = min(base_w * perf_mult, self.config.feedback_max_multiplier * base_w)

            alignment = _safe_float(
                self.config.regime_alignment.get(s.source_id, {}).get(regime_name, 1.0),
                1.0,
                0.0,
                3.0,
            )

            stress_attenuation = 1.0
            if self.regime_engine and regime_assessment:
                stress_attenuation = self.regime_engine.signal_stress_attenuation(regime_assessment, s.source_id)

            direction = 0
            if s.direction > 0:
                direction = 1
            elif s.direction < 0:
                direction = -1

            conviction = _safe_float(s.conviction, 0.0, 0.0, 1.0)
            raw_weight_contrib = eff_w * conviction * alignment * stress_attenuation
            if raw_weight_contrib > 1e-7:
                raw_denom += raw_weight_contrib

            correlation_penalty = 1.0
            if direction != 0:
                group_key = (
                    direction,
                    _normalize_key(s.timeframe),
                    _resolve_correlation_group_id(s),
                )
                similar_count = max(1, int(correlation_group_sizes.get(group_key, 1)))
                if (
                    conviction >= self.config.correlation_min_conviction
                    and similar_count >= self.config.correlation_min_group_size
                ):
                    smooth_size = 1.0 + 0.5 * float(similar_count - 1)
                    correlation_penalty = 1.0 / math.sqrt(smooth_size)
                if not math.isfinite(correlation_penalty):
                    correlation_penalty = 1.0

            effective_weight_contrib = raw_weight_contrib * correlation_penalty
            if effective_weight_contrib > 1e-7:
                adjusted_total_weight += effective_weight_contrib

        # Pass 2: Fusion with Concentration Guard.
        for s in signals:
            base_w = _safe_float(
                self.config.signal_weights.get(s.source_id, self.config.default_unknown_weight),
                0.0,
            )
            perf_mult = 1.0
            is_fb = False
            is_dr = False

            # FIX 9: Determine source policy for this signal entry.
            # "known"             – source_id has an explicit weight in signal_weights.
            # "unknown_defaulted" – source_id accepted under allow_unknown_sources=True
            #                       and weighted via default_unknown_weight.
            source_policy = (
                "known"
                if s.source_id in self.config.signal_weights
                else "unknown_defaulted"
            )

            # FIX 1: Read from snapshot, not live self.performance_stats.
            st_snap = perf_snapshot.get(s.source_id) if perf_snapshot else None
            if self.config.feedback_enabled and st_snap:
                perf_mult = st_snap["current_multiplier"]
                is_fb = st_snap["fallback_used"]
                is_dr = st_snap["drift_detected"]
                if self.config.regime_feedback_enabled:
                    r_snap = st_snap["regimes"].get(regime_name)
                    if r_snap:
                        perf_mult = r_snap["current_multiplier"]
                        is_fb = r_snap["fallback_used"]
                        is_dr = r_snap["drift_detected"]

            eff_w = min(base_w * perf_mult, self.config.feedback_max_multiplier * base_w)

            direction = 0
            if s.direction > 0:
                direction = 1
            elif s.direction < 0:
                direction = -1

            alignment = _safe_float(
                self.config.regime_alignment.get(s.source_id, {}).get(regime_name, 1.0),
                1.0,
                0.0,
                3.0,
            )

            stress_attenuation = 1.0
            if self.regime_engine and regime_assessment:
                stress_attenuation = self.regime_engine.signal_stress_attenuation(regime_assessment, s.source_id)

            conviction = _safe_float(s.conviction, 0.0, 0.0, 1.0)
            raw_weight_contrib = eff_w * conviction * alignment * stress_attenuation

            correlation_group_id = ""
            if direction != 0:
                timeframe_bucket = _normalize_key(s.timeframe)
                correlation_group_id = _resolve_correlation_group_id(s)
                correlation_group_key = (direction, timeframe_bucket, correlation_group_id)
            else:
                correlation_group_key = None

            similar_count = 1
            correlation_penalty = 1.0
            if correlation_group_key is not None:
                similar_count = max(1, int(correlation_group_sizes.get(correlation_group_key, 1)))
                if (
                    conviction >= self.config.correlation_min_conviction
                    and similar_count >= self.config.correlation_min_group_size
                ):
                    smooth_size = 1.0 + 0.5 * float(similar_count - 1)
                    correlation_penalty = 1.0 / math.sqrt(smooth_size)
                if not math.isfinite(correlation_penalty):
                    correlation_penalty = 1.0
                    similar_count = 1

            weight_contrib = raw_weight_contrib * correlation_penalty
            effective_weight_contrib = weight_contrib

            if weight_contrib <= 1e-7:
                breakdown.append(
                    {
                        "source_id": s.source_id,
                        "source_policy": source_policy,
                        "perf_multiplier": perf_mult,
                        "timeframe": _normalize_key(s.timeframe),
                        "direction": direction,
                        "expected_edge_bps": s.expected_edge_bps,
                        "base_weight": base_w,
                        "regime_alignment_weight": alignment,
                        "raw_weight_contribution": raw_weight_contrib,
                        "effective_weight_contribution": effective_weight_contrib,
                        "final_weight_contribution": 0.0,
                        "fallback_active": is_fb,
                        "drift_active": is_dr,
                        "dominance_cap_active": False,
                        "stress_attenuation": stress_attenuation,
                        "correlation_penalty": correlation_penalty,
                        "similar_signal_count": similar_count,
                        "correlation_group_id": correlation_group_id,
                        "correlation_group_key": (
                            [correlation_group_key[0], correlation_group_key[1], correlation_group_key[2]]
                            if correlation_group_key is not None
                            else None
                        ),
                    }
                )
                continue

            dominance_cap_active = False
            if signal_count >= 3 and adjusted_total_weight > 1e-7:
                other_weights_sum = adjusted_total_weight - weight_contrib
                strict_cap = (0.4 * other_weights_sum) / 0.6
                if weight_contrib > strict_cap:
                    weight_contrib = strict_cap
                    dominance_cap_active = True

            denom += weight_contrib
            if direction != 0:
                raw_weighted_sum += raw_weight_contrib * direction
                raw_weighted_edge += raw_weight_contrib * (
                    direction * _safe_float(s.expected_edge_bps, 0.0, 0.0, _EDGE_BPS_CLAMP)
                )
                weighted_sum += weight_contrib * direction
                # FIX 5: expected_edge_bps is absolute magnitude; direction gives sign.
                weighted_edge += weight_contrib * (
                    direction * _safe_float(s.expected_edge_bps, 0.0, 0.0, _EDGE_BPS_CLAMP)
                )

            breakdown.append(
                {
                    "source_id": s.source_id,
                    "source_policy": source_policy,
                    "perf_multiplier": perf_mult,
                    "timeframe": _normalize_key(s.timeframe),
                    "direction": direction,
                    "expected_edge_bps": s.expected_edge_bps,
                    "base_weight": base_w,
                    "regime_alignment_weight": alignment,
                    "raw_weight_contribution": raw_weight_contrib,
                    "effective_weight_contribution": effective_weight_contrib,
                    "fallback_active": is_fb,
                    "drift_active": is_dr,
                    "dominance_cap_active": dominance_cap_active,
                    "final_weight_contribution": weight_contrib,
                    "stress_attenuation": stress_attenuation,
                    "correlation_penalty": correlation_penalty,
                    "similar_signal_count": similar_count,
                    "correlation_group_id": correlation_group_id,
                    "correlation_group_key": (
                        [correlation_group_key[0], correlation_group_key[1], correlation_group_key[2]]
                        if correlation_group_key is not None
                        else None
                    ),
                }
            )

        if denom < self.config.min_aggregate_weight:
            return 0.0, 0.0, {"error": "low_aggregate_weight", "denom": denom}
        raw_score = weighted_sum / denom
        if not math.isfinite(raw_score):
            raw_score = 0.0
        raw_score_unadjusted = 0.0
        if raw_denom > 1e-12:
            raw_score_unadjusted = raw_weighted_sum / raw_denom
        if not math.isfinite(raw_score_unadjusted):
            raw_score_unadjusted = 0.0

        correlation_groups = []
        group_adjusted_weight: Dict[Tuple[int, str, str], float] = {}
        for row in breakdown:
            direction = row.get("direction")
            timeframe_bucket = _normalize_key(row.get("timeframe", "")) or _normalize_key(
                row.get("source_timeframe", "")
            )
            group_id = _normalize_key(row.get("correlation_group_id"))
            if direction not in (-1, 1) or not group_id:
                continue
            gk = (int(direction), timeframe_bucket or _DEFAULT_TIMEFRAME, group_id)
            group_adjusted_weight[gk] = group_adjusted_weight.get(gk, 0.0) + _safe_float(
                row.get("final_weight_contribution"), 0.0, 0.0
            )

        largest_group_size = 0
        for group_key in sorted(correlation_group_sizes.keys()):
            size = correlation_group_sizes[group_key]
            group_size = max(1, int(size))
            largest_group_size = max(largest_group_size, group_size)
            penalty = 1.0
            if group_size >= self.config.correlation_min_group_size:
                smooth_size = 1.0 + 0.5 * float(group_size - 1)
                penalty = 1.0 / math.sqrt(smooth_size)
            if not math.isfinite(penalty):
                penalty = 1.0
                group_size = 1
            correlation_groups.append(
                {
                    "key": [group_key[0], group_key[1], group_key[2]],
                    "size": group_size,
                    "penalty": penalty,
                    "adjusted_weight": _safe_float(group_adjusted_weight.get(group_key, 0.0), 0.0, 0.0),
                }
            )

        logger.debug(
            "CORRELATION | groups=%d | largest=%d | raw_denom=%.6f | adj_denom=%.6f",
            len(correlation_groups),
            largest_group_size,
            raw_denom,
            denom,
        )

        attenuated_score = raw_score
        if not math.isfinite(attenuated_score):
            attenuated_score = 0.0

        raw_blended_edge = 0.0
        if raw_denom > 1e-12:
            raw_blended_edge = raw_weighted_edge / raw_denom
        if not math.isfinite(raw_blended_edge):
            raw_blended_edge = 0.0
        blended_edge = weighted_edge / denom
        if not math.isfinite(blended_edge):
            blended_edge = 0.0
        correlation_attenuation = 1.0
        if abs(raw_blended_edge) > 1e-12:
            correlation_attenuation = max(
                0.0, min(1.0, abs(blended_edge) / abs(raw_blended_edge))
            )
        if not math.isfinite(correlation_attenuation):
            correlation_attenuation = 1.0
        logger.debug(
            "CORRELATION_ATTENUATION | raw_edge=%.6f | adj_edge=%.6f | factor=%.6f",
            raw_blended_edge,
            blended_edge,
            correlation_attenuation,
        )

        return (
            _safe_float(attenuated_score, 0.0, -1.0, 1.0),
            _safe_float(blended_edge, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP),
            {
                "breakdown": breakdown,
                "correlation_summary": {
                    "groups": correlation_groups,
                    "total_groups": len(correlation_groups),
                    "largest_group_size": largest_group_size,
                    "attenuation_factor": correlation_attenuation,
                    "penalty_logic": "smooth_inverse_sqrt_size",
                    "conviction_gate_active": False,
                    "raw_blended_edge_bps": _safe_float(
                        raw_blended_edge, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP
                    ),
                    "attenuated_blended_edge_bps": _safe_float(
                        blended_edge, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP
                    ),
                },
            },
        )

    # ----------------------------------------
    # Performance multiplier
    # ----------------------------------------

    def _calculate_performance_multiplier(
        self,
        stats: AlphaPerformanceStats,
        regime_name: Optional[str] = None,
    ) -> Tuple[float, float, bool, bool, float, float]:
        """Recalculates alpha allocation multipliers using Bayesian shrinkage."""
        min_trades = max(1, int(self.config.feedback_min_trades))
        n = max(0, int(stats.trade_count))
        raw_score = self._calc_score_block(stats)

        conf = 0.0
        if n > min_trades:
            log_denom = math.log1p(float(min_trades) * self.config.confidence_scaling_factor)
            conf = min(1.0, math.log1p(float(n - min_trades)) / log_denom)

        global_perf = (1.0 - conf) * 0.5 + conf * raw_score

        final_perf = global_perf
        is_fb = conf == 0.0
        is_dr = False
        dr_score = 0.0
        r_conf = conf

        if self.config.regime_feedback_enabled and regime_name:
            rs = stats.regimes.get(regime_name)
            if rs:
                if rs.trade_count > max(1, self.config.regime_min_trades) and n > min_trades:
                    wr_gap = stats.win_rate - rs.win_rate
                    edge_gap = (
                        stats.avg_realized_edge_bps - rs.avg_realized_edge_bps
                    ) / max(2.0, abs(stats.avg_realized_edge_bps))
                    dr_score = min(1.0, max(0.0, wr_gap) + max(0.0, edge_gap))
                    if dr_score > self.config.regime_drift_threshold:
                        is_dr = True

                r_conf = 0.0
                if rs.trade_count > self.config.regime_min_trades:
                    r_log_denom = math.log1p(
                        float(self.config.regime_min_trades)
                        * self.config.confidence_scaling_factor
                    )
                    r_conf = min(
                        1.0,
                        math.log1p(float(rs.trade_count - self.config.regime_min_trades))
                        / r_log_denom,
                    )

                applied_penalty = dr_score * self.config.regime_drift_penalty
                fallback_baseline = (
                    global_perf * (1.0 - self.config.regime_fallback_weight)
                ) + (0.5 * self.config.regime_fallback_weight)
                final_perf = (
                    self._calc_score_block(rs, applied_penalty) * r_conf
                ) + (fallback_baseline * (1.0 - r_conf))

                is_fb = r_conf == 0.0
            else:
                is_fb = True
                r_conf = 0.0

        ratio = final_perf / 0.5
        if final_perf >= 0.5:
            ratio = (final_perf - 0.5) / 0.5

        mult = self.config.feedback_min_multiplier + ratio * (
            1.0 - self.config.feedback_min_multiplier
        )
        if final_perf >= 0.5:
            mult = 1.0 + ratio * (self.config.feedback_max_multiplier - 1.0)

        if self.config.regime_feedback_enabled and regime_name:
            mult = min(mult, self.config.regime_max_adjustment)

        return (
            max(
                self.config.feedback_min_multiplier,
                min(self.config.feedback_max_multiplier, mult),
            ),
            final_perf,
            is_fb,
            is_dr,
            dr_score,
            r_conf,
        )

    def _calc_score_block(
        self,
        stats_block: Union[AlphaPerformanceStats, AlphaRegimeStats],
        drift_pen: float = 0.0,
    ) -> float:
        """Calculates internal direction-aware performance score.

        expected_edge_bps is absolute magnitude (>= 0).
        avg_realized_edge_bps is signed; positive = profitable.
        """
        wr = _safe_float(stats_block.ema_win_rate, 0.5)
        edge_r = 0.5
        if stats_block.expected_edge_bps > 1e-6:
            realized = stats_block.avg_realized_edge_bps
            if realized >= 0.0:
                edge_r = min(
                    1.0, abs(realized) / max(2.0, stats_block.expected_edge_bps)
                )
            else:
                edge_r = 0.0  # Realized negative: alpha delivered opposite of prediction.

        score = (wr * self.config.feedback_win_rate_weight) + (
            edge_r * self.config.feedback_edge_weight
        )
        decay_pen = stats_block.decay_score * self.config.feedback_decay_penalty
        return max(0.0, min(1.0, score - decay_pen - drift_pen))

    # ----------------------------------------
    # Multi-timeframe combination
    # ----------------------------------------

    def _combine_timeframes(
        self, tf_results: Dict[str, Dict[str, Any]]
    ) -> Tuple[float, float, Dict[str, Any]]:
        """Synthesises signals across multiple timeframes with deterministic HTF dominance.

        When higher_tf_dominance=True and a dominant direction is established,
        conflicting lower timeframes are HARD-EXCLUDED from both the numerator and
        the denominator. They cannot numerically overturn the dominant direction.
        Neutral TFs (|score| <= deadband) are always included.
        Aligned TFs receive an alignment bonus on their timeframe weight.
        The "default" sentinel TF is excluded from dominance selection: it is a
        legacy catch-all bucket, not a real market horizon.

        FIX 8 / FIX 11: Ordering correctness and label canonicality are both
        guaranteed at construction time by OrchestratorConfig.__post_init__.
        This method needs no additional runtime ordering guard.
        """
        active = list(tf_results.keys())
        if not active:
            return 0.0, 0.0, {}

        if len(active) == 1:
            active_tf = active[0]
            return (
                tf_results[active_tf]["net_score"],
                tf_results[active_tf]["blended_edge"],
                {"is_mtf": False},
            )

        dom_tf: Optional[str] = None
        dom_dir: int = 0
        db = self.config.score_deadband

        if self.config.higher_tf_dominance:
            for tf in reversed(self.config.timeframe_order):
                if tf == _DEFAULT_TIMEFRAME:
                    continue
                if tf not in active:
                    continue
                score = tf_results[tf]["net_score"]
                if score > db:
                    candidate_dir = 1
                elif score < -db:
                    candidate_dir = -1
                else:
                    candidate_dir = 0

                if candidate_dir != 0:
                    dom_tf = tf
                    dom_dir = candidate_dir
                    break

        weighted_score = 0.0
        weighted_edge = 0.0
        total_w = 0.0
        excluded_tfs: List[str] = []

        for tf, res in tf_results.items():
            tw = _safe_float(self.config.timeframe_weights.get(tf, 1.0), 1.0)
            score = res["net_score"]

            tf_dir = 0
            if score > db:
                tf_dir = 1
            elif score < -db:
                tf_dir = -1

            if dom_tf and tf != dom_tf and dom_dir != 0:
                if tf_dir != 0 and tf_dir != dom_dir:
                    # Hard exclusion: conflicting lower TF cannot influence final score.
                    excluded_tfs.append(tf)
                    logger.debug(
                        "MTF_DOMINANCE | excluded tf=%s dir=%d | dominant=%s dir=%d",
                        tf, tf_dir, dom_tf, dom_dir,
                    )
                    continue
                elif tf_dir == dom_dir:
                    tw *= 1.0 + self.config.timeframe_alignment_bonus
                # tf_dir == 0: neutral, included at base weight.

            weighted_score += score * tw
            weighted_edge += res["blended_edge"] * tw
            total_w += tw

        if total_w <= 1e-6:
            return 0.0, 0.0, {"error": "total_w_zero"}

        meta: Dict[str, Any] = {
            "is_mtf": True,
            "dominant": dom_tf,
            "dominant_direction": dom_dir,
        }
        if excluded_tfs:
            meta["htf_excluded_tfs"] = excluded_tfs

        return (
            weighted_score / total_w,
            _safe_float(weighted_edge / total_w, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP),
            meta,
        )

    # ----------------------------------------
    # Quality / risk helpers
    # ----------------------------------------

    def _calculate_quality_multipliers(
        self, stale: float, missing: float, vol: float, regime_assessment: Optional[RegimeAssessment] = None
    ) -> Dict[str, float]:
        """Calculates live environmental constraints with full sub-component breakdown.

        FIX 4: Returns individual stale_multiplier, missing_multiplier, and
        vol_amplifier in addition to combined_multiplier. Each sub-component is
        surfaced in meta_info.quality_metrics so forensic debugging can determine
        exactly which factor degraded conviction and by how much.
        """
        v_f = 1.0 + (_safe_float(vol, 1.0) * 0.5)
        s_m = max(0.0, 1.0 - (_safe_float(stale, 1.0, 0.0, 1.0) * v_f))

        m_m = 1.0
        if self.config.max_missing_data_ratio > 0:
            m_m = max(
                0.0,
                1.0
                - (
                    (_safe_float(missing, 1.0, 0.0, 1.0) * v_f)
                    / self.config.max_missing_data_ratio
                ),
            )

        regime_factor = 1.0
        if self.regime_engine and regime_assessment:
            regime_factor = self.regime_engine.quality_regime_factor(regime_assessment)

        combined = max(0.1, min(1.0, s_m * m_m * regime_factor))
        return {
            "combined_multiplier": combined,
            "stale_multiplier": s_m,
            "missing_multiplier": m_m,
            "vol_amplifier": v_f,
            "regime_factor": regime_factor,
        }

    def _validate_and_prune(
        self, signals: Any, now: float
    ) -> Tuple[List[AlphaSignal], Dict[str, Any], List[Dict[str, Any]]]:
        """Rejects stale, future, or invalid alpha payloads.

        FIX 2 (signal path): Signals without a timeframe field (or with an empty
        timeframe) are routed to _DEFAULT_TIMEFRAME instead of being rejected.

        FIX 5 (complete): expected_edge_bps contract violations (negative values)
        are explicitly detected, logged at WARNING with source_id for traceability,
        and normalised to abs(). A per-call counter 'negative_edge_normalized' is
        incremented and included in the returned metrics dict, which surfaces in
        meta_info.metrics. The signal is accepted with the corrected value rather
        than dropped, preserving valid directional information while flagging the
        upstream violation clearly.

        FIX 9 (unknown-source observability): Unknown source IDs accepted under
        allow_unknown_sources=True are recorded in metrics["unknown_sources_accepted"]
        (a list of str). This list is empty when allow_unknown_sources=False because
        no unknown signal would have reached this point. The list is always present
        in the returned metrics dict; callers must not assume its type is int.

        FIX 21: Accepts any finite iterable (generators, sets, tuples, etc.).
        Strings and bytes are explicitly rejected to preserve existing invalid-payload
        behaviour. The iterable is consumed once into a list for deterministic indexing.

        Return type note: metrics is Dict[str, Any] (not Dict[str, int]) because it
        now includes the unknown_sources_accepted list alongside integer counters.
        """
        valid: List[AlphaSignal] = []
        rejection_details: List[Dict[str, Any]] = []
        metrics: Dict[str, Any] = {
            "accepted": 0,
            "stale": 0,
            "invalid": 0,
            "future_timestamp": 0,
            # FIX 5: Per-call counter for upstream edge convention violations.
            # Surfaces in meta_info.metrics for live forensic debugging.
            "negative_edge_normalized": 0,
            # FIX 9: Per-call list of unknown source IDs accepted via default weight.
            # Always a list; empty when allow_unknown_sources=False.
            "unknown_sources_accepted": [],
            # FIX (dedup): Per-call counter for duplicate (source_id, timeframe)
            # pairs collapsed by the post-validation dedup pass. The surviving
            # entry is the one with the highest timestamp.
            "duplicates_removed": 0,
        }

        # FIX 21: Reject strings/bytes explicitly (they are iterable but invalid).
        # Then normalise any other finite iterable to a list for deterministic access.
        if isinstance(signals, (str, bytes)):
            return valid, metrics, rejection_details
        if not isinstance(signals, (list, tuple)):
            try:
                signals = list(signals)
            except Exception:
                return valid, metrics, rejection_details

        for s in signals:
            try:
                if isinstance(s, dict):
                    src_raw = s.get("source_id")
                    tf_raw = s.get("timeframe")
                    ts_raw = s.get("timestamp")
                    direction_raw = s.get("direction")
                    conviction_raw = s.get("conviction")
                    edge_raw = s.get("expected_edge_bps")
                    correlation_group_raw = s.get("correlation_group_id", "")
                else:
                    src_raw = getattr(s, "source_id", "")
                    tf_raw = getattr(s, "timeframe", "")
                    ts_raw = getattr(s, "timestamp", 0.0)
                    direction_raw = getattr(s, "direction", None)
                    conviction_raw = getattr(s, "conviction", 0.0)
                    edge_raw = getattr(s, "expected_edge_bps", 0.0)
                    correlation_group_raw = getattr(s, "correlation_group_id", "")

                src = _normalize_key(src_raw)
                ts = _safe_float(ts_raw, 0.0)

                # FIX 2: Default missing/blank timeframe to "default". Legacy signals
                # without a timeframe field are accepted and routed to the catch-all
                # bucket rather than being silently dropped.
                tf = _normalize_key(tf_raw)
                if not tf:
                    tf = _DEFAULT_TIMEFRAME

                if not VALID_ID_REGEX.match(src) or tf not in self.config.timeframe_order:
                    metrics["invalid"] += 1
                    rejection_details.append(
                        {"source_id": src_raw, "reason": "invalid_id_or_timeframe"}
                    )
                    continue

                correlation_group_id = _normalize_key(correlation_group_raw)
                if correlation_group_id and not VALID_ID_REGEX.match(correlation_group_id):
                    metrics["invalid"] += 1
                    rejection_details.append(
                        {"source_id": src, "reason": "invalid_correlation_group_id"}
                    )
                    continue

                if (
                    not self.config.allow_unknown_sources
                    and src not in self.config.signal_weights
                ):
                    metrics["invalid"] += 1
                    rejection_details.append(
                        {"source_id": src, "reason": "unknown_source"}
                    )
                    continue

                age = now - ts
                if age < -0.1:
                    metrics["future_timestamp"] += 1
                    rejection_details.append(
                        {
                            "source_id": src,
                            "reason": "future_timestamp",
                            "skew_ms": round(age * 1000.0, 2),
                        }
                    )
                    continue

                if age > self.config.signal_ttl_seconds:
                    metrics["stale"] += 1
                    rejection_details.append({"source_id": src, "reason": "stale"})
                    continue

                try:
                    direction = int(direction_raw)
                except (ValueError, TypeError):
                    direction = None

                if direction not in (-1, 0, 1):
                    metrics["invalid"] += 1
                    rejection_details.append(
                        {"source_id": src, "reason": "invalid_direction"}
                    )
                    continue

                conviction = _safe_float(conviction_raw, 0.0, 0.0, 1.0)

                # FIX 5: Detect upstream edge convention violation before clamping.
                # A negative expected_edge_bps is semantically contradictory for a
                # directional signal because direction encodes the sign; edge encodes
                # magnitude only. We normalise to abs() with an explicit WARNING so
                # the upstream alpha knows it is violating the module contract. The
                # signal is retained because its directional information is still valid.
                raw_edge = _safe_float(edge_raw, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP)
                if raw_edge < 0.0:
                    logger.warning(
                        "Signal ingress: negative expected_edge_bps — upstream contract "
                        "violation | source_id=%s | raw=%.4f | normalizing to abs(). "
                        "Direction field encodes sign; edge must be absolute magnitude.",
                        src,
                        raw_edge,
                    )
                    metrics["negative_edge_normalized"] += 1
                    raw_edge = abs(raw_edge)
                expected_edge_bps = min(raw_edge, _EDGE_BPS_CLAMP)

                # FIX 9: Track unknown sources accepted via allow_unknown_sources.
                if src not in self.config.signal_weights:
                    # Reachable only when allow_unknown_sources=True (guarded above).
                    metrics["unknown_sources_accepted"].append(src)

                valid.append(
                    AlphaSignal(
                        src,
                        direction,
                        conviction,
                        expected_edge_bps,
                        ts,
                        tf,
                        correlation_group_id,
                    )
                )
                metrics["accepted"] += 1

            except Exception:
                metrics["invalid"] += 1
                rejection_details.append(
                    {"source_id": "unknown", "reason": "malformed_payload"}
                )

        # FIX (dedup): Collapse signals sharing the same (source_id, timeframe)
        # key down to the single most recent entry. Without this step a source
        # that emits multiple signals for the same timeframe in one batch would
        # be independently weighted by _fuse_signals, double-counting its
        # contribution. The newest timestamp wins; stable order is preserved
        # via the sorted-index projection below.
        seen: Dict[Tuple[str, str], int] = {}
        for idx, sig in enumerate(valid):
            key = (sig.source_id, sig.timeframe)
            if key in seen:
                prev_idx = seen[key]
                if sig.timestamp >= valid[prev_idx].timestamp:
                    seen[key] = idx
                metrics["duplicates_removed"] = metrics.get("duplicates_removed", 0) + 1
            else:
                seen[key] = idx
        if metrics.get("duplicates_removed", 0) > 0:
            valid = [valid[i] for i in sorted(seen.values())]

        return valid, metrics, rejection_details

    def _apply_risk_overlay(
        self,
        conviction: float,
        state: ExecutionState,
        dd: float,
        max_drawdown_pct: Optional[float] = None,
    ) -> Tuple[float, float, float, Optional[str]]:
        """Aggressive risk cutting based on exposure and drawdown.

        Return tuple: (scaled_conviction, risk_pressure, utilization, rationale)

        risk_pressure = util^gamma: the utilisation-proportional share of execution
        capacity consumed as pressure (0.0 = no pressure, 1.0 = full ceiling hit).
        FIX 14: renamed from risk_penalty to risk_pressure to match semantics.
        The value is NOT a signed haircut; it is a non-negative pressure fraction.
        """
        max_dd = max_drawdown_pct if max_drawdown_pct is not None else self.config.max_drawdown_pct
        max_exp = _safe_float(state.max_exposure_usd, 0.0)

        if dd > max_dd:
            return 0.0, 1.0, 1.0, "dd_breach"

        if max_exp <= 0.0:
            # FIX 16: util is 0.0 when max_exp is zero; risk_pressure = util^gamma
            # must be 0.0 to match the documented semantics. Fail-closed conviction
            # remains 0.0.
            return 0.0, 0.0, 0.0, "zero_exp"

        util = max(
            0.0,
            min(1.0, _safe_float(state.current_exposure_usd, max_exp) / max_exp),
        )
        penalty = 1.0 - math.pow(util, self.config.risk_gamma)

        final_conv = conviction * max(0.0, penalty)
        # FIX 14: renamed variable from risk_penalty to risk_pressure.
        # Value = util^gamma = 1.0 - penalty. Semantics: fraction of execution
        # capacity consumed as pressure. 0.0 means no utilisation pressure;
        # 1.0 means at drawdown / full utilisation ceiling.
        risk_pressure = 1.0 - penalty

        return final_conv, risk_pressure, util, None

    def _calculate_urgency(
        self,
        conviction: float,
        vol: float,
        liq: float,
        quality: float,
        agreement_ratio: float,
        conflict_ratio: float,
        regime_assessment: Optional[RegimeAssessment] = None,
    ) -> float:
        """Calculates execution urgency for OMS routers using conviction, stress,
        and MTF agreement/conflict signals.

        FIX 13: The `quality` parameter is retained for API compatibility but is
        NO LONGER APPLIED inside this function. The `conviction` argument passed
        from orchestrate() is already final_conviction = conviction_pre_quality *
        quality_multiplier, so quality has been applied exactly once upstream.
        Multiplying by quality_factor here would square the quality attenuation,
        creating hidden no-trade zones under mild staleness. Pass 1.0 from the
        call site to make the neutralisation self-documenting.

        Urgency components:
        - stress_modifier  : vol/liq ratio → high stress reduces urgency (conservative)
        - agreement_boost  : MTF agreement → bonus on the conviction term
        - conflict_penalty : MTF conflict  → penalty on the conviction term
        All outputs are bounded to [0, 1]. Fail-closed: conviction=0 → urgency=0.

        NOTE: The crisis urgency floor (urgency_regime_floor) is applied here as a
        minimum bound. However, orchestrate() applies FIX 27 to zero urgency when
        the risk overlay has triggered a hard stop (dd_breach / zero_exp), ensuring
        the floor cannot override a risk shutdown. The floor is therefore only active
        on live, non-stopped orchestration paths.
        """
        stress = min(10.0, _safe_float(vol, 1.0) / max(0.05, _safe_float(liq, 0.5)))
        # High stress (vol/liq) reduces urgency: bounded dampener [0.5, 1.0]
        stress_modifier = max(0.5, 1.0 - (stress / 10.0) * 0.5)

        # quality_factor intentionally removed (FIX 13). conviction already
        # incorporates quality via the quality_multiplier applied in orchestrate().
        agreement_boost = max(0.0, min(self.config.timeframe_alignment_bonus,
                                       agreement_ratio * self.config.timeframe_alignment_bonus))
        conflict_penalty = max(0.0, min(self.config.timeframe_conflict_penalty,
                                        conflict_ratio * self.config.timeframe_conflict_penalty))

        modified_conviction = conviction * (1.0 + agreement_boost - conflict_penalty)
        modified_conviction = max(0.0, min(1.0, modified_conviction))

        urgency = max(0.0, min(1.0, modified_conviction * stress_modifier))

        if self.regime_engine and regime_assessment:
            floor = self.regime_engine.urgency_regime_floor(regime_assessment)
            urgency = max(floor, urgency)

        return urgency

    def _generate_decision(
        self,
        score: float,
        conviction: float,
        edge: float,
        urgency: float,
        risk_rat: Optional[str],
        meta: Dict[str, Any],
        regime_assessment: Optional[RegimeAssessment] = None,
    ) -> OrchestratedAction:
        """Final decision gate mapping scores to BUY/SELL/HOLD."""
        action = Action.HOLD
        rationale = risk_rat

        eff_threshold = self.config.action_threshold
        if self.regime_engine and regime_assessment:
            eff_threshold = self.regime_engine.effective_action_threshold(regime_assessment, self.config.action_threshold)

        if not rationale:
            if conviction < eff_threshold:
                rationale = "weak_score"

        if not rationale:
            if score > self.config.score_deadband:
                action = Action.BUY
                rationale = "pos_bias"
            elif score < -self.config.score_deadband:
                action = Action.SELL
                rationale = "neg_bias"
            else:
                rationale = "deadband"

        meta["rationale"] = rationale
        meta["regime_adjusted_threshold"] = eff_threshold
        logger.info(
            "DECISION | action=%s | consensus=%.3f | final_conviction=%.3f | urgency=%.3f",
            action.name,
            score,
            conviction,
            urgency,
        )
        return OrchestratedAction(
            action=action,
            net_conviction=conviction,
            expected_edge_bps=_safe_float(edge, 0.0, -_EDGE_BPS_CLAMP, _EDGE_BPS_CLAMP),
            urgency=urgency,
            meta_info=meta,
        )

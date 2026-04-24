from __future__ import annotations

import numpy as np
import hashlib
import dataclasses
from scipy.special import softmax
import weakref
try:
    from observability_controller import ObservabilityController
except Exception:
    ObservabilityController = None
from dataclasses import dataclass
from typing import Dict, Any, List
from collections import Counter, OrderedDict
import logging
import time
import warnings
import traceback
from scipy.special import logsumexp
from functools import wraps
import json
import queue
import threading
try:
    from traceback_engine import TracebackEngine
except Exception:
    TracebackEngine = None
try:
    from replay_engine import ReplayEngine
except Exception:
    ReplayEngine = None

try:
    from prometheus_client import Counter as PromCounter, Gauge, Histogram
    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False

LOGGER = logging.getLogger(__name__)
_OUTPUT_SCHEMA_VERSION = "1.2.0"
_POSITION_SIZE_CAP = 0.35

# ==========================================
# Observability (Prometheus Metrics)
# ==========================================
if _PROM_AVAILABLE:
    REGIME_COUNTER = PromCounter("engine_regime_total", "Regime occurrences", ["engine_id", "regime"])
    ENGINE_HEALTH = Gauge("engine_health_status", "Engine health (1=OK,0=FAIL)", ["engine_id"])
    ENGINE_CONFIDENCE = Gauge("engine_confidence", "Model confidence", ["engine_id"])
    ENGINE_RISK = Gauge("engine_risk_level", "Risk level", ["engine_id"])
    ENGINE_VOL = Gauge("engine_expected_volatility", "Expected volatility", ["engine_id"])
    ENGINE_POSITION = Gauge("engine_position_size", "Position size", ["engine_id"])
    ENGINE_FEED_STATUS = PromCounter("engine_feed_status_total", "Feed status", ["engine_id", "status"])
    MTF_DEGRADATION = PromCounter("engine_mtf_degradation_total", "MTF degradation reasons", ["engine_id", "reason"])
    ENGINE_LATENCY = Histogram("engine_update_latency_seconds", "Update latency", ["engine_id"])

def _synchronized(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper

def _coerce_1d_vector(values: Any, expected_size: int, *, name: str) -> np.ndarray:
    """
    Coerce scalars / 0-D arrays / nested singletons into a strict 1-D vector.
    This keeps n_features=1 and single-value slices valid instead of failing
    after np.squeeze() collapses them to scalars.
    """
    arr = np.asarray(values, dtype=float)
    if arr.ndim == 0:
        arr = arr.reshape(1)
    else:
        arr = np.ravel(arr)
    if arr.shape != (expected_size,):
        raise ValueError(f"Vector '{name}' must have shape ({expected_size},), got {arr.shape}.")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"Vector '{name}' contains non-finite values: {arr}.")
    return arr

def safe_float(value: Any, default: float = 0.0, min: float | None = None, max: float | None = None) -> float:
    """Best-effort scalar coercion for schema/output hardening."""
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    if not np.isfinite(parsed):
        parsed = float(default)
    if min is not None and parsed < min:
        parsed = float(min)
    if max is not None and parsed > max:
        parsed = float(max)
    return float(parsed)

def _safe_float(value: Any, default: float = 0.0, min: float | None = None, max: float | None = None) -> float:
    """Crash-proof scalar parser for state/input hydration."""
    return safe_float(value, default=default, min=min, max=max)


def _safe_int(value: Any, default: int = 0, min: int | None = None, max: int | None = None) -> int:
    parsed = _safe_float(value, default=float(default))
    try:
        out = int(parsed)
    except Exception:
        out = int(default)
    if min is not None and out < int(min):
        out = int(min)
    if max is not None and out > int(max):
        out = int(max)
    return int(out)


def _safe_array(
    value: Any,
    shape: tuple[int, ...] | None = None,
    default: Any = None,
) -> np.ndarray:
    """Best-effort finite array coercion that never raises."""
    fallback = np.asarray(default if default is not None else [], dtype=float)
    try:
        arr = np.asarray(value, dtype=float)
    except Exception:
        arr = fallback
    if arr.ndim == 0:
        arr = arr.reshape(1)
    else:
        arr = np.ravel(arr)
    fill = 0.0
    if fallback.size > 0 and np.all(np.isfinite(fallback)):
        fill = float(np.ravel(fallback)[0])
    arr = np.where(np.isfinite(arr), arr, fill)
    if shape is not None:
        expected_size = int(np.prod(shape))
        if arr.size != expected_size:
            arr = np.asarray(fallback, dtype=float).reshape(-1)
        if arr.size != expected_size:
            arr = np.full(expected_size, fill, dtype=float)
        arr = np.ravel(arr).reshape(shape)
    arr = np.where(np.isfinite(arr), arr, fill)
    return np.asarray(arr, dtype=float)


def _safe_prob_vector(vec: Any, size: int) -> np.ndarray:
    base = np.ones(int(size), dtype=float) / max(int(size), 1)
    arr = _safe_array(vec, shape=(int(size),), default=base)
    arr = np.where(np.isfinite(arr), arr, 0.0)
    arr = np.clip(arr, 0.0, None)
    return _normalize_prob_vector(arr)


def _normalize_prob_vector(values: np.ndarray, floor: float = 1e-12) -> np.ndarray:
    try:
        arr = np.asarray(values, dtype=float)
    except Exception:
        arr = np.asarray([], dtype=float)
    if arr.ndim != 1 or arr.size == 0:
        raise ValueError(f"Probability vector must be a non-empty 1-D array, got shape {arr.shape}.")
    safe_floor = safe_float(floor, default=1e-12, min=1e-15, max=1e-2)
    arr = np.where(np.isfinite(arr), arr, safe_floor)
    arr = np.clip(arr, safe_floor, None)
    total = float(arr.sum())
    if not np.isfinite(total) or total <= 0.0:
        return np.ones(arr.size, dtype=float) / arr.size
    return arr / total

# ==========================================
# NEW: Schema Guard (prevents silent breakage)
# ==========================================
def _validate_output_schema(output: Dict[str, Any]) -> bool:
    try:
        if not isinstance(output, dict):
            raise ValueError("output must be a dict")
        if "schema_version" not in output:
            raise ValueError("missing schema_version")

        version = str(output["schema_version"]).strip()
        if version != _OUTPUT_SCHEMA_VERSION:
            raise ValueError(f"mismatch {version} != {_OUTPUT_SCHEMA_VERSION}")
        for key in ("regime_idx", "regime_label", "probabilities", "risk_metrics", "alpha"):
            if key not in output:
                raise ValueError(f"missing required key: {key}")
        if not isinstance(output["probabilities"], dict):
            raise ValueError("probabilities must be a dict")
        for pkey in ("bull", "bear", "crisis"):
            if pkey not in output["probabilities"]:
                raise ValueError(f"missing probabilities.{pkey}")
            pval = safe_float(output["probabilities"][pkey], default=np.nan)
            if not np.isfinite(pval):
                raise ValueError(f"non-finite probabilities.{pkey}")
            if pval < 0.0 or pval > 1.0:
                raise ValueError(f"out-of-bounds probabilities.{pkey}={pval}")
        prob_sum = sum(safe_float(output["probabilities"][k], default=np.nan) for k in ("bull", "bear", "crisis"))
        if not np.isfinite(prob_sum) or abs(prob_sum - 1.0) > 1e-3:
            raise ValueError(f"invalid probabilities sum={prob_sum}")
        if not isinstance(output["risk_metrics"], dict):
            raise ValueError("risk_metrics must be a dict")
        for rk in ("expected_volatility", "raw_leverage", "last_valid_vol", "switch_stability_ema"):
            if rk not in output["risk_metrics"]:
                raise ValueError(f"missing risk_metrics.{rk}")
            rval = safe_float(output["risk_metrics"][rk], default=np.nan)
            if not np.isfinite(rval):
                raise ValueError(f"non-finite risk_metrics.{rk}")
        if output["risk_metrics"]["expected_volatility"] < 0.0:
            raise ValueError("risk_metrics.expected_volatility must be >= 0")
        if output["risk_metrics"]["last_valid_vol"] <= 0.0:
            raise ValueError("risk_metrics.last_valid_vol must be > 0")
        if output["risk_metrics"]["switch_stability_ema"] <= 0.0:
            raise ValueError("risk_metrics.switch_stability_ema must be > 0")
        if not isinstance(output["alpha"], dict):
            raise ValueError("alpha must be a dict")
        if "edge_score" not in output["alpha"]:
            raise ValueError("missing alpha.edge_score")
        edge_score = safe_float(output["alpha"]["edge_score"], default=np.nan)
        if not np.isfinite(edge_score):
            raise ValueError("non-finite alpha.edge_score")
        confidence = safe_float(output.get("confidence", 0.0), default=np.nan)
        if not np.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
            raise ValueError(f"invalid confidence={confidence}")
        trend_strength = safe_float(output.get("trend_strength", 0.0), default=np.nan)
        risk_level = safe_float(output.get("risk_level", 0.0), default=np.nan)
        position_size = safe_float(output.get("position_size", 0.0), default=np.nan)
        signed_size = safe_float(output.get("signed_position_size", 0.0), default=np.nan)
        for name, val in (
            ("trend_strength", trend_strength),
            ("risk_level", risk_level),
            ("position_size", position_size),
            ("signed_position_size", signed_size),
        ):
            if not np.isfinite(val):
                raise ValueError(f"non-finite {name}")
        if risk_level < 0.0 or risk_level > 1.0:
            raise ValueError(f"invalid risk_level={risk_level}")
        macro_probs = output.get("macro_probs")
        if not isinstance(macro_probs, list) or len(macro_probs) != 3:
            raise ValueError("macro_probs must be a 3-element list")
        if any(not np.isfinite(safe_float(v, default=np.nan)) for v in macro_probs):
            raise ValueError("macro_probs contains non-finite values")
        macro_sum = sum(safe_float(v, default=np.nan) for v in macro_probs)
        if abs(macro_sum - 1.0) > 1e-3:
            raise ValueError(f"invalid macro_probs sum={macro_sum}")
        garch_regime_probs = output["risk_metrics"].get("garch_regime_probs")
        if not isinstance(garch_regime_probs, list) or len(garch_regime_probs) != 2:
            raise ValueError("risk_metrics.garch_regime_probs must be a 2-element list")
        if any(not np.isfinite(safe_float(v, default=np.nan)) for v in garch_regime_probs):
            raise ValueError("risk_metrics.garch_regime_probs contains non-finite values")
        garch_sum = sum(safe_float(v, default=np.nan) for v in garch_regime_probs)
        if abs(garch_sum - 1.0) > 1e-3:
            raise ValueError(f"invalid garch_regime_probs sum={garch_sum}")
        if position_size < 0.0 or position_size > _POSITION_SIZE_CAP:
            raise ValueError(f"invalid position_size={position_size}")
        if abs(signed_size) > (position_size + 1e-9):
            raise ValueError(
                f"signed_position_size magnitude exceeds position_size: {signed_size} vs {position_size}"
            )

        return True
    except Exception as e:
        # NEVER crash engine — degrade instead
        try:
            LOGGER.error(f"[SCHEMA VIOLATION] {e} | output={str(output)[:500]}")
        except Exception:
            warnings.warn("Schema violation logging failed", RuntimeWarning, stacklevel=2)
        return False

def _build_output(
    *,
    regime_idx: int,
    regime_label: str,
    trend_strength: float,
    risk_level: float,
    confidence: float,
    edge_score: float,
    probabilities: Dict[str, float],
    macro_probs: List[float],
    position_size: float,
    expected_vol: float,
    raw_size: float,
    is_toxic: bool,
    garch_regime_probs: List[float],
    feed_status: str,
    signed_position_size: float = 0.0,
    last_valid_vol: float = 0.0,
    switch_stability_ema: float = 1.0,
    execution_mode: str = "",
    execution_side: str = "",
    extended_schema: bool = False,
    range_ticks: int = 0,
    signal_valid: bool = True,
    include_signal_valid: bool = True,
) -> Dict[str, Any]:
    """
    Single authoritative output constructor for AdvancedRegimeEngine.update().
    Centralising schema construction here ensures both the normal and feed-failure
    paths emit identical key sets, eliminating downstream KeyError risks from
    schema divergence between code paths.
    """
    safe_expected_vol = safe_float(expected_vol, default=0.0, min=0.0)
    safe_last_valid_vol = safe_float(last_valid_vol, default=max(safe_expected_vol, 1e-12), min=1e-12)
    safe_switch_stability = safe_float(switch_stability_ema, default=1.0, min=1e-6)
    safe_raw_size = safe_float(raw_size, default=0.0, min=0.0, max=10.0)
    safe_position_size = safe_float(position_size, default=0.0, min=0.0, max=_POSITION_SIZE_CAP)
    safe_signed_position = safe_float(
        signed_position_size,
        default=0.0,
        min=-safe_position_size,
        max=safe_position_size,
    )
    safe_risk_level = safe_float(risk_level, default=1.0, min=0.0, max=1.0)
    safe_confidence = safe_float(confidence, default=0.0, min=0.0, max=1.0)
    safe_trend_strength = safe_float(trend_strength, default=0.0, min=-1.0, max=1.0)
    safe_edge_score = safe_float(edge_score, default=0.0, min=0.0, max=1.0)
    safe_regime_idx = int(safe_float(regime_idx, default=-1, min=-1, max=4))
    probabilities = probabilities if isinstance(probabilities, dict) else {}
    safe_prob_values = _normalize_prob_vector(np.asarray([
        safe_float(probabilities.get("bull", 1.0 / 3.0), default=1.0 / 3.0, min=0.0),
        safe_float(probabilities.get("bear", 1.0 / 3.0), default=1.0 / 3.0, min=0.0),
        safe_float(probabilities.get("crisis", 1.0 / 3.0), default=1.0 / 3.0, min=0.0),
    ], dtype=float))
    safe_probabilities = {
        "bull": float(safe_prob_values[0]),
        "bear": float(safe_prob_values[1]),
        "crisis": float(safe_prob_values[2]),
    }
    macro_probs = macro_probs if isinstance(macro_probs, list) else []
    safe_macro_probs = _normalize_prob_vector(np.asarray([
        safe_float(macro_probs[i] if len(macro_probs) > i else 1.0 / 3.0, default=1.0 / 3.0, min=0.0)
        for i in range(3)
    ], dtype=float)).tolist()
    safe_garch_probs = _normalize_prob_vector(np.asarray([
        safe_float(garch_regime_probs[i] if isinstance(garch_regime_probs, list) and len(garch_regime_probs) > i else 0.5, default=0.5, min=0.0)
        for i in range(2)
    ], dtype=float)).tolist()
    out = {
        'schema_version': _OUTPUT_SCHEMA_VERSION,
        'regime_idx': safe_regime_idx,
        'regime_label': str(regime_label or "UNKNOWN"),
        'trend_strength': safe_trend_strength,
        'risk_level': safe_risk_level,
        'confidence': safe_confidence,
        'probabilities': safe_probabilities,
        'macro_probs': safe_macro_probs,
        'position_size': safe_position_size,
        'execution_mode': execution_mode,
        'execution_side': execution_side,
        'signed_position_size': safe_signed_position,
        'extended_schema': bool(extended_schema) if extended_schema else False,
        **({'signal_valid': bool(signal_valid)} if include_signal_valid else {}),
        
        # --- NEW: forward compatibility anchor ---
        'schema_compat': {
            "version": _OUTPUT_SCHEMA_VERSION,
            "backward_compatible": True
        },
        
        'risk_metrics': {
            'expected_volatility': safe_expected_vol,
            'raw_leverage': safe_raw_size,
            'last_valid_vol': safe_last_valid_vol,
            'switch_stability_ema': safe_switch_stability,
            'toxic_penalty_applied': bool(is_toxic),
            'garch_regime_probs': safe_garch_probs,
            'feed_status': str(feed_status or "UNKNOWN"),
            'range_ticks': int(safe_float(range_ticks, default=0.0, min=0.0, max=1e9)),
        },
        # ==========================================
        # EDGE OUTPUT (NEW - FIXES SCHEMA GAP)
        # ==========================================
        'alpha': {
            'edge_score': safe_edge_score
        },
    }
    
    # --- HARD GUARD (fail-safe, NON-BREAKING) ---
    if not _validate_output_schema(out):
        fail_safe_probs = [1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0]
        fail_safe_macro_probs = _normalize_prob_vector(np.asarray(fail_safe_probs, dtype=float)).tolist()
        fail_safe_garch_probs = _normalize_prob_vector(np.asarray([0.5, 0.5], dtype=float)).tolist()
        return {
            "schema_version": _OUTPUT_SCHEMA_VERSION,
            "regime_idx": -1,
            "regime_label": "UNKNOWN",
            "trend_strength": 0.0,
            "risk_level": 1.0,
            "confidence": 0.0,
            "probabilities": {
                "bull": fail_safe_probs[0],
                "bear": fail_safe_probs[1],
                "crisis": fail_safe_probs[2],
            },
            "macro_probs": fail_safe_macro_probs,
            "position_size": 0.0,
            "execution_mode": "fail_safe",
            "execution_side": "flat",
            "signed_position_size": 0.0,
            "extended_schema": False,
            "schema_compat": {
                "version": _OUTPUT_SCHEMA_VERSION,
                "backward_compatible": True
            },
                "risk_metrics": {
                    "expected_volatility": 0.0,
                    "raw_leverage": 0.0,
                "last_valid_vol": safe_last_valid_vol,
                "switch_stability_ema": safe_switch_stability,
                "toxic_penalty_applied": True,
                "garch_regime_probs": fail_safe_garch_probs,
                "feed_status": "SCHEMA_FAILURE",
                "range_ticks": 0,
            },
            "signal_valid": False,
            "alpha": {
                "edge_score": 0.0
            }
        }

    return out

def _map_execution_mode(regime_label: str) -> str:
    if regime_label == "TREND":
        return "trend_follow"
    if regime_label == "BEAR":
        return "risk_off_or_short_bias"
    if regime_label == "TOXIC":
        return "flat_or_hedge"
    return "range_mean_revert"

# ==========================================
# Markov Regime Smoother (soft, no hard override)
# ==========================================
@dataclass
class RegimeMarkovSmoother:
    blend: float = 0.35
    weak_lead_gap: float = 0.08

    def __post_init__(self) -> None:
        self.states = ("TREND", "RANGE", "BEAR", "TOXIC")
        self.state_to_idx = {name: i for i, name in enumerate(self.states)}
        self.transition = np.array(
            [
                [0.90, 0.05, 0.03, 0.02],  # TREND ->
                [0.08, 0.82, 0.06, 0.04],  # RANGE ->
                [0.03, 0.05, 0.90, 0.02],  # BEAR  ->
                [0.03, 0.05, 0.04, 0.88],  # TOXIC ->
            ],
            dtype=float,
        )
        self.prev_probs = np.ones(4, dtype=float) / 4.0

    def reset(self) -> None:
        self.prev_probs = np.ones(4, dtype=float) / 4.0

    def set_prev_probs(self, probs: np.ndarray) -> None:
        self.prev_probs = self._normalize(probs)

    def _normalize(self, probs: np.ndarray) -> np.ndarray:
        probs = np.asarray(probs, dtype=float)
        probs = np.ravel(probs)
        if probs.size != 4:
            return np.ones(4, dtype=float) / 4.0
        probs = np.clip(probs, 1e-12, None)
        total = float(np.sum(probs))
        if (not np.isfinite(total)) or total <= 0.0:
            return np.ones(4, dtype=float) / 4.0
        return probs / total

    def _scores_to_evidence(self, scores: Dict[str, Any]) -> np.ndarray:
        bull = float(np.clip(scores.get("bull", 0.5), 0.0, 1.0))
        bear = float(np.clip(scores.get("bear", 0.5), 0.0, 1.0))
        trend_mass = float(np.clip(scores.get("trend_score", 0.0), 0.0, 1.0))
        range_mass = float(np.clip(scores.get("range_score", 0.0), 0.0, 1.0))
        toxic_mass = float(np.clip(scores.get("toxic_score", 0.0), 0.0, 1.0))

        directional_total = max(bull + bear, 1e-12)
        trend_prob = trend_mass * (bull / directional_total)
        bear_prob = trend_mass * (bear / directional_total)
        return self._normalize(np.array([trend_prob, range_mass, bear_prob, toxic_mass], dtype=float))

    def update(self, scores: Dict[str, Any], prev_regime: str | None) -> tuple[str, np.ndarray]:
        evidence = self._scores_to_evidence(scores)
        markov_pred = self._normalize(np.dot(self.prev_probs, self.transition))
        smoothed = self._normalize(self.blend * evidence + (1.0 - self.blend) * markov_pred)

        winner_idx = int(np.argmax(smoothed))
        winner = self.states[winner_idx]

        # Tiny hysteresis gate only when the new winner is weakly ahead.
        if prev_regime in self.state_to_idx and winner != prev_regime and winner != "TOXIC":
            prev_idx = self.state_to_idx[prev_regime]
            if (smoothed[winner_idx] - smoothed[prev_idx]) < self.weak_lead_gap:
                winner_idx = prev_idx
                winner = prev_regime

        self.prev_probs = smoothed
        return winner, smoothed

# ==========================================
# NEW: Real-Time Continuous Scoring Layer
# ==========================================
def compute_hmm_regime(
    alpha: np.ndarray,
    *,
    prev_directional_label: str | None = None,
    direction_switch_gap: float = 0.02,
) -> Dict[str, Any]:
    """
    Converts real-time filtered probabilities (alpha) into bounded regime scores.

    Classification is purely probabilistic (argmax over TREND/BEAR, RANGE, TOXIC)
    with no hard RANGE override to avoid regime collapse.
    """
    alpha_safe = np.asarray(alpha, dtype=float)
    if not np.all(np.isfinite(alpha_safe)):
        raise ValueError(f"Non-finite values in SJM probability output: {alpha_safe}")
    if alpha_safe.shape != (3,):
        raise ValueError(
            f"compute_hmm_regime expects a 3-state vector, got shape {alpha_safe.shape}. "
            "Engine is hard-coded for Bull/Bear/Crisis states."
        )
    if np.any(alpha_safe < 0):
        raise ValueError(f"Negative probability in SJM output: {alpha_safe}")
    total_alpha = alpha_safe.sum()
    if total_alpha <= 0:
        raise ValueError("SJM probability vector sums to zero or below.")
    alpha_safe = alpha_safe / total_alpha

    bull = float(alpha_safe[0])
    bear = float(alpha_safe[1])
    crisis = float(alpha_safe[2])

    directional_strength = float(np.clip(abs(bull - bear), 0.0, 1.0))
    low_directionality = 1.0 - directional_strength
    directional_confidence = float(np.clip(max(bull, bear), 0.0, 1.0))

    # Soft range evidence (bounded; no hard overrides).
    range_from_balance = low_directionality
    range_from_low_vol = float(np.clip(1.0 - crisis, 0.0, 1.0))
    range_from_low_drift = float(np.clip(1.0 - directional_confidence, 0.0, 1.0))

    # --- TREND SCORE (BOOSTED) ---
    trend_score = float(np.clip(
        (1.0 - 0.45 * crisis) * (0.85 * directional_confidence + 0.45 * directional_strength),
        0.0,
        1.0,
    ))

    # --- TOXIC SCORE (REDUCED AGGRESSION) ---
    toxic_score = float(np.clip(
        crisis * (0.70 + 0.10 * crisis),
        0.0,
        1.0,
    ))

    # --- RANGE SCORE (SLIGHTLY WEAKENED) ---
    range_score_raw = (
        0.35 * range_from_balance
        + 0.30 * range_from_low_vol
        + 0.20 * range_from_low_drift
    )
    trend_pressure = 0.55 * directional_strength + 0.35 * float(
        np.clip((directional_confidence - 0.5) / 0.5, 0.0, 1.0)
    )

    range_score = float(np.clip(
        min(range_score_raw, 0.70) - trend_pressure,
        0.0,
        1.0
    ))

    dominant = max(bull, bear)
    separation = directional_strength
    edge_score = float(np.clip((dominant - crisis) + 0.25 * separation, 0.0, 1.0))

    direction_gap = float(bull - bear)
    directional_label = "TREND" if direction_gap >= 0.0 else "BEAR"
    switch_gap = float(np.clip(direction_switch_gap, 0.0, 0.25))
    if prev_directional_label in ("TREND", "BEAR"):
        if prev_directional_label == "TREND" and direction_gap > -switch_gap:
            directional_label = "TREND"
        elif prev_directional_label == "BEAR" and direction_gap < switch_gap:
            directional_label = "BEAR"

    # Small directional bias toward TREND (alpha capture preference)
    if directional_label == "TREND":
        trend_score *= 1.10
    else:
        trend_score *= 0.95
    trend_score = float(np.clip(trend_score, 0.0, 1.0))

    score_map = {
        directional_label: trend_score,
        "RANGE": range_score,
        "TOXIC": toxic_score,
    }
    regime = max(score_map, key=score_map.get)

    entropy = float(-np.sum(alpha_safe * np.log(np.clip(alpha_safe, 1e-12, None))))
    max_entropy = float(np.log(alpha_safe.size))
    uncertainty = float(np.clip(entropy / max(max_entropy, 1e-12), 0.0, 1.0))
    conviction = float(np.clip(1.0 - uncertainty, 0.0, 1.0))

    return {
        "regime": regime,
        "bull": bull,
        "bear": bear,
        "crisis": crisis,
        "trend_strength": bull - bear,
        "risk_level": crisis,
        "confidence": max(bull, bear, crisis),
        "conviction": conviction,
        "uncertainty": uncertainty,
        "directional_margin": abs(direction_gap),
        "directional_label": directional_label,
        "edge_score": edge_score,
        "trend_score": trend_score,
        "range_score": range_score,
        "toxic_score": toxic_score,
    }

# ==========================================
# 1. NHHMM (Predictive Macro Engine)
# ==========================================
class NHHMM_Engine:
    def __init__(self, n_states=3, n_features=3):
        self.K = n_states
        self.n_features = n_features
        init_rng = np.random.default_rng(7)
        self.beta = init_rng.normal(0.0, 0.01, size=(self.K, self.K, n_features))
        self.mu = np.array([0.001, -0.001, 0.0], dtype=float)
        self.sigma = np.array([0.004, 0.004, 0.010], dtype=float)
        
    def load_weights(self, beta: np.ndarray, mu: np.ndarray, sigma: np.ndarray):
        """Inject pre-trained parameters for live inference."""
        self.beta = np.asarray(beta, dtype=float)
        self.mu = np.asarray(mu, dtype=float)
        self.sigma = np.asarray(sigma, dtype=float)

    def _compute_transition_matrix(self, x_t: np.ndarray) -> np.ndarray:
        try:
            x_t = _coerce_1d_vector(x_t, self.n_features, name="NHHMM transition x_t")
        except ValueError as e:
            raise RuntimeError(
                f"NHHMM input validation failed: {e}. "
                "Upstream feature pipeline produced invalid data."
            ) from e
        logits = np.einsum('ijk,k->ij', self.beta, x_t)
        logits[:, 0] = 0.0  # Identifiability: pin reference category column
        return softmax(logits, axis=1)

    def forward_pass_step(
        self, y_t: float, x_t: np.ndarray, prior_prob: np.ndarray
    ):
        """
        Filtered alpha_t using strictly forward data (current + past only).
        Fully vectorised; log-space emission prevents underflow on extreme returns.
        """
        y_t = safe_float(y_t, default=0.0, min=-2.0, max=2.0)
        prior_prob = _normalize_prob_vector(np.asarray(prior_prob, dtype=float))
        P_t = self._compute_transition_matrix(x_t)
        pred_prob = _normalize_prob_vector(np.dot(prior_prob, P_t))  # Chapman-Kolmogorov prediction

        # Vectorised log N(y_t | mu_k, sigma_k) across all K states.
        sigma_safe = np.clip(np.abs(np.asarray(self.sigma, dtype=float)), 1e-8, None)
        log_emission = (
            -0.5 * np.log(2.0 * np.pi)
            - np.log(sigma_safe)
            - 0.5 * ((y_t - self.mu) / sigma_safe) ** 2
        )

        log_pred = np.log(np.clip(pred_prob, 1e-300, None))
        log_posterior_unnorm = log_pred + log_emission
        log_posterior_unnorm -= logsumexp(log_posterior_unnorm)
        posterior_prob = np.exp(log_posterior_unnorm)
        posterior_prob = _normalize_prob_vector(np.asarray(posterior_prob, dtype=float))

        return posterior_prob, P_t


# ==========================================
# 2. SJM (Final Decision Engine)
# ==========================================
class SparseJumpModel:
    def __init__(self, n_states=3, jump_penalty=1.5, sparsity_kappa=2.0, max_iter=50):
        self.K = n_states
        self.lambda_pen = jump_penalty
        self.kappa = sparsity_kappa
        self.max_iter = max_iter
        self._score_scale = 2.5
        self.weights = None
        self.means = None
        
    def load_weights(self, means: np.ndarray, weights: np.ndarray):
        """Inject pre-trained centroids for live inference."""
        self.means = np.asarray(means, dtype=float)
        self.weights = np.asarray(weights, dtype=float)

    def online_predict(
        self,
        x_t: np.ndarray,
        expected_n_features: int,
        prev_state,
        nhhmm_probs: np.ndarray,
        bias_weight: float = 1.0,
    ):
        try:
            x_t = _coerce_1d_vector(x_t, expected_size=expected_n_features, name="SJM x_t")
        except ValueError as e:
            raise RuntimeError(
                f"SJM input validation failed: {e}. "
                "Feature vector invalid or corrupted."
            ) from e

        n_feat = x_t.size

        # --- Dimension guard (resolves CRITICAL-6) ---
        if self.means is None:
            self.means = np.zeros((self.K, n_feat), dtype=float)
            if n_feat > 0:
                self.means[0, 0] = 0.0030
                if self.K > 1:
                    self.means[1, 0] = -0.0030
                if self.K > 2:
                    crisis_idx = min(2, n_feat - 1)
                    self.means[2, crisis_idx] = 0.05
            self.weights = np.ones(n_feat) / np.sqrt(n_feat)
        elif self.means.shape[1] != n_feat:
            raise ValueError(
                f"SJM feature dimension mismatch: expected {self.means.shape[1]}, "
                f"got {n_feat}. Check upstream feature pipeline."
            )
        try:
            nhhmm_probs = _normalize_prob_vector(np.asarray(nhhmm_probs, dtype=float))
        except Exception:
            nhhmm_probs = np.ones(self.K, dtype=float) / self.K

        weighted_x = x_t * self.weights  # (n_feat,)

        # --- Vectorized cost computation (resolves CRITICAL-1) ---
        # costs[k] = negative squared distance to centroid k
        diffs = weighted_x[np.newaxis, :] - self.means  # (K, n_feat)
        costs = -np.sum(diffs ** 2, axis=1)              # (K,)

        # Persistence penalty: applied to all non-incumbent states uniformly
        if prev_state is not None:
            switch_mask = np.ones(self.K, dtype=bool)
            switch_mask[prev_state] = False
            # lambda_pen + additional damping combined into single penalty term
            costs[switch_mask] -= (0.25 * self.lambda_pen + 0.05)

        # NHHMM bias: symmetric clamp [0, 1] allows caller to reduce influence
        # below 1.0 during low-confidence or risk-off conditions.
        effective_bias = float(np.clip(bias_weight, 0.0, 1.0))
        biased_scores = costs + effective_bias * np.log(
            np.clip(nhhmm_probs, 1e-12, None)
        )

        best_state = int(np.argmax(biased_scores))

        # Numerically stable softmax for output probabilities
        shifted = (biased_scores - np.max(biased_scores)) * self._score_scale
        probs = np.exp(shifted)
        total = float(probs.sum())
        if not np.isfinite(total) or total <= 0.0:
            probs = np.ones(self.K, dtype=float) / self.K
        else:
            probs /= total

        return best_state, probs


# ==========================================
# 3. MS-GARCH (Risk Engine)
# ==========================================
class MSGARCH_RiskEngine:
    _VAR_CEIL = np.array([0.006, 0.025])
    _REGIME_PROB_FLOOR: float = 0.01

    def __init__(self, target_volatility=0.02, regime_prob_floor: float = None):
        self.target_vol = target_volatility
        self.omega = np.array([1e-5, 5e-4])
        self.alpha = np.array([0.05, 0.20])
        self.beta_garch = np.array([0.90, 0.70])  
        self.P = np.array([[0.98, 0.02],   
                           [0.05, 0.95]])
        if regime_prob_floor is not None:
            if not (1e-6 <= regime_prob_floor < 0.5):
                raise ValueError(
                    f"regime_prob_floor must be in [1e-6, 0.5), got {regime_prob_floor}. "
                    "Values below 1e-6 reintroduce log-space underflow risk; "
                    "values >= 0.5 collapse the two-regime model to uniform."
                )
            self._REGIME_PROB_FLOOR = float(regime_prob_floor)

    def _garch_update(
        self, current_var: np.ndarray, return_t: float
    ) -> np.ndarray:
        current_var = np.asarray(current_var, dtype=float)
        current_var = np.where(np.isfinite(current_var), current_var, self.target_vol ** 2)
        current_var = np.clip(current_var, 1e-8, self._VAR_CEIL)
        return_t = safe_float(return_t, default=0.0, min=-2.0, max=2.0)
        new_var = (
            self.omega
            + self.alpha * (return_t ** 2)
            + self.beta_garch * current_var
        )
        if not np.all(np.isfinite(new_var)):
            return np.full(2, max(self.target_vol ** 2, 1e-8), dtype=float)
        return np.clip(new_var, 1e-8, self._VAR_CEIL)

    def _update_regime_probs(
        self,
        current_probs: np.ndarray,
        predicted_var: np.ndarray,
        return_t: float,
    ) -> np.ndarray:
        current_probs = _normalize_prob_vector(np.asarray(current_probs, dtype=float))
        return_t = safe_float(return_t, default=0.0, min=-2.0, max=2.0)
        predicted_var = np.clip(
            np.asarray(predicted_var, dtype=float), 1e-8, None
        )
        if not np.all(np.isfinite(predicted_var)):
            predicted_var = np.full(2, max(self.target_vol ** 2, 1e-8), dtype=float)
        log_likelihoods = (
            -0.5 * np.log(2.0 * np.pi * predicted_var + 1e-12)
            - 0.5 * (return_t ** 2) / (predicted_var + 1e-12)
        )
        predicted_probs = np.dot(current_probs, self.P)
        predicted_probs = np.clip(predicted_probs, self._REGIME_PROB_FLOOR, None)
        predicted_probs /= predicted_probs.sum()
        log_joint = np.log(predicted_probs) + log_likelihoods
        log_joint -= logsumexp(log_joint)
        updated = np.exp(log_joint)
        updated = np.clip(updated, self._REGIME_PROB_FLOOR, None)
        updated /= updated.sum()
        return updated


class AdvancedRegimeEngine:
    # ==========================================
    # 🚨 CIRCUIT BREAKER CONFIG
    # ==========================================
    _MAX_DRAWDOWN = 0.12
    _MAX_CONSECUTIVE_LOSSES = 7
    _VOL_SHOCK_MULTIPLIER = 3.5
    _CONFIDENCE_COLLAPSE_THRESHOLD = 0.35
    _CONFIDENCE_COLLAPSE_MIN_STREAK = 3
    _HEALING_COOLDOWN_TICKS = 20

    _EWM_ALPHA: float = 0.15
    _RANGE_SIGNED_DECAY: float = 0.25
    _MIN_SIGNED_TRADE_SIZE: float = 0.01
    _MIN_POSITION_SIZE: float = 0.01
    _RANGE_NEUTRALIZE_VOL: float = 0.018
    _RANGE_DECAY_FLOOR_K: float = 0.05
    _EDGE_MIN_SWITCH_CONFIDENCE: float = 0.58
    _EDGE_MIN_DIRECTIONAL_CONFIDENCE: float = 0.64
    _EDGE_VOL_PENALTY: float = 0.18
    _EDGE_LOW_CONF_FRACTION: float = 0.85
    _EDGE_POWER: float = 1.5
    _EDGE_MIN_ACTIVE: float = 0.35
    _SWITCH_MIN_PERSISTENCE: int = 2
    _SWITCH_COOLDOWN_SEC: float = 2.0
    _SWITCH_EDGE_BUFFER: float = 0.03
    _SWITCH_VOL_WEIGHT: float = 0.18
    _SWITCH_CONF_WEIGHT: float = 0.34
    _SWITCH_EDGE_WEIGHT: float = 0.48
    _LAST_VALID_VOL_FLOOR: float = 1e-8
    _VOL_MEMORY_DECAY: float = 0.98
    _STATE_VERSION: str = "1.2.0"
    _WARNING_CACHE_LIMIT: int = 1024
    _WARNING_CACHE_TRIM_TO: int = 768
    _TRACEBACK_MAX_FRAMES: int = 12
    _TRACEBACK_MAX_CHARS: int = 3000
    _TRACEBACK_MAX_LINE_CHARS: int = 300
    _HASH_NAMESPACE: str = "ADV_REGIME_REPLAY"
    _MAX_POSITION_SIZE: float = _POSITION_SIZE_CAP
    _MIN_EQUITY_FLOOR: float = 1e-6
    _DIRECTION_SWITCH_GAP: float = 0.02
    _SJM_RESERVED_RETURN_IDX: int = 0
    _SJM_RESERVED_ABS_RETURN_IDX: int = 2
    _DEFAULT_ERROR_CATEGORY_BY_CODE: Dict[str, str] = {
        "E120": "input",
        "E130": "input",
        "E200": "numerical",
    }

    def _json_default(self, obj: Any):
        """
        Deterministic JSON fallback for unsupported objects.
        Avoid memory addresses and process-local repr output.
        """
        if dataclasses.is_dataclass(obj):
            return self._canonicalize(dataclasses.asdict(obj))
        if isinstance(obj, (bytes, bytearray, memoryview)):
            return {"__bytes__": bytes(obj).hex()}
        if isinstance(obj, complex):
            return {"__complex__": [format(obj.real, ".17g"), format(obj.imag, ".17g")]}
        if isinstance(obj, (set, frozenset)):
            return sorted(self._canonicalize(v) for v in obj)
        if hasattr(obj, "__dict__"):
            return self._canonicalize(vars(obj))
        return {"__unsupported__": f"{type(obj).__module__}.{type(obj).__qualname__}"}

    @staticmethod
    def _canonical_sort_key(value: Any) -> str:
        try:
            return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        except Exception:
            return f"{type(value).__name__}:{str(value)}"

    # ==========================================
    # STATE HASH (BITWISE DETERMINISM)
    # ==========================================
    def _canonicalize(self, obj: Any):
        if isinstance(obj, dict):
            return {
                str(k): self._canonicalize(obj[k])
                for k in sorted(obj, key=lambda x: f"{type(x).__name__}:{str(x)}")
            }
        if isinstance(obj, (list, tuple)):
            return [self._canonicalize(v) for v in obj]
        if isinstance(obj, (set, frozenset)):
            canonical_values = [self._canonicalize(v) for v in obj]
            return sorted(canonical_values, key=self._canonical_sort_key)
        if isinstance(obj, np.ndarray):
            return [self._canonicalize(v) for v in obj.tolist()]
        if isinstance(obj, np.generic):
            return self._canonicalize(obj.item())
        if isinstance(obj, float):
            if not np.isfinite(obj):
                if np.isnan(obj):
                    return {"__float__": "NaN"}
                return {"__float__": "Infinity" if obj > 0 else "-Infinity"}
            return format(obj, ".17g")
        return obj

    def _deep_sort(self, obj):
        if isinstance(obj, dict):
            return {
                k: self._deep_sort(obj[k])
                for k in sorted(obj, key=lambda x: f"{type(x).__name__}:{str(x)}")
            }
        if isinstance(obj, list):
            return [self._deep_sort(v) for v in obj]
        return obj

    def _normalize_rng_state(self, rng):
        try:
            if rng is None:
                return None
            state = rng.bit_generator.state
            return {
                "bit_generator": type(rng.bit_generator).__name__,
                "bit_generator_module": type(rng.bit_generator).__module__,
                "numpy_version": np.__version__,
                "internal_state": self._canonicalize(state),
            }
        except Exception:
            return "UNSUPPORTED_RNG"

    def _state_hash(self, state: Dict[str, Any]) -> str:
        try:
            canonical = self._deep_sort(self._canonicalize(state))
            wrapped_payload = {
                "namespace": self._HASH_NAMESPACE,
                "schema_version": str(state.get("schema_version", self._STATE_VERSION)),
                "payload": canonical,
            }
            s = json.dumps(
                wrapped_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                default=self._json_default,
            )
            return hashlib.sha256(f"{self._HASH_NAMESPACE}|{s}".encode()).hexdigest()
        except Exception:
            if not getattr(self, "_is_replay", False):
                try:
                    LOGGER.error("State hash canonicalization failed", exc_info=True)
                except Exception:
                    warnings.warn("State hash logging failed", RuntimeWarning, stacklevel=2)
            return hashlib.sha256(b"STATE_HASH_ERROR").hexdigest()

    def __init__(
        self,
        n_states=3,
        n_features=3,
        target_vol=0.02,
        allow_igarch=False,
        regime_prob_floor: float = None,
        emit_extended_schema: bool = False,
        strict_mtf_keys: bool = True,
        mtf_weights: Dict[str, float] = None,
        seed: int | None = 7,
    ):
        if n_states != 3:
            raise ValueError(
                f"AdvancedRegimeEngine requires exactly 3 states (Bull/Bear/Crisis), "
                f"got n_states={n_states}. compute_hmm_regime is hard-coded for 3 regimes."
            )
        if n_features < 1:
            raise ValueError(f"n_features must be >= 1, got {n_features}.")
        self._allow_igarch = allow_igarch
        # deterministic internal RNG for observability sampling / reproducibility
        self._rng_seed = None if seed is None else int(seed)
        self._rng = np.random.default_rng(self._rng_seed)
        # Deprecated: retained only for backward compatibility (no effect)
        self._emit_extended_schema = emit_extended_schema
        self._strict_mtf_keys = bool(strict_mtf_keys)
        self.K = n_states
        self.n_features = n_features

        # --- NEW: Multi-timeframe weights ---
        # Example: {"1m": 0.5, "5m": 0.3, "15m": 0.2}
        self.mtf_weights = mtf_weights or {"base": 1.0}

        self.nhhmm = NHHMM_Engine(n_states=n_states, n_features=n_features)
        self.sjm = SparseJumpModel(n_states=n_states)
        self.garch = MSGARCH_RiskEngine(
            target_volatility=target_vol,
            regime_prob_floor=regime_prob_floor,
        )
        self._init_params: Dict[str, Any] = {
            'n_states': n_states,
            'n_features': n_features,
            'target_vol': target_vol,
            'allow_igarch': allow_igarch,
            'regime_prob_floor': self.garch._REGIME_PROB_FLOOR,
            'schema_version': _OUTPUT_SCHEMA_VERSION,
            'seed': self._rng_seed,
        }

        for k in range(len(self.garch.alpha)):
            persistence = self.garch.alpha[k] + self.garch.beta_garch[k]
            if persistence >= 1.0:
                if allow_igarch:
                    warnings.warn(
                        f"GARCH regime {k} is non-stationary "
                        f"(alpha + beta = {persistence:.4f}). "
                        "allow_igarch=True suppresses this error. "
                        "Variance explosion risk is not bounded by the model.",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                else:
                    raise ValueError(
                        f"GARCH regime {k} is non-stationary: "
                        f"alpha + beta = {persistence:.4f} >= 1.0. "
                        "Pass allow_igarch=True to suppress this check."
                    )

        self.nhhmm_prior = np.ones(n_states) / n_states
        self.current_regime_idx = None
        self.last_signed_position_size = 0.0
        # --- NEW: PnL tracking ---
        self._last_price = None
        self._last_effective_trend_strength = 0.0
        self._last_edge_score = 0.0
        self._last_regime_change_ts = None
        self._last_valid_vol = float(target_vol)
        self._switch_stability_ema = 1.0
        self.range_ticks = 0.0
        self.range_ticks_int = 0
        self._prev_regime = None
        self._prev_directional_label = None
        self._prev_raw_regime = None
        self._confirmed_regime = None
        self._confirmed_regime_idx = None
        self._range_anchor_size = 0.0
        self._in_range = False
        self._last_timestamp = None
        self._DECAY_LAMBDA = 0.5
        self._last_valid_dt = 1.0
        self._MAX_DT = 60.0
        self._regime_persistence = 0
        self._REGIME_CONFIRMATION_TICKS = 2
        self._lock = threading.RLock()
        self._regime_smoother = RegimeMarkovSmoother()
        self._regime_state_probs = np.ones(4, dtype=float) / 4.0
        self._last_valid_sjm_probs: np.ndarray | None = None

        # ==========================================
        # 🚨 RISK STATE TRACKING
        # ==========================================
        self._equity_peak = 1.0
        self._equity = 1.0
        self._drawdown = 0.0
        self._loss_streak = 0
        self._shock_memory = 0.0
        self._return_ema = 0.0
        self._abs_return_ema = 0.0
        self._obs_controller = ObservabilityController() if ObservabilityController is not None else None

        self._circuit_breaker_active = False
        self._circuit_breaker_reason = None
        self._circuit_breaker_trigger_tick = -1
        self._healing_counter = 0
        self._last_healing_action = "NONE"
        self._last_healing_error = None
        self._last_healing_context = {}
        self._healing_count = 0

        # Warning de-duplication / rate limiting.
        self._warning_last_emitted: "OrderedDict[str, float]" = OrderedDict()
        self._warning_first_seen: "OrderedDict[str, float]" = OrderedDict()
        self._warning_counts: Dict[str, int] = {}
        self._warning_lock = threading.RLock()
        self._last_health = "OK"
        self._warning_drop_count = 0
        self._warning_drop_alerted = False
        self._warning_backend_failure_count = 0

        self.engine_id = f"engine_{id(self)}"

        # ==========================================
        # NEW: Async Warning Queue (Non-blocking I/O)
        # ==========================================
        self._warning_queue: "queue.Queue[str]" = queue.Queue(maxsize=10000)
        self._warning_stop_event = threading.Event()
        self._warning_worker = threading.Thread(
            target=self._warning_emitter_loop,
            daemon=True,
            name=f"{self.engine_id}_warning_worker"
        )
        try:
            self._warning_worker.start()
        except Exception:
            # degrade gracefully: engine remains usable even if async warning thread fails
            self._warning_worker = None
        self._warning_finalizer = weakref.finalize(
            self,
            AdvancedRegimeEngine._finalize_warning_worker,
            weakref.ref(self),
        )

        self._errors_module_available = True
        try:
            from errors import get_error as _get_error  # type: ignore
            self._error_category_resolver = _get_error
        except Exception:
            self._errors_module_available = False
            self._error_category_resolver = None
            if not getattr(self, "_is_replay", False):
                msg = (
                    "errors.get_error unavailable; self-healing category mapping "
                    "running in built-in fallback mode."
                )
                try:
                    LOGGER.warning(msg)
                except Exception:
                    warnings.warn(msg, RuntimeWarning, stacklevel=2)

        self._obs_counter = 0
        self._OBS_SAMPLE_RATE = 5  # update metrics every N ticks
        self._tick_id = 0
        self._confidence_collapse_streak = 0
        self._strict_replay = True
        self._fsm_error = None
        try:
            self._trace_engine = TracebackEngine() if TracebackEngine is not None else None
        except Exception:
            self._trace_engine = None
        try:
            self._replay_engine = ReplayEngine() if ReplayEngine is not None else None
        except Exception:
            self._replay_engine = None

        # --- FIX: Precompute static JSON overhead for traceback logging ---
        try:
            self._BASE_TRACEBACK_JSON_SIZE = len(json.dumps(
                {
                    "type": "",
                    "message": "",
                    "frames": [],
                    "dropped_frames": 0,
                    "truncated": True,
                },
                separators=(",", ":"),
                ensure_ascii=True
            ))
        except Exception:
            self._BASE_TRACEBACK_JSON_SIZE = 200  # safe fallback

        target_var = float(self.garch.target_vol ** 2)
        self.garch_var = np.full(2, target_var, dtype=float)
        self.garch_prob = np.ones(2) / 2.0
        self._smoothed_garch_prob = self.garch_prob.copy()

    def _stationary_garch_var(self) -> np.ndarray:
        target_var = float(self.garch.target_vol ** 2)
        return np.full(2, target_var, dtype=float)

    def _obs_should_sample(self) -> bool:
        """
        Randomized sampling removes deterministic blind spots (aliasing).
        """
        if getattr(self, "_is_replay", False):
            return False
        controller = getattr(self, "_obs_controller", None)
        if controller is not None:
            try:
                return bool(controller.should_sample())
            except Exception as exc:
                self._warn_rate_limited("obs_should_sample_failure", f"Observability sample fallback: {exc}", cooldown_s=30.0)
        return (int(self._rng.integers(0, self._OBS_SAMPLE_RATE)) == 0)

    def _obs_should_emit_warning(self, key: str, cooldown_s: float) -> bool:
        controller = getattr(self, "_obs_controller", None)
        if controller is not None:
            try:
                return bool(controller.should_emit_warning(key, cooldown_s))
            except Exception as exc:
                self._warn_rate_limited("obs_emit_warning_failure", f"Warning gate fallback: {exc}", cooldown_s=30.0)
        return True

    def _obs_traceback_budget(self) -> int:
        controller = getattr(self, "_obs_controller", None)
        if controller is not None:
            try:
                return max(1, int(controller.traceback_budget()))
            except Exception as exc:
                self._warn_rate_limited("obs_traceback_budget_failure", f"Traceback budget fallback: {exc}", cooldown_s=30.0)
        return int(self._TRACEBACK_MAX_FRAMES)

    def _obs_observe(
        self,
        event_type: str,
        severity: str,
        context: Dict[str, Any] | None = None,
    ) -> None:
        if getattr(self, "_is_replay", False):
            return
        controller = getattr(self, "_obs_controller", None)
        if controller is None:
            return
        try:
            controller.observe(event_type=event_type, severity=severity, context=context)
        except Exception as exc:
            self._warn_rate_limited("obs_observe_failure", f"Observability observe failed: {exc}", cooldown_s=30.0)

    def _replay_record(self, event_type: str, payload: Dict[str, Any] | None = None) -> None:
        if getattr(self, "_is_replay", False):
            return
        replay_engine = getattr(self, "_replay_engine", None)
        if replay_engine is None:
            return
        safe_payload = {}
        if isinstance(payload, dict):
            for k, v in payload.items():
                try:
                    if isinstance(v, (int, float, str, bool, type(None))):
                        safe_payload[k] = v
                    else:
                        safe_payload[k] = str(v)[:200]
                except Exception:
                    safe_payload[k] = "UNSERIALIZABLE"
        try:
            replay_engine.record_event(event_type, safe_payload)
        except Exception as exc:
            self._warn_rate_limited("replay_record_failure", f"Replay record failed: {exc}", cooldown_s=30.0)

    def _shutdown_warning_worker(self) -> None:
        """
        Best-effort shutdown path to flush queued warnings before process exit.
        """
        stop_event = getattr(self, "_warning_stop_event", None)
        worker = getattr(self, "_warning_worker", None)
        warning_queue = getattr(self, "_warning_queue", None)

        if stop_event is None or worker is None or warning_queue is None:
            return

        stop_event.set()
        try:
            warning_queue.put_nowait(None)
        except queue.Full:
            self._warn_rate_limited("warning_queue_full_shutdown", "Warning queue full during shutdown", cooldown_s=30.0)

        if worker.is_alive():
            worker.join(timeout=1.0)

    @staticmethod
    def _finalize_warning_worker(self_ref: "weakref.ReferenceType[AdvancedRegimeEngine]") -> None:
        obj = self_ref()
        if obj is not None:
            obj._shutdown_warning_worker()

    @staticmethod
    def _normalize_timestamp(ts: Any) -> float | None:
        if ts is None:
            return None
        try:
            ts_f = float(ts)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(ts_f):
            return None
        if ts_f < 0:
            return ts_f
        if ts_f >= 1e18:
            return ts_f / 1e9
        if ts_f >= 1e15:
            return ts_f / 1e6
        if ts_f >= 1e12:
            return ts_f / 1e3
        return ts_f

    @staticmethod
    def _coerce_finite_scalar(value: Any, *, default: float = 0.0) -> float:
        return _safe_float(value, default=float(default))

    def _log_state_load_issue(self, field: str, exc: Exception, value: Any = None) -> None:
        if getattr(self, "_is_replay", False):
            return
        try:
            LOGGER.error(
                "STATE_LOAD_DEGRADE field=%s error=%s value=%s",
                field,
                repr(exc),
                repr(value)[:200],
            )
        except Exception:
            warnings.warn(f"STATE_LOAD_DEGRADE logging failed for field={field}", RuntimeWarning, stacklevel=2)

    @staticmethod
    def _coerce_vector(name: str, values: Any, expected_size: int) -> np.ndarray:
        arr = np.asarray(values, dtype=float)
        if arr.ndim == 0:
            arr = arr.reshape(1)
        else:
            arr = np.ravel(arr)
        if arr.shape != (expected_size,):
            raise ValueError(f"Vector '{name}' must have shape ({expected_size},), got {arr.shape}.")
        if not np.all(np.isfinite(arr)):
            raise ValueError(f"Vector '{name}' contains non-finite values: {arr}.")
        return arr

    def _state_scalar(
        self,
        state: Dict[str, Any],
        field: str,
        *,
        default: float,
        min_value: float | None = None,
        max_value: float | None = None,
    ) -> float:
        raw = state.get(field, default)
        parsed = _safe_float(raw, default=float(default))
        invalid_reason = None
        if not np.isfinite(parsed):
            invalid_reason = "non-finite"
        elif min_value is not None and parsed < min_value:
            invalid_reason = f"value<{min_value}"
        elif max_value is not None and parsed > max_value:
            invalid_reason = f"value>{max_value}"
        if invalid_reason is not None:
            self._log_state_load_issue(
                field,
                ValueError(f"invalid scalar ({invalid_reason})"),
                raw,
            )
            parsed = float(default)
        return float(parsed)

    def _state_vector(
        self,
        state: Dict[str, Any],
        field: str,
        expected_size: int,
        *,
        fallback: np.ndarray,
        normalize_probabilities: bool = False,
    ) -> np.ndarray:
        try:
            arr = _safe_array(state.get(field, fallback), shape=(expected_size,), default=fallback)
            if not np.all(np.isfinite(arr)):
                raise ValueError(f"vector '{field}' contains non-finite values")
            if normalize_probabilities:
                arr = _safe_prob_vector(arr, expected_size)
            return arr
        except Exception as exc:
            self._log_state_load_issue(field, exc, state.get(field, None))
            out = np.asarray(fallback, dtype=float).reshape(expected_size)
            if normalize_probabilities:
                out = _normalize_prob_vector(out)
            return out

    # ==========================================
    # NEW: Async Warning Emitter Loop
    # ==========================================
    def _warning_emitter_loop(self) -> None:
        """
        Dedicated background thread for warning emission.
        Ensures logging I/O never blocks trading execution threads.
        """
        while True:
            if self._warning_stop_event.is_set() and self._warning_queue.empty():
                break

            try:
                msg = self._warning_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if msg is None:
                if self._warning_stop_event.is_set() and self._warning_queue.empty():
                    break
                continue

            try:
                LOGGER.warning(msg)
            except Exception:
                self._warning_backend_failure_count = int(getattr(self, "_warning_backend_failure_count", 0)) + 1
                try:
                    warnings.warn(msg, RuntimeWarning, stacklevel=3)
                except Exception:
                    pass

    def _warn_rate_limited(self, key: str, message: str, cooldown_s: float = 30.0) -> None:
        """
        Emit repeated operational warnings at a controlled rate.
        The event is always counted internally, but the Python warning is only
        emitted once per cooldown window for the same key.
        """
        if getattr(self, "_is_replay", False):
            return
        emit = False
        with self._warning_lock:
            now = time.monotonic()
            self._warning_counts[key] = self._warning_counts.get(key, 0) + 1

            if key not in self._warning_first_seen:
                self._warning_first_seen[key] = now
            else:
                self._warning_first_seen.move_to_end(key)

            last_emitted = self._warning_last_emitted.get(key)
            if last_emitted is None or (now - last_emitted) >= cooldown_s:
                self._warning_last_emitted[key] = now
                self._warning_last_emitted.move_to_end(key)
                emit = True

            if len(self._warning_counts) >= self._WARNING_CACHE_LIMIT:
                target_size = self._WARNING_CACHE_TRIM_TO
                to_remove = max(len(self._warning_counts) - target_size, 0)
                if to_remove > 0:
                    for old_key in list(self._warning_first_seen.keys())[:to_remove]:
                        self._warning_counts.pop(old_key, None)
                        self._warning_last_emitted.pop(old_key, None)
                        self._warning_first_seen.pop(old_key, None)

        if emit and not self._obs_should_emit_warning(key, cooldown_s):
            return

        if emit:
            try:
                self._warning_queue.put_nowait(message)
            except queue.Full:
                with self._warning_lock:
                    self._warning_drop_count = int(getattr(self, "_warning_drop_count", 0)) + 1
                    should_alert = not bool(getattr(self, "_warning_drop_alerted", False))
                    self._warning_drop_alerted = True
                self._last_health = "DEGRADED"
                if should_alert:
                    try:
                        warnings.warn(
                            "Warning queue saturated; dropping warning messages.",
                            RuntimeWarning,
                            stacklevel=2,
                        )
                    except Exception:
                        pass
                return

    def _warn_tf_failure(self, tf: str, exc: Exception) -> None:
        """
        Keep per-timeframe failures isolated while preserving enough traceback
        context to debug the exact failure site from the warning text.
        """
        if getattr(self, "_is_replay", False):
            return
        trace_engine = getattr(self, "_trace_engine", None)
        tb = None
        tb_struct = None
        if trace_engine is not None:
            try:
                frame_budget = self._obs_traceback_budget()
                captured = trace_engine.capture(
                    exc=exc,
                    context={"timeframe": tf, "component": "mtf_forward_pass"},
                    frame_budget=frame_budget,
                )
                captured_frames = captured.get("frames", [])
                tb_struct = {
                    "type": captured.get("type", type(exc).__name__),
                    "message": captured.get("message", str(exc)),
                    "frames": captured_frames,
                    "dropped_frames": max(
                        int(captured.get("frame_count", len(captured_frames))) - len(captured_frames),
                        0,
                    ),
                    "annotations": [],
                    # Backward-compatible extension fields:
                    "trace_id": captured.get("trace_id"),
                    "fingerprint": captured.get("fingerprint"),
                    "context": captured.get("context", {}),
                    "timestamp": captured.get("timestamp"),
                }
                rendered_frames = []
                for frame in captured_frames:
                    rendered_frames.append(
                        f'  File "{frame.get("file")}", line {frame.get("line")}, in {frame.get("func")}'
                    )
                    code_line = frame.get("code")
                    if isinstance(code_line, str) and code_line:
                        rendered_frames.append(f"    {code_line}")
                tb_parts = [
                    f'{tb_struct.get("type", type(exc).__name__)}: {tb_struct.get("message", str(exc))}'
                ]
                if rendered_frames:
                    tb_parts = rendered_frames + tb_parts
                tb = "\n".join(tb_parts)
                if len(tb) > self._TRACEBACK_MAX_CHARS:
                    tb = tb[: self._TRACEBACK_MAX_CHARS - 3] + "..."
            except Exception:
                # Preserve existing traceback fallback behavior.
                self._warn_rate_limited("traceback_structured_failure", "Structured traceback rendering failed", cooldown_s=30.0)
        if tb is None or tb_struct is None:
            tb = self._summarize_traceback(exc)
            tb_struct = self._summarize_traceback_structured(exc)

        self._replay_record(
            "error",
            {"type": "mtf_failure", "timeframe": tf, "error": type(exc).__name__},
        )

        # --- FIX: ASCII-safe JSON encoding (prevents ingestion issues in strict pipelines) ---
        try:
            tb_struct_json = json.dumps(
                tb_struct,
                separators=(",", ":"),
                ensure_ascii=True
            )
        except Exception:
            tb_struct_json = '{"error":"traceback_serialization_failed"}'

        # Hard cap to prevent log explosion
        MAX_STRUCT_CHARS = 1200
        if len(tb_struct_json) > MAX_STRUCT_CHARS:
            msg_full = str(tb_struct.get("message", ""))
            msg_full = msg_full if isinstance(msg_full, str) else repr(msg_full)

            frames = tb_struct.get("frames", [])
            max_keep = min(len(frames), 6)

            tb_struct_json = None

            # --- FIX: Use precomputed JSON base size ---
            base_size = getattr(self, "_BASE_TRACEBACK_JSON_SIZE", 200)

            # --- FIX: Joint optimization (message + frames) ---
            # Try multiple message budgets, then binary search frames inside each
            msg_budgets = [300, 220, 160, 120, 80]

            for msg_limit in msg_budgets:
                if len(msg_full) > msg_limit:
                    msg = msg_full[:msg_limit - 3] + "..."
                else:
                    msg = msg_full

                # --- FIX: Account for JSON escaping overhead ---
                msg_size = len(msg) + 10

                low, high = 0, max_keep
                best_json = None

                while low <= high:
                    mid = (low + high) // 2

                    try:
                        # --- FIX: Adaptive heuristic (safer upper bound) ---
                        # Increased from 120 → 180 to avoid underestimation
                        est_frame_size = mid * 180

                        est_total = base_size + msg_size + est_frame_size

                        if est_total > MAX_STRUCT_CHARS:
                            high = mid - 1
                            continue

                        candidate = {
                            "type": tb_struct.get("type"),
                            "message": msg,
                            "frames": frames[:mid],
                            "dropped_frames": tb_struct.get("dropped_frames", 0),
                            "truncated": True,
                        }

                        candidate_json = json.dumps(
                            candidate,
                            separators=(",", ":"),
                            ensure_ascii=True
                        )

                        if len(candidate_json) <= MAX_STRUCT_CHARS:
                            best_json = candidate_json
                            low = mid + 1   # try keeping more frames
                        else:
                            high = mid - 1  # reduce frames

                    except Exception:
                        high = mid - 1

                if best_json is not None:
                    tb_struct_json = best_json
                    break

            if tb_struct_json is None:
                # --- FIX: Absolute-safe fallback (cannot fail) ---
                try:
                    safe_msg = str(msg_full)
                except Exception:
                    safe_msg = "unrepresentable_error"

                safe_msg = safe_msg.encode("ascii", errors="replace").decode("ascii")

                if len(safe_msg) > 200:
                    safe_msg = safe_msg[:197] + "..."

                try:
                    minimal = {
                        "type": str(tb_struct.get("type", "UnknownError")),
                        "message": safe_msg,
                        "truncated": True
                    }
                    tb_struct_json = json.dumps(minimal, separators=(",", ":"), ensure_ascii=True)
                except Exception:
                    tb_struct_json = '{"type":"UnknownError","message":"serialization_failed","truncated":true}'

        self._warn_rate_limited(
            key=f"mtf_forward_pass_failure:{tf}",
            message=(
                f"Skipping mtf[{tf}] due to forward-pass failure:\n{tb}\n"
                f"STRUCTURED_JSON={tb_struct_json}"
            ),
            cooldown_s=30.0,
        )

    def _summarize_traceback(self, exc: Exception) -> str:
        """
        Compact traceback summary for warning transport.
        Preserves chained exceptions and the most relevant Python frames, then
        truncates long lines and the final rendered text so array dumps and other
        oversized payloads cannot flood logs.
        """
        tb_exc = traceback.TracebackException.from_exception(exc, capture_locals=False)
        rendered = "".join(tb_exc.format(chain=True)).rstrip()

        budget_frames = self._obs_traceback_budget()
        lines = rendered.splitlines()
        if len(lines) > budget_frames:
            head = lines[: min(3, len(lines))]
            tail = lines[-max(4, budget_frames - len(head)):]
            rendered = "\n".join(head + ["..."] + tail)

        clipped_lines = []
        for line in rendered.splitlines():
            if len(line) > self._TRACEBACK_MAX_LINE_CHARS:
                clipped_lines.append(
                    line[: self._TRACEBACK_MAX_LINE_CHARS - 3] + "..."
                )
            else:
                clipped_lines.append(line)
        # Preserve readable multi-line structure (primary mode)
        rendered = "\n".join(clipped_lines)

        if len(rendered) > self._TRACEBACK_MAX_CHARS:
            # Truncate ONLY — preserve multi-line structure for consistency
            rendered = rendered[: self._TRACEBACK_MAX_CHARS - 3] + "..."

        return rendered

    # ==========================================
    # NEW: Structured Traceback (for observability systems)
    # ==========================================
    def _summarize_traceback_structured(self, exc: Exception) -> Dict[str, Any]:
        """
        Structured traceback representation for log pipelines / monitoring systems.
        Avoids fragile string parsing and enables deterministic alerting.
        """
        tb_exc = traceback.TracebackException.from_exception(exc, capture_locals=False)

        full_stack = list(tb_exc.stack)
        max_frames = min(self._TRACEBACK_MAX_FRAMES, self._obs_traceback_budget())

        # --- HARD CAP ON STACK SIZE (secondary protection) ---
        if len(full_stack) > 100:
            full_stack = full_stack[-100:]

        # --- FIX: Preserve causality using head + mid + tail sampling ---
        if len(full_stack) > max_frames:
            # --- FIX: Adaptive frame allocation (prevents loss of user-level context) ---
            # Dynamically rebalance head/tail instead of fixed 3/4 split
            tail_n = min(6, max_frames // 2)
            head_n = min(4, max_frames - tail_n)
            mid_n = max_frames - (head_n + tail_n)

            dropped_frames = max(len(full_stack) - max_frames, 0)

            # --- FIX: Improved causal window sampling ---
            if mid_n > 0:
                tail_start = max(len(full_stack) - tail_n, 0)

                # --- NEW: Ensure user-level frames are preserved ---
                # Prefer keeping frames closer to origin when stack is deep
                if len(full_stack) > 2 * max_frames:
                    # very deep stack → bias towards earlier frames
                    causal_start = max(head_n, 0)
                    causal_end = causal_start + mid_n
                else:
                    # normal case → keep frames near failure
                    causal_end = tail_start
                    causal_start = max(causal_end - mid_n, 0)

                causal_slice = full_stack[causal_start:causal_end]

                selected_frames = (
                    full_stack[:head_n]        # entry context (user code)
                    + causal_slice             # adaptive causal window
                    + full_stack[-tail_n:]     # failure site (deep stack)
                )
            else:
                selected_frames = (
                    full_stack[:head_n] + full_stack[-tail_n:]
                )
        else:
            selected_frames = full_stack
            dropped_frames = 0

        # --- FIX: Preserve frame schema; emit gaps separately to avoid downstream
        #         consumers rejecting non-standard frame objects.
        frames = [
            {
                "file": frame.filename,
                "line": frame.lineno,
                "func": frame.name,
            }
            for frame in selected_frames
        ]

        annotations = []

        if len(full_stack) > max_frames:
            # Preserve downstream compatibility: keep frame objects unchanged and
            # record gaps as metadata only. This avoids schema drift in parsers that
            # expect every item in `frames` to be {"file", "line", "func"}.
            selected_indices = sorted({
                idx
                for idx, frame in enumerate(full_stack)
                if frame in selected_frames
            })

            prev_idx = None
            for idx in selected_indices:
                if prev_idx is not None and idx - prev_idx > 1:
                    annotations.append({
                        "type": "trace_gap",
                        "skipped": idx - prev_idx - 1,
                        "from_index": prev_idx,
                        "to_index": idx,
                        "message": "Traceback compressed: non-contiguous frame regions removed",
                    })
                prev_idx = idx

        return {
            "type": type(exc).__name__,
            "message": str(exc),
            "frames": frames,
            "dropped_frames": dropped_frames,
            "annotations": annotations,
        }

    @_synchronized
    def get_state(self) -> Dict[str, Any]:
        return {
            "state_version": self._STATE_VERSION,
            "model_signature": f"AdvancedRegimeEngine|v={self._STATE_VERSION}|schema={_OUTPUT_SCHEMA_VERSION}|n_states={self.K}|n_features={self.n_features}",
            
            # --- NEW: persist SJM fallback memory ---
            "last_valid_sjm_probs": (
                self._last_valid_sjm_probs.tolist()
                if self._last_valid_sjm_probs is not None else None
            ),
            
            "last_timestamp": None if self._last_timestamp is None else float(self._last_timestamp),
            "last_valid_dt": float(self._last_valid_dt),
            "current_regime_idx": None if self.current_regime_idx is None else int(self.current_regime_idx),
            "last_effective_trend_strength": float(self._last_effective_trend_strength),
            "last_edge_score": float(self._last_edge_score),
            "last_valid_vol": float(self._last_valid_vol),
            "switch_stability_ema": float(self._switch_stability_ema),
            "last_regime_change_ts": None if self._last_regime_change_ts is None else float(self._last_regime_change_ts),
            "range_ticks": float(self.range_ticks),
            "range_anchor_size": float(self._range_anchor_size),
            "last_signed_position_size": float(self.last_signed_position_size),
            "last_price": None if self._last_price is None else float(self._last_price),
            "in_range": bool(self._in_range),
            "prev_regime": self._prev_regime,
            "prev_raw_regime": self._prev_raw_regime,
            "confirmed_regime": self._confirmed_regime,
            "confirmed_regime_idx": None if self._confirmed_regime_idx is None else int(self._confirmed_regime_idx),
            "regime_persistence": int(self._regime_persistence),
            "nhhmm_prior": self.nhhmm_prior.astype(float).tolist(),
            "garch_prob": self.garch_prob.astype(float).tolist(),
            "smoothed_garch_prob": self._smoothed_garch_prob.astype(float).tolist(),
            "regime_state_probs": self._regime_state_probs.astype(float).tolist(),
            "garch_var": self.garch_var.astype(float).tolist(),
            "circuit_breaker_active": bool(self._circuit_breaker_active),
            "circuit_breaker_reason": self._circuit_breaker_reason,
            "circuit_breaker_trigger_tick": int(getattr(self, "_circuit_breaker_trigger_tick", -1)),
            "equity": float(self._equity),
            "equity_peak": float(self._equity_peak),
            "drawdown": float(self._drawdown),
            "loss_streak": int(self._loss_streak),
            "healing_count": int(getattr(self, "_healing_count", 0)),
            "last_healing_action": str(getattr(self, "_last_healing_action", "NONE")),
            "last_healing_error": getattr(self, "_last_healing_error", None),
            "last_healing_context": dict(getattr(self, "_last_healing_context", {})),
            "shock_memory": float(self._shock_memory),
            "return_ema": float(self._return_ema),
            "abs_return_ema": float(self._abs_return_ema),
            "prev_directional_label": getattr(self, "_prev_directional_label", None),
            "rng_seed": self._rng_seed,
            "engine_rng_state": (
                dict(self._rng.bit_generator.state)
                if getattr(self, "_rng", None) is not None else None
            ),
            "confidence_collapse_streak": int(getattr(self, "_confidence_collapse_streak", 0)),
            # Explicitly mark deprecated field as False to avoid confusion in external systems
            "emit_extended_schema": False,
        }

    @_synchronized
    def serialize_state(self) -> Dict[str, Any]:
        return self.get_state()

    @_synchronized
    def reset_state(self) -> None:
        self.nhhmm_prior = np.ones(self.K, dtype=float) / self.K
        self.current_regime_idx = None
        self._fsm_error = None
        self.last_signed_position_size = 0.0
        self._last_effective_trend_strength = 0.0
        self._last_edge_score = 0.0
        self._last_regime_change_ts = None
        self._last_valid_vol = float(self.garch.target_vol)
        self._switch_stability_ema = 1.0
        self.range_ticks = 0.0
        self.range_ticks_int = 0
        self._prev_regime = None
        self._prev_directional_label = None
        self._prev_raw_regime = None
        self._confirmed_regime = None
        self._confirmed_regime_idx = None
        self._range_anchor_size = 0.0
        self._in_range = False
        self._last_timestamp = None
        self._last_valid_dt = 1.0
        self._regime_persistence = 0
        self.garch_prob = np.ones(2, dtype=float) / 2.0
        
        # --- NEW: reset SJM fallback memory ---
        self._last_valid_sjm_probs = None
        self._smoothed_garch_prob = self.garch_prob.copy()
        self._regime_state_probs = np.ones(4, dtype=float) / 4.0
        if getattr(self, "_regime_smoother", None) is not None:
            self._regime_smoother.reset()
        self.garch_var = self._stationary_garch_var()
        self._shock_memory = 0.0
        self._return_ema = 0.0
        self._abs_return_ema = 0.0
        self._equity = 1.0
        self._equity_peak = 1.0
        self._drawdown = 0.0
        self._loss_streak = 0
        self._last_price = None
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = None
        self._circuit_breaker_trigger_tick = -1
        self._healing_counter = 0
        self._last_healing_action = "NONE"
        self._last_healing_error = None
        self._last_healing_context = {}
        self._healing_count = 0
        self._confidence_collapse_streak = 0

    @_synchronized
    def load_state(self, state: Dict[str, Any]) -> None:
        if not isinstance(state, dict):
            self._log_state_load_issue("state", TypeError("state must be dict"), type(state).__name__)
            return

        expected_signature = f"AdvancedRegimeEngine|v={self._STATE_VERSION}|schema={_OUTPUT_SCHEMA_VERSION}|n_states={self.K}|n_features={self.n_features}"
        incoming_signature = state.get("model_signature")
        if incoming_signature is not None and incoming_signature != expected_signature:
            self._log_state_load_issue(
                "model_signature",
                ValueError("signature mismatch"),
                incoming_signature,
            )
            self.reset_state()
            return

        incoming_version = state.get("state_version")
        if incoming_version is not None and incoming_version != self._STATE_VERSION:
            self._log_state_load_issue(
                "state_version",
                ValueError("version mismatch"),
                incoming_version,
            )
            self.reset_state()
            return

        ts = self._normalize_timestamp(state.get("last_timestamp", None))
        if state.get("last_timestamp", None) is not None and ts is None:
            self._log_state_load_issue("last_timestamp", ValueError("invalid timestamp"), state.get("last_timestamp"))
        self._last_timestamp = ts

        self._last_valid_dt = self._state_scalar(state, "last_valid_dt", default=1.0, min_value=1e-9)

        current_regime_idx = state.get("current_regime_idx", None)
        if current_regime_idx is not None:
            parsed_idx = _safe_int(current_regime_idx, default=-1)
            if parsed_idx < 0 or parsed_idx >= self.K:
                self._log_state_load_issue("current_regime_idx", ValueError("out of bounds"), current_regime_idx)
                self.current_regime_idx = None
            else:
                self.current_regime_idx = int(parsed_idx)
        else:
            self.current_regime_idx = None

        self.range_ticks = self._state_scalar(state, "range_ticks", default=0.0, min_value=0.0)
        self.range_ticks_int = int(self.range_ticks)

        # --- NEW: restore SJM fallback memory ---
        self._last_valid_sjm_probs = None
        if "last_valid_sjm_probs" in state and state["last_valid_sjm_probs"] is not None:
            self._last_valid_sjm_probs = _safe_prob_vector(state["last_valid_sjm_probs"], self.K)

        self._range_anchor_size = self._state_scalar(state, "range_anchor_size", default=0.0, min_value=0.0)
        self.last_signed_position_size = self._state_scalar(
            state,
            "last_signed_position_size",
            default=0.0,
            min_value=-1.0,
            max_value=1.0,
        )
        self._in_range = bool(state.get("in_range", False))
        last_price = state.get("last_price", None)
        self._last_price = None
        if last_price is not None:
            last_price_f = self._state_scalar(state, "last_price", default=np.nan)
            if np.isfinite(last_price_f):
                self._last_price = float(last_price_f)
            else:
                self._log_state_load_issue("last_price", ValueError("non-finite last_price"), last_price)

        self._last_effective_trend_strength = self._state_scalar(
            state,
            "last_effective_trend_strength",
            default=0.0,
            min_value=-1.0,
            max_value=1.0,
        )
        self._last_edge_score = self._state_scalar(state, "last_edge_score", default=0.0, min_value=-1.0, max_value=1.0)
        self._last_valid_vol = self._state_scalar(
            state,
            "last_valid_vol",
            default=float(self.garch.target_vol),
            min_value=1e-12,
        )
        self._switch_stability_ema = self._state_scalar(state, "switch_stability_ema", default=1.0, min_value=1e-12)

        self._last_regime_change_ts = self._normalize_timestamp(state.get("last_regime_change_ts", None))
        if state.get("last_regime_change_ts", None) is not None and self._last_regime_change_ts is None:
            self._log_state_load_issue("last_regime_change_ts", ValueError("invalid timestamp"), state.get("last_regime_change_ts"))

        self._prev_regime = state.get("prev_regime", None)
        self._prev_directional_label = state.get("prev_directional_label", None)
        self._prev_raw_regime = state.get("prev_raw_regime", None)
        self._confirmed_regime = state.get("confirmed_regime", None)

        confirmed_idx = state.get("confirmed_regime_idx", None)
        if confirmed_idx is not None:
            parsed_confirmed = _safe_int(confirmed_idx, default=-1)
            if parsed_confirmed < 0 or parsed_confirmed >= self.K:
                self._log_state_load_issue("confirmed_regime_idx", ValueError("out of bounds"), confirmed_idx)
                self._confirmed_regime_idx = None
            else:
                self._confirmed_regime_idx = int(parsed_confirmed)
        else:
            self._confirmed_regime_idx = None

        raw_regime_persistence = state.get("regime_persistence", 0)
        self._regime_persistence = _safe_int(raw_regime_persistence, default=0, min=0)
        if str(raw_regime_persistence) != str(self._regime_persistence):
            self._log_state_load_issue("regime_persistence", ValueError("invalid int"), raw_regime_persistence)

        self.nhhmm_prior = self._state_vector(
            state,
            "nhhmm_prior",
            self.K,
            fallback=np.ones(self.K, dtype=float) / self.K,
            normalize_probabilities=True,
        )
        self.garch_prob = self._state_vector(
            state,
            "garch_prob",
            2,
            fallback=np.ones(2, dtype=float) / 2.0,
            normalize_probabilities=True,
        )
        self._smoothed_garch_prob = self._state_vector(
            state,
            "smoothed_garch_prob",
            2,
            fallback=self.garch_prob.copy(),
            normalize_probabilities=True,
        )

        if "regime_state_probs" in state and state["regime_state_probs"] is not None:
            self._regime_state_probs = self._state_vector(
                state,
                "regime_state_probs",
                4,
                fallback=np.ones(4, dtype=float) / 4.0,
                normalize_probabilities=True,
            )
        else:
            self._regime_state_probs = np.ones(4, dtype=float) / 4.0
        if getattr(self, "_regime_smoother", None) is not None:
            self._regime_smoother.set_prev_probs(self._regime_state_probs)

        self.garch_var = self._state_vector(
            state,
            "garch_var",
            2,
            fallback=self._stationary_garch_var(),
            normalize_probabilities=False,
        )
        # HARD SAFETY: prevent NaN/Inf contamination from snapshots
        if not np.all(np.isfinite(self.garch_var)):
            self._log_state_load_issue("garch_var", ValueError("non-finite garch_var after coercion"), self.garch_var.tolist())
            self.garch_var = self._stationary_garch_var()

        self.range_ticks_int = _safe_int(self.range_ticks, default=0, min=0)
        self._circuit_breaker_active = bool(_safe_int(state.get("circuit_breaker_active", 0), default=0, min=0, max=1))
        breaker_reason = state.get("circuit_breaker_reason", None)
        self._circuit_breaker_reason = None if breaker_reason is None else str(breaker_reason)[:128]
        self._circuit_breaker_trigger_tick = _safe_int(state.get("circuit_breaker_trigger_tick", -1), default=-1)
        self._equity = self._state_scalar(state, "equity", default=1.0, min_value=self._MIN_EQUITY_FLOOR)
        self._equity_peak = self._state_scalar(state, "equity_peak", default=max(self._equity, 1.0), min_value=self._MIN_EQUITY_FLOOR)
        if self._equity_peak < self._equity:
            self._log_state_load_issue("equity_peak", ValueError("equity_peak<equity"), self._equity_peak)
            self._equity_peak = self._equity
        self._drawdown = float(np.clip(
            (self._equity_peak - self._equity) / max(self._equity_peak, self._MIN_EQUITY_FLOOR),
            0.0,
            1.0,
        ))
        raw_loss_streak = state.get("loss_streak", 0)
        self._loss_streak = _safe_int(raw_loss_streak, default=0, min=0)
        if str(raw_loss_streak) != str(self._loss_streak):
            self._log_state_load_issue("loss_streak", ValueError("invalid int"), raw_loss_streak)
        raw_healing_count = state.get("healing_count", 0)
        self._healing_count = _safe_int(raw_healing_count, default=0, min=0)
        if str(raw_healing_count) != str(self._healing_count):
            self._log_state_load_issue("healing_count", ValueError("invalid int"), raw_healing_count)
        self._last_healing_action = str(state.get("last_healing_action", "NONE"))[:64]
        self._last_healing_error = state.get("last_healing_error", None)
        raw_healing_context = state.get("last_healing_context", {})
        self._last_healing_context = dict(raw_healing_context) if isinstance(raw_healing_context, dict) else {}
        self._shock_memory = self._state_scalar(state, "shock_memory", default=0.0, min_value=0.0)
        self._return_ema = self._state_scalar(state, "return_ema", default=0.0)
        self._abs_return_ema = self._state_scalar(state, "abs_return_ema", default=0.0, min_value=0.0)
        self._confidence_collapse_streak = _safe_int(state.get("confidence_collapse_streak", 0), default=0, min=0)
        rng_state = state.get("engine_rng_state", None)
        if isinstance(rng_state, dict) and getattr(self, "_rng", None) is not None:
            try:
                self._rng.bit_generator.state = dict(rng_state)
            except Exception as exc:
                self._log_state_load_issue("engine_rng_state", exc, "invalid_rng_state")

    @_synchronized
    def update(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(market_data, dict):
            market_data = {}
            self._obs_observe("input_validation", "high", {"reason": "market_data_not_dict"})
            self._warn_rate_limited(
                key="market_data_not_dict",
                message="update() received non-dict market_data; degraded to fail-safe defaults.",
                cooldown_s=30.0,
            )
        # NOTE: enforce globally across codebase:
        # ALL side effects must follow:
        # if not getattr(self, "_is_replay", False): LOGGER / metrics / hooks

        start_time = time.perf_counter()
        self._tick_id = int(getattr(self, "_tick_id", 0)) + 1
        obs_sample = self._obs_should_sample()
        if obs_sample and not getattr(self, "_is_replay", False):
            self._replay_record(
                "update_start",
                {
                    "price": market_data.get("price"),
                    "has_mtf": "mtf" in market_data,
                },
            )

        def _observe_latency() -> None:
            if _PROM_AVAILABLE and obs_sample and not getattr(self, "_is_replay", False):
                elapsed = time.perf_counter() - start_time
                ENGINE_LATENCY.labels(self.engine_id).observe(elapsed)

        def _build_halted_output() -> Dict[str, Any]:
            self.last_signed_position_size = 0.0
            return _build_output(
                regime_idx=-1,
                regime_label="HALTED",
                execution_mode="circuit_breaker",
                trend_strength=0.0,
                risk_level=1.0,
                confidence=0.0,
                edge_score=0.0,
                probabilities={"bull": 0.0, "bear": 0.0, "crisis": 1.0},
                macro_probs=[1 / 3, 1 / 3, 1 / 3],
                position_size=0.0,
                signed_position_size=0.0,
                expected_vol=self._last_valid_vol,
                raw_size=0.0,
                is_toxic=True,
                garch_regime_probs=[0.5, 0.5],
                feed_status=f"CIRCUIT_BREAKER:{self._circuit_breaker_reason}",
                last_valid_vol=self._last_valid_vol,
                switch_stability_ema=self._switch_stability_ema,
                execution_side="flat",
                include_signal_valid=True,
                signal_valid=False,
            )

        # ==========================================
        # 🚨 STEP -1: PnL TRACKING (NEW)
        # ==========================================
        price = market_data.get("price", None)
        if price is not None:
            try:
                price = float(price)
                if np.isfinite(price):
                    if not self._circuit_breaker_active and self._last_price is not None:
                        prev_price = float(self._last_price)
                        if np.isfinite(prev_price) and abs(prev_price) > 1e-12:
                            frac_ret = (price - prev_price) / prev_price
                            pnl = frac_ret * self.last_signed_position_size
                            if np.isfinite(pnl):
                                self._equity += pnl
                                if self._equity < self._MIN_EQUITY_FLOOR:
                                    self._equity = self._MIN_EQUITY_FLOOR
                                    self._trigger_circuit_breaker("EQUITY_FLOOR")

                                if pnl < -1e-6:
                                    self._loss_streak += 1
                                elif pnl > 1e-6:
                                    self._loss_streak = 0

                                self._equity_peak = max(self._equity_peak, self._equity)
                                self._drawdown = float(np.clip(
                                    (self._equity_peak - self._equity) / max(self._equity_peak, 1e-8),
                                    0.0,
                                    1.0,
                                ))
                                breaker_triggered = self._circuit_breaker_active
                                if (not breaker_triggered) and self._drawdown > self._MAX_DRAWDOWN:
                                    self._trigger_circuit_breaker("MAX_DRAWDOWN")
                                    breaker_triggered = True
                                if (not breaker_triggered) and self._loss_streak >= self._MAX_CONSECUTIVE_LOSSES:
                                    self._trigger_circuit_breaker("LOSS_STREAK")
                    self._last_price = price
            except Exception as exc:
                self._warn_rate_limited(
                    key="pnl_tracking_failure",
                    message=f"PnL tracking degraded due to price parse error: {exc}",
                    cooldown_s=15.0,
                )
                self._obs_observe("pnl_tracking_failure", "medium", {"reason": "price_parse_error"})

        # ==========================================
        # 🚨 STEP 0: CIRCUIT BREAKER CHECK
        # ==========================================
        if self._circuit_breaker_active:
            self._healing_counter += 1

            if self._healing_counter > self._HEALING_COOLDOWN_TICKS:
                self._self_heal()
                output = _build_halted_output()
                _observe_latency()
                if obs_sample and not getattr(self, "_is_replay", False):
                    self._replay_record("update_end", {"regime": "HALTED_HEALING"})
                return output
            else:
                self._obs_observe("circuit_breaker", "critical", {"reason": self._circuit_breaker_reason})
                output = _build_halted_output()
                _observe_latency()
                if obs_sample and not getattr(self, "_is_replay", False):
                    self._replay_record("update_end", {"regime": "HALTED"})
                return output
        
        # ==========================================
        # FIXED: Explicit base timeframe + safe MTF fusion
        # NEW: Multi-Timeframe Input Handling
        # ==========================================
        
        nhhmm_posterior = None
        safe_nhhmm_posterior = None
        
        mtf_data = market_data.get("mtf", None)

        mtf_partial_survival = False
        mtf_degradation_reasons = Counter()

        if mtf_data is not None:
            # ==========================================
            # FIX 1: BASE TF MUST BE DEFINED FIRST
            # ==========================================
            if not isinstance(mtf_data, dict):
                raise ValueError("MTF payload must be a dict keyed by timeframe")
            base_tf = mtf_data.get("base", None)
            if base_tf is None:
                raise ValueError(
                    "MTF payload must include explicit 'base' timeframe key"
                )
            if not isinstance(base_tf, dict):
                raise ValueError("MTF payload base timeframe must be a dict")

            y_t = base_tf.get("return", 0.0)
            x_t = base_tf.get("features", np.zeros(self.n_features))

            fused_probs = np.zeros(self.K)
            total_weight = 0.0
            valid_tf_count = 0

            # Detect silent weight misconfiguration.
            # Build the candidate set once to avoid redundant passes.
            unknown_tfs = []
            candidate_tfs = []
            for tf, tf_data in mtf_data.items():
                if tf == "base":
                    continue
                weight = float(self.mtf_weights.get(tf, 0.0))
                if tf not in self.mtf_weights:
                    unknown_tfs.append(tf)
                    continue
                if weight <= 0.0:
                    continue
                candidate_tfs.append((tf, tf_data, weight))

            for tf, tf_data, weight in candidate_tfs:
                y_raw = tf_data.get("return", 0.0)
                try:
                    y_t_tf = float(y_raw) if y_raw is not None else 0.0
                except (TypeError, ValueError):
                    mtf_degradation_reasons["invalid_return"] += 1
                    continue
                if not np.isfinite(y_t_tf):
                    mtf_degradation_reasons["invalid_return"] += 1
                    continue
                x_t_tf = tf_data.get("features", np.zeros(self.n_features))

                try:
                    x_t_tf = _coerce_1d_vector(
                        x_t_tf,
                        expected_size=self.n_features,
                        name=f"mtf[{tf}] features",
                    )
                except (ValueError, TypeError):
                    continue

                # NHHMM per timeframe.
                # Expected data/math failures are isolated per timeframe.
                try:
                    nhhmm_post_tf, _ = self.nhhmm.forward_pass_step(
                        y_t_tf, x_t_tf, self.nhhmm_prior
                    )
                except Exception as e:
                    self._warn_tf_failure(tf, e)
                    mtf_degradation_reasons["forward_pass_failure"] += 1
                    continue

                fused_probs += weight * nhhmm_post_tf
                total_weight += weight
                valid_tf_count += 1

            total_candidate_tfs = len(candidate_tfs)

            if total_candidate_tfs > 0 and valid_tf_count < total_candidate_tfs:
                mtf_partial_survival = True
                mtf_degradation_reasons["partial_survival"] += 1

            if unknown_tfs:
                mtf_degradation_reasons["unknown_tf_keys"] += len(unknown_tfs)
                if self._strict_mtf_keys:
                    raise ValueError(
                        "MTF payload contains unknown timeframe keys: "
                        f"{unknown_tfs}. Allowed keys: {sorted(list(self.mtf_weights.keys())) + ['base']}"
                    )

            # OBS: record degradation reasons
            if _PROM_AVAILABLE and not getattr(self, "_is_replay", False):
                for k, v in mtf_degradation_reasons.items():
                    if v > 0:
                        MTF_DEGRADATION.labels(self.engine_id, k).inc(v)

            expected_weighted_tf_count = total_candidate_tfs
            if expected_weighted_tf_count > 1 and valid_tf_count == 1:
                mtf_partial_survival = True
                self._warn_rate_limited(
                    key="mtf_partial_survival_single_survivor",
                    message=(
                        "MTF partial survival: only one timeframe contributed to fusion "
                        "after validation/failures. Output remains valid, but higher-timeframe "
                        "resolution was degraded for this tick."
                    ),
                    cooldown_s=60.0,
                )

            # ==========================================
            # FIX 2: SAFE FALLBACK (NO UNDEFINED VARS)
            # ==========================================
            if total_weight <= 0 or valid_tf_count == 0:
                self._warn_rate_limited(
                    key="mtf_total_failure",
                    message="MTF fusion failed, falling back to SAFE base timeframe",
                    cooldown_s=30.0,
                )
                self._obs_observe("mtf_failure", "high", {"source": "mtf_total_failure"})
                self._self_heal("E130", {"source": "mtf_total_failure"})

                # 🔒 HARD VALIDATION BEFORE FALLBACK
                try:
                    y_safe = float(y_t) if y_t is not None else 0.0
                    if not np.isfinite(y_safe):
                        raise ValueError("Invalid y_t")

                    x_safe = _coerce_1d_vector(
                        x_t,
                        expected_size=self.n_features,
                        name="fallback x_t"
                    )

                    nhhmm_posterior, _ = self.nhhmm.forward_pass_step(
                        y_safe, x_safe, self.nhhmm_prior
                    )

                except Exception:
                    # 🚨 FINAL SAFE MODE (DO NOT TRUST INPUT)
                    nhhmm_posterior = np.ones(self.K) / self.K

            else:
                # ==========================================
                # FIX 3: SAFE NORMALIZATION (NO DIV BY ZERO)
                # ==========================================
                nhhmm_posterior = _normalize_prob_vector(
                    fused_probs / max(total_weight, 1e-12)
                )

        else:
            # ==========================================
            # ORIGINAL SINGLE-TF PATH (UNCHANGED)
            # ==========================================
            y_t = market_data.get('return', None)
            x_t = market_data.get('features', None)

        # ==========================================
        # FIX #2: Preserve valid MTF posterior before execution validation
        # ==========================================
        if mtf_data is not None:
            safe_nhhmm_posterior = (
                None if nhhmm_posterior is None else nhhmm_posterior.copy()
            )

        if mtf_data is not None and safe_nhhmm_posterior is None:
            raise RuntimeError("MTF fusion failed to produce valid posterior")

        y_t = self._coerce_finite_scalar(y_t, default=0.0)
        if abs(y_t) > 2.0:
            self._warn_rate_limited(
                key="return_out_of_bounds",
                message=(
                    f"Return value {y_t:.6f} exceeds plausible fractional range (|r| > 2.0); "
                    "clamping to preserve engine continuity."
                ),
                cooldown_s=30.0,
            )
            self._obs_observe("return_out_of_bounds", "medium", {"source": "update"})
            y_t = float(np.clip(y_t, -2.0, 2.0))

        if x_t is None:
            x_t = np.zeros(self.n_features)
        try:
            x_t = _coerce_1d_vector(
                x_t,
                expected_size=self.n_features,
                name="update x_t",
            )
        except (ValueError, TypeError):
            x_t = np.array([], dtype=float)

        # Repair corrupted risk state before it can contaminate the next tick.
        if not np.all(np.isfinite(self.garch_var)):
            self.garch_var = self._stationary_garch_var()

        current_ts = self._normalize_timestamp(market_data.get("timestamp", None))
        if self._last_timestamp is None or current_ts is None:
            time_delta = self._last_valid_dt
        else:
            raw_dt = max(current_ts - self._last_timestamp, 0.0)
            time_delta = min(raw_dt, self._MAX_DT)
        decay_dt = max(time_delta, 0.0)
        if time_delta > 0:
            self._last_valid_dt = time_delta
        if current_ts is not None and (self._last_timestamp is None or current_ts > self._last_timestamp):
            self._last_timestamp = current_ts

        is_dim_fail = (x_t.ndim != 1) or (x_t.shape[0] != self.n_features)
        n_corrupt = 0 if is_dim_fail else int(np.sum(~np.isfinite(x_t)))

        # Make MTF degradation explicit in telemetry even when the tick remains usable.
        # Safeguard: Added check for expected_weighted_tf_count > 1 to prevent false positives on single-TF feeds.
        if mtf_data is not None and valid_tf_count == 1 and not is_dim_fail and n_corrupt == 0:
            if expected_weighted_tf_count > 1:
                mtf_partial_survival = True

        if mtf_partial_survival and mtf_data is not None:
            mtf_degradation_reasons["telemetry_partial_survival"] += 1

        # --- FIX #2: If execution features fail, still preserve macro posterior ---
        if (is_dim_fail or n_corrupt > 0) and mtf_data is not None:
            if safe_nhhmm_posterior is not None:
                self.nhhmm_prior = _normalize_prob_vector(safe_nhhmm_posterior)

        if is_dim_fail or n_corrupt > 0:
            self._self_heal(
                "E120" if is_dim_fail else "E200",
                {
                    "source": "update",
                    "feed_status": (
                        "DIMENSION_FAILURE"
                        if is_dim_fail else f"DATA_FAILURE:{n_corrupt}_CORRUPT"
                    ),
                },
            )
            self.last_signed_position_size = 0.0
            self._range_anchor_size = 0.0
            self.range_ticks *= np.exp(-self._DECAY_LAMBDA * decay_dt)
            self.range_ticks_int = int(self.range_ticks)
            feed_status = 'DIMENSION_FAILURE' if is_dim_fail else f'DATA_FAILURE:{n_corrupt}_CORRUPT'
            
            # --- FIX: preserve last valid trend strength ---
            safe_trend_strength = float(
                getattr(self, "_last_effective_trend_strength", 0.0)
            )

            safe_prob = _normalize_prob_vector(self._smoothed_garch_prob)
            safe_var = np.asarray(self.garch_var, dtype=float)
            if not np.all(np.isfinite(safe_var)):
                safe_var = self._stationary_garch_var()
            safe_var = np.clip(safe_var, 1e-8, None)
            expected_vol_frozen = float(np.sqrt(np.dot(safe_prob, safe_var)))
            
            if _PROM_AVAILABLE:
                if obs_sample and not getattr(self, "_is_replay", False):
                    ENGINE_FEED_STATUS.labels(self.engine_id, feed_status).inc()
                    ENGINE_HEALTH.labels(self.engine_id).set(0)

            self._obs_observe("data_failure", "high", {"feed_status": feed_status})
            self._obs_counter += 1  # PRODUCTION HARDENING: prevent rate-limit bypass on outage

            output = _build_output(
                # expose MTF degradation state without changing schema shape
                # via risk_metrics.feed_status + comment trail in warnings
                regime_idx=int(self.current_regime_idx) if self.current_regime_idx is not None else -1,
                regime_label='UNKNOWN',
                execution_mode='fail_safe',
                trend_strength=safe_trend_strength,
                risk_level=1.0,
                confidence=0.0,
                edge_score=0.0,
                probabilities={'bull': 0.0, 'bear': 0.0, 'crisis': 1.0},
                macro_probs=self.nhhmm_prior.tolist(),
                position_size=0.0,
                expected_vol=expected_vol_frozen,
                raw_size=0.0,
                is_toxic=True,
                garch_regime_probs=self.garch_prob.tolist(),
                feed_status=feed_status,
                last_valid_vol=float(getattr(self, "_last_valid_vol", self.garch.target_vol)),
                switch_stability_ema=float(getattr(self, "_switch_stability_ema", 1.0)),
                execution_side='flat',
                extended_schema=self._emit_extended_schema,
                range_ticks=self.range_ticks_int,
                include_signal_valid=True,
                signal_valid=False,
            )
            if obs_sample and not getattr(self, "_is_replay", False):
                self._replay_record("update_end", {"regime": "UNKNOWN"})
            _observe_latency()
            return output

        feed_status = 'OK'
        if mtf_partial_survival:
            feed_status = 'MTF_PARTIAL_SURVIVAL'
            if mtf_degradation_reasons:
                self._warn_rate_limited(
                    key="mtf_degradation_summary",
                    message=(
                        "MTF degradation summary: " + ", ".join(
                            f"{k}={v}" for k, v in sorted(mtf_degradation_reasons.items())
                        )
                    ),
                    cooldown_s=60.0,
                )

        # OBS: feed tracking
        if _PROM_AVAILABLE and obs_sample and not getattr(self, "_is_replay", False):
            ENGINE_FEED_STATUS.labels(self.engine_id, feed_status).inc()

        # Only compute if not already from MTF
        if mtf_data is None:
            nhhmm_posterior, _ = self.nhhmm.forward_pass_step(
                y_t, x_t, self.nhhmm_prior
            )
        else:
            if safe_nhhmm_posterior is None:
                raise RuntimeError("MTF posterior missing after validation")
            nhhmm_posterior = safe_nhhmm_posterior

        if not isinstance(nhhmm_posterior, np.ndarray):
            nhhmm_posterior = np.asarray(nhhmm_posterior, dtype=float)
        if nhhmm_posterior.shape != (self.K,) or not np.all(np.isfinite(nhhmm_posterior)):
            self._warn_rate_limited(
                key="nhhmm_non_finite",
                message="NHHMM posterior invalid; falling back to prior",
                cooldown_s=10.0,
            )
            fallback_prior = np.asarray(self.nhhmm_prior, dtype=float)
            if fallback_prior.shape == (self.K,) and np.all(np.isfinite(fallback_prior)):
                nhhmm_posterior = fallback_prior
            else:
                nhhmm_posterior = np.ones(self.K, dtype=float) / self.K

        self.nhhmm_prior = _normalize_prob_vector(nhhmm_posterior)

        nhhmm_confidence = float(np.max(nhhmm_posterior))
        effective_bias_weight = float(np.clip(nhhmm_confidence, 0.0, 1.0))
        sjm_x_t = np.asarray(x_t, dtype=float).copy()
        if sjm_x_t.size > self._SJM_RESERVED_ABS_RETURN_IDX and np.isfinite(y_t):
            sjm_x_t[self._SJM_RESERVED_RETURN_IDX] = float(y_t)
            sjm_x_t[self._SJM_RESERVED_ABS_RETURN_IDX] = abs(float(y_t))
        sjm_state, sjm_probs = self.sjm.online_predict(
            x_t=sjm_x_t,
            expected_n_features=self.n_features,
            prev_state=self.current_regime_idx,
            nhhmm_probs=nhhmm_posterior,
            bias_weight=effective_bias_weight,
        )
        try:
            sjm_probs = self._coerce_vector("sjm_probs", sjm_probs, self.K)
        except Exception:
            sjm_probs = np.full(self.K, np.nan, dtype=float)
        
        # ==========================================
        # FIX: STICKY SJM FALLBACK (NO REGIME COLLAPSE)
        # ==========================================
        if not np.all(np.isfinite(sjm_probs)):
            self._warn_rate_limited(
                key="sjm_non_finite",
                message=f"SJM produced non-finite probs, using last valid state",
                cooldown_s=10.0,
            )
            self._obs_observe("sjm_non_finite", "high", {"source": "update"})
            self._self_heal("E200", {"source": "sjm_non_finite"})

            if self._last_valid_sjm_probs is not None:
                sjm_probs = self._last_valid_sjm_probs.copy()
                sjm_state = int(np.argmax(sjm_probs))
            else:
                # --- OPTIONAL IMPROVEMENT: align fallback with macro model ---
                if nhhmm_posterior is not None and np.all(np.isfinite(nhhmm_posterior)):
                    sjm_probs = nhhmm_posterior.copy()
                    sjm_state = int(np.argmax(sjm_probs))
                else:
                    sjm_probs = np.ones(self.K) / self.K
                    sjm_state = int(np.argmax(sjm_probs))

        else:
            sjm_probs = _normalize_prob_vector(sjm_probs)
            self._last_valid_sjm_probs = sjm_probs.copy()

        if np.isfinite(y_t):
            self._shock_memory = max(abs(float(y_t)), 0.90 * float(getattr(self, "_shock_memory", 0.0)))
            shock_intensity = float(np.clip(self._shock_memory / 0.02, 0.0, 1.0))
            if self.K >= 3:
                sjm_probs = np.asarray(sjm_probs, dtype=float).copy()
                non_crisis_scale = max(1.0 - 0.8 * shock_intensity, 0.1)
                sjm_probs[0] *= non_crisis_scale
                sjm_probs[1] *= non_crisis_scale
                sjm_probs[2] *= (0.2 + 1.8 * shock_intensity)
                sjm_probs = _normalize_prob_vector(sjm_probs)
                sjm_state = int(np.argmax(sjm_probs))
            
        self.current_regime_idx = sjm_state
        regime_scores = compute_hmm_regime(
            sjm_probs,
            prev_directional_label=getattr(self, "_prev_directional_label", None),
            direction_switch_gap=self._DIRECTION_SWITCH_GAP,
        )
        self._prev_directional_label = regime_scores.get("directional_label")
        regime = regime_scores["regime"]
        if getattr(self, "_regime_smoother", None) is not None:
            regime, self._regime_state_probs = self._regime_smoother.update(
                regime_scores,
                self._confirmed_regime,
            )
        directional_recovery_label = None
        if regime == "RANGE":
            directional_recovery = (
                regime_scores["trend_score"] > (regime_scores["range_score"] + 0.15)
                and float(regime_scores.get("directional_margin", 0.0)) >= (2.0 * self._DIRECTION_SWITCH_GAP)
                and float(regime_scores.get("risk_level", 1.0)) < 0.35
            )
            if directional_recovery:
                candidate_label = str(regime_scores.get("directional_label", "RANGE"))
                if candidate_label in ("TREND", "BEAR"):
                    directional_recovery_label = candidate_label

        # Capture base trend strength before any execution-level overrides (fixes Issue 1)
        base_trend_strength = float(regime_scores["trend_strength"])
        alpha_conf = float(regime_scores["conviction"])
        regime_edge_raw = float(regime_scores.get("edge_score", alpha_conf))
        
        # ==========================================
        # EDGE MOMENTUM (stability over time)
        # ==========================================
        regime_edge = (
            # ==========================================
            # FIX: STRONGER HYSTERESIS (ANTI-COLLAPSE)
            # ==========================================
            0.8 * self._last_edge_score +
            0.2 * regime_edge_raw
        )
        alpha_bias = float(nhhmm_posterior[0] - nhhmm_posterior[1]) # bull - bear

        # Snapshot persistence BEFORE update (intentional, avoids off-by-one)
        prev_persistence = max(int(getattr(self, "_regime_persistence", 0)), 0)

        if self._prev_raw_regime is None:
            self._regime_persistence = 1
        elif regime == self._prev_raw_regime:
            self._regime_persistence += 1
        else:
            self._regime_persistence = 1

        if self._confirmed_regime is None:
            confirmed_regime = regime
            confirmed_regime_idx = self.current_regime_idx
        elif self._regime_persistence >= self._REGIME_CONFIRMATION_TICKS:
            confirmed_regime = regime
            confirmed_regime_idx = self.current_regime_idx
        else:
            confirmed_regime = self._confirmed_regime
            confirmed_regime_idx = self._confirmed_regime_idx

        if regime == "TOXIC":
            confirmed_regime = "TOXIC"
            confirmed_regime_idx = self.current_regime_idx
        elif self._confirmed_regime == "TOXIC":
            # Exit TOXIC only on strong recovery evidence.
            # RANGE may recover directly, but only after stable confirmation.
            # If persistence state is missing/corrupted, a very strong clean signal
            # is still allowed to escape TOXIC instead of getting stuck.
            persistence_ok = (
                prev_persistence >= max(2, self._REGIME_CONFIRMATION_TICKS)
                or (
                    prev_persistence <= 0
                    and regime_scores["conviction"] > 0.72
                    and regime_scores["risk_level"] < 0.25
                )
            )
            stable_exit = (
                regime == "RANGE"
                and regime_scores["conviction"] > 0.60
                and regime_scores["risk_level"] < 0.30
                and persistence_ok
            )
            directional_exit = (
                regime in ("TREND", "BEAR")
                and regime_scores["conviction"] > 0.68
                and regime_scores["risk_level"] < 0.45
            )
            if stable_exit or directional_exit:
                confirmed_regime = regime
                confirmed_regime_idx = self.current_regime_idx
            else:
                confirmed_regime = "TOXIC"
                confirmed_regime_idx = self._confirmed_regime_idx

        # Recovery is applied at confirmed-regime stage only, so hysteresis/switch
        # controls remain authoritative and raw regime stays deterministic.
        if directional_recovery_label is not None and confirmed_regime == "RANGE":
            recovery_persistence_ok = self._regime_persistence >= self._REGIME_CONFIRMATION_TICKS
            recovery_cooldown_ok = True
            if self._last_regime_change_ts is not None and current_ts is not None:
                elapsed_since_change = max(current_ts - float(self._last_regime_change_ts), 0.0)
                recovery_cooldown_ok = elapsed_since_change >= self._SWITCH_COOLDOWN_SEC
            if recovery_persistence_ok and recovery_cooldown_ok:
                confirmed_regime = directional_recovery_label
                confirmed_regime_idx = self.current_regime_idx

        # ==========================================
        # EARLY EDGE-BASED REGIME OVERRIDE
        # Prevent weak trend signals from activating directional modes
        # ==========================================
        if confirmed_regime in ("TREND", "BEAR"):
            directional_margin = float(regime_scores.get("directional_margin", 0.0))
            weak_directional_evidence = (
                regime_edge < self._EDGE_MIN_DIRECTIONAL_CONFIDENCE
                and regime_scores["conviction"] < 0.55
                and directional_margin < max(self._DIRECTION_SWITCH_GAP, 0.04)
            )
            if weak_directional_evidence:
                confirmed_regime = "RANGE"

        # ==========================================
        # EDGE + VOL + CONFIDENCE COMPOSITE SWITCH FILTER
        # ==========================================
        prev_regime_snapshot = self._prev_regime
        prev_vol = getattr(self, "_last_valid_vol", None)
        if prev_vol is None or not np.isfinite(prev_vol) or prev_vol <= 0.0:
            prev_vol = float(np.sqrt(np.dot(self._smoothed_garch_prob, np.clip(self.garch_var, 1e-8, None))))
        prev_vol = max(float(prev_vol), self._LAST_VALID_VOL_FLOOR)

        # ==========================================
        # FIX: PRE-COMPUTE SHOCK (used in switch filter)
        # Must be defined BEFORE regime_changed block
        # ==========================================
        baseline_vol_pre = float(
            np.sqrt(np.dot(self._smoothed_garch_prob, np.clip(self.garch_var, 1e-8, None)))
        )
        shock_threshold_pre = max(2.2 * baseline_vol_pre, 0.008)
        shock_pre = abs(y_t) > shock_threshold_pre

        regime_changed = prev_regime_snapshot is not None and confirmed_regime != prev_regime_snapshot
        if regime_changed:
            switch_gate = self._EDGE_MIN_SWITCH_CONFIDENCE
            if confirmed_regime in ("TREND", "BEAR"):
                switch_gate = min(self._EDGE_MIN_DIRECTIONAL_CONFIDENCE, 0.50)

            if mtf_partial_survival:
                switch_gate += self._SWITCH_EDGE_BUFFER

            switch_strength = (
                self._SWITCH_EDGE_WEIGHT * regime_edge
                + self._SWITCH_CONF_WEIGHT * regime_scores["conviction"]
                + self._SWITCH_VOL_WEIGHT * max(
                    0.0,
                    1.0 - min(prev_vol / max(self.garch.target_vol, 1e-8), 1.0)
                )
            )

            if shock_pre:
                switch_strength += 0.03

            self._switch_stability_ema = 0.25 * switch_strength + 0.75 * getattr(self, "_switch_stability_ema", 1.0)

            cooldown_ok = True
            if self._last_regime_change_ts is not None and current_ts is not None:
                elapsed_since_change = max(current_ts - float(self._last_regime_change_ts), 0.0)
                cooldown_ok = elapsed_since_change >= self._SWITCH_COOLDOWN_SEC

            persistence_ok = self._regime_persistence >= self._SWITCH_MIN_PERSISTENCE
            conviction_ok = regime_scores["conviction"] >= 0.65
            toxic_override = confirmed_regime == "TOXIC"

            if not toxic_override:
                if (not cooldown_ok or switch_strength < switch_gate) and not (persistence_ok and conviction_ok):
                    confirmed_regime = prev_regime_snapshot
                    confirmed_regime_idx = self._confirmed_regime_idx
                    regime_changed = False

        self._prev_raw_regime = regime
        self._confirmed_regime = confirmed_regime
        self._confirmed_regime_idx = confirmed_regime_idx

        if regime_changed:
            self._last_regime_change_ts = current_ts

        self._prev_regime = confirmed_regime

        if confirmed_regime != prev_regime_snapshot:
            if confirmed_regime == "RANGE":
                self._range_anchor_size = abs(self.last_signed_position_size)
                self._in_range = True
            elif confirmed_regime == "TREND":
                self.range_ticks = 0.0
                self._in_range = False
            elif confirmed_regime in ("BEAR", "TOXIC"):
                decay_factor = np.exp(-self._DECAY_LAMBDA * decay_dt)
                self.range_ticks *= decay_factor
                self._in_range = False

        if confirmed_regime == "RANGE":
            self.range_ticks += time_delta
            self.range_ticks = min(self.range_ticks, 1000.0)

        self.range_ticks_int = int(self.range_ticks)

        execution_mode = _map_execution_mode(confirmed_regime)
        if confirmed_regime == "TREND":
            execution_side = "long"
        elif confirmed_regime == "BEAR":
            execution_side = "short"
        elif confirmed_regime == "RANGE":
            execution_side = "range_mean_revert"
        else:
            execution_side = "flat"

        # Apply predictive alpha execution overrides if confidence overrides baseline regime (fixes Issue 2)
        if (
            alpha_conf > 0.65
            and abs(alpha_bias) > 0.2
            and abs(base_trend_strength) > 0.15
        ):
            if alpha_bias > 0.2 and confirmed_regime in ("TREND", "RANGE"):
                execution_side = "long"
            elif alpha_bias < -0.2 and confirmed_regime in ("BEAR", "RANGE"):
                execution_side = "short"

        predicted_var = self.garch_var.copy()
        self.garch_var = self.garch._garch_update(self.garch_var, y_t)
        if self.garch_var.shape != (2,) or not np.all(np.isfinite(self.garch_var)):
            self._warn_rate_limited(
                key="garch_var_invalid",
                message="GARCH variance invalid; resetting to stationary variance",
                cooldown_s=10.0,
            )
            self.garch_var = self._stationary_garch_var()
        self.garch_prob = self.garch._update_regime_probs(self.garch_prob, predicted_var, y_t)
        if self.garch_prob.shape != (2,) or not np.all(np.isfinite(self.garch_prob)):
            self.garch_prob = np.ones(2, dtype=float) / 2.0
        self.garch_prob = _normalize_prob_vector(self.garch_prob)

        self._smoothed_garch_prob = (
            self._EWM_ALPHA * self.garch_prob
            + (1.0 - self._EWM_ALPHA) * self._smoothed_garch_prob
        )
        self._smoothed_garch_prob = _normalize_prob_vector(self._smoothed_garch_prob)

        # FIX #2: HYBRID VOL ESTIMATION with adaptive spike response
        baseline_vol = float(np.sqrt(np.dot(self._smoothed_garch_prob, np.clip(predicted_var, 1e-8, None))))
        vol_spike = abs(y_t) > (2.0 * max(baseline_vol, 1e-8))
        if vol_spike:
            effective_prob = self.garch_prob
        else:
            effective_prob = 0.6 * self.garch_prob + 0.4 * self._smoothed_garch_prob
        effective_prob = _normalize_prob_vector(effective_prob)

        expected_var = float(np.dot(effective_prob, self.garch_var))
        expected_var = max(expected_var, 1e-8)
        expected_vol = np.sqrt(expected_var)
        expected_vol = min(expected_vol, 0.20)
        
        # ==========================================
        # 🚨 STEP 1: VOLATILITY SHOCK DETECTOR
        # ==========================================
        if abs(y_t) > self._VOL_SHOCK_MULTIPLIER * max(expected_vol, 1e-8):
            self._trigger_circuit_breaker("VOL_SHOCK")
            self._obs_observe("circuit_breaker", "critical", {"reason": self._circuit_breaker_reason})
            output = _build_halted_output()
            _observe_latency()
            if obs_sample and not getattr(self, "_is_replay", False):
                self._replay_record("update_end", {"regime": "HALTED"})
            return output

        # ==========================================
        # 🚨 STEP 2: CONFIDENCE COLLAPSE
        # ==========================================
        collapse_signal = (
            regime_scores["conviction"] < 0.05
            and regime_scores["confidence"] < self._CONFIDENCE_COLLAPSE_THRESHOLD
        )
        if collapse_signal:
            self._confidence_collapse_streak = int(getattr(self, "_confidence_collapse_streak", 0)) + 1
        else:
            self._confidence_collapse_streak = 0
        collapse_signal = (
            collapse_signal
            and self._confidence_collapse_streak >= self._CONFIDENCE_COLLAPSE_MIN_STREAK
        )
        if collapse_signal:
            self._trigger_circuit_breaker("CONFIDENCE_COLLAPSE")
            self._obs_observe("circuit_breaker", "critical", {"reason": self._circuit_breaker_reason})
            output = _build_halted_output()
            _observe_latency()
            if obs_sample and not getattr(self, "_is_replay", False):
                self._replay_record("update_end", {"regime": "HALTED"})
            return output
            
        if np.isfinite(expected_vol) and expected_vol > 0.0:
            self._last_valid_vol = float(expected_vol)
        else:
            expected_vol = float(max(self._last_valid_vol, self._LAST_VALID_VOL_FLOOR))
            self._last_valid_vol = float(expected_vol)

        self._return_ema = 0.92 * float(getattr(self, "_return_ema", 0.0)) + 0.08 * float(y_t)
        self._abs_return_ema = 0.92 * float(getattr(self, "_abs_return_ema", 0.0)) + 0.08 * abs(float(y_t))

        low_vol_range_gate = (
            expected_vol < (0.75 * self.garch.target_vol)
            and abs(self._return_ema) < 4.5e-4
            and self._abs_return_ema < 2.5e-3
            and regime_scores["risk_level"] < 0.35
        )
        low_vol_regime_soft_penalty = 0.0
        if low_vol_range_gate and confirmed_regime in ("TREND", "BEAR"):
            # Soft discourage directional conviction in low-vol/drift environments
            # without force-switching the regime label.
            low_vol_regime_soft_penalty = 0.18

        # ==========================================
        # EDGE-ADJUSTED VOL TARGETING
        # ==========================================
        edge_vol_boost = 0.5 + 0.5 * regime_edge
        effective_target_vol = self.garch.target_vol * edge_vol_boost

        # Edge score adjustment after volatility is known.
        vol_ratio = expected_vol / max(self.garch.target_vol, 1e-8)
        edge_score = float(np.clip(
            regime_edge
            - self._EDGE_VOL_PENALTY * max(vol_ratio - 1.0, 0.0)
            - low_vol_regime_soft_penalty,
            0.0,
            1.0,
        ))
        self._last_edge_score = edge_score
        
        if _PROM_AVAILABLE and obs_sample and not getattr(self, "_is_replay", False):
            ENGINE_VOL.labels(self.engine_id).set(expected_vol)
            ENGINE_CONFIDENCE.labels(self.engine_id).set(regime_scores["conviction"])
            ENGINE_RISK.labels(self.engine_id).set(regime_scores["risk_level"])
            
        if not np.isfinite(expected_vol):
            expected_vol = self.garch.target_vol

        target_leverage = effective_target_vol / expected_vol
        if not np.isfinite(target_leverage):
            target_leverage = 0.0

        raw_size = float(np.clip(target_leverage, 0.0, 10.0))
        position_size = float(np.clip(raw_size, 0.0, self._MAX_POSITION_SIZE))
        
        # ==========================================
        # 🚨 STEP 3: SOFT RISK BRAKE
        # ==========================================
        if regime_scores["conviction"] < 0.5:
            position_size *= 0.5
        if regime_scores["conviction"] < 0.4:
            position_size *= 0.25

        if _PROM_AVAILABLE and obs_sample and not getattr(self, "_is_replay", False):
            ENGINE_POSITION.labels(self.engine_id).set(position_size)

        # FIX #4: MORE RESPONSIVE SHOCK DETECTION
        shock_threshold = max(2.2 * baseline_vol, 0.008)
        shock_post = abs(y_t) > shock_threshold

        is_toxic = (
            confirmed_regime == "TOXIC"
            or (regime_scores["risk_level"] > 0.6 and confirmed_regime != "TREND")
            or expected_vol > 0.06
            or shock_post
        )

        # ==========================================
        # HEALTH SIGNAL
        # ==========================================
        if is_dim_fail or n_corrupt > 0:
            health = "FAIL"
        elif mtf_partial_survival:
            health = "DEGRADED"
        elif is_toxic:
            health = "RISK"
        else:
            health = "OK"

        self._last_health = health

        if _PROM_AVAILABLE and obs_sample and not getattr(self, "_is_replay", False):
            ENGINE_HEALTH.labels(self.engine_id).set(1 if health == "OK" else 0)

        if is_toxic:
            position_size = float(np.clip(position_size * 0.1, 0.0, 0.1 * self._MAX_POSITION_SIZE))
        elif confirmed_regime == "BEAR":
            position_size = float(np.clip(position_size * 0.5, 0.0, 0.5 * self._MAX_POSITION_SIZE))
        else:
            position_size = float(np.clip(position_size, 0.0, self._MAX_POSITION_SIZE))

        # ==========================================
        # CONVEX EDGE-BASED POSITION SIZING (CORE UPGRADE)
        # ==========================================
        if edge_score < self._EDGE_MIN_ACTIVE:
            edge_scaled = 0.0
        else:
            edge_scaled = (edge_score ** self._EDGE_POWER) / (
                edge_score ** self._EDGE_POWER +
                (1 - edge_score) ** self._EDGE_POWER +
                1e-8
            )

        position_size = float(np.clip(position_size * edge_scaled, 0.0, self._MAX_POSITION_SIZE))

        signed_position_size = self.last_signed_position_size
        if confirmed_regime == "TREND":
            signed_position_size = position_size
        elif confirmed_regime == "BEAR":
            signed_position_size = -position_size
        elif confirmed_regime == "RANGE":
            prior_sign = np.sign(self.last_signed_position_size)

            if self._range_anchor_size < self._MIN_SIGNED_TRADE_SIZE:
                rebuilt_anchor = position_size * max(abs(base_trend_strength), 0.3)
                self._range_anchor_size = float(min(rebuilt_anchor, 0.5 * position_size))

            if prior_sign == 0.0:
                if abs(base_trend_strength) > 0.1:
                    prior_sign = np.sign(base_trend_strength)
                else:
                    prior_sign = 1.0

            anchor_size = max(self._range_anchor_size, 1e-8)
            if anchor_size < self._MIN_SIGNED_TRADE_SIZE:
                anchor_size = position_size

            vol_ratio = expected_vol / max(self.garch.target_vol, 1e-8)
            if not np.isfinite(vol_ratio):
                vol_ratio = 1.0

            decay = self._RANGE_SIGNED_DECAY / (1.0 + vol_ratio)
            decay *= float(max(np.exp(-0.15 * self.range_ticks), 1e-3))
            decay = max(decay, self._RANGE_DECAY_FLOOR_K)
            if not np.isfinite(decay):
                decay = 0.1

            signed_position_size = float(
                prior_sign * min(anchor_size, position_size) * decay
            )
            if not np.isfinite(signed_position_size):
                signed_position_size = 0.0

            dynamic_min = max(
                self._MIN_SIGNED_TRADE_SIZE,
                0.1 * position_size
            )
            if abs(signed_position_size) < dynamic_min:
                signed_position_size = 0.0
        elif confirmed_regime == "TOXIC":
            signed_position_size = 0.0

        # Final telemetry hygiene
        if not np.isfinite(position_size):
            position_size = 0.0

        # HARD SAFETY: prevent alpha leakage into sizing logic
        effective_trend_strength = base_trend_strength

        # --- FIX: persist last valid trend strength ---
        if np.isfinite(effective_trend_strength):
            self._last_effective_trend_strength = float(effective_trend_strength)

        # --- FIX: persist edge state for next-tick hysteresis and state restore ---
        self._last_regime_change_ts = current_ts if regime_changed else self._last_regime_change_ts

        position_size = float(np.clip(position_size, 0.0, self._MAX_POSITION_SIZE))
        if not np.isfinite(signed_position_size):
            signed_position_size = 0.0
        signed_position_size = float(np.clip(signed_position_size, -position_size, position_size))
        if not np.isfinite(expected_vol):
            expected_vol = self.garch.target_vol
        if not np.isfinite(raw_size):
            raw_size = 0.0

        self.last_signed_position_size = signed_position_size
        rticks = self.range_ticks_int

        # OBS: latency tracking
        _observe_latency()

        self._obs_observe(
            "update",
            {"OK": "low", "DEGRADED": "medium", "RISK": "high", "FAIL": "critical"}.get(self._last_health, "low"),
            {"feed_status": feed_status, "regime": confirmed_regime},
        )
        self._obs_counter += 1

        # Final execution guard (redundant safety layer)
        if confirmed_regime in ("TREND", "BEAR") and edge_score < self._EDGE_MIN_DIRECTIONAL_CONFIDENCE:
            execution_side = "flat"

        # Keep regime label and returned index semantically aligned.
        # RANGE and TOXIC do not map cleanly to the 3-state SJM index space.
        final_regime_idx = -1
        if confirmed_regime in ("TREND", "BEAR"):
            final_regime_idx = int(self.current_regime_idx) if self.current_regime_idx is not None else -1

        output = _build_output(
            regime_idx=final_regime_idx,
            regime_label=confirmed_regime,
            execution_mode=execution_mode,
            trend_strength=float(effective_trend_strength),
            risk_level=float(regime_scores["risk_level"]),
            confidence=float(regime_scores["confidence"]),
            edge_score=float(edge_score),
            probabilities={
                'bull': float(regime_scores["bull"]),
                'bear': float(regime_scores["bear"]),
                'crisis': float(regime_scores["crisis"]),
            },
            macro_probs=self.nhhmm_prior.tolist(),
            position_size=position_size,
            signed_position_size=signed_position_size,
            expected_vol=expected_vol,
            raw_size=raw_size,
            is_toxic=is_toxic,
            garch_regime_probs=self.garch_prob.tolist(),
            feed_status=feed_status,
            last_valid_vol=float(self._last_valid_vol),
            switch_stability_ema=float(self._switch_stability_ema),
            execution_side=execution_side,
            extended_schema=self._emit_extended_schema,
            range_ticks=rticks,
            include_signal_valid=True,
            signal_valid=True,
        )
        if obs_sample and not getattr(self, "_is_replay", False):
            self._replay_record("update_end", {"regime": confirmed_regime})
        if _PROM_AVAILABLE and not getattr(self, "_is_replay", False):
            REGIME_COUNTER.labels(self.engine_id, confirmed_regime).inc()
        if self._replay_engine is not None and (self._tick_id % 100 == 0) and not getattr(self, "_is_replay", False):
            try:
                state_blob = self.serialize_state()
                normalized_rng = self._normalize_rng_state(self._rng)

                # HARD ASSERT: dual-state consistency
                runtime_map = {
                    "garch_prob": "garch_prob",
                    "nhhmm_prior": "nhhmm_prior",
                    "garch_var": "garch_var",
                    "smoothed_garch_prob": "_smoothed_garch_prob",
                }
                for k, runtime_attr in runtime_map.items():
                    if k in state_blob:
                        try:
                            if not np.allclose(
                                np.asarray(state_blob[k], dtype=float),
                                np.asarray(getattr(self, runtime_attr), dtype=float),
                                atol=1e-12,
                            ):
                                LOGGER.critical("CRITICAL SNAPSHOT MISMATCH: %s", k)
                        except Exception as exc:
                            self._warn_rate_limited("snapshot_consistency_check_failure", f"Snapshot consistency check failed for {k}: {exc}", cooldown_s=30.0)

                snapshot_payload = {
                    "engine_state": state_blob,
                    "regime": confirmed_regime,
                    "equity": self._equity,
                    "drawdown": self._drawdown,
                    "loss_streak": self._loss_streak,
                    "garch_prob": self.garch_prob.tolist(),
                    "nhhmm_prior": self.nhhmm_prior.tolist(),
                    "smoothed_garch_prob": self._smoothed_garch_prob.tolist(),
                    "regime_state_probs": self._regime_state_probs.tolist(),
                    "last_valid_sjm_probs": (
                        self._last_valid_sjm_probs.tolist()
                        if isinstance(self._last_valid_sjm_probs, np.ndarray)
                        else None
                    ),
                    "last_effective_trend_strength": float(self._last_effective_trend_strength),
                    "last_edge_score": float(self._last_edge_score),
                    "garch_var": self.garch_var.tolist(),
                    "last_valid_vol": float(self._last_valid_vol),
                    "switch_stability_ema": float(self._switch_stability_ema),
                    "last_timestamp": self._last_timestamp,
                    "last_valid_dt": float(self._last_valid_dt),
                    "range_ticks": float(self.range_ticks),
                    "range_ticks_int": int(self.range_ticks_int),
                    "in_range": bool(self._in_range),
                    "range_anchor_size": float(self._range_anchor_size),
                    "prev_raw_regime": self._prev_raw_regime,
                    "last_regime_change_ts": self._last_regime_change_ts,
                    "shock_memory": self._shock_memory,
                    "return_ema": self._return_ema,
                    "abs_return_ema": self._abs_return_ema,
                    "last_price": self._last_price,
                    "_rng_state": None,
                    "_engine_rng_state": getattr(self._rng, "bit_generator", None).state if getattr(self, "_rng", None) is not None else None,
                    "_engine_rng_type": type(self._rng.bit_generator).__name__ if getattr(self, "_rng", None) is not None else None,
                    "schema_version": "2.3",
                    "rng": normalized_rng,
                }
                hash_payload = dict(snapshot_payload)
                hash_payload.pop("state_hash", None)
                hash_payload.pop("_checksum", None)
                snapshot_payload["state_hash"] = self._state_hash(hash_payload)
                self._replay_engine.snapshot(snapshot_payload)
            except Exception as exc:
                self._warn_rate_limited("snapshot_emit_failure", f"Snapshot emission failed: {exc}", cooldown_s=30.0)
        return output

    # ==========================================
    # SNAPSHOT RESTORE (DETERMINISTIC REPLAY)
    # ==========================================
    @_synchronized
    def load_snapshot(self, snapshot: Dict[str, Any]) -> None:
        prior_state = self.serialize_state()
        prior_rng_state = (
            dict(self._rng.bit_generator.state)
            if getattr(self, "_rng", None) is not None else None
        )
        try:
            incoming = snapshot if isinstance(snapshot, dict) else {}
            state = incoming.get("state", incoming)
            if not isinstance(state, dict):
                raise ValueError("snapshot state must be a dict")

            # ==========================================
            # SNAPSHOT INTEGRITY CHECK
            # ==========================================
            expected_hash = state.get("state_hash")
            if expected_hash:
                hash_payload = dict(state)
                hash_payload.pop("_checksum", None)
                hash_payload.pop("state_hash", None)
                actual_hash = self._state_hash(hash_payload)
                if expected_hash != actual_hash:
                    raise ValueError("SNAPSHOT_CORRUPTION_HASH_MISMATCH")
            expected_checksum = state.get("_checksum")
            if expected_checksum:
                check_blob = dict(state)
                check_blob.pop("_checksum", None)
                actual_checksum = self._state_hash(check_blob)
                if expected_checksum != actual_checksum:
                    raise ValueError("SNAPSHOT_CORRUPTION_CHECKSUM_MISMATCH")

            engine_state = state.get("engine_state", state)
            if not isinstance(engine_state, dict):
                raise ValueError("snapshot engine_state must be a dict")
            expected_signature = f"AdvancedRegimeEngine|v={self._STATE_VERSION}|schema={_OUTPUT_SCHEMA_VERSION}|n_states={self.K}|n_features={self.n_features}"
            incoming_signature = engine_state.get("model_signature")
            incoming_version = engine_state.get("state_version")
            if incoming_signature is not None and incoming_signature != expected_signature:
                raise ValueError("snapshot model signature mismatch")
            if incoming_version is not None and incoming_version != self._STATE_VERSION:
                raise ValueError("snapshot state version mismatch")
            self.load_state(engine_state)

            if "_engine_rng_state" in state and getattr(self, "_rng", None) is not None:
                self._rng.bit_generator.state = dict(state["_engine_rng_state"])
            if "engine_rng_state" in engine_state and getattr(self, "_rng", None) is not None:
                self._rng.bit_generator.state = dict(engine_state["engine_rng_state"])
            if "_engine_rng_type" in state and getattr(self, "_rng", None) is not None:
                if type(self._rng.bit_generator).__name__ != state["_engine_rng_type"]:
                    raise ValueError("snapshot RNG type mismatch")

            # Snapshot-only state carried by replay checkpoints.
            self.garch_var = self._state_vector(
                state,
                "garch_var",
                2,
                fallback=self._stationary_garch_var(),
                normalize_probabilities=False,
            )
            if not np.all(np.isfinite(self.garch_var)):
                self.garch_var = self._stationary_garch_var()
            self._last_valid_vol = self._state_scalar(
                state, "last_valid_vol", default=float(self.garch.target_vol), min_value=1e-12
            )
            self._switch_stability_ema = self._state_scalar(
                state, "switch_stability_ema", default=1.0, min_value=1e-12
            )
            snapshot_ts = self._normalize_timestamp(state.get("last_timestamp", self._last_timestamp))
            if state.get("last_timestamp", self._last_timestamp) is not None and snapshot_ts is None:
                self._log_state_load_issue("snapshot.last_timestamp", ValueError("invalid timestamp"), state.get("last_timestamp"))
            self._last_timestamp = snapshot_ts
            self._last_valid_dt = self._state_scalar(state, "last_valid_dt", default=1.0, min_value=1e-9)
            self.range_ticks = self._state_scalar(state, "range_ticks", default=0.0, min_value=0.0)
            self.range_ticks_int = _safe_int(state.get("range_ticks_int", self.range_ticks_int), default=0, min=0)
            self._in_range = bool(state.get("in_range", self._in_range))
            self._range_anchor_size = self._state_scalar(state, "range_anchor_size", default=0.0, min_value=0.0)
            self._prev_raw_regime = state.get("prev_raw_regime", self._prev_raw_regime)
            self._last_regime_change_ts = self._normalize_timestamp(
                state.get("last_regime_change_ts", self._last_regime_change_ts)
            )
            if state.get("last_regime_change_ts", self._last_regime_change_ts) is not None and self._last_regime_change_ts is None:
                self._log_state_load_issue(
                    "snapshot.last_regime_change_ts",
                    ValueError("invalid timestamp"),
                    state.get("last_regime_change_ts"),
                )
            raw_last_price = state.get("last_price", self._last_price)
            self._last_price = None if raw_last_price is None else _safe_float(raw_last_price, default=np.nan)
            if self._last_price is not None and not np.isfinite(self._last_price):
                raise ValueError("snapshot last_price must be finite")
            if state.get("last_valid_sjm_probs") is not None:
                self._last_valid_sjm_probs = _safe_prob_vector(state.get("last_valid_sjm_probs"), self.K)
            self._last_effective_trend_strength = self._state_scalar(
                state,
                "last_effective_trend_strength",
                default=self._last_effective_trend_strength,
                min_value=-1.0,
                max_value=1.0,
            )
            self._last_edge_score = self._state_scalar(
                state,
                "last_edge_score",
                default=self._last_edge_score,
                min_value=-1.0,
                max_value=1.0,
            )
            self._confirmed_regime = state.get("regime", self._confirmed_regime)
        except Exception as e:
            try:
                self.load_state(prior_state)
            except Exception:
                pass
            if prior_rng_state is not None and getattr(self, "_rng", None) is not None:
                try:
                    self._rng.bit_generator.state = dict(prior_rng_state)
                except Exception:
                    pass
            if not getattr(self, "_is_replay", False):
                try:
                    LOGGER.error(
                        "Snapshot load failed context_keys=%s error=%s",
                        sorted(list(snapshot.keys())) if isinstance(snapshot, dict) else [],
                        repr(e),
                        exc_info=True,
                    )
                except Exception:
                    LOGGER.debug("Snapshot load error logging failed")
            # Never crash live engine during replay recovery.
            return

    # ==========================================
    # 🚨 CIRCUIT BREAKER TRIGGER
    # ==========================================
    def _trigger_circuit_breaker(self, reason: str):
        current_tick = int(getattr(self, "_tick_id", -1))
        if self._circuit_breaker_active:
            if self._circuit_breaker_reason is None:
                self._circuit_breaker_reason = str(reason)
            return
        if int(getattr(self, "_circuit_breaker_trigger_tick", -1)) == current_tick:
            return
        self._circuit_breaker_active = True
        self._circuit_breaker_reason = str(reason)
        self._circuit_breaker_trigger_tick = current_tick
        self._healing_counter = 0
        if not getattr(self, "_is_replay", False):
            self._replay_record("circuit_breaker", {"reason": reason})

        try:
            if not getattr(self, "_is_replay", False):
                LOGGER.critical(f"[CIRCUIT BREAKER TRIGGERED] Reason={reason}")
        except Exception:
            self._warn_rate_limited("circuit_breaker_log_failure", "Circuit breaker logging failed", cooldown_s=30.0)

    # ==========================================
    # 🔄 SELF HEALING SYSTEM
    # ==========================================
    def _self_heal(self, error_code: str | None = None, context: Dict[str, Any] | None = None) -> str:
        """
        Best-effort healing.

        - Called without an error_code: preserves existing circuit-breaker recovery behavior.
        - Called with an error_code: applies category-aware recovery action.
        """
        self._healing_count = int(getattr(self, "_healing_count", 0)) + 1
        self._last_healing_error = error_code
        self._last_healing_context = dict(context or {})
        if getattr(self, "_is_replay", False):
            context = dict(context or {})

        try:
            if not getattr(self, "_is_replay", False):
                LOGGER.warning("[SELF HEALING INITIATED]")
        except Exception:
            self._warn_rate_limited("self_heal_log_failure", "Self-healing logging failed", cooldown_s=30.0)

        # Legacy breaker recovery path: keep existing behavior intact.
        if error_code is None:
            # Reset probabilities
            self.nhhmm_prior = np.ones(self.K) / self.K
            self.garch_prob = np.ones(2) / 2.0
            self._smoothed_garch_prob = self.garch_prob.copy()
            self._regime_state_probs = np.ones(4, dtype=float) / 4.0
            if getattr(self, "_regime_smoother", None) is not None:
                self._regime_smoother.reset()

            # Reset volatility
            self.garch_var = self._stationary_garch_var()
            self._last_valid_vol = self.garch.target_vol

            # Reset regime state
            self.current_regime_idx = None
            self._confirmed_regime = None
            self._confirmed_regime_idx = None
            self._prev_regime = None
            self._prev_raw_regime = None
            self._regime_persistence = 0
            self.last_signed_position_size = 0.0
            self._last_effective_trend_strength = 0.0
            self._last_edge_score = 0.0
            self._last_regime_change_ts = None
            self._range_anchor_size = 0.0
            self._in_range = False
            self.range_ticks = 0.0
            self.range_ticks_int = 0

            # Reset PnL state
            self._equity = 1.0
            self._equity_peak = 1.0
            self._drawdown = 0.0
            self._loss_streak = 0
            self._last_price = None

            # Reset memory variables
            self._shock_memory = 0.0
            self._return_ema = 0.0
            self._abs_return_ema = 0.0
            self._last_timestamp = None
            self._last_valid_dt = 1.0
            self._last_valid_sjm_probs = np.ones(self.K) / self.K

            # Reset breaker
            self._circuit_breaker_active = False
            self._circuit_breaker_reason = None
            self._circuit_breaker_trigger_tick = -1
            self._healing_counter = 0
            self._confidence_collapse_streak = 0
            self._last_healing_action = "RESET_FULL"
            if not getattr(self, "_is_replay", False):
                self._replay_record("self_heal", {"error": error_code, "action": "RESET_FULL"})
            return self._last_healing_action

        action = "NO_ACTION"
        category = ""
        err_code = error_code
        resolver = getattr(self, "_error_category_resolver", None)
        if resolver is not None:
            try:
                err = resolver(error_code)
                category = str(getattr(err, "category", "") or "")
                err_code = getattr(err, "code", error_code)
            except Exception:
                category = ""
        if not category:
            category = self._DEFAULT_ERROR_CATEGORY_BY_CODE.get(str(error_code), "")
            if (not getattr(self, "_errors_module_available", True)) and (not getattr(self, "_is_replay", False)):
                self._warn_rate_limited(
                    "self_heal_fallback_mapping",
                    "Self-healing used built-in fallback category mapping.",
                    cooldown_s=120.0,
                )

        if category == "numerical":
            if (
                self._last_valid_sjm_probs is None
                or not np.all(np.isfinite(np.asarray(self._last_valid_sjm_probs, dtype=float)))
                or np.asarray(self._last_valid_sjm_probs, dtype=float).shape != (self.K,)
            ):
                self._last_valid_sjm_probs = np.ones(self.K, dtype=float) / self.K
            smooth_len = int(np.size(self._smoothed_garch_prob))
            if smooth_len <= 0:
                self._smoothed_garch_prob = np.ones(2, dtype=float) / 2.0
            else:
                self._smoothed_garch_prob = np.ones(smooth_len, dtype=float) / smooth_len
            self._shock_memory = 0.0
            if getattr(self, "_regime_smoother", None) is not None:
                self._regime_smoother.reset()
            self._regime_state_probs = np.ones(4, dtype=float) / 4.0
            action = "RESET_NUMERICAL"

        elif category == "state":
            self.reset_state()
            action = "RESET_STATE"

        elif category == "smoothing":
            if getattr(self, "_regime_smoother", None) is not None:
                self._regime_smoother.reset()
            self._regime_state_probs = np.ones(4, dtype=float) / 4.0
            self._confirmed_regime = None
            self._confirmed_regime_idx = None
            self._regime_persistence = 0
            action = "RESET_SMOOTHER"

        elif category == "classification":
            self._regime_persistence = max(0, int(getattr(self, "_regime_persistence", 0)) - 1)
            action = "SOFT_REBALANCE"

        elif category == "input":
            action = "SKIP_AND_DEGRADE"

        elif category == "risk":
            self._trigger_circuit_breaker(str(err_code))
            action = "CIRCUIT_BREAK"
        else:
            # Deterministic fallback: always execute a safe degradation path.
            self.nhhmm_prior = _normalize_prob_vector(self.nhhmm_prior)
            self.garch_prob = _safe_prob_vector(self.garch_prob, 2)
            self._smoothed_garch_prob = _safe_prob_vector(self._smoothed_garch_prob, 2)
            self._confidence_collapse_streak = 0
            action = "SKIP_AND_DEGRADE"

        if not getattr(self, "_is_replay", False):
            self._replay_record(
                "self_heal",
                {
                    "error": error_code,
                    "action": action,
                },
            )

        self._last_healing_action = action
        return action

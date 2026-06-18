from __future__ import annotations

import numpy as np
import hashlib
import dataclasses
import copy
from scipy.special import softmax
import weakref
try:
    from observability_controller import ObservabilityController
except Exception:
    ObservabilityController = None
from dataclasses import dataclass
from typing import Dict, Any, List
from collections import Counter, OrderedDict, deque
import logging
import time
import warnings
import traceback
from scipy.special import logsumexp
from functools import wraps
import json
import os
import queue
import threading
from model_weights import ModelWeightManager
from regime_vol_calibration import (
    DEFAULT_TARGET_VOL_ARTIFACT_PATH,
    calibrate_target_vol,
    load_target_vol_artifact,
    write_target_vol_artifact,
)
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

# FIX-L1: module-level guard so the "errors.get_error unavailable" notice is
# emitted at most once per process (was a WARNING fired on every ARE
# construction). See AdvancedRegimeEngine.__init__ for the gated emission.
_ERROR_MAP_WARN_EMITTED: bool = False
_OUTPUT_SCHEMA_VERSION = "1.2.0"
_POSITION_SIZE_CAP = 0.35
_VALID_ENGINE_STATUS = frozenset({
    "OK", "DEGRADED", "HEALING", "HEALING_COMPLETE", "SCHEMA_FAILURE",
    "NO_FEATURES", "CIRCUIT_BREAKER", "WARMUP", "TOXIC_HALT",
    "OK_WITH_HISTORY", "RNG_RESTORE_FAILED", "UNKNOWN",
})
_VALID_OPERATIONAL_MODES = frozenset({"LIVE", "PAPER", "HALTED", "SIMULATION"})
_VALID_EXECUTION_STRATEGIES = frozenset({"trend_follow", "scalp", "mean_revert", "neutral", "risk_off_or_short_bias", "flat_or_hedge", "range_mean_revert", "fail_safe", "circuit_breaker", "halt", "halt_igarch"})
_VALID_EXECUTION_SIDE = frozenset({"long", "short", "flat", "range_mean_revert"})
_PROMETHEUS_ENGINE_ID_LIMIT = 50
_prometheus_engine_ids: set[str] = set()
_PROMETHEUS_LOCK = threading.Lock()

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
    REGIME_GARCH_PERSISTENCE_HIGH = PromCounter(
        "regime_garch_persistence_high_total",
        "Number of times alpha+beta exceeded persistence threshold post-refit",
        ["engine_id"],
    )
    REGIME_SCHEMA_VIOLATIONS = PromCounter(
        "regime_schema_violations_total",
        "Number of times _validate_output_schema returned False",
        ["engine_id", "violation_type"],
    )
    REGIME_FAILSAFE_EMITTED = PromCounter(
        "regime_failsafe_emitted_total",
        "Number of times the engine emitted a fail-safe payload",
        ["engine_id", "reason"],
    )
    # FIX-5.5: per-reason regime downgrade gauge (mirrors get_health()).
    REGIME_DOWNGRADE_COUNT = Gauge(
        "regime_downgrade_count",
        "Per-reason regime downgrade tally (mirrors get_health()['regime_downgrade_count'])",
        ["engine_id", "reason"],
    )

# FIX-5.3: pluggable downgrade-reason registry. Unknown reasons are bucketed
# into "unspecified" by AdvancedRegimeEngine._record_regime_downgrade so typos
# don't silently create new buckets.
_REGIME_DOWNGRADE_REASONS: frozenset = frozenset({
    "microstructure_required_but_missing",
    "uncalibrated_weights",
    "circuit_breaker",
    "nhhmm_warmup",
    "unspecified",
})

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
    try:
        arr = np.asarray(values, dtype=float)
    except Exception as exc:
        raise ValueError(f"{name} is not numeric/coercible") from exc
    if arr.ndim == 0:
        arr = arr.reshape(1)
    else:
        arr = np.ravel(arr)
    if arr.shape != (expected_size,):
        raise ValueError(f"{name} has shape {arr.shape}, expected {(expected_size,)}")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} contains non-finite values")
    return arr

def _safe_float(value: Any, default: float = 0.0, min: float | None = None, max: float | None = None) -> float:
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

# Backward-compatible alias; prefer _safe_float internally.
safe_float = _safe_float


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
def _validate_output_schema(output: Dict[str, Any], engine_id: str = "unknown") -> bool:
    try:
        if not isinstance(output, dict):
            raise ValueError("output must be a dict")
        if "schema_version" not in output:
            raise ValueError("missing schema_version")

        version = str(output["schema_version"]).strip()
        if version != _OUTPUT_SCHEMA_VERSION:
            raise ValueError(f"mismatch {version} != {_OUTPUT_SCHEMA_VERSION}")
        for key in ("regime_idx", "regime_label", "probabilities", "risk_metrics", "alpha", "conviction"):
            if key not in output:
                raise ValueError(f"missing required key: {key}")
        if output.get("engine_status", output.get("risk_metrics", {}).get("engine_status")) not in _VALID_ENGINE_STATUS:
            raise ValueError(f"Invalid engine_status: {output.get('engine_status', output.get('risk_metrics', {}).get('engine_status'))}")
        if output.get("execution_mode") not in (_VALID_OPERATIONAL_MODES | _VALID_EXECUTION_STRATEGIES):
            raise ValueError(f"Invalid execution_mode: {output.get('execution_mode')}")
        if output.get("execution_side") not in _VALID_EXECUTION_SIDE:
            raise ValueError(f"Invalid execution_side: {output.get('execution_side')}")
        if "signal_valid" not in output:
            raise ValueError("missing required key: signal_valid")
        if not isinstance(output["signal_valid"], bool):
            raise ValueError(
                f"signal_valid must be bool, got "
                f"{type(output['signal_valid']).__name__}"
            )
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
        if "engine_status" not in output["risk_metrics"]:
            raise ValueError("missing risk_metrics.engine_status")
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
        conviction = safe_float(output.get("conviction", 0.0), default=np.nan)
        if not np.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
            raise ValueError(f"invalid confidence={confidence}")
        if not np.isfinite(conviction) or conviction < 0.0 or conviction > 1.0:
            raise ValueError(f"invalid conviction={conviction}")
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
        if _PROM_AVAILABLE:
            try:
                msg = str(e)
                lowered = msg.lower()
                if "schema_version" in lowered or "mismatch" in lowered:
                    vt = "schema_version"
                elif "probabilities" in lowered:
                    vt = "probabilities"
                elif "macro_probs" in lowered:
                    vt = "macro_probs"
                elif "garch_regime_probs" in lowered:
                    vt = "garch_regime_probs"
                elif "risk_metrics" in lowered:
                    vt = "risk_metrics"
                elif "engine_status" in lowered:
                    vt = "engine_status"
                elif "execution_mode" in lowered:
                    vt = "execution_mode"
                elif "execution_side" in lowered:
                    vt = "execution_side"
                elif "signal_valid" in lowered:
                    vt = "signal_valid"
                elif "confidence" in lowered:
                    vt = "confidence"
                elif "conviction" in lowered:
                    vt = "conviction"
                elif "edge_score" in lowered or "alpha" in lowered:
                    vt = "alpha"
                elif "position_size" in lowered:
                    vt = "position_size"
                elif "missing required key" in lowered or "missing " in lowered:
                    vt = "missing_key"
                else:
                    vt = "other"
                REGIME_SCHEMA_VIOLATIONS.labels(str(engine_id or "unknown"), vt).inc()
            except Exception as _swallowed_exc:
                LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)
        return False

def _build_output(
    *,
    regime_idx: int,
    regime_label: str,
    trend_strength: float,
    risk_level: float,
    confidence: float,
    conviction: float = 0.0,
    edge_score: float,
    probabilities: Dict[str, float],
    macro_probs: List[float],
    position_size: float,
    expected_vol: float,
    raw_size: float,
    is_toxic: bool,
    garch_regime_probs: List[float],
    feed_status: Any,
    engine_status: str = "OK",
    signed_position_size: float = 0.0,
    last_valid_vol: float = 0.0,
    switch_stability_ema: float = 1.0,
    execution_mode: str = "",
    execution_side: str = "",
    extended_schema: bool = False,
    range_ticks: int = 0,
    signal_valid: bool = True,
    include_signal_valid: bool = True,
    weights_loaded: bool = False,
    calibration_valid: bool = False,
    production_valid: bool = False,
    research_mode: bool = False,
    calibration_status: str = "uncalibrated",
    engine_id: str = "unknown",
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
    # confidence := max probability mass, conviction := entropy-derived certainty.
    safe_confidence = safe_float(confidence, default=0.0, min=0.0, max=1.0)
    safe_conviction = safe_float(conviction, default=0.0, min=0.0, max=1.0)
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
    if isinstance(feed_status, dict):
        primary_status = str(feed_status.get("primary", "UNKNOWN"))
        raw_flags = feed_status.get("flags", [])
        if not isinstance(raw_flags, list):
            raw_flags = []
        status_flags = [str(v)[:64] for v in raw_flags[:8]]
    else:
        primary_status = str(feed_status or "UNKNOWN")
        status_flags = []

    if not execution_mode:
        if str(regime_label or "UNKNOWN") == "TREND":
            execution_mode = "trend_follow"
        elif str(regime_label or "UNKNOWN") == "BEAR":
            execution_mode = "risk_off_or_short_bias"
        elif str(regime_label or "UNKNOWN") == "TOXIC":
            execution_mode = "flat_or_hedge"
        else:
            execution_mode = "range_mean_revert"
    if not execution_side:
        execution_side = "flat"

    out = {
        'schema_version': _OUTPUT_SCHEMA_VERSION,
        'regime_idx': safe_regime_idx,
        'regime_label': str(regime_label or "UNKNOWN"),
        'trend_strength': safe_trend_strength,
        'risk_level': safe_risk_level,
        'confidence': safe_confidence,
        'conviction': safe_conviction,
        'probabilities': safe_probabilities,
        'macro_probs': safe_macro_probs,
        'position_size': safe_position_size,
        'execution_mode': execution_mode,
        'execution_side': execution_side,
        'signed_position_size': safe_signed_position,
        'signal_valid': bool(signal_valid),
        'weights_loaded': bool(weights_loaded),
        'calibration_valid': bool(calibration_valid),
        'production_valid': bool(production_valid),
        'research_mode': bool(research_mode),
        'calibration_status': str(calibration_status or "uncalibrated"),
        'engine_status': str(engine_status or "UNKNOWN"),
        'feed_status': primary_status,
        
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
            'feed_status': {"primary": primary_status, "flags": status_flags},
            'engine_status': str(engine_status or "UNKNOWN"),
            'range_ticks': int(safe_float(range_ticks, default=0.0, min=0.0, max=1e9)),
        },
        # ==========================================
        # EDGE OUTPUT (NEW - FIXES SCHEMA GAP)
        # ==========================================
        'alpha': {
            'edge_score': safe_edge_score
        },
    }

    # Centralized DEGRADED enforcement (defense-in-depth).
    if str(engine_status or "OK") == "DEGRADED":
        out["signal_valid"] = False
    
    # --- HARD GUARD (fail-safe, NON-BREAKING) ---
    try:
        schema_ok = _validate_output_schema(out, engine_id=engine_id)
    except TypeError:
        try:
            schema_ok = _validate_output_schema(out)
        except Exception:
            schema_ok = False
    except Exception:
        schema_ok = False
    if not schema_ok:
        if _PROM_AVAILABLE:
            try:
                REGIME_FAILSAFE_EMITTED.labels(
                    str(engine_id or "unknown"), "schema_validation_failed"
                ).inc()
            except Exception as _swallowed_exc:
                LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)
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
            "conviction": 0.0,
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
            "schema_compat": {
                "version": _OUTPUT_SCHEMA_VERSION,
                "backward_compatible": True
            },
            "engine_status": "SCHEMA_FAILURE",
            "feed_status": "SCHEMA_FAILURE",
            "risk_metrics": {
                "expected_volatility": 0.0,
                "raw_leverage": 0.0,
                "last_valid_vol": safe_last_valid_vol,
                "switch_stability_ema": safe_switch_stability,
                "toxic_penalty_applied": True,
                "garch_regime_probs": fail_safe_garch_probs,
                "feed_status": {"primary": "SCHEMA_FAILURE", "flags": []},
                "range_ticks": 0,
                "engine_status": "SCHEMA_FAILURE",
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
        bear_mass = float(np.clip(scores.get("bear_score", scores.get("bear_trend_score", 0.0)), 0.0, 1.0))
        range_mass = float(np.clip(scores.get("range_score", 0.0), 0.0, 1.0))
        toxic_mass = float(np.clip(scores.get("toxic_score", 0.0), 0.0, 1.0))

        # Symmetric blend prevents structural suppression of directional evidence.
        # Both TREND and BEAR receive evidence on the same [0, 1] scale used by
        # RANGE/TOXIC, while retaining directionality from bull/bear probabilities.
        dir_total = float(np.clip(bull + bear, 1e-12, 2.0))
        bull_share = bull / dir_total
        bear_share = bear / dir_total
        blend_w = 0.25
        trend_prob = float(np.clip((1.0 - blend_w) * trend_mass + blend_w * bull_share, 0.0, 1.0))
        bear_core = bear_mass
        bear_prob = float(np.clip((1.0 - blend_w) * bear_core + blend_w * bear_share, 0.0, 1.0))
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
    last_signed_return: float = 0.0,
    return_score_map: bool = False,
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
    directional_probability = float(np.clip(max(bull, bear), 0.0, 1.0))

    # Soft range evidence (bounded; no hard overrides).
    range_from_balance = low_directionality
    range_from_low_vol = float(np.clip(1.0 - crisis, 0.0, 1.0))
    range_from_low_drift = float(np.clip(1.0 - directional_probability, 0.0, 1.0))

    # --- Directional score (bounded, symmetric between TREND and BEAR) ---
    trend_score = float(np.clip(
        (1.0 - 0.35 * crisis) * (0.70 * directional_probability + 0.30 * directional_strength),
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
    trend_pressure = 0.50 * directional_strength + 0.25 * float(
        np.clip((directional_probability - 0.5) / 0.5, 0.0, 1.0)
    )

    range_score = float(np.clip(
        min(range_score_raw, 0.70) - trend_pressure,
        0.0,
        1.0
    ))

    dominant = max(bull, bear)
    separation = directional_strength
    edge_score = float(np.clip((dominant - crisis) + 0.35 * separation, 0.0, 1.0))

    direction_gap = float(bull - bear)
    signed_return_hint = float(last_signed_return) if np.isfinite(float(last_signed_return)) else 0.0
    return_direction = 0.0
    if abs(signed_return_hint) >= 5.0e-4 and crisis < 0.35:
        return_direction = 1.0 if signed_return_hint > 0.0 else -1.0
    effective_direction_gap = direction_gap
    # Return direction may only break ties. Minimum directional_strength
    # threshold ensures bull/bear probability spread is the primary driver.
    # A return hint is applied only when:
    #   (a) the return magnitude exceeds 5e-4
    #   (b) crisis < 0.35 (not a fear regime)
    #   (c) directional_strength already exceeds 0.12 (non-trivial prob spread)
    #   (d) the return hint agrees with direction_gap sign or direction_gap ≈ 0
    # The hint contribution is capped at min(directional_strength, 0.15) so it
    # can never flip the label when bull/bear are meaningfully separated.
    if (
        return_direction != 0.0
        and directional_strength >= 0.12
        and crisis < 0.35
    ):
        agree = (return_direction > 0.0 and direction_gap >= 0.0) or \
                (return_direction < 0.0 and direction_gap <= 0.0)
        tie = abs(direction_gap) < 0.06
        if agree or tie:
            hint_magnitude = min(directional_strength, 0.15) * return_direction
            effective_direction_gap = direction_gap + 0.25 * hint_magnitude
    directional_label = "TREND" if effective_direction_gap >= 0.0 else "BEAR"
    switch_gap = float(np.clip(direction_switch_gap, 0.0, 0.25))
    # Hysteresis: require gap to exceed 1.5x switch_gap before flipping direction.
    # This improves recall by preventing premature directional flips during noise.
    if prev_directional_label in ("TREND", "BEAR"):
        if prev_directional_label == "TREND" and effective_direction_gap > -(switch_gap * 1.5):
            directional_label = "TREND"
        elif prev_directional_label == "BEAR" and effective_direction_gap < (switch_gap * 1.5):
            directional_label = "BEAR"

    directional_mass = float(np.clip(bull + bear, 1e-12, 2.0))
    bull_share = float(np.clip(bull / directional_mass, 0.0, 1.0))
    bear_share = float(np.clip(bear / directional_mass, 0.0, 1.0))
    trend_score_trend = float(np.clip(trend_score * bull_share, 0.0, 1.0))
    trend_score_bear = float(np.clip(trend_score * bear_share, 0.0, 1.0))
    if return_direction > 0.0 and directional_strength >= 0.04:
        directional_label_winner = "TREND"
    elif return_direction < 0.0 and directional_strength >= 0.04:
        directional_label_winner = "BEAR"
    elif bull_share > bear_share:
        directional_label_winner = "TREND"
    elif bear_share > bull_share:
        directional_label_winner = "BEAR"
    else:
        directional_label_winner = "TREND" if float(last_signed_return) >= 0.0 else "BEAR"
    score_map = {
        "TREND": trend_score if directional_label_winner == "TREND" else 0.0,
        "BEAR": trend_score if directional_label_winner == "BEAR" else 0.0,
        "RANGE": range_score,
        "TOXIC": toxic_score,
    }
    for score_key, score_val in score_map.items():
        if score_val < 0.0:
            LOGGER.warning("compute_hmm_regime: negative score detected key=%s value=%.6f action=clamp_to_zero", score_key, score_val)
            score_map[score_key] = 0.0
    score_sum = float(sum(score_map.values()))
    if not np.isfinite(score_sum) or score_sum <= 0.0:
        LOGGER.error("compute_hmm_regime: invalid score sum=%.6f using uniform fallback", score_sum)
        score_map = {"TREND": 0.25, "BEAR": 0.25, "RANGE": 0.25, "TOXIC": 0.25}
    elif abs(score_sum - 1.0) > 0.10:
        LOGGER.warning("compute_hmm_regime: score sum out-of-band sum=%.6f", score_sum)
    max_score = max(score_map.values())
    tied_labels = [label for label, score in score_map.items() if abs(score - max_score) <= 1e-12]
    tie_priority = {"TOXIC": 0, "TREND": 1, "BEAR": 2, "RANGE": 3}
    regime = sorted(tied_labels, key=lambda label: tie_priority.get(label, 99))[0]

    entropy = float(-np.sum(alpha_safe * np.log(np.clip(alpha_safe, 1e-12, None))))
    max_entropy = float(np.log(alpha_safe.size))
    uncertainty = float(np.clip(entropy / max(max_entropy, 1e-12), 0.0, 1.0))
    certainty_base = float(np.clip(1.0 - uncertainty, 0.0, 1.0))
    directional_component = float(np.clip(max(edge_score, directional_strength), 0.0, 1.0))
    conviction = float(np.clip(0.20 * certainty_base + 0.80 * directional_component, 0.0, 1.0))
    certainty_score = float(np.clip(1.0 - uncertainty, 0.0, 1.0))
    directional_confidence = float(np.clip(
        0.5 * directional_strength + 0.5 * edge_score,
        0.0,
        1.0,
    ))

    out = {
        "regime": regime,
        "bull": bull,
        "bear": bear,
        "crisis": crisis,
        "trend_strength": effective_direction_gap,
        "risk_level": crisis,
        "confidence": max(bull, bear, crisis),
        "conviction": conviction,
        "certainty_score": certainty_score,
        "directional_confidence": directional_confidence,
        "uncertainty": uncertainty,
        "directional_margin": abs(effective_direction_gap),
        "directional_label": directional_label,
        "edge_score": edge_score,
        "trend_score": trend_score,
        "bear_score": trend_score_bear,
        "range_score": range_score,
        "toxic_score": toxic_score,
        "score_map": dict(score_map),
        "metadata": {
            "trend_score_trend": trend_score_trend,
            "trend_score_bear": trend_score_bear,
            "directional_label_winner": directional_label_winner,
            "score_sum": score_sum,
        },
    }
    if return_score_map:
        return dict(score_map)
    return out

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
        beta_arr = np.asarray(beta, dtype=float)
        mu_arr = np.asarray(mu, dtype=float)
        sigma_arr = np.asarray(sigma, dtype=float)
        if beta_arr.shape != (self.K, self.K, self.n_features):
            raise ValueError(
                f"beta must have shape {(self.K, self.K, self.n_features)}, got {beta_arr.shape}"
            )
        if mu_arr.shape != (self.K,) or sigma_arr.shape != (self.K,):
            raise ValueError(
                f"mu/sigma must both have shape {(self.K,)}, got mu={mu_arr.shape}, sigma={sigma_arr.shape}"
            )
        if not (np.all(np.isfinite(beta_arr)) and np.all(np.isfinite(mu_arr)) and np.all(np.isfinite(sigma_arr))):
            raise ValueError("NHHMM weights contain non-finite values.")
        self.beta = beta_arr
        if np.any(np.abs(self.beta) > 50.0):
            LOGGER.warning(
                "load_weights: beta contains values with |β| > 50. This may cause logit saturation. Consider re-normalizing features."
            )
        self.mu = mu_arr
        if np.any(sigma_arr < 1e-4):
            LOGGER.warning(
                "load_weights: sigma contains values below 1e-4 (min=%.2e). Emission distributions may be over-concentrated. Consider recalibrating.",
                float(np.min(sigma_arr))
            )
        self.sigma = np.clip(np.abs(sigma_arr), 1e-4, None)

    def _compute_transition_matrix(self, x_t: np.ndarray) -> np.ndarray:
        try:
            x_t = _coerce_1d_vector(x_t, self.n_features, name="NHHMM transition x_t")
        except ValueError as e:
            raise RuntimeError(
                f"NHHMM input validation failed: {e}. "
                "Upstream feature pipeline produced invalid data."
            ) from e
        x_t_norm = x_t / (np.std(x_t) + 1e-8)
        x_t_safe = np.clip(x_t_norm, -3.0, 3.0)
        beta_safe = np.clip(self.beta, -5.0, 5.0)
        logits = np.einsum('ijk,k->ij', beta_safe, x_t_safe)
        logits[:, 0] = 0.0  # Identifiability: pin reference category column
        logits = np.clip(logits, -20.0, 20.0)
        p_t = softmax(logits, axis=1)
        if np.any(p_t > 0.9999):
            LOGGER.warning(
                "[NHHMM] Softmax saturation detected — transition matrix degenerate. Check x_t range [%.4f, %.4f]",
                float(np.min(x_t)),
                float(np.max(x_t)),
            )
        p_t = np.clip(p_t, 1.1e-3, None)
        row_sums = p_t.sum(axis=1, keepdims=True)
        p_t = p_t / np.clip(row_sums, 1e-12, None)
        if not np.all(np.isfinite(p_t)):
            LOGGER.error("_compute_transition_matrix: non-finite output after clipping. Falling back to uniform transition matrix.")
            p_t = np.full((self.K, self.K), 1.0 / self.K)
        return p_t

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
        sigma_safe = np.clip(np.abs(np.asarray(self.sigma, dtype=float)), 1e-4, None)
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
        self._default_params_initialized = False
        
    def load_weights(self, means: np.ndarray, weights: np.ndarray):
        """Inject pre-trained centroids for live inference."""
        means_arr = np.asarray(means, dtype=float)
        weights_arr = np.asarray(weights, dtype=float)
        if means_arr.ndim != 2:
            raise ValueError(f"means must be 2-D [K, n_features], got ndim={means_arr.ndim}")
        if means_arr.shape[0] != self.K:
            raise ValueError(f"means first dimension must equal K={self.K}, got {means_arr.shape[0]}")
        if weights_arr.ndim != 1 or weights_arr.shape[0] != means_arr.shape[1]:
            raise ValueError(
                f"weights must be 1-D and match means feature dimension ({means_arr.shape[1]}), got {weights_arr.shape}"
            )
        if not (np.all(np.isfinite(means_arr)) and np.all(np.isfinite(weights_arr))):
            raise ValueError("SJM weights contain non-finite values.")
        self.means = means_arr
        self.weights = weights_arr
        self._default_params_initialized = False

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

        # Default fallback must be symmetric across feature dimensions to avoid
        # startup classification bias when pre-trained centroids are unavailable.
        if self.means is None:
            self.means = np.zeros((self.K, n_feat), dtype=float)
            self.weights = np.ones(n_feat, dtype=float) / np.sqrt(max(n_feat, 1))
            self._default_params_initialized = True
            LOGGER.warning(
                "SparseJumpModel fallback initialized with symmetric zero centroids; "
                "load_weights() is recommended for production inference."
            )
        elif self.means.shape[1] != n_feat:
            raise ValueError(
                f"SJM feature dimension mismatch: expected {self.means.shape[1]}, "
                f"got {n_feat}. Check upstream feature pipeline."
            )
        if self.weights is None:
            self.weights = np.ones(n_feat, dtype=float) / np.sqrt(max(n_feat, 1))
            LOGGER.warning(
                "SparseJumpModel weights were missing at inference time; "
                "applied deterministic uniform fallback weights."
            )
        elif self.weights.shape != (n_feat,):
            raise ValueError(
                f"SJM weights shape mismatch: expected {(n_feat,)}, got {self.weights.shape}. "
                "Check model load path."
            )
        try:
            nhhmm_probs = _normalize_prob_vector(np.asarray(nhhmm_probs, dtype=float))
        except Exception:
            nhhmm_probs = np.ones(self.K, dtype=float) / self.K
        if self._default_params_initialized:
            # Safe fallback mode: reduce overconfidence when model centroids are
            # not explicitly loaded, while preserving deterministic behavior.
            if bool(getattr(self, "_just_restored", False)):
                LOGGER.warning(
                    "sjm: using zero-centroid fallback after state restore — classifications are uniform-dampened."
                )
            uniform = np.ones(self.K, dtype=float) / self.K
            nhhmm_probs = _normalize_prob_vector(0.5 * nhhmm_probs + 0.5 * uniform)

        weighted_x = x_t * self.weights  # (n_feat,)

        # --- Vectorized cost computation (resolves CRITICAL-1) ---
        # costs[k] = negative squared distance to centroid k
        diffs = weighted_x[np.newaxis, :] - self.means  # (K, n_feat)
        costs = -np.sum(diffs ** 2, axis=1)              # (K,)

        # Persistence penalty: applied to all non-incumbent states uniformly
        if prev_state is not None:
            switch_mask = np.ones(self.K, dtype=bool)
            switch_mask[prev_state] = False
            # FIX CRITICAL-3 / SJM penalty sign:
            # costs[k] = NEGATIVE squared distance (already large-negative for far centroids).
            # `argmax(costs)` picks the closest centroid. To DISCOURAGE switching we must
            # SUBTRACT extra utility from the switch candidates (i.e. add a positive penalty
            # value to the absolute magnitude of their cost), making them LESS attractive.
            # Previously the code wrote `-=` here, which made non-incumbent states MORE
            # attractive (cheaper to switch) — the exact opposite of the intended semantics.
            # The correct sign is `+=` so the penalty is SUBTRACTED from the switch cost
            # (costs are negative; adding (-pen) reduces the score of switching).
            costs[switch_mask] += -(0.25 * self.lambda_pen + 0.05)

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
    _VAR_CEIL = np.array([0.04, 0.04])
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
        current_var = np.clip(current_var, 1e-8, None)
        return_t = safe_float(return_t, default=0.0, min=-2.0, max=2.0)
        new_var = (
            self.omega
            + self.alpha * (return_t ** 2)
            + self.beta_garch * current_var
        )
        # FIX-3: runtime IGARCH guardrail — detect post-refit persistence drift
        try:
            persistence = np.asarray(self.alpha, dtype=float) + np.asarray(self.beta_garch, dtype=float)
            if np.any(persistence >= 0.99):
                engine = getattr(self, "_regime_engine_ref", None)
                eid = "unknown"
                if engine is not None:
                    eid = str(getattr(engine, "_metrics_engine_id", getattr(engine, "engine_id", "unknown")))
                if _PROM_AVAILABLE:
                    try:
                        REGIME_GARCH_PERSISTENCE_HIGH.labels(eid).inc()
                    except Exception as _swallowed_exc:
                        LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)
                if engine is not None:
                    try:
                        engine._warn_rate_limited(
                            "garch_persistence_high",
                            f"GARCH persistence {persistence.tolist()} >= 0.99 (IGARCH risk).",
                        )
                    except Exception as _swallowed_exc:
                        LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)
        except Exception as _swallowed_exc:
            LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)
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
    # FIX-7 (REGIME_ENGINE_AUDIT 2026-04-23): hard portfolio-level drawdown
    # stop, separate from the engine's own model-based drawdown. Tripped via
    # report_realized_pnl() by the executor (live or backtest) on every
    # closed trade. Without this, a 50 x -2% ruin scenario walks past the
    # engine without firing the circuit breaker.
    _MAX_PORTFOLIO_DRAWDOWN = 0.20
    _MAX_CONSECUTIVE_LOSSES = 7
    _VOL_SHOCK_MULTIPLIER = 3.5
    _CONFIDENCE_COLLAPSE_THRESHOLD = 0.35
    _CONFIDENCE_COLLAPSE_MIN_STREAK = 3
    _CONF_COLLAPSE_WARMUP_UPDATES: int = 20
    _CONF_COLLAPSE_WARMUP_SECONDS: float = 60.0
    _HEALING_COOLDOWN_TICKS = 20

    _EWM_ALPHA: float = 0.15
    _RANGE_SIGNED_DECAY: float = 0.25
    _RANGE_SIGNED_DECAY_LAMBDA: float = 0.0001
    _MIN_SIGNED_TRADE_SIZE: float = 0.01
    _MIN_POSITION_SIZE: float = 0.01
    _RANGE_NEUTRALIZE_VOL: float = 0.018
    _RANGE_DECAY_FLOOR_K: float = 0.05
    _EDGE_MIN_SWITCH_CONFIDENCE: float = 0.58
    # Conviction threshold constants calibrated from conviction_calibration_report.md.
    _CONV_THRESHOLD_FLOOR: float = 0.182039
    _CONV_THRESHOLD_BASE: float = 0.182039
    _CONV_THRESHOLD_UNCERTAINTY_WEIGHT: float = 0.0
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
    _SHOCK_MEMORY_DECAY: float = 0.75
    _SHOCK_INTENSITY_VOL_MULT: float = 3.0
    _SNAPSHOT_QUEUE_MAXSIZE: int = 256
    _STATE_VERSION: str = "1.2.0"
    _WARNING_CACHE_LIMIT: int = 1024
    _WARNING_CACHE_TRIM_TO: int = 768
    _TRACEBACK_MAX_FRAMES: int = 12
    _TRACEBACK_MAX_CHARS: int = 3000
    _TRACEBACK_MAX_LINE_CHARS: int = 300
    _HASH_NAMESPACE: str = "ADV_REGIME_REPLAY"
    _MAX_POSITION_SIZE: float = _POSITION_SIZE_CAP
    _MIN_EQUITY_FLOOR: float = 1e-6
    _MAX_PRICE_STALENESS_SEC: float = 300.0
    _MAX_PRICE_STALENESS_TICKS: int = 5
    _PRICE_RETURN_MISMATCH_TOLERANCE: float = 1e-3
    _CANONICAL_RETURN_MISMATCH_TOLERANCE: float = 1e-5
    _DIRECTION_SWITCH_GAP: float = 0.02
    _SJM_RESERVED_RETURN_IDX: int = 0
    _SJM_RESERVED_ABS_RETURN_IDX: int = 2
    _DEFAULT_ERROR_CATEGORY_BY_CODE: Dict[str, str] = {
        "E120": "input",
        "E130": "input",
        "E200": "numerical",
    }
    _VALID_REGIME_LABELS: set[str] = {"TREND", "RANGE", "BEAR", "TOXIC"}
    _VALID_DIRECTIONAL_LABELS: set[str] = {"TREND", "BEAR"}
    _VALID_DETERMINISM_STATUS: set[str] = {"OK", "RNG_RESTORE_FAILED", "OK_WITH_HISTORY"}
    _VALID_PNL_MODES: set[str] = {"TIMESTAMP", "TICK"}
    _SHOCK_STARTUP_MULTIPLIER: float = 2.25
    _SHOCK_STARTUP_VOL_FLOOR_MULT: float = 0.85
    _SHOCK_WARMUP_TICKS: int = 32
    _SHOCK_WARMUP_SECONDS: float = 120.0
    _RETURN_EMA_BASE_DECAY: float = 0.92
    _MAX_EMA_GAP_DT: float = 10.0

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

    def _validate_rng_state_payload(self, rng_state: Any) -> Dict[str, Any]:
        if not isinstance(rng_state, dict):
            raise ValueError("rng state must be a dict")
        if getattr(self, "_rng", None) is None:
            raise ValueError("engine rng is unavailable")
        candidate_state = copy.deepcopy(dict(rng_state))
        try:
            probe_a = np.random.Generator(type(self._rng.bit_generator)())
            probe_b = np.random.Generator(type(self._rng.bit_generator)())
            probe_a.bit_generator.state = copy.deepcopy(candidate_state)
            probe_b.bit_generator.state = copy.deepcopy(candidate_state)
            a_sample = probe_a.integers(0, np.iinfo(np.uint64).max, size=8, dtype=np.uint64)
            b_sample = probe_b.integers(0, np.iinfo(np.uint64).max, size=8, dtype=np.uint64)
            if not np.array_equal(a_sample, b_sample):
                raise ValueError("rng reproducibility probe mismatch")
        except (ValueError, TypeError) as exc:
            LOGGER.warning(
                "rng_state validation failed (%s). Non-default BitGenerator may be in use.",
                exc,
            )
            raise
        return candidate_state

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
        target_vol: float | None = None,
        allow_igarch=False,
        regime_prob_floor: float = None,
        emit_extended_schema: bool = False,
        strict_mtf_keys: bool = True,
        mtf_weights: Dict[str, float] = None,
        sjm_reserved_feature_indices: tuple[int, int] | None = None,
        allow_timestamp_free_pnl: bool = True,
        max_price_staleness_ticks: int = _MAX_PRICE_STALENESS_TICKS,
        shock_warmup_ticks: int = _SHOCK_WARMUP_TICKS,
        shock_warmup_seconds: float = _SHOCK_WARMUP_SECONDS,
        shock_startup_multiplier: float = _SHOCK_STARTUP_MULTIPLIER,
        shock_startup_vol_floor_mult: float = _SHOCK_STARTUP_VOL_FLOOR_MULT,
        seed: int | None = 7,
        engine_id: str | None = None,
        enable_background_workers: bool = True,
        load_model_weights_on_init: bool = True,
        target_vol_artifact_path: str | None = None,
        use_calibrated_target_vol_default: bool = False,
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

        # --- Multi-timeframe weights ---
        # Contract: "base" is anchor-only and is not part of weighted fusion.
        # Weights map only non-base timeframe keys to positive finite weights.
        self.mtf_weights = self._normalize_mtf_weights(mtf_weights)

        self._sjm_reserved_feature_indices = self._validate_sjm_reserved_feature_indices(
            sjm_reserved_feature_indices
        )

        self.nhhmm = NHHMM_Engine(n_states=n_states, n_features=n_features)
        self.sjm = SparseJumpModel(n_states=n_states)
        self._target_vol_provenance: Dict[str, Any] = {}
        self._target_vol_calibrated = False
        self._target_vol_missing_artifact = False
        self._target_vol_artifact_path = (
            target_vol_artifact_path
            or os.environ.get("REGIME_TARGET_VOL_PATH")
            or DEFAULT_TARGET_VOL_ARTIFACT_PATH
        )
        env_use_calibrated_target_vol = str(
            os.environ.get("REGIME_USE_CALIBRATED_TARGET_VOL", "")
        ).strip().lower() in {"1", "true", "yes", "on"}
        self._use_calibrated_target_vol_default = bool(
            use_calibrated_target_vol_default or env_use_calibrated_target_vol
        )
        if target_vol is not None:
            effective_target_vol = float(target_vol)
            self._target_vol_calibrated = True
            self._target_vol_provenance = {"source": "explicit_override", "target_vol": effective_target_vol}
        elif not self._use_calibrated_target_vol_default:
            effective_target_vol = 0.02
            self._target_vol_calibrated = False
            self._target_vol_provenance = {
                "source": "literal_default",
                "target_vol": effective_target_vol,
                "artifact_resolution_enabled": False,
            }
        else:
            artifact = load_target_vol_artifact(self._target_vol_artifact_path)
            if artifact is not None:
                effective_target_vol = float(artifact["calibrated_target_vol"])
                self._target_vol_calibrated = True
                self._target_vol_provenance = copy.deepcopy(artifact)
            else:
                effective_target_vol = 0.02
                self._target_vol_missing_artifact = True
                self._target_vol_calibrated = False
                self._target_vol_provenance = {
                    "source": "literal_fallback",
                    "path": self._target_vol_artifact_path,
                    "target_vol": effective_target_vol,
                    "artifact_resolution_enabled": True,
                }
        self.garch = MSGARCH_RiskEngine(
            target_volatility=effective_target_vol,
            regime_prob_floor=regime_prob_floor,
        )
        # FIX-3: back-reference enables runtime IGARCH telemetry from _garch_update
        self.garch._regime_engine_ref = self
        self._init_params: Dict[str, Any] = {
            'n_states': n_states,
            'n_features': n_features,
            'target_vol': effective_target_vol,
            'allow_igarch': allow_igarch,
            'regime_prob_floor': self.garch._REGIME_PROB_FLOOR,
            'schema_version': _OUTPUT_SCHEMA_VERSION,
            'seed': self._rng_seed,
            'allow_timestamp_free_pnl': bool(allow_timestamp_free_pnl),
            'max_price_staleness_ticks': int(max_price_staleness_ticks),
            'shock_warmup_ticks': int(shock_warmup_ticks),
            'shock_warmup_seconds': float(shock_warmup_seconds),
            'shock_startup_multiplier': float(shock_startup_multiplier),
            'shock_startup_vol_floor_mult': float(shock_startup_vol_floor_mult),
            'target_vol_artifact_path': self._target_vol_artifact_path,
            'use_calibrated_target_vol_default': bool(self._use_calibrated_target_vol_default),
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
        self._last_price_timestamp = None
        self._last_price_tick_id = None
        self._pnl_mode = None
        self._allow_timestamp_free_pnl = bool(allow_timestamp_free_pnl)
        self._max_price_staleness_ticks = max(int(max_price_staleness_ticks), 1)
        self._last_effective_trend_strength = 0.0
        self._last_edge_score = 0.0
        self._last_regime_change_ts = None
        self._last_valid_vol = float(effective_target_vol)
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
        self._return_ema_base_decay = float(np.clip(self._RETURN_EMA_BASE_DECAY, 1e-6, 0.999999))
        self._valid_return_count = 0
        self._first_valid_return_ts = None
        self._posterior_update_count = 0
        self._first_posterior_ts = None
        self._shock_warmup_ticks = max(int(shock_warmup_ticks), 1)
        self._shock_warmup_seconds = max(float(shock_warmup_seconds), 1.0)
        self._shock_startup_multiplier = float(np.clip(shock_startup_multiplier, 1.0, self._VOL_SHOCK_MULTIPLIER))
        self._shock_startup_vol_floor_mult = float(np.clip(shock_startup_vol_floor_mult, 0.1, 1.5))
        self._MAX_DT = 60.0
        self._regime_persistence = 0
        self._REGIME_CONFIRMATION_TICKS = 2
        self._lock = threading.RLock()
        self._weights_loaded = False
        self._calibration_valid = False
        self._production_valid = False
        self._research_mode = str(os.environ.get("REGIME_RESEARCH_MODE", "")).strip().lower() in {"1", "true", "yes", "on"}
        self._calibration_status = "uncalibrated"
        self._calibration_provenance: Dict[str, Any] = {}
        self._weights_checksum = ""
        self._igarch_hard_limit = 1.05
        self._weight_path = os.environ.get("REGIME_WEIGHT_PATH", "weights/advanced_regime_weights.npz")
        self._require_calibrated_weights = True
        self._regime_smoother = RegimeMarkovSmoother()
        self._regime_state_probs = np.ones(4, dtype=float) / 4.0
        self._last_valid_sjm_probs: np.ndarray | None = None

        # ==========================================
        # 🚨 RISK STATE TRACKING
        # ==========================================
        self._equity_peak = 1.0
        self._equity = 1.0
        self._drawdown = 0.0
        self._cumulative_drawdown = 0.0
        self._loss_streak = 0
        self._shock_memory = 0.0
        self._return_ema = 0.0
        self._abs_return_ema = 0.0
        self._obs_controller = ObservabilityController() if ObservabilityController is not None else None

        self._circuit_breaker_active = False
        self._circuit_breaker_reason = None
        self._circuit_breaker_trigger_tick = -1
        # FIX-7: portfolio-DD tracking (driven externally by report_realized_pnl)
        self._portfolio_peak_equity = float("nan")
        self._portfolio_drawdown = 0.0
        # FIX-6 (consumer): calibration-time feature normalisation moments.
        # Populated by _load_model_weights when present in the .npz; otherwise
        # remain None and _normalize_features falls back to an identity pass.
        self._feature_mean: Optional[np.ndarray] = None
        self._feature_std:  Optional[np.ndarray] = None
        self._feature_norm_source: str = "rolling"
        self._healing_counter = 0
        self._last_healing_action = "NONE"
        self._cb_trigger_history: "deque[tuple[float, str, float]]" = deque(maxlen=50)
        self._last_healing_error = None
        self._last_healing_context = {}
        self._healing_count = 0

        # Warning de-duplication / rate limiting.
        self._warning_last_emitted: "OrderedDict[str, float]" = OrderedDict()
        self._warning_first_seen: "OrderedDict[str, float]" = OrderedDict()
        self._warning_counts: Dict[str, int] = {}
        self._warning_lock = threading.RLock()
        self._last_health = "OK"
        self._determinism_status = "OK"
        self._determinism_had_failure = False
        self._warning_drop_count = 0
        self._warning_drop_alerted = False
        self._warning_backend_failure_count = 0
        self._background_workers_enabled = bool(enable_background_workers)

        if engine_id is not None:
            self.engine_id = str(engine_id)
        else:
            stable_source = json.dumps(
                {
                    "n_states": int(n_states),
                    "n_features": int(n_features),
                    "target_vol_key": (
                        float(target_vol) if target_vol is not None else "target_vol:default"
                    ),
                    "use_calibrated_target_vol_default": bool(self._use_calibrated_target_vol_default),
                    "allow_igarch": bool(allow_igarch),
                    "regime_prob_floor": float(self.garch._REGIME_PROB_FLOOR),
                    "seed": self._rng_seed,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            stable_hash = hashlib.sha256(stable_source.encode("utf-8")).hexdigest()[:16]
            self.engine_id = f"engine_{stable_hash}"
        effective_engine_id = self.engine_id
        with _PROMETHEUS_LOCK:
            if self.engine_id not in _prometheus_engine_ids:
                if len(_prometheus_engine_ids) >= _PROMETHEUS_ENGINE_ID_LIMIT:
                    LOGGER.warning(
                        "prometheus: engine_id cardinality limit reached (%d). Metrics for engine '%s' will use 'overflow' label.",
                        _PROMETHEUS_ENGINE_ID_LIMIT,
                        self.engine_id,
                    )
                    effective_engine_id = "overflow"
                else:
                    _prometheus_engine_ids.add(self.engine_id)
        self._metrics_engine_id = effective_engine_id

        # ==========================================
        # NEW: Async Warning Queue (Non-blocking I/O)
        # ==========================================
        self._warning_queue: "queue.Queue[str]" = queue.Queue(maxsize=10000)
        self._warning_stop_event = threading.Event()
        self_weakref = weakref.ref(self)
        self._warning_worker = None
        self._warning_finalizer = None
        if self._background_workers_enabled:
            self._warning_worker = threading.Thread(
                target=AdvancedRegimeEngine._warning_emitter_loop,
                args=(self_weakref, self._warning_stop_event, self._warning_queue),
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
                AdvancedRegimeEngine._shutdown_worker,
                self._warning_stop_event,
                self._warning_queue,
                self._warning_worker,
            )
        self._snapshot_queue: "queue.Queue[Dict[str, Any] | None]" = queue.Queue(
            maxsize=int(self._SNAPSHOT_QUEUE_MAXSIZE)
        )
        self._snapshot_stop_event = threading.Event()
        self._snapshot_drop_count = 0
        self._snapshot_backend_failure_count = 0
        self._snapshot_worker = None
        self._snapshot_finalizer = None
        if self._background_workers_enabled:
            self._snapshot_worker = threading.Thread(
                target=AdvancedRegimeEngine._snapshot_emitter_loop,
                args=(self_weakref, self._snapshot_stop_event, self._snapshot_queue),
                daemon=True,
                name=f"{self.engine_id}_snapshot_worker"
            )
            try:
                self._snapshot_worker.start()
            except Exception:
                self._snapshot_worker = None
            self._snapshot_finalizer = weakref.finalize(
                self,
                AdvancedRegimeEngine._shutdown_worker,
                self._snapshot_stop_event,
                self._snapshot_queue,
                self._snapshot_worker,
            )

        self._errors_module_available = True
        try:
            from errors import get_error as _get_error  # type: ignore
            self._error_category_resolver = _get_error
        except Exception:
            self._errors_module_available = False
            self._error_category_resolver = None
            if not getattr(self, "_is_replay", False):
                # FIX-L1: downgrade to INFO + once-per-process guard so
                # constructing many ARE instances does not spam the log
                # with the same fallback notice.
                global _ERROR_MAP_WARN_EMITTED
                if not _ERROR_MAP_WARN_EMITTED:
                    msg = (
                        "advanced_regime_engine: errors.get_error unavailable; "
                        "self-healing category mapping running in built-in "
                        "fallback mode."
                    )
                    try:
                        LOGGER.info(msg)
                    except Exception:
                        warnings.warn(msg, RuntimeWarning, stacklevel=2)
                    _ERROR_MAP_WARN_EMITTED = True

        # FIX-27 (M-1): per-reason regime downgrade counter. Incremented at
        # every _build_output() call site that emits execution_mode in
        # {"halt","fail_safe","circuit_breaker"} via _record_regime_downgrade().
        # Exposed verbatim through get_health(). Registered reason codes:
        #   - "microstructure_required_but_missing"
        #   - "uncalibrated_weights"
        #   - "circuit_breaker"
        #   - "nhhmm_warmup"
        self._regime_downgrade_count: Dict[str, int] = {
            "microstructure_required_but_missing": 0,
            "uncalibrated_weights": 0,
            "circuit_breaker": 0,
            "nhhmm_warmup": 0,
            "unspecified": 0,  # FIX-5.3: registry-validated catch-all bucket
        }

        self._obs_counter = 0
        self._OBS_SAMPLE_RATE = 5  # update metrics every N ticks
        self._tick_id = 0
        self._engine_status = "DEGRADED" if getattr(self, "_target_vol_missing_artifact", False) else "OK"
        if getattr(self, "_target_vol_missing_artifact", False):
            LOGGER.critical(
                "[REGIME] Missing valid target-vol calibration artifact at %s; using literal fallback target_vol=0.02.",
                getattr(self, "_target_vol_artifact_path", DEFAULT_TARGET_VOL_ARTIFACT_PATH),
            )
        self._health_status = "OK"
        self._last_heal_ts = None
        self._just_restored = False
        self._last_signed_return = 0.0
        self._regime_suppression_log: list = []
        self._regime_audit_log: list = []
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
        if bool(load_model_weights_on_init):
            self._load_model_weights()

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
        safe_payload = {
            "source_engine_id": str(getattr(self, "engine_id", "UNKNOWN")),
            "source_tick_id": int(getattr(self, "_tick_id", -1)),
            "source_time_s": float(getattr(self, "_last_update_ts", 0.0)),
        }
        if isinstance(payload, dict):
            for k, v in payload.items():
                try:
                    if isinstance(v, (int, float, str, bool, type(None))):
                        safe_payload[k] = v
                    else:
                        safe_payload[k] = copy.deepcopy(v)
                except Exception:
                    safe_payload[k] = {"__unserializable__": type(v).__name__}
        try:
            replay_engine.record_event(event_type, safe_payload)
        except Exception as exc:
            self._warn_rate_limited("replay_record_failure", f"Replay record failed: {exc}", cooldown_s=30.0)

    def _shutdown_warning_worker(self) -> None:
        AdvancedRegimeEngine._shutdown_worker(
            getattr(self, "_warning_stop_event", None),
            getattr(self, "_warning_queue", None),
            getattr(self, "_warning_worker", None),
        )

    def _shutdown_snapshot_worker(self) -> None:
        AdvancedRegimeEngine._shutdown_worker(
            getattr(self, "_snapshot_stop_event", None),
            getattr(self, "_snapshot_queue", None),
            getattr(self, "_snapshot_worker", None),
        )

    @staticmethod
    def _shutdown_worker(
        stop_event: threading.Event | None,
        warning_queue: "queue.Queue[str] | None",
        worker: threading.Thread | None,
    ) -> None:
        """
        Best-effort shutdown path to flush queued warnings before process exit.
        """
        if stop_event is None or worker is None or warning_queue is None:
            return

        stop_event.set()
        try:
            warning_queue.put_nowait(None)
        except queue.Full as _swallowed_exc:
            LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)

        if worker.is_alive():
            worker.join(timeout=1.0)

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
        # Timestamp normalization is intentionally strict: seconds only.
        return ts_f

    def _set_regime_change_timestamp(self, current_ts: float | None) -> None:
        ts_norm = self._normalize_timestamp(current_ts)
        if ts_norm is None:
            self._warn_rate_limited(
                key="invalid_regime_change_timestamp",
                message="Regime change timestamp missing/invalid; preserving previous cooldown anchor.",
                cooldown_s=30.0,
            )
            self._obs_observe("regime_change_timestamp_invalid", "medium", {"timestamp": current_ts})
            return
        self._last_regime_change_ts = float(ts_norm)

    def _validate_regime_label(self, value: Any, field: str) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and value in self._VALID_REGIME_LABELS:
            return value
        self._log_state_load_issue(field, ValueError(f"invalid label: {value}"), value)
        return None

    def _validate_directional_label(self, value: Any, field: str) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and value in self._VALID_DIRECTIONAL_LABELS:
            return value
        self._log_state_load_issue(field, ValueError(f"invalid directional label: {value}"), value)
        return None

    @staticmethod
    def _coerce_finite_scalar(value: Any, *, default: float = 0.0) -> float:
        return _safe_float(value, default=float(default))

    def _normalize_mtf_weights(self, mtf_weights: Dict[str, float] | None) -> Dict[str, float]:
        if mtf_weights is None:
            return {}
        if not isinstance(mtf_weights, dict):
            raise ValueError("mtf_weights must be a dict of timeframe -> positive weight")
        normalized: Dict[str, float] = {}
        for key, raw_weight in mtf_weights.items():
            tf = str(key)
            if tf == "base":
                try:
                    LOGGER.warning("Ignoring mtf_weights['base']; base is anchor-only and not a fusion candidate.")
                except Exception as _swallowed_exc:
                    LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)
                continue
            weight = _safe_float(raw_weight, default=np.nan)
            if not np.isfinite(weight) or weight <= 0.0:
                try:
                    LOGGER.warning("Ignoring non-positive/invalid MTF weight for '%s': %r", tf, raw_weight)
                except Exception as _swallowed_exc:
                    LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)
                continue
            normalized[tf] = float(weight)
        return normalized

    def _validate_sjm_reserved_feature_indices(
        self,
        value: tuple[int, int] | None,
    ) -> tuple[int, int] | None:
        if value is None:
            return None
        if not isinstance(value, tuple) or len(value) != 2:
            raise ValueError("sjm_reserved_feature_indices must be a tuple (return_idx, abs_return_idx)")
        ret_idx = int(value[0])
        abs_idx = int(value[1])
        if ret_idx == abs_idx:
            raise ValueError("sjm_reserved_feature_indices must reference two distinct indices")
        if not (0 <= ret_idx < self.n_features) or not (0 <= abs_idx < self.n_features):
            raise ValueError(
                f"sjm_reserved_feature_indices out of bounds for n_features={self.n_features}: {value}"
            )
        return ret_idx, abs_idx

    @staticmethod
    def _parse_strict_return(value: Any) -> tuple[bool, float, str]:
        try:
            parsed = float(value)
        except Exception:
            return False, 0.0, "non_numeric"
        if not np.isfinite(parsed):
            return False, 0.0, "non_finite"
        return True, float(parsed), ""

    def _update_timestamp_anchor(self, current_ts: float | None) -> None:
        if current_ts is not None:
            self._last_timestamp = float(current_ts)

    def _set_price_anchor(self, price: float, timestamp: float | None, tick_id: int) -> tuple[bool, str]:
        """Atomically set price anchor components used by PnL reconciliation."""
        inferred_mode = "TIMESTAMP" if timestamp is not None else "TICK"
        current_mode = getattr(self, "_pnl_mode", None)
        if current_mode is None:
            self._pnl_mode = inferred_mode
        elif current_mode != inferred_mode:
            stale_switch = (
                (current_mode == "TICK" and inferred_mode == "TIMESTAMP")
                or (
                    current_mode == "TIMESTAMP"
                    and inferred_mode == "TICK"
                    and bool(self._allow_timestamp_free_pnl)
                )
            )
            if stale_switch:
                self._warn_rate_limited(
                    key="pnl_mode_recovered",
                    message=f"Recovering pnl mode from {current_mode} to {inferred_mode}",
                    cooldown_s=30.0,
                )
                self._pnl_mode = inferred_mode
            else:
                return False, f"PNL_MODE_SWITCH:{current_mode}->{inferred_mode}"
        self._last_price = float(price)
        self._last_price_timestamp = None if timestamp is None else float(timestamp)
        self._last_price_tick_id = int(tick_id)
        return True, "OK"

    def _record_valid_return(self, current_ts: float | None) -> None:
        self._valid_return_count = int(getattr(self, "_valid_return_count", 0)) + 1
        if self._first_valid_return_ts is None and current_ts is not None:
            self._first_valid_return_ts = float(current_ts)

    def _record_posterior_update(self, current_ts: float | None) -> None:
        self._posterior_update_count = int(getattr(self, "_posterior_update_count", 0)) + 1
        if self._first_posterior_ts is None and current_ts is not None:
            self._first_posterior_ts = float(current_ts)

    def _is_confidence_collapse_warmup(self, current_ts: float | None) -> bool:
        count_progress = float(np.clip(
            float(getattr(self, "_posterior_update_count", 0))
            / max(float(self._CONF_COLLAPSE_WARMUP_UPDATES), 1.0),
            0.0,
            1.0,
        ))
        time_progress = 0.0
        if self._first_posterior_ts is not None and current_ts is not None:
            elapsed = max(float(current_ts) - float(self._first_posterior_ts), 0.0)
            time_progress = float(np.clip(
                elapsed / max(float(self._CONF_COLLAPSE_WARMUP_SECONDS), 1.0),
                0.0,
                1.0,
            ))
        blended_progress = count_progress
        if self._first_posterior_ts is not None and current_ts is not None:
            blended_progress = 0.7 * count_progress + 0.3 * time_progress
        blended_progress = float(np.clip(blended_progress, 0.0, 1.0))
        return blended_progress < 1.0

    def _normalize_determinism_status(self, raw_status: Any) -> str:
        candidate = str(raw_status).strip().upper() if raw_status is not None else "OK"
        if candidate in self._VALID_DETERMINISM_STATUS:
            return candidate
        return "OK"

    def _mark_determinism_failure(self) -> None:
        self._determinism_status = "RNG_RESTORE_FAILED"
        self._determinism_had_failure = True

    def _mark_determinism_success(self) -> None:
        self._determinism_status = "OK_WITH_HISTORY" if getattr(self, "_determinism_had_failure", False) else "OK"

    def _pnl_staleness_policy(self, current_ts: float | None) -> tuple[bool, bool, str]:
        """
        Returns (policy_allows_pnl, stale_price, reason).
        Deterministic policy:
        - Mode is locked on first anchor (TIMESTAMP or TICK) and cannot switch mid-run.
        - TIMESTAMP mode requires valid timestamp gap.
        - TICK mode requires deterministic tick-gap freshness.
        - If timestamp-free mode is disabled and timestamp gap cannot be evaluated, block PnL explicitly.
        """
        mode = getattr(self, "_pnl_mode", None)
        if mode not in ("TIMESTAMP", "TICK"):
            return False, True, "PNL_MODE_INVALID"
        last_ts = self._last_price_timestamp
        if mode == "TIMESTAMP" and current_ts is not None and last_ts is not None:
            stale_gap = float(current_ts - last_ts)
            stale = stale_gap < 0.0 or stale_gap > self._MAX_PRICE_STALENESS_SEC
            return True, stale, "TIMESTAMP_GAP"
        if mode == "TIMESTAMP":
            return False, True, "PNL_MODE_CONFLICT"
        prev_anchor_tick = self._last_price_tick_id
        if prev_anchor_tick is None:
            return True, True, "TICK_ANCHOR_MISSING"
        if int(self._tick_id) <= int(prev_anchor_tick):
            return False, True, "TICK_ORDER_VIOLATION"
        tick_gap = int(self._tick_id - prev_anchor_tick)
        stale = tick_gap <= 0 or tick_gap > self._max_price_staleness_ticks
        return True, stale, "TICK_GAP"

    def _warmup_progress(self, current_ts: float | None) -> float:
        tick_progress = float(np.clip(
            float(getattr(self, "_valid_return_count", 0)) / max(float(self._shock_warmup_ticks), 1.0),
            0.0,
            1.0,
        ))
        time_progress = 0.0
        first_ts = getattr(self, "_first_valid_return_ts", None)
        if first_ts is not None and current_ts is not None:
            elapsed = max(float(current_ts) - float(first_ts), 0.0)
            time_progress = float(np.clip(elapsed / max(float(self._shock_warmup_seconds), 1.0), 0.0, 1.0))
        return min(tick_progress, time_progress)

    def _shock_threshold(self, baseline_vol: float, current_ts: float | None) -> tuple[float, float]:
        warmup = self._warmup_progress(current_ts)
        # FIX-5.4: surface NHHMM warmup as a regime downgrade reason whenever
        # the warmup fraction is below 1.0 (i.e. the engine is still in its
        # initial post-construction stabilisation window). Fires at most once
        # per tick because _shock_threshold is called once per tick.
        if warmup < 1.0:
            try:
                self._record_regime_downgrade("nhhmm_warmup")
            except Exception as _swallowed_exc:
                LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)
        floor_mult = (
            self._shock_startup_vol_floor_mult
            + (1.5 - self._shock_startup_vol_floor_mult) * warmup
        )
        shock_vol_basis = max(
            float(baseline_vol),
            float(getattr(self, "_last_valid_vol", self.garch.target_vol)),
            float(self.garch.target_vol) * float(floor_mult),
            1e-8,
        )
        shock_multiplier = (
            self._shock_startup_multiplier
            + (self._VOL_SHOCK_MULTIPLIER - self._shock_startup_multiplier) * warmup
        )
        return float(shock_multiplier * shock_vol_basis), float(shock_vol_basis)

    def _ema_decay(self, dt: float) -> float:
        dt_safe = float(np.clip(dt, 0.0, self._MAX_EMA_GAP_DT))
        decay = float(np.clip(self._return_ema_base_decay ** dt_safe, 1e-9, 1.0))
        if float(dt) > float(self._MAX_EMA_GAP_DT):
            LOGGER.warning("[EMA] Gap of %.1fs exceeds max — decay clamped, data may be stale", float(dt))
        return decay

    def _resolve_canonical_return(
        self,
        market_data: Dict[str, Any],
        mtf_data: Any,
    ) -> tuple[bool, float, str]:
        if mtf_data is not None:
            if not isinstance(mtf_data, dict):
                return False, 0.0, "mtf_not_dict"
            base_payload = mtf_data.get("base", None)
            if not isinstance(base_payload, dict):
                return False, 0.0, "mtf_base_missing_or_invalid"
            ok, base_ret, reason = self._parse_strict_return(base_payload.get("return", None))
            if not ok:
                return False, 0.0, f"mtf_base_return_{reason}"
            top_ret = market_data.get("return", None)
            if top_ret is not None:
                top_ok, top_val, _ = self._parse_strict_return(top_ret)
                if top_ok and abs(top_val - base_ret) > self._CANONICAL_RETURN_MISMATCH_TOLERANCE:
                    self._warn_rate_limited(
                        key="mtf_top_level_return_mismatch",
                        message=(
                            f"Top-level return ({top_val:.12g}) differs from mtf.base return "
                            f"({base_ret:.12g}); using mtf.base as canonical return."
                        ),
                        cooldown_s=30.0,
                    )
                    self._obs_observe(
                        "mtf_top_level_return_mismatch",
                        "medium",
                        {"top_return": top_val, "base_return": base_ret},
                    )
            return True, float(base_ret), "mtf_base"
        ok, single_ret, reason = self._parse_strict_return(market_data.get("return", None))
        if not ok:
            return False, 0.0, f"single_return_{reason}"
        return True, float(single_ret), "single"

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
    def _increment_warning_backend_failure_count(self) -> None:
        with self._warning_lock:
            self._warning_backend_failure_count = int(
                getattr(self, "_warning_backend_failure_count", 0)
            ) + 1

    def _get_warning_backend_failure_count(self) -> int:
        with self._warning_lock:
            return int(getattr(self, "_warning_backend_failure_count", 0))

    @staticmethod
    def _emit_warning_with_timeout(message: str, timeout_s: float = 1.0) -> bool:
        # Timeout parameter retained for API compatibility. Warning emission is
        # now single-threaded in the dedicated worker to prevent unbounded
        # per-warning thread creation under log-backend stalls.
        _ = timeout_s
        LOGGER.warning(message)
        return True

    @staticmethod
    def _warning_emitter_loop(
        engine_ref: "weakref.ReferenceType[AdvancedRegimeEngine]",
        stop_event: threading.Event,
        warning_queue: "queue.Queue[str]",
    ) -> None:
        """
        Dedicated background thread for warning emission.
        Ensures logging I/O never blocks trading execution threads.
        """
        while True:
            engine = engine_ref()
            if engine is None:
                # Engine is already gone: drain and discard queued warnings without
                # spawning emitter threads or touching logging backends.
                while not warning_queue.empty():
                    try:
                        warning_queue.get_nowait()
                    except queue.Empty:
                        break
                break

            if stop_event.is_set() and warning_queue.empty():
                break

            try:
                msg = warning_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if msg is None:
                if stop_event.is_set() and warning_queue.empty():
                    break
                continue

            try:
                emitted = AdvancedRegimeEngine._emit_warning_with_timeout(msg, timeout_s=1.0)
                if not emitted:
                    engine._increment_warning_backend_failure_count()
                    try:
                        warnings.warn(
                            "LOGGER.warning timeout in background worker; message emission skipped.",
                            RuntimeWarning,
                            stacklevel=1,
                        )
                    except Exception as _swallowed_exc:
                        LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)
            except Exception:
                engine._increment_warning_backend_failure_count()
                try:
                    warnings.warn(msg, RuntimeWarning, stacklevel=1)
                except Exception as _swallowed_exc:
                    LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)

    def _resolve_provenance_path(self) -> str:
        explicit = os.environ.get("REGIME_PROVENANCE_PATH")
        if explicit:
            return explicit
        weight_dir = os.path.dirname(os.path.abspath(self._weight_path)) or "."
        return os.path.join(weight_dir, "calibration_provenance.json")

    def _load_calibration_provenance(self) -> Dict[str, Any]:
        provenance_path = self._resolve_provenance_path()
        with open(provenance_path, "r", encoding="utf-8") as fh:
            provenance = json.load(fh)
        if not isinstance(provenance, dict):
            raise ValueError("provenance_not_object")
        return provenance

    def _is_signal_permitted(self) -> bool:
        return bool(
            getattr(self, "_weights_loaded", False)
            and getattr(self, "_calibration_valid", False)
            and (getattr(self, "_production_valid", False) or getattr(self, "_research_mode", False))
        )

    def _load_model_weights(self) -> None:
        weights = ModelWeightManager.load_weights("advanced_regime", self._weight_path)
        if not weights:
            self._weights_loaded = False
            self._calibration_valid = False
            self._production_valid = False
            self._calibration_provenance = {}
            self._calibration_status = "missing"
            self._engine_status = "DEGRADED"
            msg = f"[REGIME] Missing trained weights at {self._weight_path}; blocking regime engine until calibration artifacts are available."
            LOGGER.critical(msg)
            return
        try:
            canonical_blob = json.dumps({k: np.asarray(v).tolist() for k, v in weights.items()}, sort_keys=True, separators=(",", ":"))
            self._weights_checksum = hashlib.sha256(canonical_blob.encode("utf-8")).hexdigest()

            required = ("nhhmm_beta", "nhhmm_mu", "nhhmm_sigma", "sjm_centroids")
            missing = [k for k in required if k not in weights]
            if missing:
                raise ValueError(f"missing_weight_keys:{missing}")

            beta = np.asarray(weights["nhhmm_beta"], dtype=float)
            mu = np.asarray(weights["nhhmm_mu"], dtype=float)
            sigma = np.asarray(weights["nhhmm_sigma"], dtype=float)
            if beta.ndim != 3 or mu.ndim != 1 or sigma.ndim != 1:
                raise ValueError("invalid_nhhmm_weight_shapes")
            if beta.shape != (self.K, self.K, self.n_features):
                raise ValueError(
                    f"invalid nhhmm_beta shape={beta.shape}; expected ({self.K}, {self.K}, {self.n_features})"
                )
            if mu.shape != (self.K,) or sigma.shape != (self.K,):
                raise ValueError(
                    f"invalid nhhmm_mu/sigma shapes: mu={mu.shape}, sigma={sigma.shape}; expected ({self.K},)"
                )
            self.nhhmm.load_weights(beta, mu, sigma)

            means = np.asarray(weights["sjm_centroids"], dtype=float)
            if means.ndim != 2 or means.shape[1] != self.n_features:
                raise ValueError(
                    f"invalid sjm_centroids shape={means.shape}; expected (*, {self.n_features})"
                )
            raw_w = weights.get("sjm_feature_weights", np.ones(means.shape[1], dtype=float))
            w = np.asarray(raw_w, dtype=float).reshape(-1)
            if w.shape[0] != means.shape[1]:
                raise ValueError(
                    f"invalid sjm_feature_weights shape={w.shape}; expected ({means.shape[1]},)"
                )
            if not np.all(np.isfinite(w)) or float(np.sum(np.abs(w))) <= 0.0:
                raise ValueError("sjm_feature_weights must be finite and non-zero")
            self.sjm.load_weights(means, w)

            # FIX-6 (consumer): consume calibration-time normalisation moments
            # if the .npz includes them (saver in calibrate_regime.py writes
            # feature_mean(3,) and feature_std(3,)). Falls back gracefully on
            # legacy artefacts.
            if "feature_mean" in weights and "feature_std" in weights:
                fm = np.asarray(weights["feature_mean"], dtype=np.float64)
                fs = np.asarray(weights["feature_std"],  dtype=np.float64)
                # guard against zero-std degeneracy (calibration on flat feature)
                fs = np.where(fs > 1e-12, fs, 1.0)
                self._feature_mean = fm
                self._feature_std  = fs
                self._feature_norm_source = "calibrated"
            else:
                self._feature_mean = None
                self._feature_std  = None
                self._feature_norm_source = "rolling"

            self._weights_loaded = True
            try:
                provenance = self._load_calibration_provenance()
                production_valid = bool(provenance.get("production_valid", False))
                data_source = str(provenance.get("data_source", "")).strip().lower()
                if data_source == "synthetic":
                    production_valid = False
                self._calibration_valid = True
                self._production_valid = bool(production_valid)
                self._calibration_provenance = dict(provenance)
                if self._production_valid:
                    self._calibration_status = "calibrated"
                elif self._research_mode:
                    self._calibration_status = "research"
                else:
                    self._calibration_status = "not_production_valid"
                    self._engine_status = "DEGRADED"
            except Exception:
                LOGGER.critical("[REGIME] Failed to load calibration provenance", exc_info=True)
                self._calibration_valid = False
                self._production_valid = False
                self._calibration_provenance = {}
                self._calibration_status = "invalid_provenance"
                self._engine_status = "DEGRADED"
        except Exception:
            LOGGER.critical("[REGIME] Failed to load trained weights", exc_info=True)
            self._weights_loaded = False
            self._calibration_valid = False
            self._production_valid = False
            self._calibration_provenance = {}
            self._calibration_status = "invalid"
            self._engine_status = "DEGRADED"
            return

    def _normalize_features(self, x: np.ndarray) -> np.ndarray:
        """FIX-6 (consumer): apply calibration-time normalisation when
        available. When no calibrated moments are loaded, returns ``x``
        unchanged (the engine's downstream paths perform their own scaling).
        Always returns a finite ndarray; NaN/Inf inputs propagate as-is so
        upstream fail-closed checks can detect them.
        """
        arr = np.asarray(x, dtype=np.float64)
        if (getattr(self, "_feature_mean", None) is not None
                and getattr(self, "_feature_std", None) is not None):
            try:
                return (arr - self._feature_mean) / self._feature_std
            except Exception:
                return arr
        return arr

    @staticmethod
    def _snapshot_emitter_loop(
        engine_ref: "weakref.ReferenceType[AdvancedRegimeEngine]",
        stop_event: threading.Event,
        snapshot_queue: "queue.Queue[Dict[str, Any] | None]",
    ) -> None:
        while True:
            engine = engine_ref()
            if engine is None:
                while not snapshot_queue.empty():
                    try:
                        snapshot_queue.get_nowait()
                    except queue.Empty:
                        break
                break

            if stop_event.is_set() and snapshot_queue.empty():
                break

            try:
                snapshot_payload = snapshot_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if snapshot_payload is None:
                if stop_event.is_set() and snapshot_queue.empty():
                    break
                continue

            try:
                replay_engine = getattr(engine, "_replay_engine", None)
                if replay_engine is None:
                    continue
                acquired = engine._lock.acquire(blocking=False)
                if not acquired:
                    engine._snapshot_drop_count = int(
                        getattr(engine, "_snapshot_drop_count", 0)
                    ) + 1
                    if engine is not None:
                        try:
                            engine._warn_rate_limited(
                                "snapshot_lock_contention",
                                "Snapshot dropped: _lock held by update() thread.",
                                cooldown_s=30.0,
                            )
                        except Exception as _swallowed_exc:
                            LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)
                    continue
                try:
                    materialized_payload = engine._materialize_snapshot_payload(snapshot_payload)
                    hash_payload = dict(materialized_payload)
                    hash_payload.pop("state_hash", None)
                    hash_payload.pop("_checksum", None)
                    materialized_payload["state_hash"] = engine._state_hash(hash_payload)
                    replay_engine_local = replay_engine
                finally:
                    engine._lock.release()

                try:
                    replay_engine_local.snapshot(materialized_payload)
                except Exception as exc:
                    engine._snapshot_backend_failure_count = int(
                        getattr(engine, "_snapshot_backend_failure_count", 0)
                    ) + 1
                    engine._warn_rate_limited(
                        "snapshot_emit_failure",
                        f"Snapshot emission failed: {exc}",
                        cooldown_s=30.0,
                    )
            except Exception:
                continue

    def _enqueue_snapshot(self, snapshot_payload: Dict[str, Any]) -> None:
        try:
            self._snapshot_queue.put_nowait(snapshot_payload)
        except queue.Full:
            self._snapshot_drop_count = int(getattr(self, "_snapshot_drop_count", 0)) + 1
            self._warn_rate_limited(
                "snapshot_queue_saturated",
                "Snapshot queue saturated; dropping snapshot payload.",
                cooldown_s=30.0,
            )

    def _capture_snapshot_payload_unlocked(self, regime: str) -> Dict[str, Any]:
        return {
            "regime": str(regime),
            "state_raw": self._capture_state_raw_unlocked(),
        }

    def _materialize_snapshot_payload(self, snapshot_payload: Dict[str, Any]) -> Dict[str, Any]:
        raw_state = snapshot_payload.get("state_raw", {})
        if not isinstance(raw_state, dict):
            raise ValueError("snapshot payload missing state_raw")
        def _raw_get(key: str, default: Any) -> Any:
            if key not in raw_state:
                LOGGER.warning("materialize_payload: '%s' missing from snapshot, using fallback default.", key)
            return raw_state.get(key, default)
        engine_state = self._materialize_state_from_raw(raw_state)
        garch_prob = np.asarray(_raw_get("garch_prob", np.ones(2) / 2.0), dtype=float)
        nhhmm_prior = np.asarray(_raw_get("nhhmm_prior", np.ones(self.K) / self.K), dtype=float)
        smoothed_garch_prob = np.asarray(_raw_get("smoothed_garch_prob", np.ones(2) / 2.0), dtype=float)
        regime_state_probs = np.asarray(_raw_get("regime_state_probs", np.ones(4) / 4.0), dtype=float)
        last_valid_sjm_probs = _raw_get("last_valid_sjm_probs", None)
        garch_var = np.asarray(_raw_get("garch_var", np.array([self.garch.target_vol ** 2] * 2)), dtype=float)
        rng_state = _raw_get("engine_rng_state", None)
        rng_type = _raw_get("engine_rng_type", None)
        rng_module = _raw_get("engine_rng_module", None)
        normalized_rng = None
        if rng_state is not None:
            normalized_rng = {
                "bit_generator": rng_type,
                "bit_generator_module": rng_module,
                "numpy_version": np.__version__,
                "internal_state": self._canonicalize(rng_state),
            }
        return {
            "engine_state": engine_state,
            "regime": str(snapshot_payload.get("regime", "UNKNOWN")),
            "equity": float(_raw_get("equity", 0.0)),
            "drawdown": float(_raw_get("drawdown", 0.0)),
            "loss_streak": int(_raw_get("loss_streak", 0)),
            "garch_prob": garch_prob.tolist(),
            "nhhmm_prior": nhhmm_prior.tolist(),
            "smoothed_garch_prob": smoothed_garch_prob.tolist(),
            "regime_state_probs": regime_state_probs.tolist(),
            "last_valid_sjm_probs": (
                None
                if last_valid_sjm_probs is None
                else np.asarray(last_valid_sjm_probs, dtype=float).tolist()
            ),
            "last_effective_trend_strength": float(_raw_get("last_effective_trend_strength", 0.0)),
            "last_edge_score": float(_raw_get("last_edge_score", 0.0)),
            "garch_var": garch_var.tolist(),
            "last_valid_vol": float(_raw_get("last_valid_vol", 0.0)),
            "switch_stability_ema": float(_raw_get("switch_stability_ema", 1.0)),
            "last_timestamp": _raw_get("last_timestamp", None),
            "last_valid_dt": float(_raw_get("last_valid_dt", 1.0)),
            "range_ticks": float(_raw_get("range_ticks", 0.0)),
            "range_ticks_int": int(_raw_get("range_ticks_int", 0)),
            "in_range": bool(_raw_get("in_range", False)),
            "range_anchor_size": float(_raw_get("range_anchor_size", 0.0)),
            "prev_raw_regime": _raw_get("prev_raw_regime", None),
            "last_regime_change_ts": _raw_get("last_regime_change_ts", None),
            "shock_memory": float(_raw_get("shock_memory", 0.0)),
            "return_ema": float(_raw_get("return_ema", 0.0)),
            "abs_return_ema": float(_raw_get("abs_return_ema", 0.0)),
            "last_price": _raw_get("last_price", None),
            "_rng_state": None,
            "_engine_rng_state": rng_state,
            "_engine_rng_type": rng_type,
            "schema_version": self._STATE_VERSION,
            "rng": normalized_rng,
        }


    def _warn_rate_limited(self, key: str, message: str, cooldown_s: float = 30.0) -> bool:
        """
        Emit repeated operational warnings at a controlled rate.
        The event is always counted internally, but the Python warning is only
        emitted once per cooldown window for the same key.
        """
        if getattr(self, "_is_replay", False):
            return False

        now = time.monotonic()
        should_emit = False
        with self._warning_lock:
            self._warning_counts[key] = self._warning_counts.get(key, 0) + 1

            if key not in self._warning_first_seen:
                self._warning_first_seen[key] = now
            else:
                self._warning_first_seen.move_to_end(key)

            last_emitted = self._warning_last_emitted.get(key)
            should_emit = bool(last_emitted is None or (now - last_emitted) >= cooldown_s)
            if should_emit:
                self._warning_last_emitted[key] = now
                self._warning_last_emitted.move_to_end(key)

            if len(self._warning_counts) >= self._WARNING_CACHE_LIMIT:
                target_size = self._WARNING_CACHE_TRIM_TO
                to_remove = max(len(self._warning_counts) - target_size, 0)
                if to_remove > 0:
                    for old_key in list(self._warning_first_seen.keys())[:to_remove]:
                        self._warning_counts.pop(old_key, None)
                        self._warning_last_emitted.pop(old_key, None)
                        self._warning_first_seen.pop(old_key, None)

        if not should_emit:
            return False

        if not self._obs_should_emit_warning(key, cooldown_s):
            return False

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
                except Exception as _swallowed_exc:
                    LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)
            return False
        return True


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

    def _capture_state_raw_unlocked(self) -> Dict[str, Any]:
        raw = {
            "state_version": self._STATE_VERSION,
            "model_signature": f"AdvancedRegimeEngine|v={self._STATE_VERSION}|schema={_OUTPUT_SCHEMA_VERSION}|n_states={self.K}|n_features={self.n_features}",
            "last_valid_sjm_probs": (
                None
                if self._last_valid_sjm_probs is None
                else np.asarray(self._last_valid_sjm_probs, dtype=float).copy()
            ),
            "last_timestamp": None if self._last_timestamp is None else float(self._last_timestamp),
            "last_valid_dt": float(self._last_valid_dt),
            "current_regime_idx": None if self.current_regime_idx is None else int(self.current_regime_idx),
            "last_effective_trend_strength": float(self._last_effective_trend_strength),
            "last_edge_score": float(self._last_edge_score),
            "last_valid_vol": float(self._last_valid_vol),
            "target_vol": float(self.garch.target_vol),
            "switch_stability_ema": float(self._switch_stability_ema),
            "last_regime_change_ts": None if self._last_regime_change_ts is None else float(self._last_regime_change_ts),
            "range_ticks": float(self.range_ticks),
            "range_ticks_int": int(self.range_ticks_int),
            "range_anchor_size": float(self._range_anchor_size),
            "last_signed_position_size": float(self.last_signed_position_size),
            "last_price": None if self._last_price is None else float(self._last_price),
            "last_price_timestamp": None if self._last_price_timestamp is None else float(self._last_price_timestamp),
            "last_price_tick_id": None if self._last_price_tick_id is None else int(self._last_price_tick_id),
            "pnl_mode": self._pnl_mode,
            "allow_timestamp_free_pnl": bool(self._allow_timestamp_free_pnl),
            "max_price_staleness_ticks": int(self._max_price_staleness_ticks),
            "in_range": bool(self._in_range),
            "prev_regime": self._prev_regime,
            "prev_raw_regime": self._prev_raw_regime,
            "confirmed_regime": self._confirmed_regime,
            "confirmed_regime_idx": None if self._confirmed_regime_idx is None else int(self._confirmed_regime_idx),
            "regime_persistence": int(self._regime_persistence),
            "nhhmm_prior": np.asarray(self.nhhmm_prior, dtype=float).copy(),
            "garch_prob": np.asarray(self.garch_prob, dtype=float).copy(),
            "smoothed_garch_prob": np.asarray(self._smoothed_garch_prob, dtype=float).copy(),
            "regime_state_probs": np.asarray(self._regime_state_probs, dtype=float).copy(),
            "regime_smoother_prev_probs": (
                np.asarray(self._regime_smoother.prev_probs, dtype=float).copy()
                if getattr(self, "_regime_smoother", None) is not None
                else np.asarray(self._regime_state_probs, dtype=float).copy()
            ),
            "garch_var": np.asarray(self.garch_var, dtype=float).copy(),
            "circuit_breaker_active": bool(self._circuit_breaker_active),
            "circuit_breaker_reason": self._circuit_breaker_reason,
            "circuit_breaker_trigger_tick": int(getattr(self, "_circuit_breaker_trigger_tick", -1)),
            "cb_trigger_history": list(getattr(self, "_cb_trigger_history", [])),
            "equity": float(self._equity),
            "equity_peak": float(self._equity_peak),
            "drawdown": float(self._drawdown),
            "cumulative_drawdown": float(getattr(self, "_cumulative_drawdown", self._drawdown)),
            "loss_streak": int(self._loss_streak),
            "healing_count": int(getattr(self, "_healing_count", 0)),
            "last_healing_action": str(getattr(self, "_last_healing_action", "NONE")),
            "last_healing_error": getattr(self, "_last_healing_error", None),
            "last_healing_context": copy.deepcopy(getattr(self, "_last_healing_context", {})),
            "shock_memory": float(self._shock_memory),
            "return_ema": float(self._return_ema),
            "abs_return_ema": float(self._abs_return_ema),
            "valid_return_count": int(getattr(self, "_valid_return_count", 0)),
            "first_valid_return_ts": (
                None if self._first_valid_return_ts is None else float(self._first_valid_return_ts)
            ),
            "posterior_update_count": int(getattr(self, "_posterior_update_count", 0)),
            "first_posterior_ts": (
                None if self._first_posterior_ts is None else float(self._first_posterior_ts)
            ),
            "shock_warmup_ticks": int(self._shock_warmup_ticks),
            "shock_warmup_seconds": float(self._shock_warmup_seconds),
            "shock_startup_multiplier": float(self._shock_startup_multiplier),
            "shock_startup_vol_floor_mult": float(self._shock_startup_vol_floor_mult),
            "prev_directional_label": getattr(self, "_prev_directional_label", None),
            "rng_seed": self._rng_seed,
            "engine_rng_state": (
                dict(self._rng.bit_generator.state)
                if getattr(self, "_rng", None) is not None else None
            ),
            "engine_rng_type": (
                type(self._rng.bit_generator).__name__
                if getattr(self, "_rng", None) is not None else None
            ),
            "engine_rng_module": (
                type(self._rng.bit_generator).__module__
                if getattr(self, "_rng", None) is not None else None
            ),
            "confidence_collapse_streak": int(getattr(self, "_confidence_collapse_streak", 0)),
            "determinism_status": str(getattr(self, "_determinism_status", "OK")),
            "determinism_had_failure": bool(getattr(self, "_determinism_had_failure", False)),
            "engine_status": str(getattr(self, "_engine_status", "OK")),
            "target_vol_calibrated": bool(getattr(self, "_target_vol_calibrated", False)),
            "target_vol_provenance": copy.deepcopy(getattr(self, "_target_vol_provenance", {})),
            "use_calibrated_target_vol_default": bool(getattr(self, "_use_calibrated_target_vol_default", False)),
            "tick_id": int(getattr(self, "_tick_id", 0)),
            # Explicitly mark deprecated field as False to avoid confusion in external systems
            "emit_extended_schema": False,
        }
        try:
            raw["nhhmm_beta"] = self.nhhmm.beta.tolist()
            raw["nhhmm_mu"] = self.nhhmm.mu.tolist()
            raw["nhhmm_sigma"] = self.nhhmm.sigma.tolist()
            for key in ("nhhmm_beta", "nhhmm_mu", "nhhmm_sigma"):
                arr = np.asarray(raw[key], dtype=float)
                if not np.all(np.isfinite(arr)):
                    LOGGER.warning("capture_state: non-finite values in %s, zeroing for safety", key)
                    raw[key] = np.zeros_like(arr).tolist()
        except Exception as exc:
            LOGGER.error("capture_state: failed to serialize nhhmm params: %s", exc)
            raw["nhhmm_beta"] = raw["nhhmm_mu"] = raw["nhhmm_sigma"] = None
        try:
            raw["sjm_means"] = self.sjm.means.tolist() if self.sjm.means is not None else None
            raw["sjm_weights"] = self.sjm.weights.tolist() if self.sjm.weights is not None else None
            raw["sjm_score_scale"] = float(getattr(self.sjm, "_score_scale", 1.0))
            raw["sjm_default_params_initialized"] = bool(getattr(self.sjm, "_default_params_initialized", False))
        except Exception as exc:
            LOGGER.error("capture_state: failed to serialize sjm params: %s", exc)
            raw["sjm_means"] = raw["sjm_weights"] = None
            raw["sjm_score_scale"] = 1.0
            raw["sjm_default_params_initialized"] = False
        return raw

    # ------------------------------------------------------------------
    # FIX-27 (M-1) — regime downgrade telemetry surface.
    # ------------------------------------------------------------------
    def _record_regime_downgrade(self, reason: str) -> None:
        """Increment the per-reason regime downgrade counter.

        Called at every _build_output() call site that emits
        execution_mode in {"halt", "fail_safe", "circuit_breaker"}.
        Fail-soft so observability never breaks the trading path.

        FIX-5.3: unknown reasons are bucketed into ``unspecified`` and a
        rate-limited warning is emitted so typos don't silently create
        new buckets.
        """
        try:
            if not isinstance(reason, str) or not reason:
                reason = "unspecified"
            if reason not in _REGIME_DOWNGRADE_REASONS:
                try:
                    self._warn_rate_limited(
                        key=f"unknown_downgrade_reason_{reason}",
                        message=(
                            f"Unknown downgrade reason '{reason}'; "
                            "bucketing as 'unspecified'"
                        ),
                        cooldown_s=300.0,
                    )
                except Exception as _swallowed_exc:
                    LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)
                reason = "unspecified"
            self._regime_downgrade_count[reason] = (
                self._regime_downgrade_count.get(reason, 0) + 1
            )
            # FIX-5.5: mirror the per-reason tally to a Prometheus gauge so
            # operators can scrape downgrade counts without an RPC into
            # get_health(). Skipped on replay to preserve determinism.
            if _PROM_AVAILABLE and not getattr(self, "_is_replay", False):
                try:
                    REGIME_DOWNGRADE_COUNT.labels(
                        getattr(self, "_metrics_engine_id", "unknown"),
                        reason,
                    ).set(self._regime_downgrade_count[reason])
                except Exception as _swallowed_exc:
                    LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)
        except Exception as _swallowed_exc:
            LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)

    @_synchronized
    def reconcile_drawdown(
        self, realized_dd: float, *, gap_threshold: float = 0.05
    ) -> Dict[str, Any]:
        """FIX-7 (CRITICAL-2): cross-check engine model DD against externally
        reported realised portfolio DD.

        Returns the model DD, realised DD, absolute gap (in pp), a divergence
        flag set when the gap exceeds ``gap_threshold`` (default 5pp), and the
        tick id at reconciliation time. Operators MUST consume this in shadow
        mode and refuse to promote to live while ``divergence`` is True.

        Pure observability — never raises, no side effects.
        """
        try:
            model_dd = float(getattr(self, "_MAX_DRAWDOWN", 0.0))
        except Exception:
            model_dd = 0.0
        try:
            realized = float(realized_dd or 0.0)
        except Exception:
            realized = 0.0
        gap = abs(model_dd - realized)
        return {
            "model_drawdown": model_dd,
            "realized_drawdown": realized,
            "gap_pp": gap,
            "divergence": bool(gap > float(gap_threshold)),
            "reconciled_at_tick": int(getattr(self, "_tick_id", 0)),
        }

    def get_health(self) -> Dict[str, Any]:
        """FIX-27 (M-1): public health snapshot.

        Exposes regime_downgrade_count alongside the existing engine
        status flags so operators can quantify how often (and why) the
        engine is dropping into a degraded execution mode without
        mining the structured logs.
        """
        try:
            _downgrade = dict(getattr(self, "_regime_downgrade_count", {}) or {})
        except Exception:
            _downgrade = {}
        return {
            "engine_status": str(getattr(self, "_engine_status", "OK")),
            "health_status": str(getattr(self, "_health_status", "OK")),
            "determinism_status": str(getattr(self, "_determinism_status", "OK")),
            "circuit_breaker_active": bool(getattr(self, "_circuit_breaker_active", False)),
            "circuit_breaker_reason": getattr(self, "_circuit_breaker_reason", None),
            "weights_loaded": bool(getattr(self, "_weights_loaded", False)),
            "tick_id": int(getattr(self, "_tick_id", 0)),
            "healing_count": int(getattr(self, "_healing_count", 0)),
            "regime_downgrade_count": _downgrade,
            # UPGRADE-5.7: surface the FIX-6 normalisation provenance so
            # operators can verify at a glance whether a deployed engine is
            # using calibrated or rolling-stats feature normalisation.
            "feature_norm_source": str(getattr(self, "_feature_norm_source", "rolling")),
            "target_vol": float(getattr(getattr(self, "garch", None), "target_vol", 0.02)),
            "target_vol_calibrated": bool(getattr(self, "_target_vol_calibrated", False)),
            "target_vol_provenance": copy.deepcopy(getattr(self, "_target_vol_provenance", {})),
            "use_calibrated_target_vol_default": bool(getattr(self, "_use_calibrated_target_vol_default", False)),
        }

    @staticmethod
    def _materialize_state_from_raw(raw_state: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(raw_state)
        out.pop("engine_rng_type", None)
        out.pop("engine_rng_module", None)
        for key in (
            "last_valid_sjm_probs",
            "nhhmm_prior",
            "garch_prob",
            "smoothed_garch_prob",
            "regime_state_probs",
            "regime_smoother_prev_probs",
            "garch_var",
        ):
            arr = out.get(key, None)
            if arr is None:
                continue
            out[key] = np.asarray(arr, dtype=float).tolist()
        return out

    def _get_state_unlocked(self) -> Dict[str, Any]:
        return self._materialize_state_from_raw(self._capture_state_raw_unlocked())

    @_synchronized
    def get_state(self) -> Dict[str, Any]:
        return self._get_state_unlocked()

    @_synchronized
    def recalibrate_target_vol(self, returns, timestamps, **kwargs) -> Dict[str, Any]:
        """Calibrate, persist, and hot-swap target volatility for future ticks only."""
        path = kwargs.pop(
            "path",
            getattr(self, "_target_vol_artifact_path", DEFAULT_TARGET_VOL_ARTIFACT_PATH),
        )
        before = float(self.garch.target_vol)
        result = calibrate_target_vol(returns, timestamps, **kwargs)
        write_target_vol_artifact(result, path=path)
        after = float(result["calibrated_target_vol"])
        self.garch.target_vol = after
        self._init_params["target_vol"] = after
        self._init_params["target_vol_artifact_path"] = path
        self._init_params["use_calibrated_target_vol_default"] = True
        self._target_vol_calibrated = True
        artifact = load_target_vol_artifact(path, min_samples=int(kwargs.get("min_samples", 5000)))
        self._target_vol_provenance = copy.deepcopy(artifact if artifact is not None else result)
        self._target_vol_artifact_path = path
        self._target_vol_missing_artifact = False
        LOGGER.info(
            "[REGIME] target_vol recalibrated and hot-swapped for future ticks only: %.12g -> %.12g provenance=%s",
            before,
            after,
            self._target_vol_provenance,
        )
        return copy.deepcopy(self._target_vol_provenance)

    def report_realized_pnl(self, realized_pnl: float, equity: float) -> None:
        """FIX-7 (REGIME_ENGINE_AUDIT 2026-04-23): the executor (live or
        backtest) calls this on EVERY realized trade close. Updates the
        portfolio peak/drawdown trackers and trips the circuit breaker when
        portfolio drawdown exceeds ``_MAX_PORTFOLIO_DRAWDOWN``.

        This is the only path that connects portfolio-level losses to the
        engine's circuit breaker. Without it, a sequence of losing trades is
        invisible to the engine and the breaker never fires.
        """
        with self._lock:
            try:
                eq = float(equity)
            except Exception:
                return
            if not (np.isfinite(eq) and eq > 0):
                return
            if (not np.isfinite(self._portfolio_peak_equity)) or eq > self._portfolio_peak_equity:
                self._portfolio_peak_equity = eq
            dd = max(0.0, 1.0 - eq / self._portfolio_peak_equity)
            self._portfolio_drawdown = float(dd)
            if dd >= self._MAX_PORTFOLIO_DRAWDOWN and not self._circuit_breaker_active:
                self._circuit_breaker_active = True
                self._circuit_breaker_reason = (
                    f"PORTFOLIO_DD_{dd:.4f}_GE_{self._MAX_PORTFOLIO_DRAWDOWN}"
                )
                # FIX-7 supplement: also bump the engine's circuit-breaker history
                # ring buffer and the FIX-27 per-reason downgrade counter so this
                # event is observable through both get_state() and get_health().
                try:
                    self._circuit_breaker_trigger_tick = int(getattr(self, "_tick_id", 0))
                    self._cb_trigger_history.append(
                        (float(time.time()), "portfolio_drawdown_breach", float(dd))
                    )
                except Exception as _swallowed_exc:
                    LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)
                try:
                    self._record_regime_downgrade("circuit_breaker")
                except Exception as _swallowed_exc:
                    LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)
                # UPGRADE-5.8: persist cb_trigger_history to disk so the
                # operator can recover trip context after a crash. Replay-
                # skipped (preserves determinism) and fail-soft on I/O.
                if not getattr(self, "_is_replay", False):
                    try:
                        import pathlib as _pl
                        _pl.Path("audit_engine_output").mkdir(exist_ok=True)
                        with open("audit_engine_output/cb_trigger_history.json", "w") as _f:
                            json.dump(list(self._cb_trigger_history)[-100:], _f)
                    except Exception:
                        pass  # never let persistence kill the engine
                try:
                    LOGGER.error(
                        "CIRCUIT_BREAKER: portfolio DD %.4f >= %.4f (realized_pnl=%.6f equity=%.6f)",
                        dd, self._MAX_PORTFOLIO_DRAWDOWN, float(realized_pnl), eq,
                    )
                except Exception as _swallowed_exc:
                    LOGGER.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)

    @_synchronized
    def serialize_state(self) -> Dict[str, Any]:
        return self._get_state_unlocked()

    @_synchronized
    def save_state(self) -> Dict[str, Any]:
        return self._get_state_unlocked()

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
        self._regime_suppression_log = []
        self._regime_audit_log = []
        self.garch_prob = np.ones(2, dtype=float) / 2.0
        
        # --- NEW: reset SJM fallback memory ---
        self._last_valid_sjm_probs = None
        self._smoothed_garch_prob = self.garch_prob.copy()
        self._regime_state_probs = np.ones(4, dtype=float) / 4.0
        if getattr(self, "_regime_smoother", None) is not None:
            self._regime_smoother.reset()
            self._regime_smoother.prev_probs = self._regime_state_probs.copy()
        self.garch_var = self._stationary_garch_var()
        self._shock_memory = 0.0
        self._return_ema = 0.0
        self._abs_return_ema = 0.0
        self._equity = 1.0
        self._equity_peak = 1.0
        self._drawdown = 0.0
        self._cumulative_drawdown = 0.0
        self._loss_streak = 0
        self._last_price = None
        self._last_price_timestamp = None
        self._last_price_tick_id = None
        self._valid_return_count = 0
        self._first_valid_return_ts = None
        self._posterior_update_count = 0
        self._first_posterior_ts = None
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = None
        self._circuit_breaker_trigger_tick = -1
        # FIX-7: also reset portfolio-DD trackers on engine reset
        self._portfolio_peak_equity = float("nan")
        self._portfolio_drawdown = 0.0
        self._healing_counter = 0
        self._last_healing_action = "NONE"
        self._cb_trigger_history: "deque[tuple[float, str, float]]" = deque(maxlen=50)
        self._last_healing_error = None
        self._last_healing_context = {}
        self._healing_count = 0
        self._confidence_collapse_streak = 0
        self._determinism_status = "OK"
        self._determinism_had_failure = False

    def _load_state_inplace(self, state: Dict[str, Any]) -> None:
        if not isinstance(state, dict):
            self._log_state_load_issue("state", TypeError("state must be dict"), type(state).__name__)
            return
        expected_hash = state.get("state_hash", None)
        if expected_hash is not None:
            hash_payload = dict(state)
            hash_payload.pop("state_hash", None)
            hash_payload.pop("_checksum", None)
            actual_hash = self._state_hash(hash_payload)
            if str(expected_hash) != str(actual_hash):
                self._log_state_load_issue("state_hash", ValueError("state hash mismatch"), expected_hash)
                return
        validated_rng_state = None
        rng_state = state.get("engine_rng_state", None)
        if rng_state is not None and getattr(self, "_rng", None) is not None:
            try:
                validated_rng_state = self._validate_rng_state_payload(rng_state)
            except Exception as exc:
                self._mark_determinism_failure()
                self._log_state_load_issue("engine_rng_state", exc, "invalid_rng_state")
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
        self._allow_timestamp_free_pnl = bool(state.get("allow_timestamp_free_pnl", self._allow_timestamp_free_pnl))
        self._max_price_staleness_ticks = _safe_int(
            state.get("max_price_staleness_ticks", self._max_price_staleness_ticks),
            default=self._max_price_staleness_ticks,
            min=1,
        )
        incoming_pnl_mode = state.get("pnl_mode", None)
        self._pnl_mode = None if incoming_pnl_mode is None else str(incoming_pnl_mode).upper()
        if self._pnl_mode not in (None, "TIMESTAMP", "TICK"):
            self._log_state_load_issue("pnl_mode", ValueError("invalid pnl_mode"), incoming_pnl_mode)
            self._pnl_mode = None
        last_price = state.get("last_price", None)
        self._last_price = None
        if last_price is not None:
            last_price_f = self._state_scalar(state, "last_price", default=np.nan)
            if np.isfinite(last_price_f):
                self._last_price = float(last_price_f)
            else:
                self._log_state_load_issue("last_price", ValueError("non-finite last_price"), last_price)
        self._last_price_timestamp = self._normalize_timestamp(state.get("last_price_timestamp", None))
        self._last_price_tick_id = None
        if state.get("last_price_tick_id", None) is not None:
            self._last_price_tick_id = _safe_int(state.get("last_price_tick_id"), default=-1, min=-1)
            if self._last_price_tick_id < 0:
                self._last_price_tick_id = None
        restored_tick_id = state.get("tick_id", None)
        if restored_tick_id is not None:
            self._tick_id = int(_safe_int(restored_tick_id, default=int(getattr(self, "_tick_id", 0)), min=0))
        else:
            LOGGER.warning(
                "load_state: 'tick_id' absent from snapshot. Inferring from _last_price_tick_id to avoid TICK_ORDER_VIOLATION."
            )
        if self._last_price_tick_id is not None and int(self._tick_id) <= int(self._last_price_tick_id):
            self._tick_id = int(self._last_price_tick_id) + 1
            LOGGER.info("load_state: advanced _tick_id to %d to clear anchor.", self._tick_id)
        if state.get("last_price_timestamp", None) is not None and self._last_price_timestamp is None:
            self._log_state_load_issue(
                "last_price_timestamp",
                ValueError("invalid timestamp"),
                state.get("last_price_timestamp"),
            )
        if (
            self._last_price is not None
            and self._last_price_timestamp is None
            and (not self._allow_timestamp_free_pnl or self._last_price_tick_id is None)
        ):
            self._log_state_load_issue(
                "last_price",
                ValueError("last_price provided without valid anchor policy"),
                self._last_price,
            )
            self._last_price = None
        if self._last_price is None:
            self._pnl_mode = None
        elif self._pnl_mode is None:
            self._pnl_mode = "TIMESTAMP" if self._last_price_timestamp is not None else "TICK"

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

        self._prev_regime = self._validate_regime_label(state.get("prev_regime", None), "prev_regime")
        raw_directional_label = state.get("prev_directional_label", None)
        self._prev_directional_label = self._validate_directional_label(raw_directional_label, "prev_directional_label")
        self._prev_raw_regime = self._validate_regime_label(state.get("prev_raw_regime", None), "prev_raw_regime")
        self._confirmed_regime = self._validate_regime_label(state.get("confirmed_regime", None), "confirmed_regime")

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

        smoother_probs = state.get("regime_smoother_prev_probs")
        state_probs = state.get("regime_state_probs")
        if smoother_probs is not None:
            authoritative_probs = np.asarray(smoother_probs, dtype=np.float64)
        elif state_probs is not None:
            LOGGER.warning("load_state: falling back to regime_state_probs (smoother probs absent).")
            authoritative_probs = np.asarray(state_probs, dtype=np.float64)
        else:
            LOGGER.error("load_state: neither smoother nor state probs found. Using uniform prior.")
            authoritative_probs = np.ones(4, dtype=np.float64) / 4.0
        if not np.all(np.isfinite(authoritative_probs)):
            authoritative_probs = np.ones(4, dtype=np.float64) / 4.0
        authoritative_probs = _normalize_prob_vector(authoritative_probs)
        self._regime_state_probs = authoritative_probs.copy()
        if getattr(self, "_regime_smoother", None) is not None:
            self._regime_smoother.prev_probs = authoritative_probs.copy()

        self._engine_status = str(state.get("engine_status", "OK"))
        restored_target_vol = self._state_scalar(
            state,
            "target_vol",
            default=float(self.garch.target_vol),
            min_value=1e-12,
        )
        if np.isfinite(restored_target_vol) and restored_target_vol > 0.0:
            self.garch.target_vol = float(restored_target_vol)
            self._init_params["target_vol"] = float(restored_target_vol)
        else:
            self._log_state_load_issue("target_vol", ValueError("invalid target_vol"), state.get("target_vol"))
        self._target_vol_calibrated = bool(state.get("target_vol_calibrated", getattr(self, "_target_vol_calibrated", False)))
        self._use_calibrated_target_vol_default = bool(state.get("use_calibrated_target_vol_default", getattr(self, "_use_calibrated_target_vol_default", False)))
        self._init_params["use_calibrated_target_vol_default"] = bool(self._use_calibrated_target_vol_default)
        incoming_target_vol_provenance = state.get("target_vol_provenance", getattr(self, "_target_vol_provenance", {}))
        self._target_vol_provenance = copy.deepcopy(incoming_target_vol_provenance) if isinstance(incoming_target_vol_provenance, dict) else {}
        # ── Restore NHHMM model parameters ──────────────────────────────────────
        # Track per-parameter success with a set.
        # "Fully restored" requires ALL THREE keys to succeed.
        # A single boolean would latch on the first success and mask later failures.
        _nhhmm_restored_keys: set[str] = set()
        _nhhmm_required_keys: frozenset[str] = frozenset(
            {"nhhmm_beta", "nhhmm_mu", "nhhmm_sigma"}
        )

        for key, attr, shape_ref in [
            ("nhhmm_beta",  "beta",  (self.nhhmm.K, self.nhhmm.K, self.nhhmm.n_features)),
            ("nhhmm_mu",    "mu",    (self.nhhmm.K,)),
            ("nhhmm_sigma", "sigma", (self.nhhmm.K,)),
        ]:
            raw_val = state.get(key)

            if raw_val is None:
                LOGGER.warning(
                    "load_state: '%s' absent from snapshot — "
                    "parameter will retain default value. "
                    "This key is REQUIRED for coherent regime inference.",
                    key,
                )
                self._engine_status = "DEGRADED"
                continue

            try:
                arr = np.asarray(raw_val, dtype=np.float64)
            except (TypeError, ValueError) as exc:
                LOGGER.error(
                    "load_state: cannot coerce '%s' to ndarray: %s — "
                    "parameter will retain default value.",
                    key, exc,
                )
                self._engine_status = "DEGRADED"
                continue

            if arr.shape != shape_ref:
                LOGGER.error(
                    "load_state: shape mismatch for '%s': "
                    "got %s, expected %s — "
                    "parameter will retain default value. "
                    "Snapshot may be from a different model architecture.",
                    key, arr.shape, shape_ref,
                )
                self._engine_status = "DEGRADED"
                continue

            if not np.all(np.isfinite(arr)):
                n_bad = int(np.sum(~np.isfinite(arr)))
                LOGGER.error(
                    "load_state: '%s' contains %d non-finite value(s) — "
                    "parameter will retain default value.",
                    key, n_bad,
                )
                self._engine_status = "DEGRADED"
                continue

            # All checks passed for this parameter — safe to apply.
            setattr(self.nhhmm, attr, arr)
            _nhhmm_restored_keys.add(key)
            LOGGER.debug(
                "load_state: '%s' restored successfully (shape=%s).",
                key, arr.shape,
            )

        # ── Authoritative restoration status ─────────────────────────────────────
        _nhhmm_fully_restored: bool = (_nhhmm_restored_keys == _nhhmm_required_keys)
        _nhhmm_missing: frozenset[str] = _nhhmm_required_keys - _nhhmm_restored_keys

        if not _nhhmm_fully_restored:
            LOGGER.critical(
                "load_state: NHHMM parameter restore INCOMPLETE. "
                "Restored: %s. Failed/missing: %s. "
                "Regime posteriors will be INCOHERENT — emission distribution "
                "is using default parameters, not trained values. "
                "Engine requires retrain or a valid snapshot before live trading.",
                sorted(_nhhmm_restored_keys),
                sorted(_nhhmm_missing),
            )
            # Ensure engine_status is DEGRADED regardless of what earlier
            # restore steps may have set it to.
            self._engine_status = "DEGRADED"
        else:
            LOGGER.info(
                "load_state: all NHHMM parameters restored successfully "
                "(beta shape=%s, mu shape=%s, sigma shape=%s).",
                self.nhhmm.beta.shape,
                self.nhhmm.mu.shape,
                self.nhhmm.sigma.shape,
            )

        sjm_means_raw = state.get("sjm_means")
        sjm_weights_raw = state.get("sjm_weights")
        if sjm_means_raw is not None and sjm_weights_raw is not None:
            try:
                m = np.asarray(sjm_means_raw, dtype=np.float64)
                w = np.asarray(sjm_weights_raw, dtype=np.float64)
                if self.sjm.means is None or self.sjm.weights is None:
                    self.sjm.means = np.zeros_like(m)
                    self.sjm.weights = np.ones_like(w)
                if m.shape == self.sjm.means.shape and w.shape == self.sjm.weights.shape:
                    if np.all(np.isfinite(m)) and np.all(np.isfinite(w)):
                        self.sjm.means = m
                        self.sjm.weights = w
                    else:
                        LOGGER.error("load_state: non-finite SJM params, keeping defaults.")
                else:
                    LOGGER.error("load_state: SJM shape mismatch means=%s weights=%s, keeping defaults.", m.shape, w.shape)
            except (TypeError, ValueError) as exc:
                LOGGER.error("load_state: SJM params restore failed: %s", exc)
        else:
            LOGGER.warning("load_state: SJM params absent from snapshot.")
        self.sjm._score_scale = float(state.get("sjm_score_scale", 1.0))
        self.sjm._default_params_initialized = bool(state.get("sjm_default_params_initialized", False))
        self.sjm._just_restored = True
        self._just_restored = True

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
        self._cumulative_drawdown = self._state_scalar(
            state,
            "cumulative_drawdown",
            default=self._drawdown,
            min_value=0.0,
            max_value=1.0,
        )
        self._cumulative_drawdown = max(float(self._cumulative_drawdown), float(self._drawdown))
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
        self._valid_return_count = _safe_int(state.get("valid_return_count", 0), default=0, min=0)
        self._first_valid_return_ts = self._normalize_timestamp(state.get("first_valid_return_ts", None))
        self._posterior_update_count = _safe_int(state.get("posterior_update_count", 0), default=0, min=0)
        self._first_posterior_ts = self._normalize_timestamp(state.get("first_posterior_ts", None))
        self._shock_warmup_ticks = _safe_int(
            state.get("shock_warmup_ticks", self._shock_warmup_ticks),
            default=self._shock_warmup_ticks,
            min=1,
        )
        self._shock_warmup_seconds = self._state_scalar(
            state,
            "shock_warmup_seconds",
            default=self._shock_warmup_seconds,
            min_value=1.0,
        )
        self._shock_startup_multiplier = self._state_scalar(
            state,
            "shock_startup_multiplier",
            default=self._shock_startup_multiplier,
            min_value=1.0,
            max_value=self._VOL_SHOCK_MULTIPLIER,
        )
        self._shock_startup_vol_floor_mult = self._state_scalar(
            state,
            "shock_startup_vol_floor_mult",
            default=self._shock_startup_vol_floor_mult,
            min_value=0.1,
            max_value=1.5,
        )
        if validated_rng_state is not None and getattr(self, "_rng", None) is not None:
            self._rng.bit_generator.state = validated_rng_state
        incoming_det_status = self._normalize_determinism_status(state.get("determinism_status", "OK"))
        prior_had_failure = bool(getattr(self, "_determinism_had_failure", False))
        self._determinism_had_failure = bool(_safe_int(state.get("determinism_had_failure", 0), default=0, min=0, max=1))
        if incoming_det_status in ("RNG_RESTORE_FAILED", "OK_WITH_HISTORY"):
            self._determinism_had_failure = True
        self._determinism_had_failure = bool(self._determinism_had_failure or prior_had_failure)
        restore_attempted = validated_rng_state is not None
        if restore_attempted:
            self._mark_determinism_success()
        elif incoming_det_status == "RNG_RESTORE_FAILED":
            # Preserve historical failure marker only; do not mark current load as failed.
            self._determinism_had_failure = True
            self._determinism_status = "OK_WITH_HISTORY"

    def load_state(self, state: Dict[str, Any]) -> None:
        """
        Atomic state load:
        1) hydrate staging engine
        2) fully validate/apply on staging
        3) commit materialized state to self in one swap
        """
        if not isinstance(state, dict):
            self._log_state_load_issue("state", TypeError("state must be dict"), type(state).__name__)
            return
        expected_hash = state.get("state_hash", None)
        if expected_hash is not None:
            hash_payload = dict(state)
            hash_payload.pop("state_hash", None)
            hash_payload.pop("_checksum", None)
            if str(expected_hash) != str(self._state_hash(hash_payload)):
                self._log_state_load_issue("state_hash", ValueError("state hash mismatch"), expected_hash)
                return
        incoming_rng_state = state.get("engine_rng_state", None)
        if incoming_rng_state is not None and getattr(self, "_rng", None) is not None:
            try:
                self._validate_rng_state_payload(incoming_rng_state)
            except Exception as exc:
                self._mark_determinism_failure()
                self._log_state_load_issue("engine_rng_state", exc, "invalid_rng_state")
                return
        with self._lock:
            init_params = dict(self._init_params)
            allow_igarch = bool(self._allow_igarch)
            emit_extended_schema = bool(self._emit_extended_schema)
            strict_mtf_keys = bool(self._strict_mtf_keys)
            mtf_weights = copy.deepcopy(self.mtf_weights)
            sjm_reserved = self._sjm_reserved_feature_indices
            allow_timestamp_free_pnl = bool(self._allow_timestamp_free_pnl)
            max_price_staleness_ticks = int(self._max_price_staleness_ticks)
            shock_warmup_ticks = int(self._shock_warmup_ticks)
            shock_warmup_seconds = float(self._shock_warmup_seconds)
            shock_startup_multiplier = float(self._shock_startup_multiplier)
            shock_startup_vol_floor_mult = float(self._shock_startup_vol_floor_mult)
            rng_seed = self._rng_seed
            engine_id = self.engine_id
            determinism_status = str(getattr(self, "_determinism_status", "OK"))
            determinism_had_failure = bool(getattr(self, "_determinism_had_failure", False))
        staging = AdvancedRegimeEngine(
            n_states=int(init_params.get("n_states", self.K)),
            n_features=int(init_params.get("n_features", self.n_features)),
            target_vol=float(init_params.get("target_vol", self.garch.target_vol)),
            allow_igarch=allow_igarch,
            regime_prob_floor=float(self.garch._REGIME_PROB_FLOOR),
            emit_extended_schema=emit_extended_schema,
            strict_mtf_keys=strict_mtf_keys,
            mtf_weights=mtf_weights,
            sjm_reserved_feature_indices=sjm_reserved,
            allow_timestamp_free_pnl=allow_timestamp_free_pnl,
            max_price_staleness_ticks=max_price_staleness_ticks,
            shock_warmup_ticks=shock_warmup_ticks,
            shock_warmup_seconds=shock_warmup_seconds,
            shock_startup_multiplier=shock_startup_multiplier,
            shock_startup_vol_floor_mult=shock_startup_vol_floor_mult,
            seed=rng_seed,
            engine_id=engine_id,
            enable_background_workers=False,
            load_model_weights_on_init=False,
        )
        _staging_warnings: List[tuple[str, str]] = []
        original_warn = staging._warn_rate_limited
        def _accumulating_warn(key, message, level="WARNING", **kwargs):
            _ = key, kwargs
            _staging_warnings.append((str(level), str(message)))
            return True
        staging._warn_rate_limited = _accumulating_warn
        try:
            staging._is_replay = True
            staging._determinism_status = determinism_status
            staging._determinism_had_failure = determinism_had_failure
            staging._load_state_inplace(copy.deepcopy(state))
            if str(getattr(staging, "_determinism_status", "OK")) == "RNG_RESTORE_FAILED":
                self._mark_determinism_failure()
                return
            committed_state = staging.serialize_state()
            committed_rng_state = (
                copy.deepcopy(staging._rng.bit_generator.state)
                if getattr(staging, "_rng", None) is not None else None
            )
        except Exception as exc:
            self._log_state_load_issue("state_atomic", exc, "atomic_stage_failure")
            return
        finally:
            staging._warn_rate_limited = original_warn
            staging._shutdown_warning_worker()
            staging._shutdown_snapshot_worker()

        # Commit in one shot (no validation failures expected after staging).
        with self._lock:
            self._load_state_inplace(committed_state)
            if committed_rng_state is not None and getattr(self, "_rng", None) is not None:
                self._rng.bit_generator.state = committed_rng_state
        for level, message in _staging_warnings:
            LOGGER.log(getattr(logging, level, logging.WARNING), "[state_load] %s", message)
        if _staging_warnings:
            LOGGER.info("load_state: replayed %d staging warnings to live engine.", len(_staging_warnings))

    # update() and load_snapshot() use self._lock, which is an RLock;
    # reentrant acquisition here cannot deadlock, so the decorator remains safe.
    @_synchronized
    def _set_feed_status(
        self,
        output: Dict[str, Any],
        status: Any,
        flags: list | None = None,
    ) -> None:
        """
        Single authoritative writer for feed_status.
        Updates both output["feed_status"] (primary string) and
        output["risk_metrics"]["feed_status"] (structured dict) atomically
        so they can never diverge.
        """
        if isinstance(status, dict):
            primary = str(status.get("primary", "UNKNOWN"))
            raw_flags = status.get("flags", [])
            if not isinstance(raw_flags, list):
                raw_flags = []
            effective_flags = [str(v)[:64] for v in raw_flags[:8]]
        else:
            primary = str(status or "UNKNOWN")
            effective_flags = []
        if flags is not None:
            for f in flags:
                fs = str(f)[:64]
                if fs not in effective_flags:
                    effective_flags.append(fs)
        output["feed_status"] = primary
        output.setdefault("risk_metrics", {})["feed_status"] = {
            "primary": primary,
            "flags": effective_flags,
        }

    def _compute_shadow_switch_metrics(self, regime_scores, switch_strength, switch_gate, persistence_ok, cooldown_ok, current_threshold, proposed_threshold):
        current_pass = (cooldown_ok and switch_strength >= switch_gate) or (persistence_ok and regime_scores["conviction"] >= current_threshold)
        proposed_pass = (cooldown_ok and switch_strength >= switch_gate) or (persistence_ok and regime_scores["conviction"] >= proposed_threshold)
        return {
            "current_threshold": float(current_threshold),
            "proposed_threshold": float(proposed_threshold),
            "current_pass": bool(current_pass),
            "proposed_pass": bool(proposed_pass),
        }

    def update(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(market_data, dict):
            market_data = {}
            self._obs_observe("input_validation", "high", {"reason": "market_data_not_dict"})
            self._warn_rate_limited(
                key="market_data_not_dict",
                message="update() received non-dict market_data; degraded to fail-safe defaults.",
                cooldown_s=30.0,
            )
        if "price" in market_data:
            try:
                market_data["price"] = float(market_data["price"])
            except Exception as exc:
                raise ValueError(f"price must be numeric, got {market_data.get('price')!r}") from exc
            if not np.isfinite(market_data["price"]):
                raise ValueError(f"price must be finite, got {market_data.get('price')}")
            if market_data["price"] <= 0.0:
                raise ValueError(f"price must be positive, got {market_data['price']!r}")
        require_microstructure = bool(market_data.get("require_microstructure", False))
        has_orderbook = isinstance(market_data.get("orderbook"), dict) and bool(market_data.get("orderbook"))
        has_trades = isinstance(market_data.get("trades"), list) and len(market_data.get("trades")) > 0
        if require_microstructure and not (has_orderbook and has_trades):
            self.last_signed_position_size = 0.0
            self._engine_status = "DEGRADED"
            blocked_label = "UNCALIBRATED" if not self._weights_loaded else "UNKNOWN"
            blocked_feed = "UNCALIBRATED_WEIGHTS" if not self._weights_loaded else "INVALID_INPUT_MICROSTRUCTURE_REQUIRED"
            # FIX-27 (M-1): bin this halt under the appropriate reason code.
            self._record_regime_downgrade(
                "uncalibrated_weights" if not self._weights_loaded
                else "microstructure_required_but_missing"
            )
            return _build_output(
                regime_idx=-1,
                regime_label=blocked_label,
                execution_mode="halt",
                trend_strength=0.0,
                risk_level=1.0,
                confidence=0.0,
                conviction=0.0,
                edge_score=0.0,
                probabilities={"bull": 0.0, "bear": 0.0, "crisis": 1.0},
                macro_probs=[1 / 3, 1 / 3, 1 / 3],
                position_size=0.0,
                signed_position_size=0.0,
                expected_vol=self._last_valid_vol,
                raw_size=0.0,
                is_toxic=True,
                garch_regime_probs=[0.5, 0.5],
                feed_status=blocked_feed,
                engine_status="DEGRADED",
                last_valid_vol=self._last_valid_vol,
                switch_stability_ema=self._switch_stability_ema,
                execution_side="flat",
                include_signal_valid=True,
                signal_valid=False,
                engine_id=self._metrics_engine_id,
            )

        require_calibration = bool(market_data.get("require_calibration", False))
        if require_calibration and not self._weights_loaded:
            self.last_signed_position_size = 0.0
            self._engine_status = "DEGRADED"
            # FIX-27 (M-1)
            self._record_regime_downgrade("uncalibrated_weights")
            return _build_output(
                regime_idx=-1,
                regime_label="UNCALIBRATED",
                execution_mode="halt",
                trend_strength=0.0,
                risk_level=1.0,
                confidence=0.0,
                conviction=0.0,
                edge_score=0.0,
                probabilities={"bull": 0.0, "bear": 0.0, "crisis": 1.0},
                macro_probs=[1 / 3, 1 / 3, 1 / 3],
                position_size=0.0,
                signed_position_size=0.0,
                expected_vol=self._last_valid_vol,
                raw_size=0.0,
                is_toxic=True,
                garch_regime_probs=[0.5, 0.5],
                feed_status="UNCALIBRATED_WEIGHTS",
                engine_status="DEGRADED",
                last_valid_vol=self._last_valid_vol,
                switch_stability_ema=self._switch_stability_ema,
                execution_side="flat",
                include_signal_valid=True,
                signal_valid=False,
                engine_id=self._metrics_engine_id,
            )

        # NOTE: enforce globally across codebase:
        # ALL side effects must follow:
        # if not getattr(self, "_is_replay", False): LOGGER / metrics / hooks

        if getattr(self, "_engine_status", "OK") == "DEGRADED":
            self._warn_rate_limited(
                key="update_while_degraded",
                message=(
                    "update() called while engine_status=DEGRADED. "
                    "NHHMM parameters may be incoherent. "
                    "Signal reliability is reduced. "
                    "Reload a valid snapshot or retrain."
                ),
                cooldown_s=30.0,
            )

        start_time = time.perf_counter()
        self._tick_id = int(getattr(self, "_tick_id", 0)) + 1
        self._obs_counter += 1
        valid_tf_count = 0
        expected_weighted_tf_count = 0
        total_candidate_tfs = 0
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
                ENGINE_LATENCY.labels(self._metrics_engine_id).observe(elapsed)

        def _build_halted_output() -> Dict[str, Any]:
            self.last_signed_position_size = 0.0
            # FIX-27 (M-1): every circuit-breaker halt is tallied here.
            self._record_regime_downgrade("circuit_breaker")
            return _build_output(
                regime_idx=-1,
                regime_label="HALTED",
                execution_mode="circuit_breaker",
                trend_strength=0.0,
                risk_level=1.0,
                confidence=0.0,
                conviction=0.0,
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
                engine_status=str(getattr(self, "_determinism_status", "OK")),
                last_valid_vol=self._last_valid_vol,
                switch_stability_ema=self._switch_stability_ema,
                execution_side="flat",
                include_signal_valid=True,
                signal_valid=False,
                engine_id=self._metrics_engine_id,
            )

        # ==========================================
        # 🚨 STEP 0: CIRCUIT BREAKER CHECK
        # ==========================================
        current_ts = self._normalize_timestamp(market_data.get("timestamp", None))
        def _halt_if_breaker_after_heal() -> Dict[str, Any] | None:
            if self._circuit_breaker_active:
                output = _build_halted_output()
                _observe_latency()
                if obs_sample and not getattr(self, "_is_replay", False):
                    self._replay_record("update_end", {"regime": "HALTED"})
                if not self._weights_loaded:
                    output["signal_valid"] = False
                    output["regime_label"] = "UNCALIBRATED"
                return output
            return None

        if self._circuit_breaker_active:
            self._healing_counter += 1

            if self._healing_counter > self._HEALING_COOLDOWN_TICKS:
                self._self_heal()
                halted = _halt_if_breaker_after_heal()
                if halted is not None:
                    if obs_sample and not getattr(self, "_is_replay", False):
                        self._replay_record("update_end", {"regime": "HALTED_HEALING"})
                    return halted
            else:
                self._obs_observe("circuit_breaker", "critical", {"reason": self._circuit_breaker_reason})
                output = _build_halted_output()
                _observe_latency()
                if obs_sample and not getattr(self, "_is_replay", False):
                    self._replay_record("update_end", {"regime": "HALTED"})
                return output

        max_persistence = float(np.max(np.asarray(self.garch.alpha) + np.asarray(self.garch.beta_garch)))
        if bool(self._allow_igarch) and max_persistence >= 1.0:
            LOGGER.critical("[IGARCH] Non-stationary persistence detected alpha+beta=%.4f; blocking trading", max_persistence)
            out = _build_halted_output()
            out["execution_mode"] = "halt_igarch"
            out["signal_valid"] = False
            self._set_feed_status(out, "HALT_IGARCH_NON_STATIONARY")
            _observe_latency()
            return out

        mtf_data = market_data.get("mtf", None)
        canonical_ok, canonical_return, canonical_source = self._resolve_canonical_return(market_data, mtf_data)
        if not canonical_ok:
            self._warn_rate_limited(
                key="invalid_canonical_return",
                message=f"Invalid canonical return input ({canonical_source}); emitting fail-safe output.",
                cooldown_s=30.0,
            )
            self._obs_observe("invalid_canonical_return", "high", {"reason": canonical_source})
            self.last_signed_position_size = 0.0
            self._update_timestamp_anchor(current_ts)
            output = _build_output(
                regime_idx=-1,
                regime_label="UNKNOWN",
                execution_mode="fail_safe",
                trend_strength=float(getattr(self, "_last_effective_trend_strength", 0.0)),
                risk_level=1.0,
                confidence=0.0,
                conviction=0.0,
                edge_score=0.0,
                probabilities={'bull': 0.0, 'bear': 0.0, 'crisis': 1.0},
                macro_probs=self.nhhmm_prior.tolist(),
                position_size=0.0,
                signed_position_size=0.0,
                expected_vol=float(getattr(self, "_last_valid_vol", self.garch.target_vol)),
                raw_size=0.0,
                is_toxic=True,
                garch_regime_probs=self.garch_prob.tolist(),
                feed_status='INVALID_RETURN_INPUT',
                engine_status=str(getattr(self, "_determinism_status", "OK")),
                last_valid_vol=float(getattr(self, "_last_valid_vol", self.garch.target_vol)),
                switch_stability_ema=float(getattr(self, "_switch_stability_ema", 1.0)),
                execution_side='flat',
                include_signal_valid=True,
                signal_valid=False,
                engine_id=self._metrics_engine_id,
            )
            _observe_latency()
            return output

        # ==========================================
        # 🚨 STEP -1: PRE-SHOCK GATE + PnL TRACKING
        # ==========================================
        y_preview = float(canonical_return)
        self._record_valid_return(current_ts)
        pre_shock_baseline_vol = float(
            np.sqrt(np.dot(self._smoothed_garch_prob, np.clip(self.garch_var, 1e-8, None)))
        )
        pre_shock_threshold, shock_vol_basis = self._shock_threshold(pre_shock_baseline_vol, current_ts)
        feed_status_annotations: list[str] = []
        if abs(y_preview) > pre_shock_threshold:
            self.last_signed_position_size = 0.0
            self._trigger_circuit_breaker("VOL_SHOCK")
            output = _build_halted_output()
            _observe_latency()
            if obs_sample and not getattr(self, "_is_replay", False):
                self._replay_record("update_end", {"regime": "HALTED"})
            return output

        stale_reason: str = "NO_PREV_PRICE"
        stale_price: bool = True
        policy_allows_pnl: bool = False
        price = market_data.get("price", None)
        observed_return = market_data.get("return", None)
        if price is not None:
            try:
                parsed_price = float(price)
                if not np.isfinite(parsed_price):
                    raise ValueError("price is non-finite")
            except (TypeError, ValueError) as exc:
                self._warn_rate_limited(
                    key="pnl_price_parse_failure",
                    message=f"PnL tracking skipped due to invalid price input: {exc}",
                    cooldown_s=15.0,
                )
                self._obs_observe("pnl_price_parse_failure", "medium", {"reason": type(exc).__name__})
                parsed_price = None
            if parsed_price is not None:
                try:
                    if self._last_price is not None:
                        prev_price = float(self._last_price)
                        stale_price = True
                        policy_allows_pnl = True
                        stale_reason = "UNKNOWN"
                        try:
                            policy_allows_pnl, stale_price, stale_reason = self._pnl_staleness_policy(current_ts)
                        except (TypeError, ValueError) as exc:
                            self._warn_rate_limited(
                                key="pnl_stale_price_check_failure",
                                message=f"Stale-price check failed; treating tick as stale-safe: {exc}",
                                cooldown_s=15.0,
                            )
                            stale_price = True
                            policy_allows_pnl = False
                            stale_reason = "STALE_CHECK_ERROR"
                        if not policy_allows_pnl:
                            if stale_reason == "TICK_ORDER_VIOLATION":
                                self._warn_rate_limited(
                                    key="pnl_tick_order_violation",
                                    message=(
                                        "PnL anchor tick ordering violated (non-monotonic tick id); "
                                        "PnL update blocked and feed degraded."
                                    ),
                                    cooldown_s=30.0,
                                )
                                self._obs_observe("pnl_tick_order_violation", "high", {"reason": stale_reason})
                            self._warn_rate_limited(
                                key="pnl_timestamp_policy_blocked",
                                message=(
                                    "PnL tracking requires timestamp anchors but feed is timestamp-less or mixed; "
                                    "PnL update skipped and feed marked degraded."
                                ),
                                cooldown_s=30.0,
                            )
                            if "PNL_TIMESTAMP_POLICY_BLOCKED" not in feed_status_annotations:
                                feed_status_annotations.append("PNL_TIMESTAMP_POLICY_BLOCKED")
                            self._obs_observe("pnl_timestamp_policy_blocked", "high", {"reason": stale_reason})

                        pnl_ret = None
                        if np.isfinite(prev_price) and abs(prev_price) > 1e-12 and not stale_price and policy_allows_pnl:
                            try:
                                frac_ret = float((parsed_price - prev_price) / prev_price)
                            except (TypeError, ValueError, ZeroDivisionError) as exc:
                                self._warn_rate_limited(
                                    key="pnl_return_reconciliation_failure",
                                    message=f"PnL return reconciliation failed: {exc}",
                                    cooldown_s=15.0,
                                )
                                frac_ret = None

                            if frac_ret is not None:
                                pnl_ret = float(frac_ret)
                                if observed_return is not None:
                                    has_return, return_value, _ = self._parse_strict_return(observed_return)
                                    if has_return:
                                        mismatch = abs(float(return_value) - float(frac_ret))
                                        if mismatch > self._PRICE_RETURN_MISMATCH_TOLERANCE:
                                            self._warn_rate_limited(
                                                key="price_return_mismatch",
                                                message=(
                                                    f"Price/return mismatch detected (|Δ|={mismatch:.6f} > "
                                                    f"{self._PRICE_RETURN_MISMATCH_TOLERANCE:.6f}); "
                                                    "degrading to fail-safe output and freezing risk-state mutation."
                                                ),
                                                cooldown_s=15.0,
                                            )
                                            self._obs_observe(
                                                "price_return_mismatch",
                                                "high",
                                                {"delta": mismatch, "tolerance": self._PRICE_RETURN_MISMATCH_TOLERANCE},
                                            )
                                            anchor_ok, anchor_reason = self._set_price_anchor(
                                                float(parsed_price), current_ts, int(self._tick_id)
                                            )
                                            if not anchor_ok:
                                                self._warn_rate_limited(
                                                    key="pnl_anchor_mode_conflict",
                                                    message=f"Last-price anchor rejected: {anchor_reason}",
                                                    cooldown_s=15.0,
                                                )
                                            self.last_signed_position_size = 0.0
                                            output = _build_output(
                                                regime_idx=-1,
                                                regime_label="UNKNOWN",
                                                execution_mode="fail_safe",
                                                trend_strength=float(getattr(self, "_last_effective_trend_strength", 0.0)),
                                                risk_level=1.0,
                                                confidence=0.0,
                                                conviction=0.0,
                                                edge_score=0.0,
                                                probabilities={'bull': 0.0, 'bear': 0.0, 'crisis': 1.0},
                                                macro_probs=self.nhhmm_prior.tolist(),
                                                position_size=0.0,
                                                signed_position_size=0.0,
                                                expected_vol=float(getattr(self, "_last_valid_vol", self.garch.target_vol)),
                                                raw_size=0.0,
                                                is_toxic=True,
                                                garch_regime_probs=self.garch_prob.tolist(),
                                                feed_status='PRICE_RETURN_MISMATCH',
                                                engine_status=str(getattr(self, "_determinism_status", "OK")),
                                                last_valid_vol=float(getattr(self, "_last_valid_vol", self.garch.target_vol)),
                                                switch_stability_ema=float(getattr(self, "_switch_stability_ema", 1.0)),
                                                execution_side='flat',
                                                extended_schema=self._emit_extended_schema,
                                                range_ticks=self.range_ticks_int,
                                                include_signal_valid=True,
                                                signal_valid=False,
                                                engine_id=self._metrics_engine_id,
                                            )
                                            self._update_timestamp_anchor(current_ts)
                                            _observe_latency()
                                            return output
                                        pnl_ret = float(return_value)

                        if pnl_ret is not None:
                            try:
                                pnl = float(pnl_ret) * float(self.last_signed_position_size)
                                if not np.isfinite(pnl):
                                    raise ValueError("non-finite pnl")
                            except (TypeError, ValueError) as exc:
                                self._warn_rate_limited(
                                    key="pnl_calculation_failure",
                                    message=f"PnL calculation failed for tick: {exc}",
                                    cooldown_s=15.0,
                                )
                            else:
                                try:
                                    next_equity = float(self._equity) + float(pnl)
                                    if next_equity < self._MIN_EQUITY_FLOOR:
                                        next_equity = self._MIN_EQUITY_FLOOR
                                        self._trigger_circuit_breaker("EQUITY_FLOOR")
                                    next_loss_streak = int(self._loss_streak)
                                    if pnl < -1e-6:
                                        next_loss_streak += 1
                                    elif pnl > 1e-6:
                                        next_loss_streak = 0
                                    next_equity_peak = max(float(self._equity_peak), float(next_equity))
                                    next_drawdown = float(np.clip(
                                        (next_equity_peak - next_equity) / max(next_equity_peak, 1e-8),
                                        0.0,
                                        1.0,
                                    ))
                                    next_cumulative_drawdown = max(
                                        float(getattr(self, "_cumulative_drawdown", 0.0)),
                                        float(next_drawdown),
                                    )
                                    self._equity = float(next_equity)
                                    self._loss_streak = int(next_loss_streak)
                                    self._equity_peak = float(next_equity_peak)
                                    self._drawdown = float(next_drawdown)
                                    self._cumulative_drawdown = float(next_cumulative_drawdown)
                                    breaker_triggered = self._circuit_breaker_active
                                    if (not breaker_triggered) and self._drawdown > self._MAX_DRAWDOWN:
                                        self._trigger_circuit_breaker("MAX_DRAWDOWN")
                                        breaker_triggered = True
                                    if (not breaker_triggered) and self._loss_streak >= self._MAX_CONSECUTIVE_LOSSES:
                                        self._trigger_circuit_breaker("LOSS_STREAK")
                                except Exception as exc:
                                    self._warn_rate_limited(
                                        key="pnl_equity_update_failure",
                                        message=f"PnL equity-state update failed: {exc}",
                                        cooldown_s=15.0,
                                    )
                except Exception as exc:
                    self._warn_rate_limited(
                        key="pnl_tracking_outer_guard",
                        message=f"PnL tracking outer-guard activated: {exc}",
                        cooldown_s=15.0,
                    )

                update_anchor = True
                if self._last_price is not None and stale_reason in {"TICK_ORDER_VIOLATION", "PNL_MODE_CONFLICT", "PNL_MODE_INVALID"}:
                    update_anchor = False
                if update_anchor:
                    anchor_ok, anchor_reason = self._set_price_anchor(float(parsed_price), current_ts, int(self._tick_id))
                    if not anchor_ok:
                        self._warn_rate_limited(
                            key="pnl_anchor_mode_conflict",
                            message=f"Last-price anchor rejected: {anchor_reason}",
                            cooldown_s=15.0,
                        )
        if self._circuit_breaker_active:
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

            y_t = canonical_return
            x_t = base_tf.get("features", None)
            if x_t is None:
                self._warn_rate_limited(
                    key="mtf_base_features_missing",
                    message="MTF base timeframe is missing features; emitting fail-safe output.",
                    cooldown_s=30.0,
                )
                self._obs_observe("mtf_base_features_missing", "high", {"source": "update"})
                self.last_signed_position_size = 0.0
                LOGGER.debug("update: x_t=None, zeroing last_signed_position_size (tick=%d)", self._tick_id)
                output = _build_output(
                    regime_idx=-1,
                    regime_label="UNKNOWN",
                    execution_mode="fail_safe",
                    trend_strength=float(getattr(self, "_last_effective_trend_strength", 0.0)),
                    risk_level=1.0,
                    confidence=0.0,
                    conviction=0.0,
                    edge_score=0.0,
                    probabilities={'bull': 0.0, 'bear': 0.0, 'crisis': 1.0},
                    macro_probs=self.nhhmm_prior.tolist(),
                    position_size=0.0,
                    signed_position_size=0.0,
                    expected_vol=float(getattr(self, "_last_valid_vol", self.garch.target_vol)),
                    raw_size=0.0,
                    is_toxic=True,
                    garch_regime_probs=self.garch_prob.tolist(),
                    feed_status="MTF_BASE_FEATURES_MISSING",
                    engine_status="NO_FEATURES",
                    last_valid_vol=float(getattr(self, "_last_valid_vol", self.garch.target_vol)),
                    switch_stability_ema=float(getattr(self, "_switch_stability_ema", 1.0)),
                    execution_side="flat",
                    extended_schema=self._emit_extended_schema,
                    range_ticks=self.range_ticks_int,
                    include_signal_valid=True,
                    signal_valid=False,
                    engine_id=self._metrics_engine_id,
                )
                _observe_latency()
                return output

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

            rolling_prior = np.asarray(self.nhhmm_prior, dtype=float).copy()
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
                        y_t_tf, x_t_tf, rolling_prior
                    )
                    rolling_prior = _normalize_prob_vector(nhhmm_post_tf)
                except Exception as e:
                    self._warn_tf_failure(tf, e)
                    mtf_degradation_reasons["forward_pass_failure"] += 1
                    continue

                fused_probs += weight * nhhmm_post_tf
                total_weight += weight
                valid_tf_count += 1

            total_candidate_tfs = len(candidate_tfs)
            if candidate_tfs:
                self.nhhmm_prior = np.asarray(rolling_prior, dtype=float).copy()

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
                        MTF_DEGRADATION.labels(self._metrics_engine_id, k).inc(v)

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
            if expected_weighted_tf_count == 0:
                try:
                    x_safe = _coerce_1d_vector(
                        x_t,
                        expected_size=self.n_features,
                        name="base x_t",
                    )
                    nhhmm_posterior, _ = self.nhhmm.forward_pass_step(
                        float(y_t), x_safe, self.nhhmm_prior
                    )
                    mtf_degradation_reasons["base_only_anchor"] += 1
                except Exception:
                    nhhmm_posterior = np.ones(self.K) / self.K
                    mtf_degradation_reasons["base_only_forward_failure"] += 1
            elif total_weight <= 0 or valid_tf_count == 0:
                self._warn_rate_limited(
                    key="mtf_total_failure",
                    message="MTF fusion failed, falling back to SAFE base timeframe",
                    cooldown_s=30.0,
                )
                self._obs_observe("mtf_failure", "high", {"source": "mtf_total_failure"})
                self._self_heal("E130", {"source": "mtf_total_failure"})
                halted = _halt_if_breaker_after_heal()
                if halted is not None:
                    return halted

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
            y_t = canonical_return
            x_t = market_data.get('features', None)
            if x_t is None:
                self._warn_rate_limited(
                    key="single_tf_missing_data",
                    message="Single-TF update missing required return/features payload; emitting fail-safe output.",
                    cooldown_s=30.0,
                )
                self.last_signed_position_size = 0.0
                LOGGER.debug("update: x_t=None, zeroing last_signed_position_size (tick=%d)", self._tick_id)
                output = _build_output(
                    regime_idx=-1,
                    regime_label="UNKNOWN",
                    execution_mode="fail_safe",
                    trend_strength=float(getattr(self, "_last_effective_trend_strength", 0.0)),
                    risk_level=1.0,
                    confidence=0.0,
                    conviction=0.0,
                    edge_score=0.0,
                    probabilities={'bull': 0.0, 'bear': 0.0, 'crisis': 1.0},
                    macro_probs=self.nhhmm_prior.tolist(),
                    position_size=0.0,
                    signed_position_size=0.0,
                    expected_vol=float(getattr(self, "_last_valid_vol", self.garch.target_vol)),
                    raw_size=0.0,
                    is_toxic=True,
                    garch_regime_probs=self.garch_prob.tolist(),
                    feed_status='MISSING_DATA',
                    last_valid_vol=float(getattr(self, "_last_valid_vol", self.garch.target_vol)),
                    switch_stability_ema=float(getattr(self, "_switch_stability_ema", 1.0)),
                    execution_side='flat',
                    extended_schema=self._emit_extended_schema,
                    range_ticks=self.range_ticks_int,
                    include_signal_valid=True,
                    signal_valid=False,
                    engine_id=self._metrics_engine_id,
                )
                _observe_latency()
                return output

        # ==========================================
        # FIX #2: Preserve valid MTF posterior before execution validation
        # ==========================================
        if mtf_data is not None:
            safe_nhhmm_posterior = (
                None if nhhmm_posterior is None else nhhmm_posterior.copy()
            )

        if mtf_data is not None and safe_nhhmm_posterior is None:
            raise RuntimeError("MTF fusion failed to produce valid posterior")

        y_t = float(canonical_return)
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

        final_shock_threshold, shock_vol_basis = self._shock_threshold(pre_shock_baseline_vol, current_ts)
        if abs(y_t) > final_shock_threshold:
            self.last_signed_position_size = 0.0
            self._trigger_circuit_breaker("VOL_SHOCK")
            self._obs_observe("circuit_breaker", "critical", {"reason": self._circuit_breaker_reason})
            output = _build_halted_output()
            _observe_latency()
            if obs_sample and not getattr(self, "_is_replay", False):
                self._replay_record("update_end", {"regime": "HALTED"})
            return output

        if x_t is None:
            self._warn_rate_limited(
                key="features_missing_after_validation",
                message="Features missing after validation; emitting fail-safe output.",
                cooldown_s=30.0,
            )
            self.last_signed_position_size = 0.0
            LOGGER.debug("update: x_t=None, zeroing last_signed_position_size (tick=%d)", self._tick_id)
            output = _build_output(
                regime_idx=-1,
                regime_label="UNKNOWN",
                execution_mode="fail_safe",
                trend_strength=float(getattr(self, "_last_effective_trend_strength", 0.0)),
                risk_level=1.0,
                confidence=0.0,
                conviction=0.0,
                edge_score=0.0,
                probabilities={'bull': 0.0, 'bear': 0.0, 'crisis': 1.0},
                macro_probs=self.nhhmm_prior.tolist(),
                position_size=0.0,
                signed_position_size=0.0,
                expected_vol=float(getattr(self, "_last_valid_vol", self.garch.target_vol)),
                raw_size=0.0,
                is_toxic=True,
                garch_regime_probs=self.garch_prob.tolist(),
                feed_status="FEATURES_MISSING_AFTER_VALIDATION",
                engine_status="NO_FEATURES",
                last_valid_vol=float(getattr(self, "_last_valid_vol", self.garch.target_vol)),
                switch_stability_ema=float(getattr(self, "_switch_stability_ema", 1.0)),
                execution_side="flat",
                extended_schema=self._emit_extended_schema,
                range_ticks=self.range_ticks_int,
                include_signal_valid=True,
                signal_valid=False,
                engine_id=self._metrics_engine_id,
            )
            _observe_latency()
            return output
        raw_x_size = -1
        try:
            raw_x_size = int(np.ravel(np.asarray(x_t, dtype=float)).size)
        except Exception:
            raw_x_size = -1
        feature_coerce_failed = raw_x_size != self.n_features
        try:
            x_t = _coerce_1d_vector(
                x_t,
                expected_size=self.n_features,
                name="update x_t",
            )
        except (ValueError, TypeError):
            feature_coerce_failed = True
            x_t = np.full(self.n_features, np.nan, dtype=float)

        # Repair corrupted risk state before it can contaminate the next tick.
        if not np.all(np.isfinite(self.garch_var)):
            self.garch_var = self._stationary_garch_var()

        anchor_advanced = False
        if self._last_timestamp is None or current_ts is None:
            time_delta = self._last_valid_dt
            if current_ts is not None:
                anchor_advanced = True
        else:
            raw_dt = current_ts - self._last_timestamp
            if raw_dt < 0.0:
                self._warn_rate_limited(
                    key="timestamp_regression",
                    message=(
                        f"Timestamp regression detected (current={current_ts}, last={self._last_timestamp}); "
                        "using last_valid_dt and preserving previous timestamp anchor."
                    ),
                    cooldown_s=30.0,
                )
                time_delta = float(self._last_valid_dt)
            else:
                time_delta = min(raw_dt, self._MAX_DT)
                anchor_advanced = True
        decay_dt = max(time_delta, 0.0)
        if time_delta > 0:
            self._last_valid_dt = time_delta
        if anchor_advanced:
            self._update_timestamp_anchor(current_ts)

        is_dim_fail = feature_coerce_failed or (x_t.ndim != 1) or (x_t.shape[0] != self.n_features)
        n_corrupt = 0 if is_dim_fail else int(np.sum(~np.isfinite(x_t)))

        # Make MTF degradation explicit in telemetry even when the tick remains usable.
        # Safeguard: Added check for expected_weighted_tf_count > 1 to prevent false positives on single-TF feeds.
        if mtf_data is not None and valid_tf_count == 1 and not is_dim_fail and n_corrupt == 0:
            if expected_weighted_tf_count > 1:
                mtf_partial_survival = True

        if mtf_partial_survival and mtf_data is not None:
            mtf_degradation_reasons["telemetry_partial_survival"] += 1

        # --- FIX #2: If execution features fail, still preserve macro posterior ---
        use_fused_macro_only = False
        if (is_dim_fail or n_corrupt > 0) and mtf_data is not None and safe_nhhmm_posterior is not None:
            self.nhhmm_prior = _normalize_prob_vector(safe_nhhmm_posterior)
            use_fused_macro_only = True
            feed_status = "MTF_FUSED_BASE_FEATURE_INVALID"

        if (is_dim_fail or n_corrupt > 0) and not use_fused_macro_only:
            if mtf_data is None:
                self._warn_rate_limited(
                    key="single_tf_nhhmm_failure",
                    message="Single-TF features invalid; using deterministic uniform posterior fallback.",
                    cooldown_s=15.0,
                )
                self.nhhmm_prior = np.ones(self.K, dtype=float) / self.K
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
            halted = _halt_if_breaker_after_heal()
            if halted is not None:
                return halted
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
                    ENGINE_FEED_STATUS.labels(self._metrics_engine_id, feed_status).inc()
                    ENGINE_HEALTH.labels(self._metrics_engine_id).set(0)

            self._obs_observe("data_failure", "high", {"feed_status": feed_status})

            output = _build_output(
                # expose MTF degradation state without changing schema shape
                # via risk_metrics.feed_status + comment trail in warnings
                regime_idx=int(self.current_regime_idx) if self.current_regime_idx is not None else -1,
                regime_label='UNKNOWN',
                execution_mode='fail_safe',
                trend_strength=safe_trend_strength,
                risk_level=1.0,
                confidence=0.0,
                conviction=0.0,
                edge_score=0.0,
                probabilities={'bull': 0.0, 'bear': 0.0, 'crisis': 1.0},
                macro_probs=self.nhhmm_prior.tolist(),
                position_size=0.0,
                expected_vol=expected_vol_frozen,
                raw_size=0.0,
                is_toxic=True,
                garch_regime_probs=self.garch_prob.tolist(),
                feed_status=feed_status,
                engine_status=str(getattr(self, "_determinism_status", "OK")),
                last_valid_vol=float(getattr(self, "_last_valid_vol", self.garch.target_vol)),
                switch_stability_ema=float(getattr(self, "_switch_stability_ema", 1.0)),
                execution_side='flat',
                extended_schema=self._emit_extended_schema,
                range_ticks=self.range_ticks_int,
                include_signal_valid=True,
                signal_valid=False,
                engine_id=self._metrics_engine_id,
            )
            if obs_sample and not getattr(self, "_is_replay", False):
                self._replay_record("update_end", {"regime": "UNKNOWN"})
            _observe_latency()
            return output

        if not use_fused_macro_only:
            feed_status = 'OK'
        if mtf_partial_survival and not use_fused_macro_only:
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
        if use_fused_macro_only:
            feed_status = "MTF_FUSED_BASE_FEATURE_INVALID_MACRO_ONLY"
            if "MACRO_ONLY_FALLBACK" not in feed_status_annotations:
                feed_status_annotations.append("MACRO_ONLY_FALLBACK")

        feed_status_flags = []
        if feed_status_annotations:
            for token in feed_status_annotations:
                token_s = str(token).strip()
                if token_s and token_s not in feed_status_flags:
                    feed_status_flags.append(token_s)
        feed_status_payload = {"primary": str(feed_status), "flags": feed_status_flags}

        # OBS: feed tracking
        if _PROM_AVAILABLE and obs_sample and not getattr(self, "_is_replay", False):
            ENGINE_FEED_STATUS.labels(self._metrics_engine_id, str(feed_status)).inc()

        # Only compute if not already from MTF
        if mtf_data is None:
            try:
                nhhmm_posterior, _ = self.nhhmm.forward_pass_step(
                    y_t, x_t, self.nhhmm_prior
                )
            except Exception as exc:
                self._warn_rate_limited(
                    key="single_tf_nhhmm_failure",
                    message=f"Single-TF NHHMM forward pass failed; using uniform posterior. error={exc}",
                    cooldown_s=15.0,
                )
                nhhmm_posterior = np.ones(self.K, dtype=float) / self.K
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
        self._record_posterior_update(current_ts)

        nhhmm_confidence = float(np.max(nhhmm_posterior))
        effective_bias_weight = float(np.clip(nhhmm_confidence, 0.0, 1.0))
        if use_fused_macro_only:
            sjm_probs = _normalize_prob_vector(np.asarray(nhhmm_posterior, dtype=float))
            sjm_state = int(np.argmax(sjm_probs))
            self._obs_observe("macro_only_fallback", "medium", {"reason": "base_feature_invalid"})
        else:
            sjm_x_t = np.asarray(x_t, dtype=float).copy()
            if np.isfinite(y_t) and self._sjm_reserved_feature_indices is not None:
                ret_idx, abs_idx = self._sjm_reserved_feature_indices
                sjm_x_t[ret_idx] = float(y_t)
                sjm_x_t[abs_idx] = abs(float(y_t))
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
        if not use_fused_macro_only and not np.all(np.isfinite(sjm_probs)):
            self._warn_rate_limited(
                key="sjm_non_finite",
                message=f"SJM produced non-finite probs, using last valid state",
                cooldown_s=10.0,
            )
            self._obs_observe("sjm_non_finite", "high", {"source": "update"})
            self._self_heal("E200", {"source": "sjm_non_finite"})
            halted = _halt_if_breaker_after_heal()
            if halted is not None:
                return halted

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

        elif not use_fused_macro_only:
            sjm_probs = _normalize_prob_vector(sjm_probs)
            self._last_valid_sjm_probs = sjm_probs.copy()

        if (not use_fused_macro_only) and np.isfinite(y_t):
            shock_decay = float(np.clip(self._SHOCK_MEMORY_DECAY, 0.0, 0.999))
            prev_shock_memory = float(getattr(self, "_shock_memory", 0.0))
            abs_ret = abs(float(y_t))
            baseline_vol = max(float(self._last_valid_vol), float(self.garch.target_vol), 1e-8)
            shock_threshold = 2.0 * baseline_vol
            if abs_ret >= shock_threshold:
                self._shock_memory = max(abs_ret, shock_decay * prev_shock_memory)
            else:
                evidence_decay = np.clip(0.75 + (abs_ret / max(shock_threshold, 1e-8)) * 0.2, 0.70, 0.95)
                self._shock_memory = max(abs_ret, float(prev_shock_memory * evidence_decay))
            shock_scale = max(
                float(self.garch.target_vol) * float(self._SHOCK_INTENSITY_VOL_MULT),
                0.01,
            )
            shock_intensity = float(np.clip(self._shock_memory / shock_scale, 0.0, 1.0))
            if self.K >= 3:
                sjm_probs = np.asarray(sjm_probs, dtype=float).copy()
                non_crisis_scale = max(1.0 - 0.45 * shock_intensity, 0.4)
                sjm_probs[0] *= non_crisis_scale
                sjm_probs[1] *= non_crisis_scale
                sjm_probs[2] *= (1.0 + 0.9 * shock_intensity)
                sjm_probs = _normalize_prob_vector(sjm_probs)
                sjm_state = int(np.argmax(sjm_probs))
            
        # Defense-in-depth: final normalization before argmax (AUDIT FIX ISSUE-B)
        if np.all(np.isfinite(sjm_probs)):
            sjm_probs = _normalize_prob_vector(sjm_probs)
            sjm_state = int(np.argmax(sjm_probs))
        self.current_regime_idx = sjm_state
        regime_scores = compute_hmm_regime(
            sjm_probs,
            prev_directional_label=getattr(self, "_prev_directional_label", None),
            direction_switch_gap=self._DIRECTION_SWITCH_GAP,
            last_signed_return=float(y_t),
        )
        self._prev_directional_label = self._validate_directional_label(
            regime_scores.get("directional_label"),
            "prev_directional_label_runtime",
        )
        # --- REGIME DEBUG INSTRUMENTATION (audit only) ---
        try:
            _dbg_raw_regime = str(regime_scores.get("regime", "?"))
            _dbg_directional_label = str(regime_scores.get("directional_label", "?"))
            _dbg_conviction = float(regime_scores.get("conviction", -1.0))
            _dbg_directional_margin = float(regime_scores.get("directional_margin", -1.0))
            _dbg_trend_score = float(regime_scores.get("trend_score", -1.0))
            _dbg_range_score = float(regime_scores.get("range_score", -1.0))
            _dbg_regime_edge_raw = float(regime_scores.get("edge_score", -1.0))
        except Exception:
            _dbg_raw_regime = _dbg_directional_label = "ERR"
            _dbg_conviction = _dbg_directional_margin = _dbg_trend_score = _dbg_range_score = _dbg_regime_edge_raw = -1.0
        try:
            _dbg_regime_edge_smoothed = -1.0
            _dbg_early_override_fired = _dbg_return_ema_hint = False
            _dbg_edge_below_thresh = _dbg_conviction_below_055 = _dbg_margin_below_thresh = False
            _dbg_switch_gate = _dbg_switch_strength = -1.0
            _dbg_cooldown_ok = _dbg_persistence_ok = _dbg_conviction_ok = _dbg_switch_blocked = False
            _dbg_confirmed_pre_switch = _dbg_confirmed_after_switch = "?"
            _dbg_confirmed_pre_smoother = _dbg_smoother_output = "?"
            _dbg_adaptive_conv_threshold = -1.0
        except Exception:
            pass
        # --------------------------------------------------
        regime = regime_scores["regime"]
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
        # --- REGIME DEBUG INSTRUMENTATION ---
        try:
            _dbg_regime_edge_smoothed = float(regime_edge)
        except Exception:
            _dbg_regime_edge_smoothed = -1.0
        # ------------------------------------
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
            return_ema_directional_hint = (
                abs(float(getattr(self, "_return_ema", 0.0))) >= 5.0e-4
                and float(getattr(self, "_abs_return_ema", 0.0)) >= 5.0e-4
            )
            weak_directional_evidence = (
                not return_ema_directional_hint
                and regime_edge < self._EDGE_MIN_DIRECTIONAL_CONFIDENCE
                and regime_scores["conviction"] < 0.55
                and directional_margin < max(self._DIRECTION_SWITCH_GAP, 0.04)
            )
            if weak_directional_evidence:
                confirmed_regime = "RANGE"
            # --- REGIME DEBUG INSTRUMENTATION ---
            try:
                _dbg_early_override_fired = bool(weak_directional_evidence)
                _dbg_return_ema_hint = bool(return_ema_directional_hint)
                _dbg_edge_below_thresh = bool(regime_edge < self._EDGE_MIN_DIRECTIONAL_CONFIDENCE)
                _dbg_conviction_below_055 = bool(regime_scores["conviction"] < 0.55)
                _dbg_margin_below_thresh = bool(directional_margin < max(self._DIRECTION_SWITCH_GAP, 0.04))
            except Exception:
                _dbg_early_override_fired = _dbg_return_ema_hint = False
                _dbg_edge_below_thresh = _dbg_conviction_below_055 = _dbg_margin_below_thresh = False
            # ------------------------------------

        # --- REGIME DEBUG INSTRUMENTATION ---
        try:
            _dbg_confirmed_pre_switch = str(confirmed_regime)
        except Exception:
            _dbg_confirmed_pre_switch = "ERR"
        # ------------------------------------

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
        pre_update_baseline_vol = float(
            np.sqrt(np.dot(self._smoothed_garch_prob, np.clip(self.garch_var, 1e-8, None)))
        )
        shock_threshold_pre = max(2.2 * pre_update_baseline_vol, 0.008)
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
            # Adaptive conviction gate calibrated from audit distributions.
            _uncertainty = float(regime_scores.get("uncertainty", 0.5))
            _adaptive_conv_threshold = max(
                self._CONV_THRESHOLD_FLOOR,
                self._CONV_THRESHOLD_BASE * (1.0 - self._CONV_THRESHOLD_UNCERTAINTY_WEIGHT * _uncertainty),
            )
            conviction_ok = regime_scores["conviction"] >= _adaptive_conv_threshold
            toxic_override = confirmed_regime == "TOXIC"

            # --- REGIME DEBUG INSTRUMENTATION ---
            try:
                _dbg_switch_gate = float(switch_gate)
                _dbg_switch_strength = float(switch_strength)
                _dbg_cooldown_ok = bool(cooldown_ok)
                _dbg_persistence_ok = bool(persistence_ok)
                _dbg_conviction_ok = bool(conviction_ok)
                _dbg_adaptive_conv_threshold = float(_adaptive_conv_threshold)
                _dbg_switch_blocked = (
                    (not cooldown_ok or switch_strength < switch_gate)
                    and not (persistence_ok and conviction_ok)
                )
            except Exception:
                _dbg_switch_gate = _dbg_switch_strength = -1.0
                _dbg_cooldown_ok = _dbg_persistence_ok = _dbg_conviction_ok = _dbg_switch_blocked = False
            # ------------------------------------

            if not toxic_override:
                if (not cooldown_ok or switch_strength < switch_gate) and not (persistence_ok and conviction_ok):
                    confirmed_regime = prev_regime_snapshot
                    confirmed_regime_idx = self._confirmed_regime_idx
                    regime_changed = False

        # --- REGIME DEBUG INSTRUMENTATION ---
        try:
            _dbg_confirmed_after_switch = str(confirmed_regime)
        except Exception:
            _dbg_confirmed_after_switch = "ERR"
        # ------------------------------------

        if getattr(self, "_regime_smoother", None) is not None:
            _smoothed_regime, self._regime_state_probs = self._regime_smoother.update(
                regime_scores,
                confirmed_regime,
            )
            # --- REGIME DEBUG INSTRUMENTATION ---
            try:
                _dbg_confirmed_pre_smoother = str(confirmed_regime)
                _dbg_smoother_output = str(getattr(self, "_regime_state_probs", None))
            except Exception:
                _dbg_confirmed_pre_smoother = _dbg_smoother_output = "ERR"
            # ------------------------------------
            assert confirmed_regime == self._validate_regime_label(confirmed_regime, "confirmed_regime_for_smoother")


        self._prev_raw_regime = regime
        self._confirmed_regime = confirmed_regime
        self._confirmed_regime_idx = confirmed_regime_idx

        if regime_changed:
            self._set_regime_change_timestamp(current_ts)

        self._prev_regime = confirmed_regime

        if confirmed_regime != prev_regime_snapshot:
            if confirmed_regime == "RANGE":
                self._range_anchor_size = abs(self.last_signed_position_size)
                self._in_range = True
            elif confirmed_regime == "TREND":
                self.range_ticks = 0.0
                self._in_range = False
            elif confirmed_regime in ("BEAR", "TOXIC"):
                self.range_ticks = 0.0
                self.range_ticks_int = 0
                self._in_range = False

        if confirmed_regime == "RANGE":
            self.range_ticks += max(float(decay_dt), 0.0)
            self.range_ticks = min(self.range_ticks, 1_000_000.0)

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
            and confirmed_regime in ("TREND", "BEAR")
        ):
            if alpha_bias > 0.2 and confirmed_regime == "TREND":
                execution_side = "long"
            elif alpha_bias < -0.2 and confirmed_regime == "BEAR":
                execution_side = "short"
        elif (
            alpha_conf > 0.65
            and abs(alpha_bias) > 0.2
            and abs(base_trend_strength) > 0.15
            and confirmed_regime == "RANGE"
        ):
            LOGGER.debug(
                "update: alpha override suppressed in RANGE regime (alpha_bias=%.3f, alpha_conf=%.3f) — using range sizing.",
                alpha_bias,
                alpha_conf,
            )

        # Confidence-collapse halt must happen before risk-state mutation.
        collapse_signal = (
            regime_scores["conviction"] < 0.05
            and regime_scores["confidence"] < self._CONFIDENCE_COLLAPSE_THRESHOLD
        )
        if self._is_confidence_collapse_warmup(current_ts):
            if collapse_signal:
                self._obs_observe(
                    "confidence_collapse_warmup_suppressed",
                    "medium",
                    {
                        "posterior_updates": int(getattr(self, "_posterior_update_count", 0)),
                        "first_posterior_ts": self._first_posterior_ts,
                    },
                )
            collapse_signal = False
            self._confidence_collapse_streak = 0
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

        predicted_var = np.copy(self.garch_var)
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
        post_update_baseline_vol = float(
            np.sqrt(np.dot(self._smoothed_garch_prob, np.clip(self.garch_var, 1e-8, None)))
        )

        # FIX #2: HYBRID VOL ESTIMATION with adaptive spike response
        vol_spike = abs(y_t) > (2.0 * max(post_update_baseline_vol, 1e-8))
        if vol_spike:
            effective_prob = self.garch_prob
        else:
            effective_prob = 0.6 * self.garch_prob + 0.4 * self._smoothed_garch_prob
        effective_prob = _normalize_prob_vector(effective_prob)

        expected_var = float(np.dot(effective_prob, self.garch_var))
        expected_var = max(expected_var, 1e-8)
        expected_vol = np.sqrt(expected_var)
        expected_vol = min(expected_vol, 0.20)
        
        if np.isfinite(expected_vol) and expected_vol > 0.0:
            self._last_valid_vol = float(expected_vol)
        else:
            expected_vol = float(max(self._last_valid_vol, self._LAST_VALID_VOL_FLOOR))
            self._last_valid_vol = float(expected_vol)

        ema_decay = self._ema_decay(decay_dt)
        ema_alpha = 1.0 - ema_decay
        self._return_ema = (
            ema_decay * float(getattr(self, "_return_ema", 0.0))
            + ema_alpha * float(y_t)
        )
        self._abs_return_ema = (
            ema_decay * float(getattr(self, "_abs_return_ema", 0.0))
            + ema_alpha * abs(float(y_t))
        )

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
        # VOL TARGETING
        # ==========================================
        # Keep edge modulation in one place (edge_scaled below); target vol remains purely risk-driven.
        effective_target_vol = float(self.garch.target_vol)

        # Edge score adjustment after volatility is known.
        vol_ratio = expected_vol / max(self.garch.target_vol, 1e-8)
        edge_score = float(np.clip(
            regime_edge
            - self._EDGE_VOL_PENALTY * max(vol_ratio - 1.0, 0.0)
            - low_vol_regime_soft_penalty,
            0.0,
            1.0,
        ))
        self._last_edge_score = regime_edge

        if _PROM_AVAILABLE and obs_sample and not getattr(self, "_is_replay", False):
            ENGINE_VOL.labels(self._metrics_engine_id).set(expected_vol)
            ENGINE_CONFIDENCE.labels(self._metrics_engine_id).set(regime_scores["conviction"])
            ENGINE_RISK.labels(self._metrics_engine_id).set(regime_scores["risk_level"])
            
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
            ENGINE_POSITION.labels(self._metrics_engine_id).set(position_size)

        # FIX #4: MORE RESPONSIVE SHOCK DETECTION
        shock_threshold = max(2.2 * post_update_baseline_vol, 0.008)
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
            ENGINE_HEALTH.labels(self._metrics_engine_id).set(1 if health == "OK" else 0)

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

        range_signed_size = 0.0
        if confirmed_regime == "RANGE":
            prior_sign = np.sign(self.last_signed_position_size)

            if self._range_anchor_size < self._MIN_SIGNED_TRADE_SIZE:
                rebuilt_anchor = position_size * max(abs(base_trend_strength), 0.3)
                self._range_anchor_size = float(min(rebuilt_anchor, 0.5 * position_size))

            if prior_sign == 0.0:
                if abs(base_trend_strength) > 0.1:
                    prior_sign = np.sign(base_trend_strength)
                else:
                    directional_label = str(regime_scores.get("directional_label", ""))
                    if directional_label == "TREND":
                        prior_sign = 1.0
                    elif directional_label == "BEAR":
                        prior_sign = -1.0
                    elif abs(alpha_bias) > 1e-12:
                        prior_sign = float(np.sign(alpha_bias))
                    else:
                        prior_sign = 1.0

            anchor_size = max(self._range_anchor_size, 1e-8)
            if anchor_size < self._MIN_SIGNED_TRADE_SIZE:
                anchor_size = position_size

            vol_ratio = expected_vol / max(self.garch.target_vol, 1e-8)
            if not np.isfinite(vol_ratio):
                vol_ratio = 1.0

            decay = self._RANGE_SIGNED_DECAY / (1.0 + vol_ratio)
            decay *= float(max(np.exp(-self._RANGE_SIGNED_DECAY_LAMBDA * self.range_ticks), 1e-3))
            decay = max(decay, self._RANGE_DECAY_FLOOR_K)
            if not np.isfinite(decay):
                decay = 0.1

            range_signed_size = float(
                prior_sign * min(anchor_size, position_size) * decay
            )
            if not np.isfinite(range_signed_size):
                range_signed_size = 0.0

            dynamic_min = max(
                self._MIN_SIGNED_TRADE_SIZE,
                0.1 * position_size
            )
            if abs(range_signed_size) < dynamic_min:
                if position_size >= self._MIN_SIGNED_TRADE_SIZE:
                    range_signed_size = float(
                        np.sign(prior_sign) * min(dynamic_min, position_size)
                    )
                else:
                    range_signed_size = 0.0

        # Final telemetry hygiene
        if not np.isfinite(position_size):
            position_size = 0.0

        # HARD SAFETY: prevent alpha leakage into sizing logic
        effective_trend_strength = base_trend_strength

        # --- FIX: persist last valid trend strength ---
        if np.isfinite(effective_trend_strength):
            self._last_effective_trend_strength = float(effective_trend_strength)

        # Final execution guard (single-source execution intent).
        if execution_side in ("long", "short") and edge_score < self._EDGE_MIN_DIRECTIONAL_CONFIDENCE:
            execution_side = "flat"
        final_execution_side = execution_side

        position_size = float(np.clip(position_size, 0.0, self._MAX_POSITION_SIZE))
        if final_execution_side == "flat":
            position_size = 0.0
            signed_position_size = 0.0
        elif final_execution_side == "long":
            signed_position_size = abs(position_size)
        elif final_execution_side == "short":
            signed_position_size = -abs(position_size)
        elif final_execution_side == "range_mean_revert":
            signed_position_size = float(np.clip(range_signed_size, -position_size, position_size))
        else:
            self._warn_rate_limited(
                key="invalid_execution_side",
                message=f"Invalid execution_side='{final_execution_side}' resolved to flat.",
                cooldown_s=30.0,
            )
            final_execution_side = "flat"
            position_size = 0.0
            signed_position_size = 0.0

        if not np.isfinite(signed_position_size):
            signed_position_size = 0.0
        signed_position_size = float(np.clip(signed_position_size, -position_size, position_size))
        if not np.isfinite(expected_vol):
            expected_vol = self.garch.target_vol
        if not np.isfinite(raw_size):
            raw_size = 0.0

        self.last_signed_position_size = signed_position_size
        self._last_signed_return = float(y_t)
        if getattr(self, "_just_restored", False):
            self._just_restored = False
            self.sjm._just_restored = False
        rticks = self.range_ticks_int

        # OBS: latency tracking
        _observe_latency()

        self._obs_observe(
            "update",
            {"OK": "low", "DEGRADED": "medium", "RISK": "high", "FAIL": "critical"}.get(self._last_health, "low"),
            {"feed_status": str(feed_status), "regime": confirmed_regime, "feed_flags": feed_status_flags},
        )

        # Keep regime label and returned index semantically aligned.
        # RANGE and TOXIC do not map cleanly to the 3-state SJM index space.
        final_regime_idx = -1
        if confirmed_regime in ("TREND", "BEAR"):
            final_regime_idx = int(self.current_regime_idx) if self.current_regime_idx is not None else -1

        # --- REGIME DEBUG LOG (instrumentation only, rate-limited) ---
        try:
            if not getattr(self, "_is_replay", False):
                _want_raw = _dbg_raw_regime
                _want_final = str(confirmed_regime)
                _is_suppressed = (
                    _want_raw in ("TREND", "BEAR")
                    and _want_final not in ("TREND", "BEAR")
                )
                _audit_record = {
                    "tick": int(self._tick_id),
                    "raw_regime": _want_raw,
                    "confirmed_before_switch": str(_dbg_confirmed_pre_switch),
                    "confirmed_after_switch": str(_dbg_confirmed_after_switch),
                    "confirmed_after_smoother": _want_final,
                    "directional_label": _dbg_directional_label,
                    "timestamp": float(data.get("timestamp", time.time())) if isinstance(data, dict) else float(time.time()),
                    "regime": _want_final,
                    "conviction": _dbg_conviction,
                    "certainty_score": float(regime_scores.get("certainty_score", -1.0)),
                    "directional_confidence": float(regime_scores.get("directional_confidence", -1.0)),
                    "uncertainty": float(regime_scores.get("uncertainty", -1.0)),
                    "edge_score_raw": float(_dbg_regime_edge_raw),
                    "directional_margin_val": float(_dbg_directional_margin),
                    "adaptive_conv_threshold": _dbg_adaptive_conv_threshold,
                    "directional_margin": _dbg_directional_margin,
                    "trend_score": _dbg_trend_score,
                    "range_score": _dbg_range_score,
                    "regime_edge_raw": _dbg_regime_edge_raw,
                    "regime_edge_smoothed": _dbg_regime_edge_smoothed,
                    "regime_persistence": int(getattr(self, "_regime_persistence", 0)),
                    "switch_min_persistence": int(self._SWITCH_MIN_PERSISTENCE),
                    "switch_gate": _dbg_switch_gate,
                    "switch_strength": _dbg_switch_strength,
                    "cooldown_ok": _dbg_cooldown_ok,
                    "persistence_ok": _dbg_persistence_ok,
                    "conviction_ok": _dbg_conviction_ok,
                    "switch_blocked": _dbg_switch_blocked,
                }
                if not hasattr(self, "_regime_audit_log"):
                    self._regime_audit_log = []
                self._regime_audit_log.append(_audit_record)
                if len(self._regime_audit_log) > 5000:
                    self._regime_audit_log = self._regime_audit_log[-5000:]
                if _is_suppressed:
                    _dbg_record = {
                        "tick": int(self._tick_id),
                        "raw_regime": _want_raw,
                        "confirmed_regime": _want_final,
                        "directional_label": _dbg_directional_label,
                        "conviction": _dbg_conviction,
                        "uncertainty": float(regime_scores.get("uncertainty", -1.0)),
                        "edge_score_raw": float(_dbg_regime_edge_raw),
                        "directional_margin_val": float(_dbg_directional_margin),
                        "directional_margin": _dbg_directional_margin,
                        "trend_score": _dbg_trend_score,
                        "range_score": _dbg_range_score,
                        "regime_edge_raw": _dbg_regime_edge_raw,
                        "regime_edge_smoothed": _dbg_regime_edge_smoothed,
                        "early_override_fired": _dbg_early_override_fired,
                        "early_override_detail": {
                            "return_ema_hint": _dbg_return_ema_hint,
                            "edge_below_thresh": _dbg_edge_below_thresh,
                            "conviction_below_055": _dbg_conviction_below_055,
                            "margin_below_thresh": _dbg_margin_below_thresh,
                        },
                        "regime_changed": bool(regime_changed),
                        "switch_gate": _dbg_switch_gate,
                        "switch_strength": _dbg_switch_strength,
                        "cooldown_ok": _dbg_cooldown_ok,
                        "persistence_ok": _dbg_persistence_ok,
                        "conviction_ok": _dbg_conviction_ok,
                        "switch_blocked": _dbg_switch_blocked,
                        "prev_regime": str(prev_regime_snapshot),
                        "last_edge_score_before": float(getattr(self, "_last_edge_score", -1.0)),
                        "edge_min_directional": float(self._EDGE_MIN_DIRECTIONAL_CONFIDENCE),
                        "direction_switch_gap": float(self._DIRECTION_SWITCH_GAP),
                        "regime_persistence": int(getattr(self, "_regime_persistence", 0)),
                        "switch_min_persistence": int(self._SWITCH_MIN_PERSISTENCE),
                        "adaptive_conv_threshold": _dbg_adaptive_conv_threshold,
                        "confirmed_before_switch": str(_dbg_confirmed_pre_switch),
                        "confirmed_after_switch": str(_dbg_confirmed_after_switch),
                        "confirmed_after_smoother": _want_final,
                    }
                    # Accumulate suppression records into engine attribute for test inspection
                    if not hasattr(self, "_regime_suppression_log"):
                        self._regime_suppression_log = []
                    self._regime_suppression_log.append(_dbg_record)
                    # Trim to last 500 records
                    if len(self._regime_suppression_log) > 500:
                        self._regime_suppression_log = self._regime_suppression_log[-500:]
                    LOGGER.debug(
                        "REGIME_SUPPRESSION tick=%d raw=%s confirmed=%s "
                        "early_override=%s switch_blocked=%s conviction=%.3f "
                        "edge_smooth=%.3f switch_strength=%.3f switch_gate=%.3f",
                        _dbg_record["tick"], _want_raw, _want_final,
                        _dbg_early_override_fired, _dbg_switch_blocked,
                        _dbg_conviction, _dbg_regime_edge_smoothed,
                        _dbg_switch_strength, _dbg_switch_gate,
                    )
        except Exception:
            pass  # instrumentation must never affect trading path
        # ---------------------------------------------------------------

        output = _build_output(
            regime_idx=final_regime_idx,
            regime_label=confirmed_regime,
            execution_mode=execution_mode,
            trend_strength=float(effective_trend_strength),
            risk_level=float(regime_scores["risk_level"]),
            confidence=float(regime_scores["confidence"]),
            conviction=float(regime_scores["conviction"]),
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
                feed_status=feed_status_payload,
                engine_status=str(getattr(self, "_determinism_status", "OK")),
                last_valid_vol=float(self._last_valid_vol),
            switch_stability_ema=float(self._switch_stability_ema),
            execution_side=final_execution_side,
            extended_schema=self._emit_extended_schema,
            range_ticks=rticks,
            include_signal_valid=True,
            signal_valid=bool(self._is_signal_permitted()),
            weights_loaded=bool(self._weights_loaded),
            calibration_valid=bool(getattr(self, "_calibration_valid", False)),
            production_valid=bool(getattr(self, "_production_valid", False)),
            research_mode=bool(getattr(self, "_research_mode", False)),
            calibration_status=str(getattr(self, "_calibration_status", "uncalibrated")),
            engine_id=self._metrics_engine_id,
        )
        if not self._is_signal_permitted():
            output["signal_valid"] = False
            output["execution_mode"] = "halt"
            output["position_size"] = 0.0
            output["signed_position_size"] = 0.0
            gated_feed_status = (
                "UNCALIBRATED_WEIGHTS" if not self._weights_loaded
                else "RESEARCH_CALIBRATION" if getattr(self, "_research_mode", False)
                else "INVALID_CALIBRATION_PROVENANCE"
            )
            self._set_feed_status(output, gated_feed_status)
            self.last_signed_position_size = 0.0
        if str(getattr(self, "_engine_status", "OK")) == "DEGRADED":
            output["signal_valid"] = False
        if obs_sample and not getattr(self, "_is_replay", False):
            self._replay_record("update_end", {"regime": confirmed_regime})
            if self._replay_engine is not None:
                self._replay_engine.record_decision_trace({
                    "event_id": None,
                    "tick_id": int(self._tick_id),
                    "engine_id": str(self.engine_id),
                    "signal_type": "update_end",
                    "regime_label": str(confirmed_regime),
                    "regime_confidence": float(regime_scores.get("conviction", 0.0)),
                    "position_size": float(position_size),
                    "signed_position_size": float(signed_position_size),
                    "execution_mode": str(execution_mode),
                    "execution_side": str(final_execution_side),
                    "edge_score": float(edge_score),
                    "conviction": float(regime_scores.get("conviction", 0.0)),
                    "risk_level": float(regime_scores.get("risk_level", 0.0)),
                    "feed_status": str(feed_status),
                    "engine_status": str(getattr(self, "_determinism_status", "OK")),
                    "timestamp_ns": int(time.time_ns()),
                    "outcome_event_id": None,
                    "return_ema": float(self._return_ema),
                    "abs_return_ema": float(self._abs_return_ema),
                    "shock_memory": float(self._shock_memory),
                    "switch_stability_ema": float(self._switch_stability_ema),
                    "loss_streak": int(self._loss_streak),
                    "equity": float(self._equity),
                    "drawdown": float(self._drawdown),
                })
        if _PROM_AVAILABLE and not getattr(self, "_is_replay", False):
            REGIME_COUNTER.labels(self._metrics_engine_id, confirmed_regime).inc()
        if self._replay_engine is not None and (self._tick_id % 100 == 0) and not getattr(self, "_is_replay", False):
            try:
                snapshot_payload = self._capture_snapshot_payload_unlocked(confirmed_regime)
                self._enqueue_snapshot(snapshot_payload)
            except Exception as exc:
                self._warn_rate_limited("snapshot_emit_failure", f"Snapshot emission failed: {exc}", cooldown_s=30.0)
        return output

    # ==========================================
    # SNAPSHOT RESTORE (DETERMINISTIC REPLAY)
    # ==========================================
    @_synchronized
    def load_snapshot(self, snapshot: Dict[str, Any]) -> None:
        try:
            incoming = snapshot if isinstance(snapshot, dict) else {}
            state = incoming.get("state", incoming)
            if not isinstance(state, dict):
                raise ValueError("snapshot state must be a dict")

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

            incoming_rng_state = None
            if "_engine_rng_state" in state:
                incoming_rng_state = state["_engine_rng_state"]
            elif "engine_rng_state" in engine_state:
                incoming_rng_state = engine_state["engine_rng_state"]

            if "_engine_rng_type" in state and getattr(self, "_rng", None) is not None:
                if type(self._rng.bit_generator).__name__ != state["_engine_rng_type"]:
                    raise ValueError("snapshot RNG type mismatch")

            # Validate incoming RNG payload before mutating self.
            validated_rng_state = None
            if incoming_rng_state is not None and getattr(self, "_rng", None) is not None:
                try:
                    validated_rng_state = self._validate_rng_state_payload(incoming_rng_state)
                except Exception as exc:
                    self._mark_determinism_failure()
                    raise ValueError(f"snapshot RNG state invalid: {exc}") from exc

            # Validate-then-swap: hydrate a staging engine first.
            staging = AdvancedRegimeEngine(
                n_states=int(self._init_params.get("n_states", self.K)),
                n_features=int(self._init_params.get("n_features", self.n_features)),
                target_vol=float(self._init_params.get("target_vol", self.garch.target_vol)),
                allow_igarch=bool(self._allow_igarch),
                regime_prob_floor=float(self.garch._REGIME_PROB_FLOOR),
                emit_extended_schema=bool(self._emit_extended_schema),
                strict_mtf_keys=bool(self._strict_mtf_keys),
                mtf_weights=copy.deepcopy(self.mtf_weights),
                sjm_reserved_feature_indices=self._sjm_reserved_feature_indices,
                allow_timestamp_free_pnl=bool(self._allow_timestamp_free_pnl),
                max_price_staleness_ticks=int(self._max_price_staleness_ticks),
                shock_warmup_ticks=int(self._shock_warmup_ticks),
                shock_warmup_seconds=float(self._shock_warmup_seconds),
                shock_startup_multiplier=float(self._shock_startup_multiplier),
                shock_startup_vol_floor_mult=float(self._shock_startup_vol_floor_mult),
                seed=self._rng_seed,
                engine_id=self.engine_id,
                enable_background_workers=False,
            )
            try:
                staging._is_replay = True
                staging._load_state_inplace(copy.deepcopy(engine_state))
                if validated_rng_state is not None and getattr(staging, "_rng", None) is not None:
                    staging._rng.bit_generator.state = validated_rng_state
                committed_state = staging.serialize_state()
                committed_rng_state = (
                    copy.deepcopy(staging._rng.bit_generator.state)
                    if getattr(staging, "_rng", None) is not None else None
                )
                # Deterministic replay probe: same restored state must produce identical next output.
                probe_input = {
                    "return": 0.0,
                    "features": np.zeros(self.n_features, dtype=float).tolist(),
                }
                verifier_a = AdvancedRegimeEngine(
                    n_states=int(self._init_params.get("n_states", self.K)),
                    n_features=int(self._init_params.get("n_features", self.n_features)),
                    target_vol=float(self._init_params.get("target_vol", self.garch.target_vol)),
                    allow_igarch=bool(self._allow_igarch),
                    regime_prob_floor=float(self.garch._REGIME_PROB_FLOOR),
                    emit_extended_schema=bool(self._emit_extended_schema),
                    strict_mtf_keys=bool(self._strict_mtf_keys),
                    mtf_weights=copy.deepcopy(self.mtf_weights),
                    sjm_reserved_feature_indices=self._sjm_reserved_feature_indices,
                    allow_timestamp_free_pnl=bool(self._allow_timestamp_free_pnl),
                    max_price_staleness_ticks=int(self._max_price_staleness_ticks),
                    shock_warmup_ticks=int(self._shock_warmup_ticks),
                    shock_warmup_seconds=float(self._shock_warmup_seconds),
                    shock_startup_multiplier=float(self._shock_startup_multiplier),
                    shock_startup_vol_floor_mult=float(self._shock_startup_vol_floor_mult),
                    seed=self._rng_seed,
                    engine_id=self.engine_id,
                    enable_background_workers=False,
                )
                verifier_b = AdvancedRegimeEngine(
                    n_states=int(self._init_params.get("n_states", self.K)),
                    n_features=int(self._init_params.get("n_features", self.n_features)),
                    target_vol=float(self._init_params.get("target_vol", self.garch.target_vol)),
                    allow_igarch=bool(self._allow_igarch),
                    regime_prob_floor=float(self.garch._REGIME_PROB_FLOOR),
                    emit_extended_schema=bool(self._emit_extended_schema),
                    strict_mtf_keys=bool(self._strict_mtf_keys),
                    mtf_weights=copy.deepcopy(self.mtf_weights),
                    sjm_reserved_feature_indices=self._sjm_reserved_feature_indices,
                    allow_timestamp_free_pnl=bool(self._allow_timestamp_free_pnl),
                    max_price_staleness_ticks=int(self._max_price_staleness_ticks),
                    shock_warmup_ticks=int(self._shock_warmup_ticks),
                    shock_warmup_seconds=float(self._shock_warmup_seconds),
                    shock_startup_multiplier=float(self._shock_startup_multiplier),
                    shock_startup_vol_floor_mult=float(self._shock_startup_vol_floor_mult),
                    seed=self._rng_seed,
                    engine_id=self.engine_id,
                    enable_background_workers=False,
                )
                try:
                    verifier_a._is_replay = True
                    verifier_b._is_replay = True
                    verifier_a._load_state_inplace(copy.deepcopy(committed_state))
                    verifier_b._load_state_inplace(copy.deepcopy(committed_state))
                    if committed_rng_state is not None and getattr(verifier_a, "_rng", None) is not None:
                        verifier_a._rng.bit_generator.state = copy.deepcopy(committed_rng_state)
                    if committed_rng_state is not None and getattr(verifier_b, "_rng", None) is not None:
                        verifier_b._rng.bit_generator.state = copy.deepcopy(committed_rng_state)
                    out_a = verifier_a.update(copy.deepcopy(probe_input))
                    out_b = verifier_b.update(copy.deepcopy(probe_input))
                    if self._state_hash(out_a) != self._state_hash(out_b):
                        raise ValueError("snapshot deterministic replay probe mismatch")
                finally:
                    verifier_a._shutdown_warning_worker()
                    verifier_a._shutdown_snapshot_worker()
                    verifier_b._shutdown_warning_worker()
                    verifier_b._shutdown_snapshot_worker()
            finally:
                staging._shutdown_warning_worker()
                staging._shutdown_snapshot_worker()

            self._load_state_inplace(committed_state)
            if committed_rng_state is not None and getattr(self, "_rng", None) is not None:
                self._rng.bit_generator.state = committed_rng_state
            self._mark_determinism_success()
            return
        except Exception as e:
            if "RNG" in str(e).upper():
                self._mark_determinism_failure()
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
            return

    # ==========================================
    # 🚨 CIRCUIT BREAKER TRIGGER
    # ==========================================
    def _trigger_circuit_breaker(self, reason: str):
        current_tick = int(getattr(self, "_tick_id", -1))
        reason_text = str(reason)
        trigger_value = float(getattr(self, "_drawdown", 0.0))
        self._cb_trigger_history.append((time.time(), reason_text, trigger_value))
        if self._circuit_breaker_active:
            try:
                LOGGER.warning(
                    "CIRCUIT_BREAKER_SUPPRESSED reason=%s active_reason=%s trigger_tick=%s current_tick=%s",
                    reason_text,
                    self._circuit_breaker_reason,
                    self._circuit_breaker_trigger_tick,
                    current_tick,
                )
            except Exception:
                self._warn_rate_limited("circuit_breaker_log_failure", "Circuit breaker logging failed", cooldown_s=30.0)
            return
        if int(getattr(self, "_circuit_breaker_trigger_tick", -1)) == current_tick:
            return
        self._circuit_breaker_active = True
        self._circuit_breaker_reason = reason_text
        self._circuit_breaker_trigger_tick = current_tick
        self._healing_counter = 0
        if not getattr(self, "_is_replay", False):
            self._replay_record("circuit_breaker", {"reason": reason_text})

        try:
            if not getattr(self, "_is_replay", False):
                LOGGER.critical(f"[CIRCUIT BREAKER TRIGGERED] Reason={reason_text}")
        except Exception:
            self._warn_rate_limited("circuit_breaker_log_failure", "Circuit breaker logging failed", cooldown_s=30.0)

    # ==========================================
    # 🔄 SELF HEALING SYSTEM
    # ==========================================
    def _self_heal(
        self,
        error_code: str | None = None,
        context: Dict[str, Any] | None = None,
        reset_price_anchor: bool = False,
    ) -> str:
        """
        Best-effort healing.

        Internal-only helper: must be called while holding self._lock (typically from update()).
        - Called without an error_code: preserves existing circuit-breaker recovery behavior.
        - Called with an error_code: applies category-aware recovery action.
        """
        lock_owned_by_caller = bool(getattr(self._lock, "_is_owned", lambda: False)())
        acquired_for_call = False
        if not lock_owned_by_caller:
            self._lock.acquire()
            acquired_for_call = True

        def _run_side_effects_unlocked() -> None:
            # Exactly one lock level is owned here: either by this direct call or
            # by the synchronized caller. Release that known-owned level while
            # replay/logging side effects run, then restore it before resuming
            # stateful work. This avoids release-on-unowned-lock crashes.
            self._lock.release()
            try:
                self._run_self_heal_side_effects(side_effects)
            finally:
                self._lock.acquire()

        side_effects: List[tuple[str, Any]] = []
        try:
            self._healing_count = int(getattr(self, "_healing_count", 0)) + 1
            self._last_healing_error = error_code
            self._last_healing_context = dict(context or {})
            if getattr(self, "_is_replay", False):
                context = dict(context or {})

            side_effects.append(("log_warning", "[SELF HEALING INITIATED]"))
            _preserved_valid_return_count = int(getattr(self, "_valid_return_count", 0))
            _preserved_first_valid_return_ts = getattr(self, "_first_valid_return_ts", None)
            _preserved_posterior_update_count = int(getattr(self, "_posterior_update_count", 0))
            _preserved_first_posterior_ts = getattr(self, "_first_posterior_ts", None)

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

                # Reset PnL state without erasing cumulative risk memory.
                self._equity = max(float(getattr(self, "_equity", 1.0)), self._MIN_EQUITY_FLOOR)
                self._equity_peak = max(float(getattr(self, "_equity_peak", self._equity)), self._equity)
                self._drawdown = float(np.clip(
                    (self._equity_peak - self._equity) / max(self._equity_peak, self._MIN_EQUITY_FLOOR),
                    0.0,
                    1.0,
                ))
                self._cumulative_drawdown = max(float(getattr(self, "_cumulative_drawdown", 0.0)), float(self._drawdown))
                self._loss_streak = 0

                # Reset memory variables
                self._shock_memory = 0.0
                self._return_ema = 0.0
                self._abs_return_ema = 0.0
                self._last_timestamp = None
                self._last_valid_dt = 1.0
                self._last_valid_sjm_probs = np.ones(self.K) / self.K
                if reset_price_anchor:
                    self._last_price = None
                    self._last_price_timestamp = None
                    self._last_price_tick_id = None
                    self._pnl_mode = None
                    side_effects.append((
                        "log_warning",
                        "[REGIME] Price anchor destroyed by self-heal (reset_price_anchor=True) — PnL tracking will reinitialize on next tick",
                    ))
                else:
                    side_effects.append(("log_debug", "[REGIME] Self-heal complete — price anchor preserved"))
                self._valid_return_count = _preserved_valid_return_count
                self._first_valid_return_ts = _preserved_first_valid_return_ts
                self._posterior_update_count = _preserved_posterior_update_count
                self._first_posterior_ts = _preserved_first_posterior_ts

                # Reset breaker
                self._circuit_breaker_active = False
                self._circuit_breaker_reason = None
                self._circuit_breaker_trigger_tick = -1
                self._healing_counter = 0
                self._confidence_collapse_streak = 0
                self._last_healing_action = "RESET_FULL"
                self._health_status = "HEALING_COMPLETE"
                self._last_heal_ts = time.time()
                side_effects.append((
                    "log_info",
                    ("_self_heal: warmup state preserved (valid_returns=%d, posteriors=%d) after recovery.",
                     _preserved_valid_return_count, _preserved_posterior_update_count),
                ))
                if not getattr(self, "_is_replay", False):
                    replay_payload = self._build_self_heal_replay_payload(error=error_code, action="RESET_FULL")
                    side_effects.append(("replay", ("self_heal", replay_payload)))
                action = self._last_healing_action
                _run_side_effects_unlocked()
                return action

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
                self.garch_var = self._stationary_garch_var()
                self.garch_prob = np.ones(2, dtype=float) / 2.0
                self._smoothed_garch_prob = np.ones(2, dtype=float) / 2.0
                self._last_valid_sjm_probs = None
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
                preserved_equity = max(float(getattr(self, "_equity", 1.0)), self._MIN_EQUITY_FLOOR)
                preserved_equity_peak = max(float(getattr(self, "_equity_peak", preserved_equity)), preserved_equity)
                preserved_cumulative_drawdown = max(
                    float(getattr(self, "_cumulative_drawdown", 0.0)),
                    float(getattr(self, "_drawdown", 0.0)),
                )
                self.reset_state()
                self._equity = preserved_equity
                self._equity_peak = preserved_equity_peak
                self._drawdown = float(np.clip(
                    (self._equity_peak - self._equity) / max(self._equity_peak, self._MIN_EQUITY_FLOOR),
                    0.0,
                    1.0,
                ))
                self._cumulative_drawdown = max(preserved_cumulative_drawdown, self._drawdown)
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
                self.nhhmm_prior = np.ones(self.K, dtype=float) / self.K
                self.last_signed_position_size = 0.0
                if not np.all(np.isfinite(self.nhhmm_prior)) or self.nhhmm_prior.shape != (self.K,):
                    self.nhhmm_prior = np.ones(self.K, dtype=float) / self.K
                action = "RESET_INPUT"

            elif category == "risk":
                self._trigger_circuit_breaker(str(err_code))
                action = "CIRCUIT_BREAK"
            else:
                # Deterministic fallback: always execute a safe degradation path.
                self.nhhmm_prior = _normalize_prob_vector(self.nhhmm_prior)
                self.garch_prob = _safe_prob_vector(self.garch_prob, 2)
                self._smoothed_garch_prob = _normalize_prob_vector(
                    _safe_prob_vector(self._smoothed_garch_prob, 2)
                )
                self._confidence_collapse_streak = 0
                action = "SKIP_AND_DEGRADE"

            if not getattr(self, "_is_replay", False):
                replay_payload = self._build_self_heal_replay_payload(error=error_code, action=action)
                side_effects.append(("replay", ("self_heal", replay_payload)))

            self._last_healing_action = action
            _run_side_effects_unlocked()
            return action

        finally:
            if acquired_for_call:
                self._lock.release()

    def _build_self_heal_replay_payload(self, error: Any, action: str) -> Dict[str, Any]:
        frozen_payload = {
            "error": error,
            "action": action,
            "state": {
                "last_healing_error": getattr(self, "_last_healing_error", None),
                "last_healing_context": copy.deepcopy(getattr(self, "_last_healing_context", {})),
                "healing_count": int(getattr(self, "_healing_count", 0)),
                "circuit_breaker_active": bool(getattr(self, "_circuit_breaker_active", False)),
                "circuit_breaker_reason": getattr(self, "_circuit_breaker_reason", None),
                "circuit_breaker_trigger_tick": int(getattr(self, "_circuit_breaker_trigger_tick", -1)),
            },
        }
        return copy.deepcopy(frozen_payload)

    def _run_self_heal_side_effects(self, side_effects: List[tuple[str, Any]]) -> None:
        for effect_type, payload in side_effects:
            try:
                if effect_type == "log_warning":
                    if not getattr(self, "_is_replay", False):
                        LOGGER.warning(payload)
                elif effect_type == "log_debug":
                    LOGGER.debug(payload)
                elif effect_type == "log_info":
                    if isinstance(payload, tuple):
                        LOGGER.info(*payload)
                    else:
                        LOGGER.info(payload)
                elif effect_type == "replay":
                    event_type, event_payload = payload
                    self._replay_record(event_type, event_payload)
            except Exception:
                self._warn_rate_limited("self_heal_log_failure", "Self-healing logging failed", cooldown_s=30.0)

#### END OF MODULE: RECOVERY COMPLETE ####

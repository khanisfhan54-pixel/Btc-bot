import numpy as np
from scipy.special import softmax
import atexit
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
    from prometheus_client import Counter as PromCounter, Gauge, Histogram
    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False

LOGGER = logging.getLogger(__name__)
_OUTPUT_SCHEMA_VERSION = "1.2.0"

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

def _normalize_prob_vector(values: np.ndarray, floor: float = 1e-12) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1 or arr.size == 0:
        raise ValueError(f"Probability vector must be a non-empty 1-D array, got shape {arr.shape}.")
    arr = np.clip(arr, floor, None)
    total = float(arr.sum())
    if not np.isfinite(total) or total <= 0.0:
        return np.ones(arr.size, dtype=float) / arr.size
    return arr / total

# ==========================================
# NEW: Schema Guard (prevents silent breakage)
# ==========================================
def _validate_output_schema(output: Dict[str, Any]) -> bool:
    try:
        if "schema_version" not in output:
            raise ValueError("missing schema_version")

        version = str(output["schema_version"]).strip()
        if version != _OUTPUT_SCHEMA_VERSION:
            raise ValueError(f"mismatch {version} != {_OUTPUT_SCHEMA_VERSION}")

        return True
    except Exception as e:
        # NEVER crash engine — degrade instead
        try:
            LOGGER.error(f"[SCHEMA VIOLATION] {e} | output={str(output)[:500]}")
        except Exception:
            pass
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
    out = {
        'schema_version': _OUTPUT_SCHEMA_VERSION,
        'regime_idx': regime_idx,
        'regime_label': regime_label,
        'trend_strength': trend_strength,
        'risk_level': risk_level,
        'confidence': confidence,
        'probabilities': probabilities,
        'macro_probs': macro_probs,
        'position_size': position_size,
        'execution_mode': execution_mode,
        'execution_side': execution_side,
        'signed_position_size': float(signed_position_size),
        'extended_schema': bool(extended_schema) if extended_schema else False,
        **({'signal_valid': bool(signal_valid)} if include_signal_valid else {}),
        
        # --- NEW: forward compatibility anchor ---
        'schema_compat': {
            "version": _OUTPUT_SCHEMA_VERSION,
            "backward_compatible": True
        },
        
        'risk_metrics': {
            'expected_volatility': float(expected_vol),
            'raw_leverage': float(raw_size),
            'last_valid_vol': float(last_valid_vol),
            'switch_stability_ema': float(switch_stability_ema),
            'toxic_penalty_applied': bool(is_toxic),
            'garch_regime_probs': garch_regime_probs,
            'feed_status': feed_status,
            'range_ticks': range_ticks,
        },
        # ==========================================
        # EDGE OUTPUT (NEW - FIXES SCHEMA GAP)
        # ==========================================
        'alpha': {
            'edge_score': float(edge_score)
        },
    }
    
    # --- HARD GUARD (fail-safe, NON-BREAKING) ---
    if not _validate_output_schema(out):
        return {
            "schema_version": _OUTPUT_SCHEMA_VERSION,
            "regime_idx": -1,
            "regime_label": "UNKNOWN",
            "trend_strength": 0.0,
            "risk_level": 1.0,
            "confidence": 0.0,
            "probabilities": {"bull": 0.0, "bear": 0.0, "crisis": 0.0},
            "macro_probs": [1/3, 1/3, 1/3],
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
                "last_valid_vol": float(last_valid_vol),
                "switch_stability_ema": float(switch_stability_ema),
                "toxic_penalty_applied": True,
                "garch_regime_probs": [0.5, 0.5],
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
# NEW: Real-Time Continuous Scoring Layer
# ==========================================
def compute_hmm_regime(alpha: np.ndarray) -> Dict[str, Any]:
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

    directional_label = "TREND" if bull >= bear else "BEAR"

    # Small directional bias toward TREND (alpha capture preference)
    if directional_label == "TREND":
        trend_score *= 1.10
    else:
        trend_score *= 0.95

    score_map = {
        directional_label: trend_score,
        "RANGE": range_score,
        "TOXIC": toxic_score,
    }
    regime = max(score_map, key=score_map.get)

    return {
        "regime": regime,
        "bull": bull,
        "bear": bear,
        "crisis": crisis,
        "trend_strength": bull - bear,
        "risk_level": crisis,
        "confidence": max(bull, bear, crisis),
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
        P_t = self._compute_transition_matrix(x_t)
        pred_prob = np.dot(prior_prob, P_t)  # Chapman-Kolmogorov prediction

        # Vectorised log N(y_t | mu_k, sigma_k) across all K states.
        sigma_safe = self.sigma + 1e-12
        log_emission = (
            -0.5 * np.log(2.0 * np.pi)
            - np.log(sigma_safe)
            - 0.5 * ((y_t - self.mu) / sigma_safe) ** 2
        )

        log_pred = np.log(np.clip(pred_prob, 1e-300, None))
        log_posterior_unnorm = log_pred + log_emission
        log_posterior_unnorm -= logsumexp(log_posterior_unnorm)
        posterior_prob = np.exp(log_posterior_unnorm)

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
        new_var = (
            self.omega
            + self.alpha * (return_t ** 2)
            + self.beta_garch * current_var
        )
        return np.clip(new_var, 1e-8, self._VAR_CEIL)

    def _update_regime_probs(
        self,
        current_probs: np.ndarray,
        predicted_var: np.ndarray,
        return_t: float,
    ) -> np.ndarray:
        predicted_var = np.clip(
            np.asarray(predicted_var, dtype=float), 1e-8, None
        )
        if not np.all(np.isfinite(predicted_var)):
            raise ValueError(
                f"Non-finite predicted variance entering Bayesian filter: {predicted_var}"
            )
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

    # ==========================================
    # NEW: Safe fallback memory
    # ==========================================
    _last_valid_sjm_probs: np.ndarray | None = None

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
        self._rng = np.random.default_rng()
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

        self._circuit_breaker_active = False
        self._circuit_breaker_reason = None
        self._healing_counter = 0

        # Warning de-duplication / rate limiting.
        self._warning_last_emitted: "OrderedDict[str, float]" = OrderedDict()
        self._warning_first_seen: "OrderedDict[str, float]" = OrderedDict()
        self._warning_counts: Dict[str, int] = {}
        self._warning_lock = threading.RLock()
        self._last_health = "OK"

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
        atexit.register(self._shutdown_warning_worker)

        self._obs_counter = 0
        self._OBS_SAMPLE_RATE = 5  # update metrics every N ticks

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
        return (int(self._rng.integers(0, self._OBS_SAMPLE_RATE)) == 0)

    # ==========================================
    # 🚨 CIRCUIT BREAKER TRIGGER
    # ==========================================
    def _trigger_circuit_breaker(self, reason: str):
        self._circuit_breaker_active = True
        self._circuit_breaker_reason = reason
        self._healing_counter = 0

        try:
            LOGGER.critical(f"[CIRCUIT BREAKER TRIGGERED] Reason={reason}")
        except Exception:
            pass

    # ==========================================
    # 🔄 SELF HEALING SYSTEM
    # ==========================================
    def _self_heal(self):
        try:
            LOGGER.warning("[SELF HEALING INITIATED]")
        except Exception:
            pass

        # Reset critical states
        self.nhhmm_prior = np.ones(self.K) / self.K
        self.garch_prob = np.ones(2) / 2.0
        self._smoothed_garch_prob = self.garch_prob.copy()

        # Reset volatility memory
        self.garch_var = self._stationary_garch_var()
        self._last_valid_vol = self.garch.target_vol

        # Reset regime memory
        self.current_regime_idx = None
        self._confirmed_regime = None
        self._prev_regime = None
        self._regime_persistence = 0
        self.last_signed_position_size = 0.0

        # Reset PnL / breaker memory
        self._equity = 1.0
        self._equity_peak = 1.0
        self._drawdown = 0.0
        self._loss_streak = 0
        self._last_price = None
        self._shock_memory = 0.0
        self._return_ema = 0.0
        self._abs_return_ema = 0.0

        # Reset breaker
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = None
        self._healing_counter = 0

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
            pass

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

        if emit:
            try:
                self._warning_queue.put_nowait(message)
            except queue.Full:
                # ==========================================
                # FIX: NON-BLOCKING DROP COUNTER (NO LOGGING)
                # ==========================================
                if not hasattr(self, "_warning_drop_count"):
                    self._warning_drop_count = 0
                self._warning_drop_count += 1
                return

    def _warn_tf_failure(self, tf: str, exc: Exception) -> None:
        """
        Keep per-timeframe failures isolated while preserving enough traceback
        context to debug the exact failure site from the warning text.
        """
        tb = self._summarize_traceback(exc)
        tb_struct = self._summarize_traceback_structured(exc)

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

        lines = rendered.splitlines()
        if len(lines) > self._TRACEBACK_MAX_FRAMES:
            head = lines[: min(3, len(lines))]
            tail = lines[-max(4, self._TRACEBACK_MAX_FRAMES - len(head)):]
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
        max_frames = self._TRACEBACK_MAX_FRAMES

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
            "in_range": bool(self._in_range),
            "prev_regime": self._prev_regime,
            "prev_raw_regime": self._prev_raw_regime,
            "confirmed_regime": self._confirmed_regime,
            "confirmed_regime_idx": None if self._confirmed_regime_idx is None else int(self._confirmed_regime_idx),
            "regime_persistence": int(self._regime_persistence),
            "nhhmm_prior": self.nhhmm_prior.astype(float).tolist(),
            "garch_prob": self.garch_prob.astype(float).tolist(),
            "smoothed_garch_prob": self._smoothed_garch_prob.astype(float).tolist(),
            "garch_var": self.garch_var.astype(float).tolist(),
            "circuit_breaker_active": bool(self._circuit_breaker_active),
            "circuit_breaker_reason": self._circuit_breaker_reason,
            "equity": float(self._equity),
            "equity_peak": float(self._equity_peak),
            "drawdown": float(self._drawdown),
            "loss_streak": int(self._loss_streak),
            "shock_memory": float(self._shock_memory),
            "return_ema": float(self._return_ema),
            "abs_return_ema": float(self._abs_return_ema),
            # Explicitly mark deprecated field as False to avoid confusion in external systems
            "emit_extended_schema": False,
        }

    @_synchronized
    def reset_state(self) -> None:
        self.nhhmm_prior = np.ones(self.K, dtype=float) / self.K
        self.current_regime_idx = None
        self.last_signed_position_size = 0.0
        self._last_effective_trend_strength = 0.0
        self._last_edge_score = 0.0
        self._last_regime_change_ts = None
        self._last_valid_vol = float(self.garch.target_vol)
        self._switch_stability_ema = 1.0
        self.range_ticks = 0.0
        self.range_ticks_int = 0
        self._prev_regime = None
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
        self.garch_var = self._stationary_garch_var()
        self._shock_memory = 0.0
        self._return_ema = 0.0
        self._abs_return_ema = 0.0

    @_synchronized
    def load_state(self, state: Dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise TypeError(f"state must be a dict, got {type(state).__name__}.")

        expected_signature = f"AdvancedRegimeEngine|v={self._STATE_VERSION}|schema={_OUTPUT_SCHEMA_VERSION}|n_states={self.K}|n_features={self.n_features}"
        incoming_signature = state.get("model_signature")
        if incoming_signature is not None and incoming_signature != expected_signature:
            raise ValueError(
                f"Model signature mismatch. Expected {expected_signature}, got {incoming_signature}."
            )

        incoming_version = state.get("state_version")
        if incoming_version is not None and incoming_version != self._STATE_VERSION:
            raise ValueError(
                f"State version mismatch. Expected {self._STATE_VERSION}, got {incoming_version}."
            )

        self._last_timestamp = state.get("last_timestamp", None)
        if self._last_timestamp is not None:
            try:
                self._last_timestamp = float(self._last_timestamp)
                if not np.isfinite(self._last_timestamp):
                    self._last_timestamp = None
            except (TypeError, ValueError):
                self._last_timestamp = None

        self._last_valid_dt = float(state.get("last_valid_dt", 1.0))
        if not np.isfinite(self._last_valid_dt) or self._last_valid_dt <= 0.0:
            self._last_valid_dt = 1.0

        current_regime_idx = state.get("current_regime_idx", None)
        if current_regime_idx is not None:
            try:
                self.current_regime_idx = int(current_regime_idx)
            except (TypeError, ValueError):
                self.current_regime_idx = None
        else:
            self.current_regime_idx = None

        self.range_ticks = max(float(state.get("range_ticks", 0.0)), 0.0)
        if not np.isfinite(self.range_ticks):
            self.range_ticks = 0.0
        self.range_ticks_int = int(self.range_ticks)

        # --- NEW: restore SJM fallback memory ---
        if "last_valid_sjm_probs" in state and state["last_valid_sjm_probs"] is not None:
            self._last_valid_sjm_probs = _normalize_prob_vector(
                np.asarray(state["last_valid_sjm_probs"], dtype=float)
            )
        else:
            self._last_valid_sjm_probs = None

        self._range_anchor_size = float(state.get("range_anchor_size", 0.0))
        if not np.isfinite(self._range_anchor_size):
            self._range_anchor_size = 0.0

        self.last_signed_position_size = float(state.get("last_signed_position_size", 0.0))
        self._in_range = bool(state.get("in_range", False))

        # --- FIX: restore last valid trend strength for fail-safe continuity ---
        try:
            self._last_effective_trend_strength = float(
                state.get("last_effective_trend_strength", 0.0)
            )
            if not np.isfinite(self._last_effective_trend_strength):
                self._last_effective_trend_strength = 0.0
        except (TypeError, ValueError):
            self._last_effective_trend_strength = 0.0

        try:
            self._last_edge_score = float(state.get("last_edge_score", 0.0))
            if not np.isfinite(self._last_edge_score):
                self._last_edge_score = 0.0
        except (TypeError, ValueError):
            self._last_edge_score = 0.0

        try:
            self._last_valid_vol = float(state.get("last_valid_vol", self.garch.target_vol))
            if not np.isfinite(self._last_valid_vol) or self._last_valid_vol <= 0.0:
                self._last_valid_vol = float(self.garch.target_vol)
        except (TypeError, ValueError):
            self._last_valid_vol = float(self.garch.target_vol)

        try:
            self._switch_stability_ema = float(state.get("switch_stability_ema", 1.0))
            if not np.isfinite(self._switch_stability_ema) or self._switch_stability_ema <= 0.0:
                self._switch_stability_ema = 1.0
        except (TypeError, ValueError):
            self._switch_stability_ema = 1.0

        self._last_regime_change_ts = state.get("last_regime_change_ts", None)
        if self._last_regime_change_ts is not None:
            try:
                self._last_regime_change_ts = float(self._last_regime_change_ts)
                if not np.isfinite(self._last_regime_change_ts):
                    self._last_regime_change_ts = None
            except (TypeError, ValueError):
                self._last_regime_change_ts = None

        self._prev_regime = state.get("prev_regime", None)
        self._prev_raw_regime = state.get("prev_raw_regime", None)
        self._confirmed_regime = state.get("confirmed_regime", None)

        confirmed_idx = state.get("confirmed_regime_idx", None)
        if confirmed_idx is not None:
            try:
                self._confirmed_regime_idx = int(confirmed_idx)
            except (TypeError, ValueError):
                self._confirmed_regime_idx = None
        else:
            self._confirmed_regime_idx = None

        try:
            self._regime_persistence = int(state.get("regime_persistence", 0))
        except (TypeError, ValueError):
            self._regime_persistence = 0

        if "nhhmm_prior" in state:
            self.nhhmm_prior = _normalize_prob_vector(
                self._coerce_vector("nhhmm_prior", state["nhhmm_prior"], self.K)
            )
        else:
            self.nhhmm_prior = _normalize_prob_vector(self.nhhmm_prior)

        if "garch_prob" in state:
            self.garch_prob = _normalize_prob_vector(
                self._coerce_vector("garch_prob", state["garch_prob"], 2)
            )
        else:
            self.garch_prob = _normalize_prob_vector(self.garch_prob)

        if "smoothed_garch_prob" in state:
            self._smoothed_garch_prob = _normalize_prob_vector(
                self._coerce_vector("smoothed_garch_prob", state["smoothed_garch_prob"], 2)
            )
        else:
            self._smoothed_garch_prob = _normalize_prob_vector(self._smoothed_garch_prob)

        self.garch_var = self._coerce_vector("garch_var", state.get("garch_var", self.garch_var), 2)
        # HARD SAFETY: prevent NaN/Inf contamination from snapshots
        if not np.all(np.isfinite(self.garch_var)):
            self.garch_var = self._stationary_garch_var()

        # HARD SAFETY: signed position must be finite
        if not np.isfinite(self.last_signed_position_size):
            self.last_signed_position_size = 0.0
        self.range_ticks_int = int(self.range_ticks)
        self._circuit_breaker_active = bool(state.get("circuit_breaker_active", False))
        self._circuit_breaker_reason = state.get("circuit_breaker_reason", None)
        self._equity = float(state.get("equity", 1.0))
        self._equity_peak = float(state.get("equity_peak", 1.0))
        self._drawdown = float(state.get("drawdown", 0.0))
        self._loss_streak = int(state.get("loss_streak", 0))
        self._shock_memory = float(state.get("shock_memory", 0.0))
        if not np.isfinite(self._shock_memory):
            self._shock_memory = 0.0
        self._return_ema = float(state.get("return_ema", 0.0))
        if not np.isfinite(self._return_ema):
            self._return_ema = 0.0
        self._abs_return_ema = float(state.get("abs_return_ema", 0.0))
        if not np.isfinite(self._abs_return_ema) or self._abs_return_ema < 0.0:
            self._abs_return_ema = 0.0

    @_synchronized
    def update(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.perf_counter()
        
        # CONSISTENT OBS SAMPLING (avoid inconsistent metrics)
        obs_sample = self._obs_should_sample()

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

                                if pnl < -1e-6:
                                    self._loss_streak += 1
                                elif pnl > 1e-6:
                                    self._loss_streak = 0

                                self._equity_peak = max(self._equity_peak, self._equity)
                                self._drawdown = (self._equity_peak - self._equity) / max(self._equity_peak, 1e-8)

                                if self._drawdown > self._MAX_DRAWDOWN:
                                    self._trigger_circuit_breaker("MAX_DRAWDOWN")

                                if self._loss_streak >= self._MAX_CONSECUTIVE_LOSSES:
                                    self._trigger_circuit_breaker("LOSS_STREAK")
                    self._last_price = price
            except Exception:
                pass

        # ==========================================
        # 🚨 STEP 0: CIRCUIT BREAKER CHECK
        # ==========================================
        if self._circuit_breaker_active:
            self._healing_counter += 1

            if self._healing_counter > self._HEALING_COOLDOWN_TICKS:
                self._self_heal()
            else:
                self.last_signed_position_size = 0.0
                return _build_output(
                    regime_idx=-1,
                    regime_label="HALTED",
                    execution_mode="circuit_breaker",
                    trend_strength=0.0,
                    risk_level=1.0,
                    confidence=0.0,
                    edge_score=0.0,
                    probabilities={"bull":0,"bear":0,"crisis":1},
                    macro_probs=[1/3,1/3,1/3],
                    position_size=0.0,
                    signed_position_size=0.0,
                    expected_vol=self._last_valid_vol,
                    raw_size=0.0,
                    is_toxic=True,
                    garch_regime_probs=[0.5,0.5],
                    feed_status=f"CIRCUIT_BREAKER:{self._circuit_breaker_reason}",
                    last_valid_vol=self._last_valid_vol,
                    switch_stability_ema=self._switch_stability_ema,
                    execution_side="flat",
                )
        
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
            base_tf = mtf_data.get("base", None)
            if base_tf is None:
                raise ValueError(
                    "MTF payload must include explicit 'base' timeframe key"
                )

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
            if _PROM_AVAILABLE:
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

        try:
            y_t = float(y_t) if y_t is not None else None
        except (TypeError, ValueError):
            y_t = None
        if y_t is not None and np.isfinite(y_t) and abs(y_t) > 2.0:
            raise ValueError(
                f"Return value {y_t:.6f} exceeds plausible fractional range "
                f"(|r| > 2.0). Verify upstream pipeline normalises to fractional "
                "returns before calling update()."
            )
        if y_t is None or not np.isfinite(y_t):
            y_t = 0.0

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
                if obs_sample:
                    ENGINE_FEED_STATUS.labels(self.engine_id, feed_status).inc()
                    ENGINE_HEALTH.labels(self.engine_id).set(0)

            self._obs_counter += 1  # PRODUCTION HARDENING: prevent rate-limit bypass on outage

            return _build_output(
                # expose MTF degradation state without changing schema shape
                # via risk_metrics.feed_status + comment trail in warnings
                regime_idx=int(self.current_regime_idx) if self.current_regime_idx is not None else -1,
                regime_label='UNKNOWN',
                execution_mode='fail_safe',
                trend_strength=safe_trend_strength,
                risk_level=1.0,
                confidence=0.0,
                edge_score=0.0,
                probabilities={'bull': 0.0, 'bear': 0.0, 'crisis': 0.0},
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
        if _PROM_AVAILABLE and obs_sample:
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

        self.nhhmm_prior = _normalize_prob_vector(nhhmm_posterior)

        nhhmm_confidence = float(np.max(nhhmm_posterior))
        effective_bias_weight = float(np.clip(nhhmm_confidence, 0.0, 1.0))
        sjm_x_t = np.asarray(x_t, dtype=float).copy()
        if sjm_x_t.size > 0 and np.isfinite(y_t):
            sjm_x_t[0] = float(y_t)
            vol_idx = 2 if sjm_x_t.size > 2 else sjm_x_t.size - 1
            if vol_idx >= 0:
                sjm_x_t[vol_idx] = abs(float(y_t))
        sjm_state, sjm_probs = self.sjm.online_predict(
            x_t=sjm_x_t,
            expected_n_features=self.n_features,
            prev_state=self.current_regime_idx,
            nhhmm_probs=nhhmm_posterior,
            bias_weight=effective_bias_weight,
        )
        
        # ==========================================
        # FIX: STICKY SJM FALLBACK (NO REGIME COLLAPSE)
        # ==========================================
        if not np.all(np.isfinite(sjm_probs)):
            self._warn_rate_limited(
                key="sjm_non_finite",
                message=f"SJM produced non-finite probs, using last valid state",
                cooldown_s=10.0,
            )

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
        regime_scores = compute_hmm_regime(sjm_probs)
        regime = regime_scores["regime"]

        # Capture base trend strength before any execution-level overrides (fixes Issue 1)
        base_trend_strength = float(regime_scores["trend_strength"])
        alpha_conf = float(regime_scores["confidence"])
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
                    and regime_scores["confidence"] > 0.72
                    and regime_scores["risk_level"] < 0.25
                )
            )
            stable_exit = (
                regime == "RANGE"
                and regime_scores["confidence"] > 0.60
                and regime_scores["risk_level"] < 0.30
                and persistence_ok
            )
            directional_exit = (
                regime in ("TREND", "BEAR")
                and regime_scores["confidence"] > 0.68
                and regime_scores["risk_level"] < 0.45
            )
            if stable_exit or directional_exit:
                confirmed_regime = regime
                confirmed_regime_idx = self.current_regime_idx
            else:
                confirmed_regime = "TOXIC"
                confirmed_regime_idx = self._confirmed_regime_idx

        # ==========================================
        # EARLY EDGE-BASED REGIME OVERRIDE
        # Prevent weak trend signals from activating directional modes
        # ==========================================
        if confirmed_regime in ("TREND", "BEAR"):
            if regime_edge < self._EDGE_MIN_DIRECTIONAL_CONFIDENCE:
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
                switch_gate = self._EDGE_MIN_DIRECTIONAL_CONFIDENCE

            if mtf_partial_survival:
                switch_gate += self._SWITCH_EDGE_BUFFER

            switch_strength = (
                self._SWITCH_EDGE_WEIGHT * regime_edge
                + self._SWITCH_CONF_WEIGHT * regime_scores["confidence"]
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
            confidence_ok = regime_scores["confidence"] >= 0.70
            toxic_override = confirmed_regime == "TOXIC"

            if not toxic_override:
                if (not cooldown_ok or switch_strength < switch_gate) and not (persistence_ok and confidence_ok):
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
        self._prev_regime = confirmed_regime

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
        self.garch_prob = self.garch._update_regime_probs(self.garch_prob, predicted_var, y_t)
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

        # ==========================================
        # 🚨 STEP 2: CONFIDENCE COLLAPSE
        # ==========================================
        if regime_scores["confidence"] < self._CONFIDENCE_COLLAPSE_THRESHOLD:
            self._trigger_circuit_breaker("CONFIDENCE_COLLAPSE")
            
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
        
        if _PROM_AVAILABLE and obs_sample:
            ENGINE_VOL.labels(self.engine_id).set(expected_vol)
            ENGINE_CONFIDENCE.labels(self.engine_id).set(regime_scores["confidence"])
            ENGINE_RISK.labels(self.engine_id).set(regime_scores["risk_level"])
            
        if not np.isfinite(expected_vol):
            expected_vol = self.garch.target_vol

        target_leverage = effective_target_vol / expected_vol
        if not np.isfinite(target_leverage):
            target_leverage = 0.0

        raw_size = float(np.clip(target_leverage, 0.0, 10.0))
        position_size = float(np.clip(raw_size, 0.0, 0.35))
        
        # ==========================================
        # 🚨 STEP 3: SOFT RISK BRAKE
        # ==========================================
        if regime_scores["confidence"] < 0.5:
            position_size *= 0.5
        if regime_scores["confidence"] < 0.4:
            position_size *= 0.25

        if _PROM_AVAILABLE and obs_sample:
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

        if _PROM_AVAILABLE and obs_sample:
            ENGINE_HEALTH.labels(self.engine_id).set(1 if health == "OK" else 0)

        if is_toxic:
            position_size = float(np.clip(position_size * 0.1, 0.0, 0.035))
        elif confirmed_regime == "BEAR":
            position_size = float(np.clip(position_size * 0.5, 0.0, 0.175))
        else:
            position_size = float(np.clip(position_size, 0.0, 0.35))

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

        position_size = float(np.clip(position_size * edge_scaled, 0.0, 0.35))

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
            decay = max(decay, 0.08)
            if not np.isfinite(decay):
                decay = 0.1

            k = self._RANGE_DECAY_FLOOR_K
            decay = float(self._RANGE_SIGNED_DECAY * (decay / (decay + k)))

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

        position_size = float(np.clip(position_size, 0.0, 0.35))
        if not np.isfinite(signed_position_size):
            signed_position_size = 0.0
        if not np.isfinite(expected_vol):
            expected_vol = self.garch.target_vol
        if not np.isfinite(raw_size):
            raw_size = 0.0

        self.last_signed_position_size = signed_position_size
        rticks = self.range_ticks_int

        # OBS: latency tracking
        if _PROM_AVAILABLE and obs_sample:
            elapsed = time.perf_counter() - start_time
            ENGINE_LATENCY.labels(self.engine_id).observe(elapsed)

        self._obs_counter += 1

        # Final execution guard (redundant safety layer)
        if confirmed_regime in ("TREND", "BEAR") and edge_score < self._EDGE_MIN_DIRECTIONAL_CONFIDENCE:
            execution_side = "flat"

        # Keep regime label and returned index semantically aligned.
        # RANGE and TOXIC do not map cleanly to the 3-state SJM index space.
        final_regime_idx = -1
        if confirmed_regime in ("TREND", "BEAR"):
            final_regime_idx = int(self.current_regime_idx) if self.current_regime_idx is not None else -1

        return _build_output(
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

    # ==========================================
    # 🚨 CIRCUIT BREAKER TRIGGER
    # ==========================================
    def _trigger_circuit_breaker(self, reason: str):
        self._circuit_breaker_active = True
        self._circuit_breaker_reason = reason
        self._healing_counter = 0

        try:
            LOGGER.critical(f"[CIRCUIT BREAKER TRIGGERED] Reason={reason}")
        except Exception:
            pass

    # ==========================================
    # 🔄 SELF HEALING SYSTEM
    # ==========================================
    def _self_heal(self):
        try:
            LOGGER.warning("[SELF HEALING INITIATED]")
        except Exception:
            pass

        # Reset critical states
        self.nhhmm_prior = np.ones(self.K) / self.K
        self.garch_prob = np.ones(2) / 2.0
        self._smoothed_garch_prob = self.garch_prob.copy()

        # Reset volatility memory
        self.garch_var = self._stationary_garch_var()
        self._last_valid_vol = self.garch.target_vol

        # Reset regime memory
        self.current_regime_idx = None
        self._confirmed_regime = None
        self._prev_regime = None
        self._regime_persistence = 0
        self.last_signed_position_size = 0.0

        # Reset PnL / breaker memory
        self._equity = 1.0
        self._equity_peak = 1.0
        self._drawdown = 0.0
        self._loss_streak = 0
        self._last_price = None
        self._shock_memory = 0.0
        self._return_ema = 0.0
        self._abs_return_ema = 0.0

        # Reset breaker
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = None
        self._healing_counter = 0

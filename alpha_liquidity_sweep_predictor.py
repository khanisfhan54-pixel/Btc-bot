import math
import threading
from typing import Dict, Any, Optional, List, Tuple
from collections import deque
import time

# FIX C-3 APPLIED — canonical state / regime vocabularies enforced by
# LiquiditySweepAlpha._safe_output to prevent any unknown label from
# leaking into downstream consumers.
_VALID_STATES = frozenset({
    "NORMAL", "PRE_SWEEP_BUILDUP", "ACTIVE_SWEEP", "POST_SWEEP"
})
_VALID_REGIMES = frozenset({
    "TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE", "UNKNOWN",
})

__all__ = ["predict_sweep", "LiquiditySweepAlpha"]
LOGIT_TEMP = 1.2
EPS = 1e-12

# STRICT SAFE FLOAT (guaranteed float output)
def _safe_num(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        if not math.isfinite(v):
            return default
        return v
    except Exception:
        return default

def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default

def _try_float(x: Any) -> Optional[float]:
    """Convert to float if valid, else return None. Unlike _safe_float, does NOT substitute a default."""
    if x is None:
        return None
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None

def _is_finite(x: float) -> bool:
    return not (math.isnan(x) or math.isinf(x))

def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))

def _calibrate_prob(p: float) -> float:
    return _clamp(0.5 + (p - 0.5) * 0.8, 0.0, 1.0)


# FIX U-02 — Isotonic-regression probability calibrator.
# Falls back gracefully to the original 0.8 shrinkage when sklearn is
# unavailable or the calibrator has not been fit yet.
class ProbabilityCalibrator:
    """Isotonic-regression probability calibrator (replaces fixed 0.8 shrinkage)."""

    def __init__(self):
        self._ir = None
        self.n_samples: int = 0
        self.brier_score: float = float("nan")
        self.fitted: bool = False

    def fit(self, y_pred_oof, y_true_oof) -> "ProbabilityCalibrator":
        try:
            from sklearn.isotonic import IsotonicRegression
        except Exception:
            self.fitted = False
            return self
        try:
            yp = [float(x) for x in y_pred_oof]
            yt = [int(x) for x in y_true_oof]
        except (TypeError, ValueError):
            self.fitted = False
            return self
        if len(yp) < 5 or len(yp) != len(yt):
            self.fitted = False
            return self
        ir = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        ir.fit(yp, yt)
        self._ir = ir
        self.n_samples = len(yp)
        # Brier score (mean squared error between calibrated p and y_true)
        cal = ir.transform(yp)
        sse = sum((float(cal[i]) - float(yt[i])) ** 2 for i in range(len(yt)))
        self.brier_score = sse / len(yt)
        self.fitted = True
        return self

    def transform(self, p: float) -> float:
        if not self.fitted or self._ir is None:
            return _clamp(0.5 + (float(p) - 0.5) * 0.8, 0.0, 1.0)
        try:
            out = float(self._ir.transform([float(p)])[0])
        except Exception:
            return _clamp(0.5 + (float(p) - 0.5) * 0.8, 0.0, 1.0)
        return _clamp(out, 0.0, 1.0)

def _safe_logit(p: float, volatility: float = 0.0) -> float:
    """
    Safely compute log-odds mapping for probabilistic combinations.
    Clamps bounds to prevent domain errors or inf scaling.
    """
    p = _clamp(p, 1e-6, 1.0 - 1e-6)
    # HARD clamp volatility locally (do not trust caller)
    vr = _safe_num(volatility, 0.0)
    # HARD GLOBAL CAP (prevents scaling instability)
    if vr > 5.0:
        vr = 5.0
    if not math.isfinite(vr):
        vr = 0.0
    temp = 1.0 + min(1.0, max(0.0, vr))
    return math.log(p / (1.0 - p)) / (temp * LOGIT_TEMP)

def _safe_logit_guard(x: float, vr: float) -> float:
    try:
        val = _safe_logit(x, vr)
        if not isinstance(val, (int, float)) or not math.isfinite(val):
            return 0.0
        return val
    except Exception:
        return 0.0

def _standard_sigmoid(x: float) -> float:
    """
    Standard sigmoid evaluating logits to [0,1] probability space.
    Overflow-safe mapping for negative scalars.
    """
    # HARD CLAMP INPUT (prevents overflow entirely)
    x = _clamp(x, -60.0, 60.0)
    if x >= 0.0:
        return 1.0 / (1.0 + math.exp(-x))
    else:
        ez = math.exp(x)
        return ez / (1.0 + ez)


def _safe_output(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Institutional output contract:
    - No None values
    - Stable schema
    - Normalized probabilities
    """
    # unified clamp (remove EPS distortion)
    prob_above = _clamp(_safe_float(result.get("prob_above"), 0.5), 0.0, 1.0)
    prob_below = _clamp(_safe_float(result.get("prob_below"), 0.5), 0.0, 1.0)
    total = prob_above + prob_below
    if not math.isfinite(total) or total <= 1e-12:
        prob_above, prob_below = 0.5, 0.5
    else:
        inv_total = 1.0 / total
        prob_above *= inv_total
        prob_below *= inv_total

    action = str(result.get("action", "HOLD")).upper()
    if action not in {"BUY", "SELL", "HOLD"}:
        action = "HOLD"

    # ✅ FIX 5: Ensure strict numeric casting (institutional safety)
    confidence = float(_safe_float(result.get("confidence"), 0.0))

    # Deterministic final normalization:
    # one rounded value, then its complement.
    prob_above = round(_clamp(prob_above, 0.0, 1.0), 4)
    prob_below = round(1.0 - prob_above, 4)
    if not math.isfinite(prob_above) or not math.isfinite(prob_below):
        prob_above, prob_below = 0.5, 0.5
    else:
        total = prob_above + prob_below
        if abs(total - 1.0) > 1e-9:
            prob_above = round(_clamp(prob_above / total, 0.0, 1.0), 4)
            prob_below = round(1.0 - prob_above, 4)

    return {
        "action": action,
        "confidence": round(_clamp(confidence, 0.0, 1.0), 4),
        "state": str(result.get("state", "NORMAL")),
        "regime": str(result.get("regime", "RANGING")),
        "ofi_zscore": round(_safe_float(result.get("ofi_zscore"), 0.0), 4),
        "hawkes_intensity": round(_safe_float(result.get("hawkes_intensity"), 0.0), 4),
        "logic": str(result.get("logic", "No immediate edge")),
        "micro_prob": round(_clamp(_safe_float(result.get("micro_prob"), 0.5), 0.0, 1.0), 4),
        "macro_prob": round(_clamp(_safe_float(result.get("macro_prob"), 0.5), 0.0, 1.0), 4),
        "prob_above": prob_above,
        "prob_below": prob_below,
    }

def predict_sweep(
    liquidity: Dict[str, Any],
    market_state: Dict[str, Any],
    volume_intel: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Predict which side liquidity will be swept first based on structural context.
    """
    # ✅ FIX 1: Harden liquidity input
    if not isinstance(liquidity, dict):
        liquidity = {}

    # ✅ FIX 2: Harden market_state input (prevents None crash)
    if not isinstance(market_state, dict):
        market_state = {}

    # ✅ FIX 3: Harden volume_intel input
    vol_intel = volume_intel if isinstance(volume_intel, dict) else {}

    nearest_above = liquidity.get("nearest_above")
    nearest_below = liquidity.get("nearest_below")

    # Ensure pools are valid dicts
    if not isinstance(nearest_above, dict):
        nearest_above = None
    if not isinstance(nearest_below, dict):
        nearest_below = None

    dist_above = _try_float(nearest_above.get("distance_points")) if nearest_above else None
    dist_below = _try_float(nearest_below.get("distance_points")) if nearest_below else None
    if dist_above is not None and dist_above < 0.0:
        dist_above = 0.0
    if dist_below is not None and dist_below < 0.0:
        dist_below = 0.0

    state = str(market_state.get("state", "CHOPPY")).upper()
    compression = _safe_float(market_state.get("compression", 1.0))
    volatility = _safe_float(market_state.get("volatility", 0.0))
    bias = _safe_float(market_state.get("bias", 0.0))

    vol_spike = bool(vol_intel.get("volume_spike", False))
    vol_strength = _safe_float(vol_intel.get("volume_strength", 0.0))

    prob_above, prob_below = 0.5, 0.5

    if dist_above is not None and dist_below is not None:
        total = dist_above + dist_below
        if total >= 1e-12:
            prob_above += (dist_below / total - 0.5)
            prob_below += (dist_above / total - 0.5)
    elif dist_above is not None:
        prob_above += 0.2
    elif dist_below is not None:
        prob_below += 0.2

    # directionally orient compression towards the closest pool structure
    if dist_above is not None and dist_below is not None:
        compression_bias = (dist_below - dist_above) / (dist_above + dist_below + 1e-6)
    else:
        # [FIX] Fallback for incomplete structural liquidity visibility
        # Forces compression probability mass to tilt logically based on trend/momentum rather than collapsing to neutral
        compression_bias = _clamp(bias, -1.0, 1.0)

    if state == "COMPRESSION":
        prob_above += 0.1 * compression_bias * compression
        prob_below -= 0.1 * compression_bias * compression
    elif state == "TRENDING":
        if bias > 0:
            prob_above += 0.15
            prob_below -= 0.10
        elif bias < 0:
            prob_below += 0.15
            prob_above -= 0.10

    if vol_spike or vol_strength >= 0.7:
        if bias > 0:
            prob_above += 0.1
        elif bias < 0:
            prob_below += 0.1

    # stabilize for non-finite / negative volatility + compression inputs
    if not _is_finite(volatility) or volatility < 0.0:
        volatility = 0.0
    if not _is_finite(compression) or compression < 0.0:
        compression = 0.0
    vol_adj = volatility / (1.0 + volatility)

    # dynamic compression threshold
    comp_threshold = max(vol_adj * 5, 0.05)

    if volatility > 0 and vol_adj < 0.5 and compression < comp_threshold:
        prob_above += 0.1 * compression_bias * compression
        prob_below -= 0.1 * compression_bias * compression

    prob_above = _clamp(prob_above, EPS, 1.0 - EPS)
    prob_below = _clamp(prob_below, EPS, 1.0 - EPS)

    total_prob = prob_above + prob_below
    # ✅ FIX 4: Safe probability normalization
    if total_prob <= EPS:
        prob_above, prob_below = 0.5, 0.5
    else:
        prob_above /= total_prob
        prob_below /= total_prob
    _FLOOR = 1e-4
    prob_above = _clamp(prob_above, _FLOOR, 1.0 - _FLOOR)
    prob_below = _clamp(prob_below, _FLOOR, 1.0 - _FLOOR)

    if prob_above >= prob_below:
        side = "above"
        probability = prob_above
        target = _safe_float(nearest_above.get("price")) if nearest_above else 0.0
    else:
        side = "below"
        probability = prob_below
        target = _safe_float(nearest_below.get("price")) if nearest_below else 0.0

    return {
        "side": side,
        "probability": round(probability, 4),
        "confidence": round(probability, 4),
        "target_price": round(_safe_float(target, 0.0), 8),
        "prob_above": round(prob_above, 4),
        "prob_below": round(prob_below, 4),
        "state": state,
    }


class LiquiditySweepAlpha:
    """
    Production-grade logic for detecting Liquidity Sweeps, incorporating 
    normalized Order Flow Imbalance, Hawkes Processes, and LOB Resiliency.
    """
    def __init__(
        self,
        depth_levels: int = 10,
        resiliency_threshold: float = 0.7,
        history_window: int = 100,
        initial_high: Optional[float] = None,
        initial_low: Optional[float] = None,
        direction_mode: str = "continuation",         # FIX C-1 APPLIED
        active_sweep_lookback_bars: int = 30,         # FIX C-2 APPLIED
        pool_reset_atr_mult: float = 5.0,             # FIX H-3 APPLIED
        enable_sweep_directional_fallback: bool = False,  # FIX-28 (M-2)
        # ── Audit fixes 2026-05-18 ───────────────────────────────────────────
        vol_ratio_threshold: float = 0.015,    # FIX U-05
        atr_expiry_mult: float = 3.0,          # FIX U-06
        pool_max_age_bars: int = 200,          # FIX U-06 (secondary)
        hawkes_alpha: float = 0.1,
        hawkes_decay: float = 0.5,
    ):
        # FIX U-05 / U-06 parameter validation
        if not (0.001 < float(vol_ratio_threshold) < 1.0):
            raise ValueError(
                f"vol_ratio_threshold must be in (0.001, 1.0); got {vol_ratio_threshold}"
            )
        if not (1.0 <= float(atr_expiry_mult) <= 20.0):
            raise ValueError(
                f"atr_expiry_mult must be in [1.0, 20.0]; got {atr_expiry_mult}"
            )
        if not (10 <= int(pool_max_age_bars) <= 100000):
            raise ValueError(
                f"pool_max_age_bars must be in [10, 100000]; got {pool_max_age_bars}"
            )
        if float(hawkes_decay) <= 0.0:
            raise ValueError(f"hawkes_decay must be > 0; got {hawkes_decay}")
        if float(hawkes_alpha) <= 0.0:
            raise ValueError(f"hawkes_alpha must be > 0; got {hawkes_alpha}")
        self.levels = depth_levels
        self.resiliency_threshold = resiliency_threshold
        self.history_window = history_window

        self.liquidity_pools = {"high": None, "low": None}
        self.ofi_history = deque(maxlen=history_window)
        self.hawkes_history = deque(maxlen=history_window)
        self.short_ofi = deque(maxlen=5)

        # rolling stats (stable sliding-window Welford)
        self._ofi_count = 0
        self._ofi_mean = 0.0
        self._ofi_M2 = 0.0
        # Public alias used by get_state_metrics() / _liquidity_forecast.
        self.ofi_count = 0
        self.ofi_sum = 0.0      # FIX C-4 APPLIED
        self.ofi_sq_sum = 0.0   # FIX C-4 APPLIED
        self.hawkes_sum = 0.0

        # Hawkes Process State
        self.hawkes_lambda = 0.0
        self.last_trade_time = 0.0
        self.hawkes_decay = float(hawkes_decay)
        self.hawkes_alpha = float(hawkes_alpha)
        self._lock = threading.RLock()
        # FIX M-3 APPLIED — _time_lock created up front so get_signal()
        # never has to lazy-init it under contention.
        self._time_lock = threading.Lock()

        # FIX C-1 APPLIED — directional mode for sweep entries.
        # "continuation" = original behaviour (BUY on high sweep / SELL on low).
        # "fade"         = reverse (mean-reversion / fade-the-sweep).
        self.direction_mode = str(direction_mode)

        # FIX C-2 APPLIED — trailing window length used by detect_sweep_state
        # to read a peak Hawkes intensity rather than the instantaneous value.
        self.active_sweep_lookback_bars: int = int(active_sweep_lookback_bars)
        self._active_sweep_fired_count: int = 0   # FIX C-2 APPLIED
        self._pre_sweep_fired_count: int = 0      # FIX A3 APPLIED

        # FIX C-3 APPLIED — counter incremented every time _safe_output()
        # rejects an out-of-vocabulary state or regime label.
        self._state_invalid_count: int = 0

        # FIX H-1 APPLIED — last effective level count actually traversed
        # by calculate_ofi_zscore (capped by min depth across both books).
        self._last_ofi_levels_used: int = 0

        # FIX H-3 APPLIED — independent per-side pool reset multiplier.
        self.pool_reset_atr_mult: float = float(pool_reset_atr_mult)

        # FIX-28 (M-2) — opt-in directional fallback. When True and the
        # primary path returns action="HOLD", predict() will consult
        # predict_sweep() and surface a directional bias derived from
        # ofi_zscore / hawkes_intensity instead of the bare HOLD. Default
        # False preserves the strict prior behaviour for callers that
        # treat HOLD as authoritative.
        self.enable_sweep_directional_fallback: bool = bool(
            enable_sweep_directional_fallback
        )

        # FIX L-2 APPLIED — neutral / no-signal returns from
        # _predict_next_sweep, surfaced via get_state_metrics().
        self._neutral_predict_count: int = 0

        # ── Audit-fix state (2026-05-18) ──────────────────────────────────────
        # FIX U-02: optional isotonic probability calibrator
        self._calibrator: Optional["ProbabilityCalibrator"] = None
        # FIX U-04: counter for fake-breakout detections under hardened gate
        self._fake_breakout_ofi_required_count: int = 0
        # FIX U-05: VOLATILE-regime gate threshold (validated above)
        self.vol_ratio_threshold: float = float(vol_ratio_threshold)
        # FIX U-06: ATR-distance pool-expiry primary gate +
        # bar-count secondary expiry.
        self.atr_expiry_mult: float = float(atr_expiry_mult)
        self.pool_max_age_bars: int = int(pool_max_age_bars)
        self._pool_set_bar: Dict[str, Optional[int]] = {"high": None, "low": None}
        self._pool_expired_age_count: int = 0
        self._pool_expired_atr_count: int = 0
        # FIX U-07: regime-history + per-bar trades-count history for vol-Z
        self._regime_history: deque = deque(maxlen=50)
        self._volume_history: deque = deque(maxlen=20)
        # FIX U-10: HOLD-gate telemetry
        self._gate_fire_log: deque = deque(maxlen=200)
        self._gate_counts: Dict[str, int] = {
            "VOLATILE": 0, "LOW_LIQUIDITY": 0, "WARMUP": 0,
            "NO_EDGE": 0, "POOL_UNSET": 0, "TREND_ALIGNED": 0,
            "INVALID_PRICE": 0,
        }
        # Per-bar counter used by gate-telemetry strings
        self._bar_idx: int = 0

        # FIX L002: seed pools from constructor if provided
        if initial_high is not None and _is_finite(float(initial_high)) and float(initial_high) > 0:
            self.liquidity_pools["high"] = float(initial_high)
            self._pool_set_bar["high"] = 0
        if initial_low is not None and _is_finite(float(initial_low)) and float(initial_low) > 0:
            self.liquidity_pools["low"] = float(initial_low)
            self._pool_set_bar["low"] = 0

    # FIX C-3 APPLIED — instance wrapper around the module-level
    # _safe_output() that adds state/regime vocabulary validation. Internal
    # call sites use this method so invalid labels are counted; the
    # module-level helper is preserved for external imports.
    def _safe_output(self, output: Dict[str, Any]) -> Dict[str, Any]:
        try:
            raw_state = output.get("state", "NORMAL")
            raw_regime = output.get("regime", "RANGING")
            if raw_state not in _VALID_STATES:
                self._state_invalid_count += 1
                output["state"] = "NORMAL"
            if raw_regime not in _VALID_REGIMES:
                self._state_invalid_count += 1
                output["regime"] = "RANGING"
        except Exception:
            # Never let observability break the trading path.
            pass
        return _safe_output(output)

    @property
    def _bar_count(self) -> int:
        """Backward-compatible read-only alias for the internal bar index."""
        return int(getattr(self, "_bar_idx", 0) or 0)

    @property
    def volatile_gate_count(self) -> int:
        """Backward-compatible read-only alias for VOLATILE hold-gate fires."""
        return int(getattr(self, "_gate_counts", {}).get("VOLATILE", 0) or 0)

    @property
    def ofi_level_weighting(self) -> int:
        """Backward-compatible read-only alias for the last OFI level count."""
        return int(getattr(self, "_last_ofi_levels_used", 0) or 0)

    # ── Audit-fix helpers (2026-05-18) ──────────────────────────────────────
    def _shrink_prob(self, p: float) -> float:
        """FIX U-02 — use fitted isotonic calibrator if available, else 0.8 shrink."""
        try:
            cal = getattr(self, "_calibrator", None)
            if cal is not None and getattr(cal, "fitted", False):
                return cal.transform(p)
        except Exception:
            pass
        return _calibrate_prob(p)

    def calibrate(self, oof_preds) -> dict:
        """FIX U-02 — fit the isotonic calibrator from out-of-fold (pred, label) pairs."""
        try:
            yp = [float(p) for p, _ in oof_preds]
            yt = [int(y) for _, y in oof_preds]
        except (TypeError, ValueError):
            return self.get_calibration_status()
        cal = ProbabilityCalibrator().fit(yp, yt)
        self._calibrator = cal
        return self.get_calibration_status()

    def get_calibration_status(self) -> dict:
        """FIX U-02 — surfaces calibrator state."""
        cal = getattr(self, "_calibrator", None)
        if cal is None:
            return {"calibrated": False, "n_samples": 0, "brier_score": float("nan")}
        return {
            "calibrated": bool(getattr(cal, "fitted", False)),
            "n_samples": int(getattr(cal, "n_samples", 0)),
            "brier_score": float(getattr(cal, "brier_score", float("nan"))),
        }

    def calibrate_vol_threshold(self,
                                vol_ratio_history,
                                percentile: float = 95.0) -> float:
        """FIX U-05 — set vol_ratio_threshold to the empirical Nth percentile."""
        try:
            import numpy as _np
            history = [float(x) for x in vol_ratio_history if math.isfinite(float(x))]
        except (TypeError, ValueError):
            raise ValueError("vol_ratio_history contains non-numeric values")
        if len(history) < 100:
            raise ValueError(
                f"vol_ratio_history must have >= 100 samples; got {len(history)}"
            )
        if not (50.0 <= float(percentile) <= 99.0):
            raise ValueError(
                f"percentile must be in [50, 99]; got {percentile}"
            )
        val = float(_np.percentile(history, float(percentile)))
        if not (0.001 < val < 1.0):
            val = max(0.0015, min(0.5, val))
        self.vol_ratio_threshold = val
        return val

    def _record_hold_gate(self, gate_name: str, **extras) -> str:
        """FIX U-10 — record one HOLD-path gate firing and return the logic string."""
        try:
            parts = [f"gate:{gate_name}"]
            for k, v in extras.items():
                if isinstance(v, float):
                    parts.append(f"{k}={v:.4f}")
                else:
                    parts.append(f"{k}={v}")
            parts.append(f"bar={self._bar_idx}")
            line = "|".join(parts)
            self._gate_fire_log.append(line)
            if gate_name in self._gate_counts:
                self._gate_counts[gate_name] += 1
            else:
                self._gate_counts[gate_name] = 1
            return line
        except Exception:
            return f"gate:{gate_name}|bar={getattr(self, '_bar_idx', 0)}"

    # FIX A3 APPLIED — public telemetry snapshot mirroring
    # AdvancedRegimeEngine.get_state_metrics() contract.
    def get_state_metrics(self) -> dict:
        """
        Returns a snapshot of LSA gate-suppression telemetry.
        Mirrors AdvancedRegimeEngine.get_state_metrics() contract.
        """
        try:
            hw_list = list(self.hawkes_history)
            hawkes_baseline = (
                self.hawkes_sum / len(hw_list) if hw_list else 0.0
            )
            return {
                "ofi_count": getattr(self, "ofi_count", 0),
                "ofi_M2": getattr(self, "ofi_sq_sum", 0.0),
                "hawkes_history_len": len(hw_list),
                "hawkes_baseline": hawkes_baseline,
                "liquidity_pools": dict(self.liquidity_pools),
                "neutral_predict_count": self._neutral_predict_count,
                "state_invalid_count": self._state_invalid_count,
                "active_sweep_fired_count": self._active_sweep_fired_count,
                "pre_sweep_fired_count": getattr(self, "_pre_sweep_fired_count", 0),
                "last_ofi_levels_used": self._last_ofi_levels_used,
                "ofi_level_weighting": "cont_2014_decay",
                "active_sweep_lookback_bars": self.active_sweep_lookback_bars,
                "direction_mode": self.direction_mode,
                "pool_reset_atr_mult": self.pool_reset_atr_mult,
                # ── Audit-fix telemetry (2026-05-18) ────────────────────────
                "atr_expiry_mult": getattr(self, "atr_expiry_mult", None),
                "vol_ratio_threshold": getattr(self, "vol_ratio_threshold", None),
                "pool_expired_age_count": getattr(self, "_pool_expired_age_count", 0),
                "pool_expired_atr_count": getattr(self, "_pool_expired_atr_count", 0),
                "fake_breakout_ofi_required_count": getattr(
                    self, "_fake_breakout_ofi_required_count", 0
                ),
                "calibration_status": self.get_calibration_status(),
                "gate_fire_log_tail": list(getattr(self, "_gate_fire_log", []))[-10:],
                "gate_counts": dict(getattr(self, "_gate_counts", {})),
                "volatile_gate_count": self.volatile_gate_count,
                "regime_history_tail": list(getattr(self, "_regime_history", []))[-10:],
                "bar_idx": getattr(self, "_bar_idx", 0),
                "pool_age_high_bars": (
                    None if getattr(self, "_pool_set_bar", {}).get("high") is None
                    else max(0, int(getattr(self, "_bar_idx", 0) or 0) - int(self._pool_set_bar["high"]))
                ),
                "pool_age_low_bars": (
                    None if getattr(self, "_pool_set_bar", {}).get("low") is None
                    else max(0, int(getattr(self, "_bar_idx", 0) or 0) - int(self._pool_set_bar["low"]))
                ),
                "pool_max_age_bars": getattr(self, "pool_max_age_bars", None),
                "branching_ratio": (
                    float(getattr(self, "hawkes_alpha", 0.0)) / float(getattr(self, "hawkes_decay", 1.0))
                    if float(getattr(self, "hawkes_decay", 1.0)) > 0.0 else 0.0
                ),
            }
        except Exception:
            # Telemetry must never raise into the trading path.
            return {}

    @staticmethod
    def _normalize_timestamp(ts: float, fallback: float = 0.0) -> float:
        value = _safe_float(ts, fallback)
        if value > 1e15:
            value *= 1e-9
        elif value > 1e12:
            value *= 1e-3
        if not math.isfinite(value):
            return _safe_float(fallback, 0.0)
        return max(0.0, value)

    def _normalize_thresholds(self, atr: float, price: float) -> Dict[str, float]:
        """
        Dynamic threshold scaling based on volatility regime.
        """
        vol_ratio = atr / (price + 1e-8)

        return {
            "price_move": max(atr * 0.5, price * 0.001),   # displacement threshold
            "compression": max(vol_ratio * 0.5, 0.0005),   # compression threshold
            "trend_buffer": max(vol_ratio * 0.2, 0.001)    # EMA buffer
        }

    def _fast_sigmoid(self, x: float) -> float:
        # bounded + fast; map to [0,1] to behave like a probability score
        y = x / (1 + abs(x))   # [-1, 1]
        return 0.5 * (y + 1.0) # [0, 1]

    def _mark_pool_set(self, side: str) -> None:
        try:
            self._pool_set_bar[side] = int(getattr(self, "_bar_idx", 0) or 0)
        except Exception:
            pass

    def update_liquidity_pools(self, recent_highs: List[float], recent_lows: List[float]):
        with self._lock:
            if recent_highs is not None and len(recent_highs) > 0:
                valid_highs = [v for v in recent_highs[-20:] if isinstance(v, (int, float)) and _is_finite(v)]
                if valid_highs:
                    self.liquidity_pools['high'] = max(valid_highs)
                    self._mark_pool_set('high')
            if recent_lows is not None and len(recent_lows) > 0:
                valid_lows = [v for v in recent_lows[-20:] if isinstance(v, (int, float)) and _is_finite(v)]
                if valid_lows:
                    self.liquidity_pools['low'] = min(valid_lows)
                    self._mark_pool_set('low')

    def _update_hawkes(self, timestamp: float, trade_count: int) -> float:
        ts = self._normalize_timestamp(timestamp, self.last_trade_time)
        # if initialization wall-clock is far from feed epoch, realign baseline once
        if abs(ts - self.last_trade_time) > 3600.0 and not self.hawkes_history:
            self.last_trade_time = ts

        tc = _safe_float(trade_count, 0.0)
        if tc < 0.0:
            tc = 0.0
        tc = min(tc, 1000.0)

        dt = ts - self.last_trade_time
        if dt < 0.0:
            dt = 0.0
        decay_term = math.exp(-self.hawkes_decay * min(dt, 60.0))
        self.hawkes_lambda = (self.hawkes_lambda * decay_term) + (self.hawkes_alpha * tc)
        if self.hawkes_lambda < 0.0 or not _is_finite(self.hawkes_lambda):
            self.hawkes_lambda = 0.0
        self.hawkes_lambda = min(self.hawkes_lambda, 100.0)
        self.last_trade_time = ts

        old = self.hawkes_history[0] if len(self.hawkes_history) == self.history_window else 0.0
        self.hawkes_history.append(self.hawkes_lambda)
        self.hawkes_sum += self.hawkes_lambda - old

        return self.hawkes_lambda

    def calculate_ofi_zscore(self, prev_book: Dict, curr_book: Dict) -> float:
        ofi_total = 0.0
        if not prev_book or not curr_book:
            return 0.0
        try:
            # FIX H-1 APPLIED — only iterate over levels that actually exist
            # on BOTH sides of BOTH books, so a thin top-of-book never leaks
            # phantom levels into the OFI sum.
            n_levels = min(
                self.levels,
                len(curr_book.get('bids', [])),
                len(curr_book.get('asks', [])),
                len(prev_book.get('bids', [])),
                len(prev_book.get('asks', [])),
            )
            try:
                self._last_ofi_levels_used = int(n_levels)
            except Exception:
                pass
            for i in range(n_levels):
                curr_bid_p, curr_bid_s = _safe_float(curr_book['bids'][i]['price']), _safe_float(curr_book['bids'][i]['size'])
                prev_bid_p, prev_bid_s = _safe_float(prev_book['bids'][i]['price']), _safe_float(prev_book['bids'][i]['size'])

                if curr_bid_p > prev_bid_p: delta_bid = curr_bid_s
                elif curr_bid_p == prev_bid_p: delta_bid = curr_bid_s - prev_bid_s
                else: delta_bid = -prev_bid_s

                curr_ask_p, curr_ask_s = _safe_float(curr_book['asks'][i]['price']), _safe_float(curr_book['asks'][i]['size'])
                prev_ask_p, prev_ask_s = _safe_float(prev_book['asks'][i]['price']), _safe_float(prev_book['asks'][i]['size'])

                if curr_ask_p < prev_ask_p: delta_ask = curr_ask_s
                elif curr_ask_p == prev_ask_p: delta_ask = curr_ask_s - prev_ask_s
                else: delta_ask = -prev_ask_s

                ofi_total += (delta_bid - delta_ask)
        except (KeyError, IndexError, TypeError):
            # treat malformed/partial book as "no signal" to prevent poisoning rolling stats
            return 0.0

        if not _is_finite(ofi_total):
            return 0.0

        outgoing = self.ofi_history[0] if len(self.ofi_history) == self.history_window else None
        if outgoing is not None and self._ofi_count > 0:
            old_n = self._ofi_count
            if old_n <= 1:
                self._ofi_count = 0
                self._ofi_mean = 0.0
                self._ofi_M2 = 0.0
            else:
                new_n = old_n - 1
                delta = float(outgoing) - self._ofi_mean
                self._ofi_mean -= delta / float(new_n)
                self._ofi_M2 -= delta * (float(outgoing) - self._ofi_mean)
                self._ofi_M2 = max(0.0, self._ofi_M2)
                self._ofi_count = new_n
            # FIX C-4 APPLIED — mirror eviction into the running sum / sum-of-squares
            try:
                old_v = float(outgoing)
                self.ofi_sum -= old_v
                self.ofi_sq_sum -= old_v * old_v
                if self.ofi_sq_sum < 0.0:
                    self.ofi_sq_sum = 0.0
            except Exception:
                pass

        self.ofi_history.append(ofi_total)
        self.short_ofi.append(ofi_total)

        self._ofi_count += 1
        # FIX C-4 APPLIED — mirror append into the running sum / sum-of-squares
        try:
            v = float(ofi_total)
            self.ofi_sum += v
            self.ofi_sq_sum += v * v
        except Exception:
            pass
        # Keep public ofi_count alias in sync for telemetry / external readers.
        self.ofi_count = self._ofi_count
        delta_add = ofi_total - self._ofi_mean
        self._ofi_mean += delta_add / float(self._ofi_count)
        self._ofi_M2 += delta_add * (ofi_total - self._ofi_mean)
        self._ofi_M2 = max(0.0, self._ofi_M2)

        if self._ofi_count < 20:
            return 0.0

        n = self._ofi_count
        var = max(self._ofi_M2 / max(float(n - 1), 1.0), 1e-8)
        ofi_std = math.sqrt(var)
        if not _is_finite(ofi_std) or ofi_std <= 0.0:
            return 0.0

        z = (ofi_total - self._ofi_mean) / ofi_std
        return 4.0 * math.tanh(z / 3.0)

    def _detect_regime(self,
                       ema_fast: float,
                       ema_slow: float,
                       buffer: float = 0.001,
                       vol_ratio: float = 0.0,
                       session_volume_percentile: float = 0.5,
                       volume_zscore: float = 0.0,
                       spread_to_atr: float = 0.0) -> str:
        """
        Regime classifier — FIX U-07 expanded:
            adds VOLATILE detection driven by (vol_ratio_threshold)
            or by joint (spread_to_atr, volume_zscore) microstructure stress.
        """
        # FIX U-05 / U-07: VOLATILE has highest priority — it gates execution
        try:
            vr = float(vol_ratio)
            spread_atr = float(spread_to_atr)
            vol_z = float(volume_zscore)
        except (TypeError, ValueError):
            vr, spread_atr, vol_z = 0.0, 0.0, 0.0
        if vr > self.vol_ratio_threshold or (spread_atr > 2.0 and vol_z < -1.5):
            return "VOLATILE"

        # fully dynamic buffer using normalized thresholds
        if ema_fast > ema_slow * (1 + buffer):
            return "TRENDING_UP"
        elif ema_fast < ema_slow * (1 - buffer):
            return "TRENDING_DOWN"
        return "RANGING"

    def detect_sweep_state(self, price: float, atr: float, hawkes_intensity: float) -> str:
        # FIX U-06 (secondary) — bar-count pool expiry. Pools older than
        # `pool_max_age_bars` are cleared even if ATR distance is still
        # within range. _bar_idx is incremented in get_signal().
        try:
            cur_bar = int(getattr(self, "_bar_idx", 0) or 0)
            max_age = int(getattr(self, "pool_max_age_bars", 0) or 0)
            for side in ("high", "low"):
                set_bar = self._pool_set_bar.get(side)
                if self.liquidity_pools[side] is not None and set_bar is not None and max_age > 0:
                    if (cur_bar - set_bar) > max_age:
                        self.liquidity_pools[side] = None
                        self._pool_set_bar[side] = None
                        self._pool_expired_age_count += 1
        except Exception:
            pass

        if self.liquidity_pools['high'] is None or self.liquidity_pools['low'] is None:
            return "NORMAL"
        # FIX H-3 APPLIED — independent per-side pool reset. The previous
        # joint-AND condition required BOTH pools to be far from price before
        # either was cleared, leaving stale pools in place during one-sided
        # trends. Each side now resets only when price has moved
        # `pool_reset_atr_mult * atr` past it.
        #
        # FIX U-06 — ATR-distance is now the PRIMARY pool-expiry gate
        # (atr_expiry_mult, default 3.0× ATR). Bar-count expiry remains as
        # a secondary safety. Two counters expose which gate fired.
        if atr > 0:
            high_pool = _safe_float(self.liquidity_pools['high'], price)
            low_pool = _safe_float(self.liquidity_pools['low'], price)
            atr_expiry_dist = atr * self.atr_expiry_mult
            reset_dist = atr * self.pool_reset_atr_mult

            if self.liquidity_pools['high'] is not None:
                d_high = abs(price - high_pool) / (atr + 1e-8)
                if d_high > self.atr_expiry_mult:
                    self.liquidity_pools['high'] = None
                    self._pool_expired_atr_count += 1
                elif (price - high_pool) > reset_dist:
                    self.liquidity_pools['high'] = None
                    self._pool_expired_age_count += 1
            if self.liquidity_pools['low'] is not None:
                d_low = abs(low_pool - price) / (atr + 1e-8)
                if d_low > self.atr_expiry_mult:
                    self.liquidity_pools['low'] = None
                    self._pool_expired_atr_count += 1
                elif (low_pool - price) > reset_dist:
                    self.liquidity_pools['low'] = None
                    self._pool_expired_age_count += 1
            if self.liquidity_pools['high'] is None or self.liquidity_pools['low'] is None:
                return "NORMAL"

        dist_to_high = abs(self.liquidity_pools['high'] - price)
        dist_to_low = abs(price - self.liquidity_pools['low'])

        is_high_sweep = price >= self.liquidity_pools['high']
        is_low_sweep = price <= self.liquidity_pools['low']

        baseline = (self.hawkes_sum / max(1, len(self.hawkes_history))) if len(self.hawkes_history) > 5 else 1.0
        intensity_spike = hawkes_intensity >= baseline * 2.0

        # FIX C-2 APPLIED — trailing-window Hawkes peak. ACTIVE_SWEEP must not
        # require an instantaneous spike at the exact bar of the breach (real
        # sweeps often print one bar after the activity peak); use the max
        # over the last `active_sweep_lookback_bars` bars instead.
        _hw_list = list(self.hawkes_history)
        recent_peak = (
            max(_hw_list[-self.active_sweep_lookback_bars:])
            if len(_hw_list) >= self.active_sweep_lookback_bars
            else hawkes_intensity
        )
        recent_spike = recent_peak >= baseline * 2.0

        thresholds = self._normalize_thresholds(atr, price)

        # --- NEW: compression-aware proximity ---
        compression_threshold = thresholds["compression"]

        near_level = (
            dist_to_high < thresholds["price_move"] or
            dist_to_low < thresholds["price_move"]
        )

        # NEW: compression regime filter (tight range = higher sweep probability)
        compression_condition = (
            (dist_to_high / (price + 1e-8) < compression_threshold) or
            (dist_to_low / (price + 1e-8) < compression_threshold)
        )

        if (is_high_sweep or is_low_sweep) and recent_spike:
            try:
                self._active_sweep_fired_count += 1   # FIX C-2 APPLIED
            except Exception:
                pass
            return "ACTIVE_SWEEP"

        if (near_level or compression_condition) and intensity_spike:
            try:
                self._pre_sweep_fired_count += 1      # FIX A3 APPLIED
            except Exception:
                pass
            return "PRE_SWEEP_BUILDUP"

        return "NORMAL"

    def _detect_fake_breakout(self, sweep_side: str, close_price: float, ofi_z: float) -> Tuple[bool, float]:
        # FIX U-04 — Fake-breakout HARDENED:
        #   threshold raised 0.5 → 0.8 (both sides), and OFI confirmation
        #   is now MANDATORY. Both the price-position component AND the
        #   OFI component must fire for is_fake to be True.
        rejection_score = 0.0
        is_fake = False
        price_pos_fired = False
        ofi_fired = False

        if sweep_side == "high":
            if self.liquidity_pools.get('high') is None:
                return False, 0.0
            price_pos_fired = close_price < self.liquidity_pools['high']
            ofi_fired = ofi_z < -1.0
            rejection_score = (0.5 if price_pos_fired else 0.0) + (
                0.5 if ofi_fired else 0.0
            )
            is_fake = rejection_score >= 0.8 and ofi_fired

        elif sweep_side == "low":
            if self.liquidity_pools.get('low') is None:
                return False, 0.0
            price_pos_fired = close_price > self.liquidity_pools['low']
            ofi_fired = ofi_z > 1.0
            rejection_score = (0.5 if price_pos_fired else 0.0) + (
                0.5 if ofi_fired else 0.0
            )
            is_fake = rejection_score >= 0.8 and ofi_fired

        if is_fake:
            try:
                self._fake_breakout_ofi_required_count += 1
            except Exception:
                pass
        return is_fake, rejection_score

    def check_resiliency(self, pre_depth: float, post_depth: float, time_elapsed: float, max_time: float = 2.0) -> float:
        if time_elapsed > max_time or pre_depth <= 0:
            return 0.0
        # avoid division blow-ups / timestamp jitter
        if time_elapsed <= 1e-3:
            return 0.0
        if post_depth < 0.0:
            post_depth = 0.0
        recovery_ratio = post_depth / pre_depth
        speed = (post_depth - pre_depth) / (time_elapsed + 1e-6)
        speed_score = _clamp(speed / (pre_depth + 1e-6))
        if recovery_ratio < self.resiliency_threshold:
            return 0.0
        return _clamp(0.5 * recovery_ratio + 0.5 * speed_score)

    def _ml_sweep_probability(self, features: Dict[str, float]) -> float:
        # lightweight logistic model (no external dependency)
        ofi = features.get("ofi", 0.0)
        hawkes = features.get("hawkes", 0.0)
        vol = features.get("volatility", 0.0)
        depth = features.get("depth", 1.0)

        # basic normalization
        vol = _clamp(math.log1p(100.0 * max(0.0, vol)) / 5.0, 0.0, 1.0)
        depth = depth / (1 + depth)

        # normalize inputs to avoid dominance
        ofi = _clamp(ofi / 3.0, -1.0, 1.0)
        hawkes = _clamp(hawkes / (1 + hawkes), 0.0, 1.0)

        z = (0.8 * ofi) + (0.6 * hawkes) + (0.4 * vol) - (0.5 * depth)

        # safe sigmoid
        if z >= 0:
            return 1 / (1 + math.exp(-z))
        else:
            exp_z = math.exp(z)
            return exp_z / (1 + exp_z)

    def _liquidity_forecast(self) -> float:
        if len(self.ofi_history) < 10:
            return 0.0
        val = sum(self.short_ofi) / len(self.short_ofi)
        # scale by rolling std if available to reduce symbol/size regime dependence
        n = len(self.ofi_history)
        if n >= 20:
            ofi_mean = self.ofi_sum / n
            var = (self.ofi_sq_sum / n) - (ofi_mean * ofi_mean)
            if _is_finite(var) and var > 1e-12:
                std = math.sqrt(var)
                val = val / (std + 1e-12)
        return max(-1.0, min(1.0, val))  # preserve directional bias

    def _predict_next_sweep(self, market_data: Dict, ofi_z: float, hawkes_now: float, hawkes_delta: float) -> Dict[str, float]:
        """
        Pure function for predicting directional probability. 
        Requires precomputed variables to avoid mutating history state.
        """
        price = _safe_float(market_data.get("price"))

        # Cold start check: return neutral if no price or uninitialized history
        if price <= 0.0 or len(self.ofi_history) < 10 or self.liquidity_pools.get("high") is None or self.liquidity_pools.get("low") is None:
            try:
                self._neutral_predict_count += 1   # FIX L-2 APPLIED
            except Exception:
                pass
            return {"prob_up": 0.5, "prob_down": 0.5}

        high_pool = self.liquidity_pools.get("high")
        low_pool = self.liquidity_pools.get("low")

        dist_above = abs(high_pool - price)
        dist_below = abs(price - low_pool)
        if dist_above < 1e-6 and dist_below < 1e-6:
            try:
                self._neutral_predict_count += 1   # FIX L-2 APPLIED
            except Exception:
                pass
            return {"prob_up": 0.5, "prob_down": 0.5}

        # --- Feature 1: Distance ---
        # FIX: symmetric + controlled scaling (avoid explosion)
        dist_ratio = math.log((dist_above + 1e-6) / (dist_below + 1e-6))
        dist_ratio = _clamp(dist_ratio, -5.0, 5.0)

        # --- Feature 2: OFI ---
        ofi_signal = math.tanh(ofi_z / 2.0)

        # --- Feature 3: Hawkes acceleration ---
        hawkes_norm = hawkes_now / (1.0 + hawkes_now)
        hawkes_signal = math.tanh(hawkes_delta) * hawkes_norm

        # --- Feature 4: Compression ---
        atr = _safe_float(market_data.get("atr", price * 0.01))
        vol_ratio = atr / (price + 1e-6)
        compression = math.exp(-100.0 * _clamp(vol_ratio, 0.0, 1.0))
        compression = _clamp(compression, 0.0, 1.0)

        # --- Feature 5: Liquidity void ---
        bid_depth = _safe_float(market_data.get("bid_depth", 1.0))
        ask_depth = _safe_float(market_data.get("ask_depth", 1.0))
        if (bid_depth + ask_depth) < 1e-6:
            try:
                self._neutral_predict_count += 1   # FIX L-2 APPLIED
            except Exception:
                pass
            return {"prob_up": 0.5, "prob_down": 0.5}

        bid_depth = max(0.0, bid_depth)
        ask_depth = max(0.0, ask_depth)

        if ask_depth == 0.0 and bid_depth == 0.0:
            imbalance_norm = 0.0
        elif ask_depth == 0.0:
             imbalance_norm = 1.0
        elif bid_depth == 0.0:
             imbalance_norm = -1.0
        else:
             raw_imb = bid_depth / (ask_depth + 1e-6)
             raw_imb = _clamp(raw_imb, 0.01, 100.0)
             imbalance_norm = math.tanh(math.log(raw_imb))

        # --- Logistic model ---
        z = (
            -1.0 * dist_ratio +   # calibrated distance impact
            0.7 * ofi_signal +
            0.6 * hawkes_signal +
            0.5 * compression +
            0.4 * (-imbalance_norm)
        )

        prob_up = _standard_sigmoid(z)
        prob_down = 1.0 - prob_up

        return {
            "prob_up": prob_up,
            "prob_down": prob_down
        }

    def get_signal(self, market_data: Dict, regime_context: Optional[Dict[str, Any]] = None) -> Dict:
        """
        Main Engine Method. 
        Expects: price, close_price, prev_book, curr_book, timestamp, trades_count, 
                 pre_sweep_depth, curr_depth, sweep_time_elapsed, atr, ema_fast, ema_slow,
                 macro_liquidity (optional), macro_market_state (optional), macro_volume_intel (optional)
        """
        with self._lock:
            md = market_data if isinstance(market_data, dict) else {}  # local alias (latency)

            # FIX M-3 APPLIED — _time_lock is now created in __init__, so the
            # previous lazy-init dance is no longer needed. We keep a defensive
            # fallback for instances reconstructed without running __init__.
            if "_time_lock" not in self.__dict__:
                self._time_lock = threading.Lock()
            if not hasattr(self, "last_trade_time"):
                self.last_trade_time = 0.0
            # FIX U-10 — per-bar counter used by gate-telemetry strings.
            try:
                self._bar_idx += 1
            except AttributeError:
                self._bar_idx = 1
            price = _safe_float(md.get('price'))
            if price <= 0.0:
                logic = self._record_hold_gate("INVALID_PRICE", price=price)
                return self._safe_output({
                    "action": "HOLD",
                    "confidence": 0.0,
                    "state": "NORMAL",
                    "regime": "RANGING",
                    "ofi_zscore": 0.0,
                    "hawkes_intensity": 0.0,
                    "logic": logic,
                    "micro_prob": 0.5,
                    "macro_prob": 0.5,
                    "prob_above": 0.5,
                    "prob_below": 0.5,
                })
            close_price = _safe_float(md.get('close_price', price))
            atr = _safe_float(md.get('atr', price * 0.01)) + 1e-8
            if atr < 1e-8:
                atr = 1e-8
            vol_ratio = atr / (price + 1e-8)
            vol_ratio = _clamp(vol_ratio, 1e-6, 10.0)  # prevent extreme scaling
            # HARD safety
            if not math.isfinite(vol_ratio):
                vol_ratio = 1.0
    
            thresholds = self._normalize_thresholds(atr, price)
    
            # Calculate core signals and cache previous state for deltas
            prev_trade_time = getattr(self, "last_trade_time", 0.0)
            prev_lambda = self.hawkes_lambda
            ofi_z = self.calculate_ofi_zscore(md.get('prev_book', {}), md.get('curr_book', {}))
            ofi_z = _clamp(_safe_float(ofi_z, 0.0), -10.0, 10.0)
            hawkes = self._update_hawkes(md.get('timestamp', 0.0), md.get('trades_count', 0))
            hawkes = _clamp(_safe_float(hawkes, 0.0), -50.0, 50.0)
            hawkes_delta = hawkes - prev_lambda
            hawkes_delta = _clamp(_safe_float(hawkes_delta, 0.0), -50.0, 50.0)
    
            # FIX U-07 — compute volume z-score (vs rolling 20-bar) and
            # spread_to_atr (top-of-book) microstructure features used by
            # the expanded regime classifier.
            trades_count = _safe_float(md.get("trades_count", 0.0), 0.0)
            try:
                self._volume_history.append(float(trades_count))
            except Exception:
                pass
            volume_zscore = 0.0
            try:
                vh = list(self._volume_history)
                if len(vh) >= 5:
                    _vm = sum(vh) / len(vh)
                    _vv = sum((v - _vm) ** 2 for v in vh) / max(len(vh) - 1, 1)
                    _vs = math.sqrt(max(_vv, 1e-12))
                    volume_zscore = (float(trades_count) - _vm) / max(_vs, 1e-8)
                    volume_zscore = _clamp(volume_zscore, -10.0, 10.0)
            except Exception:
                volume_zscore = 0.0
            spread_to_atr = 0.0
            try:
                curr_book = md.get("curr_book", {}) or {}
                bids = curr_book.get("bids") or []
                asks = curr_book.get("asks") or []
                if bids and asks:
                    bid0 = _safe_float(bids[0].get("price", 0.0), 0.0)
                    ask0 = _safe_float(asks[0].get("price", 0.0), 0.0)
                    if bid0 > 0.0 and ask0 > 0.0 and ask0 > bid0:
                        spread_to_atr = (ask0 - bid0) / (atr + 1e-8)
                        spread_to_atr = _clamp(spread_to_atr, 0.0, 100.0)
            except Exception:
                spread_to_atr = 0.0

            regime = self._detect_regime(
                md.get('ema_fast', price),
                md.get('ema_slow', price),
                buffer=thresholds["trend_buffer"],
                vol_ratio=vol_ratio,
                session_volume_percentile=_safe_float(
                    md.get("session_volume_percentile", 0.5), 0.5
                ),
                volume_zscore=volume_zscore,
                spread_to_atr=spread_to_atr,
            )

            # FIX U-07 — log regime transitions for telemetry
            try:
                prev_regime = (
                    self._regime_history[-1] if self._regime_history else None
                )
                if prev_regime != regime:
                    self._regime_history.append(regime)
            except Exception:
                pass

            state = self.detect_sweep_state(price, atr, hawkes)

            if regime == "VOLATILE":
                logic_path = "VOLATILE regime gate | " + self._record_hold_gate("VOLATILE", vol_ratio=float(vol_ratio))
                return self._safe_output({
                    "action": "HOLD",
                    "confidence": 0.0,
                    "state": state,
                    "regime": regime,
                    "ofi_zscore": round(ofi_z, 4),
                    "hawkes_intensity": round(hawkes, 4),
                    "logic": logic_path,
                    "micro_prob": 0.5,
                    "macro_prob": 0.5,
                    "prob_above": 0.5,
                    "prob_below": 0.5,
                })
    
            # Microstructure Predictor
            micro_prediction = self._predict_next_sweep(md, ofi_z, hawkes, hawkes_delta)
            # HARD SANITIZATION
            micro_prediction = micro_prediction or {}
            micro_prediction["prob_up"] = _clamp(_safe_float(micro_prediction.get("prob_up", 0.5), 0.5), 0.0, 1.0)
            micro_prediction["prob_down"] = _clamp(_safe_float(micro_prediction.get("prob_down", 0.5), 0.5), 0.0, 1.0)
    
            # Macro Structural Predictor
            macro_liquidity = md.get('macro_liquidity', {})
            macro_market_state = md.get('macro_market_state', {'state': regime, 'volatility': vol_ratio})
            macro_volume_intel = md.get('macro_volume_intel', {})
            macro_reliability = 1.0
            if not macro_liquidity or not isinstance(macro_liquidity, dict):
                macro_reliability = 0.5
            if not macro_market_state or not isinstance(macro_market_state, dict):
                macro_reliability *= 0.7
            # GLOBAL SAFETY (must exist before any usage)
            if not isinstance(macro_reliability, (int, float)) or not math.isfinite(macro_reliability):
                macro_reliability = 0.5
            macro_reliability = _clamp(macro_reliability, 0.0, 1.0)
    
            macro_prediction = predict_sweep(macro_liquidity, macro_market_state, macro_volume_intel)
            macro_prediction = macro_prediction or {}
            macro_prediction["prob_above"] = _clamp(_safe_float(macro_prediction.get("prob_above", 0.5), 0.5), 0.0, 1.0)
            macro_prediction["prob_below"] = _clamp(_safe_float(macro_prediction.get("prob_below", 0.5), 0.5), 0.0, 1.0)
    
            # Handle macro fallback gracefully if pools are undefined
            macro_prob_up = macro_prediction.get("prob_above", 0.5)
            macro_prob_down = macro_prediction.get("prob_below", 0.5)

            # Hard safety clamp
            macro_prob_up = _clamp(_safe_float(macro_prob_up, 0.5), 0.0, 1.0)
            macro_prob_down = _clamp(_safe_float(macro_prob_down, 0.5), 0.0, 1.0)
            micro_prob = None
            macro_prob = None
    
            action = "HOLD"
            confidence = 0.0
            logic_path = "No immediate edge"
    
            # Dynamically define sweep side contextually based on proximity to nearest pool
            high_pool = self.liquidity_pools.get('high')
            low_pool = self.liquidity_pools.get('low')
            if high_pool is not None and low_pool is not None:
                sweep_side = "high" if abs(high_pool - price) <= abs(price - low_pool) else "low"
            elif high_pool is not None:
                sweep_side = "high"
            elif low_pool is not None:
                sweep_side = "low"
            else:
                sweep_side = "high"
    
            # Progressive Confidence Gating: Replaces hard threshold with continuous scaler based on deque warmth.
            # Stable warmup (monotonic + balanced)
            ofi_warm   = _clamp(len(self.ofi_history) / 20.0, 0.0, 1.0)
            hawkes_warm = _clamp(len(self.hawkes_history) / 5.0, 0.0, 1.0)
            warmup_factor = 0.5 * ofi_warm + 0.5 * hawkes_warm
            warmup_factor = _clamp(warmup_factor, 0.0, 1.0)
            # Use data-timestamp inter-tick gap (not wall clock) for backtest fidelity
            # ATOMIC TIME READ + UPDATE (fix race condition)
            with self._time_lock:
                current_time = self._normalize_timestamp(md.get("timestamp", self.last_trade_time), self.last_trade_time)
                if current_time <= self.last_trade_time:
                    current_time = self.last_trade_time
                raw_gap = current_time - self.last_trade_time
                if not math.isfinite(raw_gap):
                    raw_gap = 0.0

                data_gap = _clamp(raw_gap, 0.0, 60.0)
                self.last_trade_time = current_time

            time_decay = math.exp(-0.01 * data_gap)
            if not math.isfinite(time_decay):
                time_decay = 1.0

            time_decay = _clamp(time_decay, 0.0, 1.0)
            warmup_factor = _clamp(0.6 * warmup_factor + 0.4 * _clamp(time_decay, 0.3, 1.0), 0.0, 1.0)
    
            regime_name = str((regime_context or {}).get("regime", "")).upper()
            threshold_offset = 0.0
            if "TREND" in regime_name:
                threshold_offset = -0.02
            elif "TOXIC" in regime_name:
                threshold_offset = 0.05


            # Ensure macro_reliability always defined (no locals() usage)
            if not isinstance(macro_reliability, (int, float)):
                macro_reliability = 0.5
            macro_reliability = _clamp(macro_reliability, 0.0, 1.0)

            if state == "PRE_SWEEP_BUILDUP":
                # --- Early Anticipation Logic ---
                # For a breakout (anticipation), we want high probability that it continues *through* the level.
                # If approaching 'high', we want prob_up. If 'low', we want prob_down.
                pred_micro = micro_prediction["prob_up"] if sweep_side == "high" else micro_prediction["prob_down"]
                pred_macro = macro_prob_up if sweep_side == "high" else macro_prob_down
                # PRE-CALIBRATION SAFETY (critical)
                if not math.isfinite(pred_micro):
                    pred_micro = 0.5
                if not math.isfinite(pred_macro):
                    pred_macro = 0.5
                pred_micro = self._shrink_prob(pred_micro)
                pred_macro = self._shrink_prob(pred_macro)
                # HARD safety after calibration (critical)
                if not math.isfinite(pred_micro):
                    pred_micro = 0.5
                if not math.isfinite(pred_macro):
                    pred_macro = 0.5
                pred_macro = _clamp(0.5 + (pred_macro - 0.5) * macro_reliability, 0.0, 1.0)
                micro_prob = _clamp(pred_micro, 0.0, 1.0)
                macro_prob = _clamp(pred_macro, 0.0, 1.0)
    
                # Feature Decorrelation: Softly reduce macro weight when microstructure z-score is highly active
                # This mathematically decorrelates structurally repetitive features mapped in both predictive sets.
                hawkes_term = math.tanh(hawkes / 5.0)
                # HARD SAFETY BEFORE NONLINEAR TRANSFORM
                if not math.isfinite(hawkes_term):
                    hawkes_term = 0.0

                # Clamp reliability
                macro_reliability = _clamp(_safe_float(macro_reliability, 0.5), 0.0, 1.0)
                corr_proxy = _clamp(
                    0.6 * abs(ofi_z) / 3.0 +
                    0.4 * hawkes_term,
                    0.0,
                    1.0,
                )
                macro_weight = max(0.15 * macro_reliability, 0.4 * (1.0 - corr_proxy) * macro_reliability)
                # STRICT WEIGHT NORMALIZATION (prevent negative / overflow)
                macro_weight = _clamp(macro_weight, 0.0, 1.0)
                micro_weight = 1.0 - macro_weight
                if micro_weight < 0.0:
                    micro_weight = 0.0
                    macro_weight = 1.0
    
                # Logit Ensemble: Ensures proper probabilistic aggregation rather than linear weighting.
                pred_micro = _clamp(pred_micro, 1e-6, 1.0 - 1e-6)
                pred_macro = _clamp(pred_macro, 1e-6, 1.0 - 1e-6)
                lm = _safe_logit_guard(pred_micro, min(vol_ratio, 5.0))
                la = _safe_logit_guard(pred_macro, min(vol_ratio, 5.0))
                final_logit = (micro_weight * lm) + (macro_weight * la)
                # HARD SAFETY (missing)
                if not math.isfinite(final_logit):
                    final_logit = 0.0
                combined_prob = _standard_sigmoid(final_logit)
                if not math.isfinite(combined_prob):
                    combined_prob = 0.5
                combined_prob = _clamp(combined_prob, 0.0, 1.0)
                # Align with warmup logic (balanced + stable)
                ofi_hist   = _clamp(len(self.ofi_history) / 20.0, 0.0, 1.0)
                hawkes_hist = _clamp(len(self.hawkes_history) / 5.0, 0.0, 1.0)
                min_history_factor = 0.5 * ofi_hist + 0.5 * hawkes_hist
                min_history_factor = _clamp(min_history_factor, 0.0, 1.0)
    
                # Execution threshold dynamically tightens when the system is cold
                # Absorbs both warmup and history gating without destroying probability calibration
                # FIX M-2 APPLIED — hoist threshold_offset OUTSIDE the
                # floor/ceiling clamp so the clamp applies to the final
                # value (previously the offset was absorbed inside the
                # 0.45/0.9 clamp, defeating its purpose at the extremes).
                base_threshold = 0.55 + 0.10 * (1.0 - warmup_factor) + 0.10 * (1.0 - min_history_factor)
                raw_threshold = base_threshold + threshold_offset
                threshold = _clamp(raw_threshold, 0.45, 0.9)
    
                if combined_prob >= threshold:
                    # FIX C-1 APPLIED — directional mode controls whether a
                    # PRE_SWEEP_BUILDUP entry is taken as continuation
                    # (default, original behaviour) or as a fade.
                    if self.direction_mode == "fade":
                        action = "SELL" if sweep_side == "high" else "BUY"
                    else:  # continuation (default, preserves existing behavior)
                        action = "BUY" if sweep_side == "high" else "SELL"
                    # Calibrated Confidence: Confidence explicitly maps to normalized probability space.
                    confidence = combined_prob
                    logic_path = f"Anticipatory early entry on {sweep_side} buildup. Prob: {combined_prob:.2f}"
                else:
                    logic_path = "Buildup detected, awaiting breach or stronger confirmation"
    
            elif state == "ACTIVE_SWEEP":
                if warmup_factor < 0.5:
                    action = "HOLD"
                    confidence = 0.0
                    logic_path = self._record_hold_gate(
                        "WARMUP", warmup=float(warmup_factor)
                    )
                    return self._safe_output({
                        "action": action,
                        "confidence": round(confidence, 4),
                        "state": state,
                        "regime": regime,
                        "ofi_zscore": round(ofi_z, 4),
                        "hawkes_intensity": round(hawkes, 4),
                        "logic": logic_path,
                        "micro_prob": 0.5,
                        "macro_prob": 0.5,
                        "prob_above": 0.5,
                        "prob_below": 0.5,
                    })
                close_price = _safe_float(md.get("close_price", price))
                is_fake, rej_score = self._detect_fake_breakout(sweep_side, close_price, ofi_z)
                res_score = self.check_resiliency(
                    md.get('pre_sweep_depth', 1.0), 
                    md.get('curr_depth', 1.0), 
                    md.get('sweep_time_elapsed', 0.0)
                )
    
                w_ofi, w_res, w_rej = 0.3, 0.4, 0.3
                ofi_component = _clamp(abs(ofi_z) / 3.0) 
                res_component = _clamp(res_score)
    
                raw_logit = (w_ofi * ofi_component) + (w_res * res_component) + (w_rej * rej_score)
                centered_logit = (raw_logit - 0.5) * 4.0
                reaction_score = _standard_sigmoid(centered_logit)
    
                trend_penalty = 0.0
                if (sweep_side == "high" and regime == "TRENDING_UP") or (sweep_side == "low" and regime == "TRENDING_DOWN"):
                    trend_penalty = 0.2 
    
                reaction_score = _clamp(reaction_score - trend_penalty)
    
                ml_prob = self._ml_sweep_probability({
                    "ofi": ofi_z,
                    "hawkes": hawkes,
                    "volatility": vol_ratio,
                    "depth": md.get("curr_depth", 1.0)
                })
    
                liquidity_bias = self._liquidity_forecast()
                liq_prob = (liquidity_bias + 1.0) / 2.0
    
                # --- Mean Reversion (Fade) Logic ---
                # Ensure required variables exist (explicit fallback)
                if not isinstance(reaction_score, (int, float)):
                    reaction_score = 0.5
                if not isinstance(ml_prob, (int, float)):
                    ml_prob = 0.5
                reaction_score = _clamp(reaction_score, 1e-6, 1.0 - 1e-6)
                ml_prob = _clamp(ml_prob, 1e-6, 1.0 - 1e-6)
                # In an active sweep, we are looking for the fake-out / reversion.
                # If sweeping 'high', we want high prob_down. If 'low', we want prob_up.
                pred_micro = micro_prediction["prob_down"] if sweep_side == "high" else micro_prediction["prob_up"]
                pred_macro = macro_prob_down if sweep_side == "high" else macro_prob_up
                # PRE-CALIBRATION SAFETY (missing critical fix)
                if not math.isfinite(pred_micro):
                    pred_micro = 0.5
                if not math.isfinite(pred_macro):
                    pred_macro = 0.5
                pred_micro = self._shrink_prob(pred_micro)
                pred_macro = self._shrink_prob(pred_macro)
                # POST-CALIBRATION SAFETY
                if not math.isfinite(pred_micro):
                    pred_micro = 0.5
                if not math.isfinite(pred_macro):
                    pred_macro = 0.5
                pred_macro = _clamp(0.5 + (pred_macro - 0.5) * macro_reliability, 0.0, 1.0)
                micro_prob = _clamp(pred_micro, 0.0, 1.0)
                macro_prob = _clamp(pred_macro, 0.0, 1.0)
    
                hawkes_term = math.tanh(hawkes / 5.0)
                # HARD SAFETY BEFORE NONLINEAR TRANSFORM (missing)
                if not math.isfinite(hawkes_term):
                    hawkes_term = 0.0
                corr_proxy = _clamp(
                    0.6 * abs(ofi_z) / 3.0 +
                    0.4 * hawkes_term,
                    0.0,
                    1.0,
                )
                macro_weight = max(0.15 * macro_reliability, 0.4 * (1.0 - corr_proxy) * macro_reliability)
                # STRICT WEIGHT NORMALIZATION (ACTIVE_SWEEP FIX)
                macro_weight = _clamp(macro_weight, 0.0, 1.0)
                micro_weight = 1.0 - macro_weight
                if micro_weight < 0.0:
                    micro_weight = 0.0
                    macro_weight = 1.0
    
                # Predictors subset ensemble
                pred_micro = _clamp(pred_micro, 1e-6, 1.0 - 1e-6)
                pred_macro = _clamp(pred_macro, 1e-6, 1.0 - 1e-6)
                lm = _safe_logit_guard(pred_micro, min(vol_ratio, 5.0))
                la = _safe_logit_guard(pred_macro, min(vol_ratio, 5.0))
                pred_logit = (micro_weight * lm) + (macro_weight * la)
                if not math.isfinite(pred_logit):
                    pred_logit = 0.0

                # Logit Ensemble: Full statistical mapping across all primary system variables.
                # Single clamp (avoid distortion)
                reaction_score = _clamp(reaction_score, 1e-6, 1.0 - 1e-6)
                ml_prob        = _clamp(ml_prob,        1e-6, 1.0 - 1e-6)
                liq_prob       = _clamp(liq_prob,       1e-6, 1.0 - 1e-6)

                ensemble_logit = (
                    # CONSISTENT LOGIT SCALING (bounded vol_ratio)
                    0.40 * _safe_logit_guard(reaction_score, min(vol_ratio, 5.0)) +
                    0.25 * _safe_logit_guard(ml_prob,        min(vol_ratio, 5.0)) +
                    0.15 * _safe_logit_guard(liq_prob,       min(vol_ratio, 5.0)) +
                    0.20 * pred_logit
                )
                # HARD SAFETY BEFORE SIGMOID
                if not math.isfinite(ensemble_logit):
                    ensemble_logit = 0.0
                ensemble_score = _standard_sigmoid(ensemble_logit)
                if not math.isfinite(ensemble_score):
                    ensemble_score = 0.5
                ensemble_score = _clamp(ensemble_score, 0.0, 1.0)
    
                active_sweep_threshold = _clamp(0.65 + threshold_offset, 0.5, 0.9)
                trend_aligned = (
                    (sweep_side == "high" and regime == "TRENDING_UP")
                    or (sweep_side == "low" and regime == "TRENDING_DOWN")
                )
                if ensemble_score >= active_sweep_threshold and is_fake:
                    action = "SELL" if sweep_side == "high" else "BUY"
                    confidence = max(0.0, ensemble_score - 0.15 * (1.0 - warmup_factor))
                    logic_path = (
                        f"Fake {sweep_side} sweep confirmed via logit ensemble; "
                        f"trend_aligned={trend_aligned}."
                    )
                    # EXPLICIT RISK GATE: trend-aligned sweeps carry continuation risk.
                    # Suppression is isolated here rather than encoded as an inline
                    # signal-generation shortcut.
                    if trend_aligned:
                        action = "HOLD"
                        confidence = 0.0
                        self._record_hold_gate("TREND_ALIGNED", regime=str(regime), confidence=0.0)
                        logic_path = (
                            f"Active {sweep_side} sweep suppressed by trend_filter gate; "
                            f"trend_aligned={trend_aligned}."
                        )
                else:
                    logic_path = (
                        f"True breakout / lack of reversion edge on {sweep_side} sweep; "
                        f"trend_aligned={trend_aligned}."
                    )
    
            regime_label = str((regime_context or {}).get("regime", regime)).upper()

            if "RANGE" in regime_label:
                confidence *= 0.9
            # Match both internal (TRENDING_UP/TRENDING_DOWN) and external (TREND/BEAR)
            # regime labels emitted by AdvancedRegimeEngine.
            if ("TRENDING_UP" in regime_label or regime_label == "TREND") and action == "SELL":
                confidence *= 0.9
            if ("TRENDING_DOWN" in regime_label or regime_label == "BEAR") and action == "BUY":
                confidence *= 0.9
            # FINAL CONFIDENCE SAFETY (prevent drift)
            if not math.isfinite(confidence):
                confidence = 0.0
            confidence = _clamp(confidence, 0.0, 1.0)
    
            # unify probability schema for engine compatibility
            if micro_prob is None:
                final_prob_up = 0.5
            else:
                if action == "BUY":
                    final_prob_up = micro_prob
                elif action == "SELL":
                    final_prob_up = 1.0 - micro_prob
                elif sweep_side == "high":
                    final_prob_up = micro_prob
                elif sweep_side == "low":
                    final_prob_up = 1.0 - micro_prob
                else:
                    final_prob_up = 0.5
            # sanitize both first
            final_prob_up = _safe_num(final_prob_up, 0.5)
            final_prob_down = _safe_num(1.0 - final_prob_up, 0.5)

            if not math.isfinite(final_prob_up):
                final_prob_up = 0.5
            if not math.isfinite(final_prob_down):
                final_prob_down = 0.5

            # normalize to ensure sum = 1 (strict probabilistic consistency)
            total = final_prob_up + final_prob_down
            if not math.isfinite(total) or total < 1e-12:
                final_prob_up = 0.5
                final_prob_down = 0.5
            else:
                inv_total = 1.0 / total
                final_prob_up *= inv_total
                final_prob_down *= inv_total

            final_prob_up = _clamp(final_prob_up, 0.0, 1.0)
            final_prob_down = _clamp(final_prob_down, 0.0, 1.0)

            # Final output hardening (never trust upstream)
            micro_prob = _clamp(_safe_float(micro_prob if micro_prob is not None else 0.5, 0.5), 0.0, 1.0)
            macro_prob = _clamp(_safe_float(macro_prob if macro_prob is not None else 0.5, 0.5), 0.0, 1.0)

            # FIX U-10 — HOLD-gate telemetry: classify why HOLD was emitted
            if action == "HOLD":
                pools_unset = (
                    self.liquidity_pools.get("high") is None
                    or self.liquidity_pools.get("low") is None
                )
                if regime == "VOLATILE":
                    logic_path = self._record_hold_gate(
                        "VOLATILE", vol_ratio=float(vol_ratio)
                    )
                elif pools_unset:
                    logic_path = self._record_hold_gate(
                        "POOL_UNSET", state=str(state)
                    )
                elif regime in ("TRENDING_UP", "TRENDING_DOWN"):
                    if "suppressed" not in str(logic_path):
                        logic_path = self._record_hold_gate(
                            "TREND_ALIGNED", regime=str(regime),
                            confidence=float(confidence),
                        )
                else:
                    logic_path = self._record_hold_gate(
                        "NO_EDGE", state=str(state), regime=str(regime),
                        confidence=float(confidence),
                    )

            return self._safe_output({
                "action": action,
                "confidence": confidence,
                "state": state,
                "regime": regime,
                "ofi_zscore": ofi_z,
                "hawkes_intensity": hawkes,
                "logic": logic_path,
                "micro_prob": micro_prob,
                "macro_prob": macro_prob,
                "prob_above": final_prob_up,
                "prob_below": final_prob_down,
            })

    def predict(self, data: Dict[str, Any], regime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Backward-compatible predict interface:
        - Accepts raw market_data OR {"features": {...}}
        """
        if not isinstance(data, dict):
            # Route through _safe_output so every return honours the full schema
            # (action, confidence, state, regime, ofi_zscore, hawkes_intensity,
            # logic, micro_prob, macro_prob, prob_above, prob_below).
            return self._safe_output({
                "action": "HOLD",
                "confidence": 0.0,
                "state": "UNKNOWN",
                "regime": "RANGING",
                "ofi_zscore": 0.0,
                "hawkes_intensity": 0.0,
                "logic": "invalid_input",
                "micro_prob": 0.5,
                "macro_prob": 0.5,
                "prob_above": 0.5,
                "prob_below": 0.5,
            })

        if "features" in data and isinstance(data["features"], dict):
            data = data["features"]

        out = self.get_signal(data, regime_context=regime_context) or {}

        # FIX-28 (M-2): opt-in directional fallback. If the primary path
        # returned HOLD AND the operator explicitly enabled the fallback,
        # consult predict_sweep() and bias the action by ofi_zscore /
        # hawkes_intensity. Fail-soft: any error keeps the original HOLD.
        try:
            if (
                isinstance(out, dict)
                and out.get("action") == "HOLD"
                and getattr(self, "enable_sweep_directional_fallback", False)
            ):
                # FIX (audit 2026-05-18) — predict_sweep() reads
                # `nearest_above` / `nearest_below` (each {distance_points,
                # price}); pass them properly so directional bias actually
                # reflects pool structure instead of defaulting to BUY.
                _px = _safe_float(
                    (data.get("close") if isinstance(data, dict) else None)
                    or (data.get("price") if isinstance(data, dict) else None),
                    0.0,
                )
                _hi = self.liquidity_pools.get("high")
                _lo = self.liquidity_pools.get("low")
                liq_state: Dict[str, Any] = {
                    "ofi_zscore": float(out.get("ofi_zscore", 0.0) or 0.0),
                    "hawkes_intensity": float(out.get("hawkes_intensity", 0.0) or 0.0),
                }
                if _hi is not None and _px > 0:
                    liq_state["nearest_above"] = {
                        "price": float(_hi),
                        "distance_points": max(0.0, float(_hi) - _px),
                    }
                if _lo is not None and _px > 0:
                    liq_state["nearest_below"] = {
                        "price": float(_lo),
                        "distance_points": max(0.0, _px - float(_lo)),
                    }
                fallback = predict_sweep(
                    liq_state,
                    data if isinstance(data, dict) else {},
                    data.get("volume_intel", {}) if isinstance(data, dict) else {},
                ) or {}
                # FIX (audit 2026-05-18) — predict_sweep() returns
                # `side` ("above"/"below"), not `action`. Map it.
                fb_action = fallback.get("action")
                if fb_action not in ("BUY", "SELL"):
                    fb_side = str(fallback.get("side", "")).lower()
                    if fb_side == "above":
                        fb_action = "BUY"
                    elif fb_side == "below":
                        fb_action = "SELL"
                if fb_action in ("BUY", "SELL"):
                    out = dict(out)
                    out["action"] = fb_action
                    # Confidence is intentionally clamped low — fallback is
                    # a hint, not a primary signal.
                    out["confidence"] = float(
                        max(0.0, min(0.5, float(fallback.get("confidence", 0.25) or 0.25)))
                    )
                    prev_logic = str(out.get("logic", "") or "")
                    out["logic"] = (
                        prev_logic + "|" if prev_logic else ""
                    ) + "directional_fallback"
        except Exception:
            # Strictly fail-soft — keep the original HOLD output on any error.
            pass

        # HARD GUARANTEE (never bypass safety contract)
        return self._safe_output(out)

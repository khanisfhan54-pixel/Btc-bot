from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Sequence, Tuple

import numpy as np

from ..features.feature_vector import StopHuntFeatureVector
from .calibrator import ProbabilityCalibrator
from .regime_conditional import RegimeConditionalClassifier

SHPE_FEATURE_NAMES: Tuple[str, ...] = ("pool_dist_to_high_pct","pool_dist_to_low_pct","pool_high_pool_age_bars","pool_low_pool_age_bars","pool_round_number_proximity_bps","funding_rate_8h","funding_z30d","funding_oi_sign_divergence","oi_delta_oi_velocity","oi_pct_change_1h","oi_buildup_flag","oi_price_divergence_sign","volume_wick_to_body_ratio","volume_upper_wick_pct","volume_lower_wick_pct","volume_zscore","volume_at_extreme_vs_close","volume_exhaustion_candle_flag","lob_ofi_zscore","lob_queue_imbalance","lob_depth_replenishment_ratio","liq_nearest_long_cluster_dist_pct","liq_nearest_short_cluster_dist_pct","liq_cascade_amplification_flag","regime_confidence","regime_conviction","regime_edge_score","regime_signal_valid","regime_expected_volatility")


def _f(v: object) -> float:
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        f = float(v)
    except Exception:
        return 0.0
    return 0.0 if not np.isfinite(f) else f


def feature_vector_to_array(fv: StopHuntFeatureVector) -> np.ndarray:
    p,fnd,oi,vol,lob,liq,reg=(fv.pool,fv.funding,fv.oi,fv.volume,fv.lob,fv.liquidation,fv.regime)
    return np.array([_f(p.dist_to_high_pool_pct),_f(p.dist_to_low_pool_pct),_f(p.high_pool_age_bars),_f(p.low_pool_age_bars),_f(p.round_number_proximity_bps),_f(fnd.funding_rate_8h),_f(fnd.funding_z30d),_f(fnd.funding_oi_sign_divergence),_f(oi.delta_oi_velocity),_f(oi.oi_pct_change_1h),_f(oi.oi_buildup_flag),_f(oi.oi_price_divergence_sign),_f(vol.wick_to_body_ratio),_f(vol.upper_wick_pct),_f(vol.lower_wick_pct),_f(vol.volume_zscore),_f(vol.volume_at_extreme_vs_close),_f(vol.exhaustion_candle_flag),_f(lob.ofi_zscore),_f(lob.queue_imbalance),_f(lob.depth_replenishment_ratio),_f(liq.nearest_long_cluster_dist_pct),_f(liq.nearest_short_cluster_dist_pct),_f(liq.cascade_amplification_flag),_f(reg.confidence),_f(reg.conviction),_f(reg.edge_score),_f(reg.signal_valid),_f(reg.expected_volatility)],dtype=float)


@dataclass(frozen=True)
class SHPEPrediction:
    p_sweep: float
    degraded: bool
    regime_used: str
    stale_dimensions: Tuple[str, ...]
    model_version: str
    raw_p_sweep: Optional[float] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "p_sweep", float(max(0.0, min(1.0, self.p_sweep))))


DEGRADED_FALLBACK_P = 0.5
STALENESS_LIMIT = 2


@dataclass
class StopHuntProbabilityEngine:
    classifier: RegimeConditionalClassifier
    calibrator: Optional[ProbabilityCalibrator] = None
    feature_names: Tuple[str, ...] = SHPE_FEATURE_NAMES
    model_version: str = "shpe.v1.0.0-baseline"
    staleness_limit: int = STALENESS_LIMIT

    def predict(self, fv: StopHuntFeatureVector) -> SHPEPrediction:
        stale = tuple(fv.stale_dimensions)
        if len(stale) > self.staleness_limit:
            return SHPEPrediction(DEGRADED_FALLBACK_P, True, "<degraded>", stale, self.model_version, None)
        x = feature_vector_to_array(fv).reshape(1, -1)
        raw_p, used = self.classifier.predict_proba(x, fv.regime.regime_label)
        cal_p = float(self.calibrator.transform(np.array([raw_p]))[0]) if self.calibrator is not None else float(raw_p)
        return SHPEPrediction(cal_p, False, used, stale, self.model_version, float(raw_p))

    @classmethod
    def train(
        cls,
        feature_vectors: Sequence[StopHuntFeatureVector],
        labels: Sequence[int],
        regime_labels: Sequence[str],
        *,
        calibrate_method: Optional[Literal["platt", "isotonic"]] = "platt",
        calibration_holdout_frac: float = 0.2,
        min_samples_per_regime: int = 30,
        max_feature_importance: float = 0.3,
        run_importance_audit: bool = True,
        model_version: str = "shpe.v1.0.0-baseline",
    ) -> "StopHuntProbabilityEngine":
        if len(feature_vectors) != len(labels) or len(labels) != len(regime_labels):
            raise ValueError("feature_vectors, labels, regime_labels length mismatch")
        n = len(feature_vectors)
        X = np.stack([feature_vector_to_array(fv) for fv in feature_vectors])
        y = np.asarray(labels, dtype=int)
        regs = [str(r) for r in regime_labels]

        split = int(round(n * (1.0 - calibration_holdout_frac)))
        split = min(max(split, 1), n - 1) if n > 1 else 1

        clf = RegimeConditionalClassifier(
            feature_names=list(SHPE_FEATURE_NAMES),
            min_samples_per_regime=min_samples_per_regime,
            max_feature_importance=max_feature_importance,
        ).fit(X[:split], y[:split], regs[:split], run_importance_audit=run_importance_audit)

        cal = None
        if calibrate_method is not None and n - split >= 2 and np.unique(y[split:]).size >= 2:
            raw = np.array([clf.predict_proba(X[i:i+1], regs[i])[0] for i in range(split, n)], dtype=float)
            cal = ProbabilityCalibrator(method=calibrate_method).fit(raw, y[split:])
            clf.last_routing_log.clear()

        return cls(classifier=clf, calibrator=cal, feature_names=SHPE_FEATURE_NAMES, model_version=model_version)

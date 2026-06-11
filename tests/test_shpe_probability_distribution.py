import numpy as np

from stop_hunt_engine.features.feature_vector import StopHuntFeatureVector
from stop_hunt_engine.features.funding_pressure import FundingPressureFeatures
from stop_hunt_engine.features.liquidation_proximity import LiquidationProximityFeatures
from stop_hunt_engine.features.lob_imbalance import LOBImbalanceFeatures
from stop_hunt_engine.features.oi_dynamics import OIDynamicsFeatures
from stop_hunt_engine.features.pool_distance import PoolDistanceFeatures
from stop_hunt_engine.features.regime_context import RegimeContextFeatures
from stop_hunt_engine.features.volume_trap import VolumeTrapFeatures
from stop_hunt_engine.model.calibrator import ProbabilityCalibrator
from stop_hunt_engine.model.engine import SHPE_FEATURE_NAMES, StopHuntProbabilityEngine
from stop_hunt_engine.model.regime_conditional import RegimeConditionalClassifier


def _fv(x0, x1):
    return StopHuntFeatureVector(
        bar_index=0,
        timestamp=1.0,
        pool=PoolDistanceFeatures(dist_to_high_pool_pct=x0, dist_to_low_pool_pct=x1),
        funding=FundingPressureFeatures(stale=False),
        oi=OIDynamicsFeatures(stale=False),
        volume=VolumeTrapFeatures(stale=False),
        lob=LOBImbalanceFeatures(stale=False),
        liquidation=LiquidationProximityFeatures(stale=False),
        regime=RegimeContextFeatures(regime_label="trend", signal_valid=True, stale=False),
        stale_dimensions=[],
    )


def test_calibrated_probabilities_are_non_constant():
    rng = np.random.default_rng(11)
    X = rng.normal(size=(120, len(SHPE_FEATURE_NAMES)))
    y = (X[:, 0] - X[:, 1] > 0).astype(int)
    clf = RegimeConditionalClassifier(list(SHPE_FEATURE_NAMES), min_samples_per_regime=1000)
    clf.fit(X, y, ["trend"] * len(y), run_importance_audit=False)
    raw_scores = clf.global_model.predict_proba(X)
    cal = ProbabilityCalibrator(method="platt").fit(raw_scores, y)
    engine = StopHuntProbabilityEngine(clf, cal, model_version="test.v1")

    probs = [engine.predict(_fv(v, -v)).p_sweep for v in (-2.0, -0.5, 0.5, 2.0)]

    assert all(0.0 <= p <= 1.0 for p in probs)
    assert max(probs) - min(probs) > 0.05
    assert len({round(p, 4) for p in probs}) > 1

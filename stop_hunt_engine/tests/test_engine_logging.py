import logging
import numpy as np

from stop_hunt_engine.features.feature_vector import StopHuntFeatureVector
from stop_hunt_engine.features.pool_distance import PoolDistanceFeatures
from stop_hunt_engine.features.funding_pressure import FundingPressureFeatures
from stop_hunt_engine.features.oi_dynamics import OIDynamicsFeatures
from stop_hunt_engine.features.volume_trap import VolumeTrapFeatures
from stop_hunt_engine.features.lob_imbalance import LOBImbalanceFeatures
from stop_hunt_engine.features.liquidation_proximity import LiquidationProximityFeatures
from stop_hunt_engine.features.regime_context import RegimeContextFeatures
from stop_hunt_engine.model.engine import StopHuntProbabilityEngine
from stop_hunt_engine.model.regime_conditional import RegimeConditionalClassifier


def _fv(stale=None):
    return StopHuntFeatureVector(
        0, 0.0,
        PoolDistanceFeatures(), FundingPressureFeatures(stale=False), OIDynamicsFeatures(stale=False),
        VolumeTrapFeatures(), LOBImbalanceFeatures(stale=False), LiquidationProximityFeatures(stale=False),
        RegimeContextFeatures(regime_label="range"), stale or []
    )


def test_stale_dimensions_are_logged_on_degraded_fallback(caplog):
    clf = RegimeConditionalClassifier(feature_names=[str(i) for i in range(29)])
    X = np.random.randn(20, 29)
    y = np.array([0, 1] * 10)
    clf.fit(X, y, ["range"] * 20, run_importance_audit=False)
    eng = StopHuntProbabilityEngine(classifier=clf)
    with caplog.at_level(logging.WARNING, logger="shpe.engine"):
        pred = eng.predict(_fv(stale=["funding", "oi", "lob"]))
    assert pred.degraded is True
    assert any("shpe_degraded_fallback" in rec.message for rec in caplog.records)

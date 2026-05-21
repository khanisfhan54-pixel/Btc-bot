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


def _fv(i: int) -> StopHuntFeatureVector:
    return StopHuntFeatureVector(
        i, float(i),
        PoolDistanceFeatures(i % 3, i % 4, i % 5, i % 6, i % 7),
        FundingPressureFeatures(0.0001 * (i % 4 - 2), 0.1 * (i % 3 - 1), 1.0 if i % 2 else -1.0, False),
        OIDynamicsFeatures(0.1 * i, 0.01 * (i % 5), i % 2 == 0, 1.0 if i % 3 else -1.0, False),
        VolumeTrapFeatures(1.0, 0.2, 0.2, 0.0, 0.1, False, False),
        LOBImbalanceFeatures(0.0, 0.0, 1.0, False),
        LiquidationProximityFeatures(0.01, 0.01, False, False),
        RegimeContextFeatures("range", 0.7, 0.6, 0.1, True, 0.02, False),
        [],
    )


def test_train_uses_holdout_calibration() -> None:
    fvs = [_fv(i) for i in range(40)]
    labels = [i % 2 for i in range(40)]
    regs = ["range" for _ in range(40)]
    eng = StopHuntProbabilityEngine.train(fvs, labels, regs, calibrate_method="platt", calibration_holdout_frac=0.25, run_importance_audit=False)
    assert eng.classifier.global_model is not None
    # model trained on first 30 rows only
    assert eng.classifier.train_counts().get("range", 0) == 30

import numpy as np
from stop_hunt_engine.model.engine import StopHuntProbabilityEngine
from stop_hunt_engine.model.regime_conditional import RegimeConditionalClassifier
from stop_hunt_engine.features.feature_vector import StopHuntFeatureVector
from stop_hunt_engine.features.pool_distance import PoolDistanceFeatures
from stop_hunt_engine.features.funding_pressure import FundingPressureFeatures
from stop_hunt_engine.features.oi_dynamics import OIDynamicsFeatures
from stop_hunt_engine.features.volume_trap import VolumeTrapFeatures
from stop_hunt_engine.features.lob_imbalance import LOBImbalanceFeatures
from stop_hunt_engine.features.liquidation_proximity import LiquidationProximityFeatures
from stop_hunt_engine.features.regime_context import RegimeContextFeatures

def _fv(stale=None, reg="r"):
    return StopHuntFeatureVector(0,0,PoolDistanceFeatures(),FundingPressureFeatures(stale=False),OIDynamicsFeatures(stale=False),VolumeTrapFeatures(),LOBImbalanceFeatures(stale=False),LiquidationProximityFeatures(stale=False),RegimeContextFeatures(regime_label=reg),stale or [])

def test_degraded_fallback():
    clf=RegimeConditionalClassifier(feature_names=[str(i) for i in range(29)])
    X=np.random.randn(20,29);y=np.array([0,1]*10)
    clf.fit(X,y,["r"]*20,run_importance_audit=False)
    eng=StopHuntProbabilityEngine(classifier=clf)
    p=eng.predict(_fv(stale=["a","b","c"]))
    assert p.degraded and p.p_sweep==0.5

def test_regime_fallback_global():
    clf=RegimeConditionalClassifier(feature_names=[str(i) for i in range(29)],min_samples_per_regime=100)
    X=np.random.randn(20,29);y=np.array([0,1]*10)
    clf.fit(X,y,["known"]*20,run_importance_audit=False)
    p,used=clf.predict_proba(np.random.randn(1,29),"unknown")
    assert used=="<global>" and 0<=p<=1

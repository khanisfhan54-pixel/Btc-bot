import numpy as np
from stop_hunt_engine.features.feature_vector import StopHuntFeatureVector
from stop_hunt_engine.features.pool_distance import PoolDistanceFeatures
from stop_hunt_engine.features.funding_pressure import FundingPressureFeatures
from stop_hunt_engine.features.oi_dynamics import OIDynamicsFeatures
from stop_hunt_engine.features.volume_trap import VolumeTrapFeatures
from stop_hunt_engine.features.lob_imbalance import LOBImbalanceFeatures
from stop_hunt_engine.features.liquidation_proximity import LiquidationProximityFeatures
from stop_hunt_engine.features.regime_context import RegimeContextFeatures
from stop_hunt_engine.model.engine import feature_vector_to_array

def test_feature_flattening_order():
    fv=StopHuntFeatureVector(0,0.0,PoolDistanceFeatures(1,2,3,4,5),FundingPressureFeatures(6,7,8,False),OIDynamicsFeatures(9,10,True,-1,False),VolumeTrapFeatures(11,12,13,14,15,True,False),LOBImbalanceFeatures(16,17,18,False),LiquidationProximityFeatures(19,20,True,False),RegimeContextFeatures("range",21,22,23,True,24,False),[])
    arr=feature_vector_to_array(fv)
    assert np.allclose(arr[:5],[1,2,3,4,5])
    assert arr[-1]==24

import numpy as np
from stop_hunt_engine.model.calibrator import ProbabilityCalibrator

def test_calibration_in_unit_interval():
    raw=np.array([0.01,0.2,0.7,0.95]); y=np.array([0,0,1,1])
    cal=ProbabilityCalibrator(method="platt").fit(raw,y)
    out=cal.transform(np.array([0.0,0.5,1.0]))
    assert ((out>=0)&(out<=1)).all()

import numpy as np
from stop_hunt_engine.model.calibrator import ProbabilityCalibrator, brier_score, expected_calibration_error


def test_brier_and_ece_bounded_and_monotonic():
    raw=np.array([0.1,0.2,0.4,0.6,0.8,0.9])
    y=np.array([0,0,0,1,1,1])
    cal=ProbabilityCalibrator(method="isotonic").fit(raw,y)
    out=cal.transform(raw)
    assert 0.0 <= brier_score(out,y) <= 1.0
    assert 0.0 <= expected_calibration_error(out,y,n_bins=5) <= 1.0
    assert np.all(np.diff(out) >= -1e-12)

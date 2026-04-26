import numpy as np
from advanced_regime_engine import NHHMM_Engine

def test_nhhmm_saturation_bounds():
    m = NHHMM_Engine(n_states=3, n_features=3)
    m.beta[:] = 5.0
    p = m._compute_transition_matrix(np.array([10.0,10.0,10.0]))
    assert np.all(p >= 0.001)
    assert np.all(p <= 0.999)

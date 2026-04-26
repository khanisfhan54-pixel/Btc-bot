import numpy as np
from advanced_regime_engine import AdvancedRegimeEngine

def test_regime_engine_integration():
    e=AdvancedRegimeEngine()
    out=e.update({"return":0.001,"features":np.array([0.1,1000.0,950.0,50.0]),"price":50000.0,"orderbook":{},"open_interest":0.0,"funding_rate":0.0})
    assert out["regime_label"] != "UNKNOWN"
    assert "signal_valid" in out

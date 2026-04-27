import os

from advanced_regime_engine import AdvancedRegimeEngine


def test_uncalibrated_weights_fail_closed(tmp_path):
    os.environ["REGIME_WEIGHT_PATH"] = str(tmp_path / "missing_weights.npz")
    eng = AdvancedRegimeEngine(enable_background_workers=False)
    out = eng.update({"return": 0.001, "features": [0.1, 1000.0, 900.0], "price": 50000.0, "timestamp": 1_700_000_000.0})
    assert out["signal_valid"] is False
    assert out["regime_label"] == "UNCALIBRATED"
    assert out["execution_mode"] == "halt"

import os
import subprocess
import sys

import numpy as np

from advanced_regime_engine import AdvancedRegimeEngine
from model_weights import ModelWeightManager


def test_real_btc_calibration_pipeline_e2e(tmp_path, monkeypatch):
    weights_path = tmp_path / "advanced_regime_weights.npz"
    provenance_path = tmp_path / "calibration_provenance.json"

    env = os.environ.copy()
    env.update(
        {
            "REGIME_DATA_SOURCE": "real",
            "REGIME_N_BARS": "320",
            "REGIME_OUTPUT_DIR": str(tmp_path),
            "REGIME_OUTPUT_PATH": str(weights_path),
            "REGIME_PROVENANCE_PATH": str(provenance_path),
            "REGIME_AGGTRADES_PATH": "data/aggTrades.csv",
            "REGIME_BOOKDEPTH_PATH": "data/bookDepth.csv",
        }
    )
    subprocess.run([sys.executable, "calibrate_regime.py"], check=True, env=env)

    weights = ModelWeightManager.load_weights("advanced_regime", str(weights_path))
    weights_loaded = bool(weights) and weights_path.exists()
    assert weights_loaded is True

    monkeypatch.setenv("REGIME_WEIGHT_PATH", str(weights_path))
    engine = AdvancedRegimeEngine(enable_background_workers=False)
    assert engine._calibration_status == "calibrated"

    price = 68900.0
    payload = {
        "price": price,
        "return": 0.0001,
        "features": np.array([0.0001, 0.0, 0.0], dtype=float),
        "timestamp": 1774569604.0,
        "require_calibration": True,
    }
    out = engine.update(payload)

    assert out.get("engine_status") != "DEGRADED"
    assert out.get("signal_valid") is True

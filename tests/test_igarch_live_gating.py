from advanced_regime_engine import AdvancedRegimeEngine


def test_igarch_non_stationary_blocks_output():
    eng = AdvancedRegimeEngine(allow_igarch=True, enable_background_workers=False)
    eng.garch.alpha[:] = 0.7
    eng.garch.beta_garch[:] = 0.4
    out = eng.update({"return": 0.001, "features": [0.1, 1000.0, 900.0], "price": 50000.0, "timestamp": 1_700_000_000.0})
    assert out["execution_mode"] == "halt_igarch"
    assert out["signal_valid"] is False

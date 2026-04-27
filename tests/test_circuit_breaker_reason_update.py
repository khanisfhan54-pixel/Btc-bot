from advanced_regime_engine import AdvancedRegimeEngine


def test_circuit_breaker_reason_updates_when_active():
    eng = AdvancedRegimeEngine(enable_background_workers=False)
    eng._trigger_circuit_breaker("drawdown_limit")
    eng._trigger_circuit_breaker("vol_spike")
    assert eng._circuit_breaker_reason == "vol_spike"
    assert len(eng._cb_trigger_history) >= 2
    assert eng._cb_trigger_history[-1][1] == "vol_spike"

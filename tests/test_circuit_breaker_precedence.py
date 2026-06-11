from advanced_regime_engine import AdvancedRegimeEngine


def test_same_tick_triggers_first_trigger_wins():
    eng = AdvancedRegimeEngine(enable_background_workers=False, load_model_weights_on_init=False)
    eng._tick_id = 10
    eng._trigger_circuit_breaker("MAX_DRAWDOWN")
    eng._trigger_circuit_breaker("VOL_SHOCK")
    assert eng._circuit_breaker_reason == "MAX_DRAWDOWN"
    assert eng._circuit_breaker_trigger_tick == 10
    assert [entry[1] for entry in eng._cb_trigger_history][-2:] == ["MAX_DRAWDOWN", "VOL_SHOCK"]


def test_cross_tick_triggers_do_not_overwrite_active_reason():
    eng = AdvancedRegimeEngine(enable_background_workers=False, load_model_weights_on_init=False)
    eng._tick_id = 10
    eng._trigger_circuit_breaker("VOL_SHOCK")
    eng._tick_id = 11
    eng._trigger_circuit_breaker("MAX_DRAWDOWN")
    assert eng._circuit_breaker_reason == "VOL_SHOCK"
    assert eng._circuit_breaker_trigger_tick == 10
    assert [entry[1] for entry in eng._cb_trigger_history][-2:] == ["VOL_SHOCK", "MAX_DRAWDOWN"]


def test_drawdown_then_shock_and_shock_then_drawdown_ordering():
    first = AdvancedRegimeEngine(enable_background_workers=False, load_model_weights_on_init=False)
    first._trigger_circuit_breaker("MAX_DRAWDOWN")
    first._trigger_circuit_breaker("VOL_SHOCK")
    assert first._circuit_breaker_reason == "MAX_DRAWDOWN"

    second = AdvancedRegimeEngine(enable_background_workers=False, load_model_weights_on_init=False)
    second._trigger_circuit_breaker("VOL_SHOCK")
    second._trigger_circuit_breaker("MAX_DRAWDOWN")
    assert second._circuit_breaker_reason == "VOL_SHOCK"


def test_confidence_collapse_after_breaker_is_logged_not_overwritten():
    eng = AdvancedRegimeEngine(enable_background_workers=False, load_model_weights_on_init=False)
    eng._tick_id = 3
    eng._trigger_circuit_breaker("MAX_DRAWDOWN")
    eng._trigger_circuit_breaker("CONFIDENCE_COLLAPSE")
    assert eng._circuit_breaker_reason == "MAX_DRAWDOWN"
    assert eng._circuit_breaker_trigger_tick == 3
    assert eng._cb_trigger_history[-1][1] == "CONFIDENCE_COLLAPSE"


def test_cooldown_recovery_allows_new_first_trigger_after_heal():
    eng = AdvancedRegimeEngine(enable_background_workers=False, load_model_weights_on_init=False)
    eng._tick_id = 1
    eng._trigger_circuit_breaker("VOL_SHOCK")
    eng._self_heal()
    eng._tick_id = 2
    eng._trigger_circuit_breaker("MAX_DRAWDOWN")
    assert eng._circuit_breaker_active is True
    assert eng._circuit_breaker_reason == "MAX_DRAWDOWN"
    assert eng._circuit_breaker_trigger_tick == 2

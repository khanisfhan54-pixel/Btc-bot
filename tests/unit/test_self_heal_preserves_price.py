from advanced_regime_engine import AdvancedRegimeEngine

def test_self_heal_preserves_price():
    e=AdvancedRegimeEngine()
    e._last_price=50000.0
    e._self_heal()
    assert e._last_price==50000.0
    e._self_heal(reset_price_anchor=True)
    assert e._last_price is None

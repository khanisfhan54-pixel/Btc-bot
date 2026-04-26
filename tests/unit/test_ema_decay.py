from advanced_regime_engine import AdvancedRegimeEngine

def test_ema_decay_long_gap_lower():
    e = AdvancedRegimeEngine()
    d600 = e._ema_decay(600)
    d10 = e._ema_decay(10)
    assert d600 < d10

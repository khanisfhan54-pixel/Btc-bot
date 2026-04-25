import os
import sys
import threading

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from advanced_regime_engine import AdvancedRegimeEngine


def _md(ts: float, price: float, ret: float = 0.001):
    return {
        "timestamp": float(ts),
        "price": float(price),
        "return": float(ret),
        "features": np.array([0.1, 0.2, 0.3], dtype=float),
    }


def test_pnl_staleness_on_restart():
    eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=7)
    state = eng.serialize_state()
    state["last_price"] = 100.0
    state["last_price_timestamp"] = 1000.0
    state["last_signed_position_size"] = 1.0
    state["equity"] = 1.0
    state["equity_peak"] = 1.0

    eng.load_state(state)
    out = eng.update(_md(ts=1401.0, price=1000.0, ret=0.001))

    assert out["regime_label"] != "HALTED"
    assert eng._circuit_breaker_active is False
    assert np.isclose(eng._equity, 1.0)


def test_garch_state_reversion():
    eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=11)
    garch_var_before = np.copy(eng.garch_var)
    garch_prob_before = np.copy(eng.garch_prob)
    smooth_before = np.copy(eng._smoothed_garch_prob)

    out = eng.update(_md(ts=1.0, price=100.0, ret=2.0))

    assert out["regime_label"] == "HALTED"
    assert eng._circuit_breaker_active is True
    assert np.allclose(eng.garch_var, garch_var_before)
    assert np.allclose(eng.garch_prob, garch_prob_before)
    assert np.allclose(eng._smoothed_garch_prob, smooth_before)


def test_concurrent_self_heal():
    eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=17)

    def worker() -> None:
        for _ in range(200):
            eng._self_heal("E200", {"source": "concurrency_test"})

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert np.all(np.isfinite(eng._smoothed_garch_prob))
    assert np.isclose(float(np.sum(eng._smoothed_garch_prob)), 1.0)
    assert eng._healing_count >= 1600


def test_equity_persistence():
    eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=19)
    eng._equity = 0.82
    eng._equity_peak = 1.0
    eng._drawdown = 0.18
    eng._cumulative_drawdown = 0.18
    eng._circuit_breaker_active = True

    eng._self_heal()

    assert eng._circuit_breaker_active is False
    assert eng._cumulative_drawdown >= 0.18
    assert eng._cumulative_drawdown >= eng._drawdown

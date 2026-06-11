import concurrent.futures
import threading

import numpy as np

from advanced_regime_engine import AdvancedRegimeEngine


def _md(i=0):
    return {"return": 0.0001, "features": [0.1, 0.2, 0.3], "price": 100.0 + i * 0.001, "timestamp": float(i + 1)}


def test_100_concurrent_heal_calls_no_runtimeerror_or_deadlock():
    eng = AdvancedRegimeEngine(enable_background_workers=False, load_model_weights_on_init=False)
    errors = []

    def heal(i):
        try:
            return eng._self_heal("E200", {"i": i})
        except BaseException as exc:  # test records every failure, including RuntimeError
            errors.append(exc)
            raise

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(heal, i) for i in range(100)]
        results = [f.result(timeout=5.0) for f in futures]

    assert errors == []
    assert set(results) == {"RESET_NUMERICAL"}
    assert eng._healing_count == 100
    assert np.all(np.isfinite(eng.garch_prob))
    assert np.isclose(np.sum(eng.garch_prob), 1.0)


def test_heal_and_update_race_no_deadlock_or_runtimeerror():
    eng = AdvancedRegimeEngine(enable_background_workers=False, load_model_weights_on_init=False)
    barrier = threading.Barrier(12)
    errors = []

    def heal_worker(i):
        barrier.wait(timeout=5.0)
        for j in range(25):
            eng._self_heal("E120", {"worker": i, "j": j})

    def update_worker(i):
        barrier.wait(timeout=5.0)
        for j in range(25):
            eng.update(_md(i * 100 + j))

    threads = [threading.Thread(target=heal_worker, args=(i,)) for i in range(6)]
    threads += [threading.Thread(target=update_worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
        if t.is_alive():
            errors.append("deadlock")
    assert errors == []
    assert eng._healing_count >= 150


def test_repeated_heal_cycles_and_breaker_recovery_are_idempotent():
    eng = AdvancedRegimeEngine(enable_background_workers=False, load_model_weights_on_init=False)
    for _ in range(20):
        eng._tick_id = 7
        eng._trigger_circuit_breaker("MAX_DRAWDOWN")
        assert eng._circuit_breaker_active is True
        action = eng._self_heal()
        assert action == "RESET_FULL"
        assert eng._circuit_breaker_active is False
        assert eng._circuit_breaker_reason is None
        assert eng._circuit_breaker_trigger_tick == -1
        assert np.allclose(eng.nhhmm_prior, np.ones(3) / 3.0)
        assert np.allclose(eng.garch_prob, np.ones(2) / 2.0)


def test_1000_plus_healing_operations_stress_loop():
    eng = AdvancedRegimeEngine(enable_background_workers=False, load_model_weights_on_init=False)
    for i in range(1100):
        code = "E200" if i % 2 == 0 else "E120"
        action = eng._self_heal(code, {"i": i})
        assert action in {"RESET_NUMERICAL", "RESET_INPUT"}
    assert eng._healing_count == 1100
    assert np.isfinite(eng._last_valid_vol)

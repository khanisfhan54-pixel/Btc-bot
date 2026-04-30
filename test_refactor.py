import weakref
import queue
import numpy as np

from advanced_regime_engine import AdvancedRegimeEngine, RegimeMarkovSmoother


def _valid_market_data(price: float, ret: float, ts: float) -> dict:
    return {
        "price": float(price),
        "return": float(ret),
        "timestamp": float(ts),
        "features": np.array([ret, 0.0, abs(ret)], dtype=float),
    }


def test_pnl_baseline_anchoring():
    eng = AdvancedRegimeEngine(n_features=3, target_vol=0.02, seed=7)
    eng._last_price = 100.0
    eng._last_price_timestamp = 1.0
    eng.last_signed_position_size = 1.0

    mismatch_tick = _valid_market_data(price=110.0, ret=0.0, ts=2.0)
    out1 = eng.update(mismatch_tick)
    assert out1["risk_metrics"]["feed_status"]["primary"] == "OK"
    assert "PNL_TIMESTAMP_POLICY_BLOCKED" in out1["risk_metrics"]["feed_status"]["flags"]
    assert eng._last_price == 110.0
    assert eng._last_price_timestamp == 2.0

    eng.last_signed_position_size = 1.0
    ret2 = (111.0 - 110.0) / 110.0
    ok_tick = _valid_market_data(price=111.0, ret=ret2, ts=3.0)
    eng.update(ok_tick)

    expected_equity = 1.0 + ret2
    assert np.isclose(eng._equity, expected_equity, atol=1e-10), (
        eng._equity,
        expected_equity,
    )


def test_shutdown_drain(monkeypatch):
    q = queue.Queue(maxsize=10000)
    for i in range(64):
        q.put_nowait(f"msg-{i}")

    stop_event = __import__("threading").Event()

    class _Dummy:
        pass

    dummy = _Dummy()
    ref = weakref.ref(dummy)
    del dummy

    calls = {"emit": 0}

    def _should_not_be_called(*args, **kwargs):
        calls["emit"] += 1
        raise AssertionError("emit thread path must not run when engine is dead")

    monkeypatch.setattr(AdvancedRegimeEngine, "_emit_warning_with_timeout", staticmethod(_should_not_be_called))

    AdvancedRegimeEngine._warning_emitter_loop(ref, stop_event, q)

    assert q.empty()
    assert calls["emit"] == 0


def test_smoother_bear_recovery():
    smoother = RegimeMarkovSmoother()
    evidence = smoother._scores_to_evidence(
        {
            "bull": 0.8,
            "bear": 0.2,
            "trend_score": 1.0,
            "bear_score": 0.0,
            "range_score": 0.0,
            "toxic_score": 0.0,
        }
    )
    expected_bear = 0.25 * (0.2 / (0.8 + 0.2))
    assert np.isclose(evidence[2], expected_bear, atol=1e-8)
    assert evidence[2] < 0.1


def test_mismatch_tolerance():
    eng = AdvancedRegimeEngine(n_features=3, target_vol=0.02, seed=9)
    eng._last_price = 100.0
    eng._last_price_timestamp = 1.0
    eng.last_signed_position_size = 1.0

    frac_ret = (100.1 - 100.0) / 100.0
    noisy_return = frac_ret + 0.0008  # 0.08% exchange-level noise
    out = eng.update(_valid_market_data(price=100.1, ret=noisy_return, ts=2.0))

    assert out["risk_metrics"]["feed_status"] != "PRICE_RETURN_MISMATCH"
    assert eng._circuit_breaker_active is False

import os
import sys
import threading
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import advanced_regime_engine as module
from advanced_regime_engine import AdvancedRegimeEngine, SparseJumpModel


def _md(ts=1.0, ret=0.001, price=100.0, features=None):
    return {
        "timestamp": float(ts),
        "return": ret,
        "price": price,
        "features": np.array(features if features is not None else [0.1, 0.2, 0.3], dtype=float),
    }


@pytest.fixture
def engine():
    eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=7)
    yield eng
    eng._shutdown_warning_worker()


def test_pnl_tracking_subpaths_and_breakers(engine):
    engine.last_signed_position_size = 1.0
    out1 = engine.update(_md(ts=1.0, ret=0.0, price=100.0))
    assert out1["signal_valid"] is True

    # malformed price parse must not crash or mutate last valid price
    old_price = engine._last_price
    out2 = engine.update(_md(ts=2.0, ret=0.001, price="bad-price"))
    assert out2["schema_version"] == module._OUTPUT_SCHEMA_VERSION
    assert engine._last_price == old_price
    assert engine._warning_counts.get("pnl_price_parse_failure", 0) >= 1

    # mismatch must anchor timestamp but avoid pnl mutation
    engine._equity = 1.0
    out3 = engine.update(_md(ts=3.0, ret=0.0, price=101.0))
    assert out3["risk_metrics"]["feed_status"] == "PRICE_RETURN_MISMATCH"
    assert engine._last_timestamp == pytest.approx(3.0)
    assert engine._equity == pytest.approx(1.0)

    # stale price path should skip pnl update and not trigger breaker
    engine._last_price = 100.0
    engine._last_price_timestamp = 0.0
    engine._equity = 1.0
    out4 = engine.update(_md(ts=engine._MAX_PRICE_STALENESS_SEC + 5.0, ret=0.001, price=80.0))
    assert out4["schema_version"] == module._OUTPUT_SCHEMA_VERSION
    assert engine._equity == pytest.approx(1.0)
    assert engine._circuit_breaker_active is False

    # breaker trigger path
    engine.reset_state()
    engine._VOL_SHOCK_MULTIPLIER = 100.0
    engine._last_price = 100.0
    engine._last_price_timestamp = 1.0
    engine.last_signed_position_size = 1.0
    out5 = engine.update(_md(ts=2.0, ret=-0.2, price=80.0))
    assert out5["regime_label"] == "HALTED"
    assert engine._circuit_breaker_reason in {"MAX_DRAWDOWN", "LOSS_STREAK", "EQUITY_FLOOR"}


def test_pnl_equity_update_failure_isolated(engine):
    engine._last_price = 100.0
    engine._last_price_timestamp = 1.0
    engine.last_signed_position_size = 1.0
    engine._equity = 1.0
    engine._equity_peak = "corrupt"  # force drawdown update failure

    out = engine.update(_md(ts=2.0, ret=0.01, price=101.0))

    assert out["schema_version"] == module._OUTPUT_SCHEMA_VERSION
    assert engine._equity == pytest.approx(1.0)  # no partial mutation
    assert engine._warning_counts.get("pnl_equity_update_failure", 0) >= 1


def test_single_tf_invalid_features_fallback_is_safe(engine):
    out_shape = engine.update(_md(ts=1.0, ret=0.001, features=[0.1, 0.2]))
    assert out_shape["signal_valid"] is False
    assert np.allclose(engine.nhhmm_prior, np.ones(3) / 3)

    out_nan = engine.update(_md(ts=2.0, ret=0.001, features=[0.1, np.nan, 0.3]))
    assert out_nan["signal_valid"] is False
    assert np.allclose(engine.nhhmm_prior, np.ones(3) / 3)
    assert engine._warning_counts.get("single_tf_nhhmm_failure", 0) >= 1


def test_warning_worker_is_bounded_and_shutdown_clean(engine):
    before = len([t for t in threading.enumerate() if t.name == "warning_log_emit"])
    for i in range(200):
        engine._warn_rate_limited(f"warn-{i}", "x", cooldown_s=0.0)
    time.sleep(0.05)
    after = len([t for t in threading.enumerate() if t.name == "warning_log_emit"])
    assert after == before == 0

    engine._shutdown_warning_worker()
    if engine._warning_worker is not None:
        assert engine._warning_worker.is_alive() is False


def test_warning_queue_saturation_safe(engine):
    engine._shutdown_warning_worker()
    engine._warning_queue = module.queue.Queue(maxsize=1)
    engine._warning_queue.put_nowait("full")
    for i in range(20):
        engine._warn_rate_limited(f"drop-{i}", "msg", cooldown_s=0.0)
    assert engine._warning_drop_count >= 1


def test_shock_detection_uses_post_update_baseline(engine, monkeypatch):
    engine._VOL_SHOCK_MULTIPLIER = 100.0
    engine.garch_var = np.array([0.25, 0.25], dtype=float)  # huge pre baseline
    engine._smoothed_garch_prob = np.array([0.5, 0.5], dtype=float)

    monkeypatch.setattr(engine.garch, "_garch_update", lambda _var, _y: np.array([1e-8, 1e-8], dtype=float))
    monkeypatch.setattr(engine.garch, "_update_regime_probs", lambda _p, _pred, _y: np.array([0.5, 0.5], dtype=float))

    out = engine.update(_md(ts=1.0, ret=0.01, price=100.0))
    assert out["risk_metrics"]["toxic_penalty_applied"] is True


def test_load_snapshot_atomic_on_rng_failure(engine):
    before_state = engine.serialize_state()
    before_rng = engine._rng.bit_generator.state

    bad_snapshot = {
        "engine_state": before_state,
        "_engine_rng_state": {"bad": "state"},
    }
    engine._equity = 0.5
    engine.load_snapshot(bad_snapshot)

    assert engine.serialize_state() == before_state
    assert engine._rng.bit_generator.state == before_rng


def test_load_snapshot_good_restore(engine):
    snap = {"engine_state": engine.serialize_state(), "_engine_rng_state": engine._rng.bit_generator.state}
    engine._equity = 0.7
    engine.load_snapshot(snap)
    assert engine._equity == pytest.approx(1.0)


def test_confidence_collapse_halt_before_garch_mutation(engine, monkeypatch):
    engine._confidence_collapse_streak = engine._CONFIDENCE_COLLAPSE_MIN_STREAK - 1
    before_var = engine.garch_var.copy()
    before_prob = engine.garch_prob.copy()
    before_smooth = engine._smoothed_garch_prob.copy()

    monkeypatch.setattr(
        module,
        "compute_hmm_regime",
        lambda *_a, **_k: {
            "regime": "RANGE",
            "bull": 0.34,
            "bear": 0.33,
            "crisis": 0.33,
            "trend_strength": 0.0,
            "risk_level": 0.2,
            "confidence": 0.1,
            "conviction": 0.01,
            "uncertainty": 0.99,
            "directional_margin": 0.0,
            "directional_label": "TREND",
            "edge_score": 0.2,
            "trend_score": 0.4,
            "range_score": 0.6,
            "toxic_score": 0.1,
        },
    )

    out = engine.update(_md(ts=1.0, ret=0.001, price=100.0))
    assert out["regime_label"] == "HALTED"
    assert engine._circuit_breaker_reason == "CONFIDENCE_COLLAPSE"
    assert np.allclose(engine.garch_var, before_var)
    assert np.allclose(engine.garch_prob, before_prob)
    assert np.allclose(engine._smoothed_garch_prob, before_smooth)


def test_sjm_fallback_flag_reset_after_load_weights():
    sjm = SparseJumpModel(n_states=3)
    sjm.online_predict(np.array([0.1, 0.2, 0.3]), 3, None, np.ones(3) / 3)
    assert sjm._default_params_initialized is True
    sjm.load_weights(np.zeros((3, 3), dtype=float), np.ones(3, dtype=float))
    assert sjm._default_params_initialized is False


def test_directional_label_validation_scope(engine):
    state = engine.serialize_state()
    state["prev_directional_label"] = "RANGE"
    engine.load_state(state)
    assert engine._prev_directional_label is None

    state["prev_directional_label"] = "TREND"
    engine.load_state(state)
    assert engine._prev_directional_label == "TREND"


def test_snapshot_version_uses_engine_constant(monkeypatch):
    captured = {}

    class DummyReplay:
        def record_event(self, *_a, **_k):
            return None

        def snapshot(self, payload):
            captured["payload"] = payload

    eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=10)
    try:
        eng._replay_engine = DummyReplay()
        eng._tick_id = 99
        eng.update(_md(ts=1.0, ret=0.001, price=100.0))
        assert captured["payload"]["schema_version"] == eng._STATE_VERSION
    finally:
        eng._shutdown_warning_worker()


def test_edge_sizing_single_modulation_path(engine, monkeypatch):
    engine._regime_smoother = None
    engine._EDGE_MIN_DIRECTIONAL_CONFIDENCE = 0.0
    engine._EDGE_MIN_ACTIVE = 0.0

    def _scores(edge):
        return {
            "regime": "TREND",
            "bull": 0.8,
            "bear": 0.1,
            "crisis": 0.1,
            "trend_strength": 0.6,
            "risk_level": 0.2,
            "confidence": 0.9,
            "conviction": 0.9,
            "uncertainty": 0.1,
            "directional_margin": 0.6,
            "directional_label": "TREND",
            "edge_score": edge,
            "trend_score": 0.9,
            "range_score": 0.1,
            "toxic_score": 0.0,
        }

    monkeypatch.setattr(module, "compute_hmm_regime", lambda *_a, **_k: _scores(0.4))
    low = engine.update(_md(ts=1.0, ret=0.001, price=100.0))["position_size"]

    monkeypatch.setattr(module, "compute_hmm_regime", lambda *_a, **_k: _scores(0.95))
    high = engine.update(_md(ts=2.0, ret=0.001, price=100.1))["position_size"]

    assert 0.0 <= low <= module._POSITION_SIZE_CAP
    assert 0.0 <= high <= module._POSITION_SIZE_CAP
    assert high > low

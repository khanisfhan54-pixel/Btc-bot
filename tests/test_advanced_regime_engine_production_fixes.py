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
    eng._shutdown_snapshot_worker()


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
    assert out3["risk_metrics"]["feed_status"]["primary"] == "PRICE_RETURN_MISMATCH"
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
    assert engine._circuit_breaker_reason in {"MAX_DRAWDOWN", "LOSS_STREAK", "EQUITY_FLOOR", "VOL_SHOCK"}


def test_pnl_equity_update_failure_isolated(engine):
    engine._last_price = 100.0
    engine._last_price_timestamp = 1.0
    engine._pnl_mode = "TIMESTAMP"
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
    before = len([t for t in threading.enumerate() if t.name == "warning_emit"])
    for i in range(200):
        engine._warn_rate_limited(f"warn-{i}", "x", cooldown_s=0.0)
    time.sleep(0.05)
    after = len([t for t in threading.enumerate() if t.name == "warning_emit"])
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

    assert engine._equity == pytest.approx(0.5)
    assert engine._rng.bit_generator.state == before_rng
    assert engine._determinism_status == "RNG_RESTORE_FAILED"


def test_load_snapshot_good_restore(engine):
    snap = {"engine_state": engine.serialize_state(), "_engine_rng_state": engine._rng.bit_generator.state}
    engine._equity = 0.7
    engine.load_snapshot(snap)
    assert engine._equity == pytest.approx(1.0)


def test_confidence_collapse_halt_before_garch_mutation(engine, monkeypatch):
    engine._confidence_collapse_streak = engine._CONFIDENCE_COLLAPSE_MIN_STREAK - 1
    engine._posterior_update_count = engine._CONF_COLLAPSE_WARMUP_UPDATES + 1
    engine._first_posterior_ts = -100.0
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
    called = threading.Event()

    class DummyReplay:
        def record_event(self, *_a, **_k):
            return None

        def snapshot(self, payload):
            captured["payload"] = payload
            called.set()


def test_garch_persistence_warning(engine, monkeypatch):
    calls = []

    def _warn_spy(key, message, cooldown_s=30.0):
        calls.append((key, message, cooldown_s))
        return True

    monkeypatch.setattr(engine, "_warn_rate_limited", _warn_spy)

    engine.garch.alpha = np.array([0.49, 0.49], dtype=float)
    engine.garch.beta_garch = np.array([0.49, 0.49], dtype=float)
    engine.update(_md(ts=1.0, ret=0.001, price=100.0))
    assert not any(k == "garch_persistence_high" for k, _, _ in calls)

    engine.garch.alpha = np.array([0.6, 0.6], dtype=float)
    engine.garch.beta_garch = np.array([0.4, 0.4], dtype=float)
    engine.update(_md(ts=2.0, ret=0.001, price=100.1))
    assert any(k == "garch_persistence_high" for k, _, _ in calls)

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


def test_timestamp_regression_preserves_anchor(engine):
    engine.update(_md(ts=10.0, ret=0.001, price=100.0))
    assert engine._last_timestamp == pytest.approx(10.0)
    dt_before = engine._last_valid_dt

    engine.update(_md(ts=9.0, ret=0.001, price=100.1))
    assert engine._last_timestamp == pytest.approx(10.0)
    assert engine._last_valid_dt == pytest.approx(dt_before)

    engine.update(_md(ts=11.0, ret=0.001, price=100.2))
    assert engine._last_valid_dt == pytest.approx(1.0)


def test_alpha_override_cannot_bypass_directional_edge_gate(engine, monkeypatch):
    monkeypatch.setattr(
        module,
        "compute_hmm_regime",
        lambda *_a, **_k: {
            "regime": "RANGE",
            "bull": 0.5,
            "bear": 0.4,
            "crisis": 0.1,
            "trend_strength": 0.5,
            "risk_level": 0.2,
            "confidence": 0.8,
            "conviction": 0.9,
            "uncertainty": 0.2,
            "directional_margin": 0.4,
            "directional_label": "TREND",
            "edge_score": 0.2,
            "trend_score": 0.8,
            "range_score": 0.2,
            "toxic_score": 0.0,
        },
    )
    monkeypatch.setattr(
        engine.nhhmm,
        "forward_pass_step",
        lambda *_a, **_k: (np.array([0.85, 0.05, 0.10], dtype=float), None),
    )
    monkeypatch.setattr(
        engine.sjm,
        "online_predict",
        lambda **_k: (0, np.array([0.4, 0.4, 0.2], dtype=float)),
    )

    out = engine.update(_md(ts=1.0, ret=0.001, price=100.0))
    assert out["execution_side"] == "flat"
    assert out["position_size"] == pytest.approx(0.0)


def test_shock_memory_decays_after_moderate_move(engine):
    engine.update(_md(ts=1.0, ret=0.01, price=100.0))
    initial = engine._shock_memory
    assert initial >= 0.01

    for i in range(2, 12):
        engine.update(_md(ts=float(i), ret=0.0, price=100.0))
    assert engine._shock_memory < 0.002


def test_range_from_flat_has_deterministic_nonzero_signed_size(engine, monkeypatch):
    engine.last_signed_position_size = 0.0
    engine._last_edge_score = 1.0
    monkeypatch.setattr(
        module,
        "compute_hmm_regime",
        lambda *_a, **_k: {
            "regime": "RANGE",
            "bull": 0.45,
            "bear": 0.45,
            "crisis": 0.10,
            "trend_strength": 0.0,
            "risk_level": 0.2,
            "confidence": 0.7,
            "conviction": 0.5,
            "uncertainty": 0.3,
            "directional_margin": 0.3,
            "directional_label": "TREND",
            "edge_score": 0.95,
            "trend_score": 0.6,
            "range_score": 0.7,
            "toxic_score": 0.0,
        },
    )
    monkeypatch.setattr(
        engine.nhhmm,
        "forward_pass_step",
        lambda *_a, **_k: (np.array([0.34, 0.33, 0.33], dtype=float), None),
    )
    monkeypatch.setattr(
        engine.sjm,
        "online_predict",
        lambda **_k: (0, np.array([0.33, 0.33, 0.34], dtype=float)),
    )
    out = engine.update(_md(ts=1.0, ret=0.0, price=100.0))
    assert out["execution_side"] == "range_mean_revert"
    assert out["signed_position_size"] != pytest.approx(0.0)


def test_snapshot_hashing_moves_off_hot_path(monkeypatch):
    class DummyReplay:
        def __init__(self):
            self.called = threading.Event()
            self.payload = None

        def record_event(self, *_a, **_k):
            return None

        def snapshot(self, payload):
            self.payload = payload
            self.called.set()

    eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=9)
    replay = DummyReplay()
    eng._replay_engine = replay
    eng._tick_id = 99

    monkeypatch.setattr(eng, "_state_hash", lambda *_a, **_k: (time.sleep(0.2) or "hash"))
    start = time.perf_counter()
    try:
        eng.update(_md(ts=1.0, ret=0.001, price=100.0))
        elapsed = time.perf_counter() - start
        assert elapsed < 0.15
        assert replay.called.wait(timeout=1.0)
        assert replay.payload["state_hash"] == "hash"
    finally:
        eng._shutdown_warning_worker()
        eng._shutdown_snapshot_worker()


def test_snapshot_tick_does_not_call_serialize_state(monkeypatch):
    class DummyReplay:
        def __init__(self):
            self.called = threading.Event()

        def record_event(self, *_a, **_k):
            return None

        def snapshot(self, payload):
            self.payload = payload
            self.called.set()

    eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=11)
    try:
        replay = DummyReplay()
        eng._replay_engine = replay
        eng._tick_id = 99

        def _fail_serialize():
            raise AssertionError("serialize_state should not be used on update snapshot path")

        monkeypatch.setattr(eng, "serialize_state", _fail_serialize)
        out = eng.update(_md(ts=1.0, ret=0.001, price=100.0))
        assert out["schema_version"] == module._OUTPUT_SCHEMA_VERSION
        assert replay.called.wait(timeout=1.0)
    finally:
        eng._shutdown_warning_worker()
        eng._shutdown_snapshot_worker()


def test_load_snapshot_staging_disables_background_workers(monkeypatch):
    init_calls = []
    original_init = module.AdvancedRegimeEngine.__init__

    def _spy_init(self, *args, **kwargs):
        init_calls.append(bool(kwargs.get("enable_background_workers", True)))
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(module.AdvancedRegimeEngine, "__init__", _spy_init)

    eng = module.AdvancedRegimeEngine(n_states=3, n_features=3, seed=21)
    try:
        snapshot = {"engine_state": eng.serialize_state(), "_engine_rng_state": eng._rng.bit_generator.state}
        for _ in range(3):
            eng.load_snapshot(snapshot)
        assert init_calls.count(False) >= 3
    finally:
        eng._shutdown_warning_worker()
        eng._shutdown_snapshot_worker()

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import advanced_regime_engine as module
from advanced_regime_engine import AdvancedRegimeEngine


def _md(*, ts=None, ret=0.001, price=100.0, features=None, mtf=None):
    payload = {
        "return": ret,
        "price": price,
        "features": np.array(features if features is not None else [0.1, 0.2, 0.3], dtype=float),
    }
    if ts is not None:
        payload["timestamp"] = float(ts)
    if mtf is not None:
        payload["mtf"] = mtf
    return payload


@pytest.fixture
def engine():
    eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=7)
    yield eng
    eng._shutdown_warning_worker()
    eng._shutdown_snapshot_worker()


def test_timestamped_pnl_progression_and_anchor_atomic(engine):
    engine.last_signed_position_size = 1.0
    engine.update(_md(ts=1.0, price=100.0, ret=0.0))
    assert engine._equity == pytest.approx(1.0)
    assert engine._last_price == pytest.approx(100.0)
    assert engine._last_price_timestamp == pytest.approx(1.0)
    assert engine._last_price_tick_id is not None

    engine.last_signed_position_size = 1.0
    engine.update(_md(ts=2.0, price=101.0, ret=0.01))
    assert engine._equity == pytest.approx(1.01)


def test_timestamp_less_pnl_policy_enabled_updates_equity():
    eng = AdvancedRegimeEngine(
        n_states=3,
        n_features=3,
        seed=7,
        allow_timestamp_free_pnl=True,
        max_price_staleness_ticks=3,
    )
    try:
        eng.last_signed_position_size = 1.0
        out1 = eng.update(_md(ts=None, price=100.0, ret=0.0))
        eng.last_signed_position_size = 1.0
        out2 = eng.update(_md(ts=None, price=101.0, ret=0.01))
        assert out1["schema_version"] == module._OUTPUT_SCHEMA_VERSION
        assert out2["risk_metrics"]["feed_status"]["primary"].startswith("OK")
        assert eng._equity == pytest.approx(1.01)
    finally:
        eng._shutdown_warning_worker()
        eng._shutdown_snapshot_worker()


def test_timestamp_less_pnl_policy_disabled_marks_degraded():
    eng = AdvancedRegimeEngine(
        n_states=3,
        n_features=3,
        seed=7,
        allow_timestamp_free_pnl=False,
    )
    try:
        eng.last_signed_position_size = 1.0
        eng.update(_md(ts=None, price=100.0, ret=0.0))
        eng.last_signed_position_size = 1.0
        out = eng.update(_md(ts=None, price=101.0, ret=0.01))
        assert eng._equity == pytest.approx(1.01)
        assert out["risk_metrics"]["feed_status"]["primary"] == "OK"
        assert module._validate_output_schema(out)
    finally:
        eng._shutdown_warning_worker()
        eng._shutdown_snapshot_worker()


def test_mixed_timestamp_feed_falls_back_to_tick_policy():
    eng = AdvancedRegimeEngine(
        n_states=3,
        n_features=3,
        seed=7,
        allow_timestamp_free_pnl=True,
        max_price_staleness_ticks=3,
    )
    try:
        eng.last_signed_position_size = 1.0
        eng.update(_md(ts=None, price=100.0, ret=0.0))
        eng.last_signed_position_size = 1.0
        eng.update(_md(ts=2.0, price=101.0, ret=0.01))
        assert eng._equity == pytest.approx(1.01)
    finally:
        eng._shutdown_warning_worker()
        eng._shutdown_snapshot_worker()


def test_startup_shock_threshold_is_tight(engine):
    out = engine.update(_md(ts=1.0, price=100.0, ret=0.06))
    assert out["regime_label"] == "HALTED"
    assert engine._circuit_breaker_reason == "VOL_SHOCK"


def test_shock_warmup_transition_is_smooth(engine):
    engine._valid_return_count = 1
    t0, _ = engine._shock_threshold(baseline_vol=0.01, current_ts=1.0)
    engine._valid_return_count = engine._shock_warmup_ticks // 2
    t1, _ = engine._shock_threshold(baseline_vol=0.01, current_ts=30.0)
    engine._valid_return_count = engine._shock_warmup_ticks * 4
    t2, _ = engine._shock_threshold(baseline_vol=0.01, current_ts=600.0)
    assert t0 < t1 < t2


def test_time_aware_ema_and_range_are_rate_consistent(monkeypatch):
    def always_range(*_a, **_k):
        return {
            "regime": "RANGE",
            "bull": 0.34,
            "bear": 0.33,
            "crisis": 0.33,
            "trend_strength": 0.0,
            "risk_level": 0.2,
            "confidence": 0.8,
            "conviction": 0.7,
            "uncertainty": 0.2,
            "directional_margin": 0.0,
            "directional_label": "TREND",
            "edge_score": 0.4,
            "trend_score": 0.2,
            "range_score": 0.8,
            "toxic_score": 0.0,
        }

    monkeypatch.setattr(module, "compute_hmm_regime", always_range)
    fast = AdvancedRegimeEngine(n_states=3, n_features=3, seed=1)
    slow = AdvancedRegimeEngine(n_states=3, n_features=3, seed=1)
    try:
        fast.update(_md(ts=0.0, ret=0.001, price=None))
        slow.update(_md(ts=0.0, ret=0.001, price=None))
        price = 100.0
        for i in range(1, 11):
            price *= 1.001
            fast.update(_md(ts=float(i), ret=0.001, price=None))
        price = 100.0
        for i in range(1, 3):
            price *= (1.001 ** 5)
            slow.update(_md(ts=float(i * 5), ret=0.001, price=None))

        assert fast._return_ema > 0.0
        assert slow._return_ema > 0.0
        assert fast._return_ema > slow._return_ema
        assert fast.range_ticks == pytest.approx(slow.range_ticks, rel=0.05)
    finally:
        fast._shutdown_warning_worker()
        fast._shutdown_snapshot_worker()
        slow._shutdown_warning_worker()
        slow._shutdown_snapshot_worker()


def test_macro_only_fallback_bypasses_shock_memory_modulation(monkeypatch, engine):
    captured = {}

    def fake_forward(*_a, **_k):
        return np.array([0.7, 0.2, 0.1], dtype=float), None

    def capture_probs(probs, **_kwargs):
        captured["probs"] = np.asarray(probs, dtype=float).copy()
        return {
            "regime": "TREND",
            "bull": float(probs[0]),
            "bear": float(probs[1]),
            "crisis": float(probs[2]),
            "trend_strength": 0.4,
            "risk_level": 0.2,
            "confidence": 0.9,
            "conviction": 0.8,
            "uncertainty": 0.1,
            "directional_margin": 0.6,
            "directional_label": "TREND",
            "edge_score": 0.7,
            "trend_score": 0.8,
            "range_score": 0.2,
            "toxic_score": 0.0,
        }

    monkeypatch.setattr(engine.nhhmm, "forward_pass_step", fake_forward)
    monkeypatch.setattr(module, "compute_hmm_regime", capture_probs)
    engine.mtf_weights = {"5m": 1.0}

    engine._shock_memory = 0.5
    out = engine.update(
        _md(
            ts=10.0,
            ret=0.01,
            mtf={
                "base": {"return": 0.01, "features": [0.1, 0.2]},  # invalid dims -> macro-only
                "5m": {"return": 0.01, "features": [0.1, 0.2, 0.3]},
            },
        )
    )
    assert np.allclose(captured["probs"], np.array([0.7, 0.2, 0.1]))
    assert engine._shock_memory == pytest.approx(0.5)
    assert "MACRO_ONLY_FALLBACK" in out["risk_metrics"]["feed_status"]["flags"]


def test_rng_restore_is_atomic_on_invalid_payload(engine):
    baseline = engine.serialize_state()
    baseline_rng = dict(engine._rng.bit_generator.state)

    corrupt = dict(baseline)
    corrupt["engine_rng_state"] = {"bad": "payload"}
    corrupt["equity"] = 0.4
    engine.load_state(corrupt)

    assert engine._equity == pytest.approx(baseline["equity"])
    assert engine._last_price == baseline["last_price"]
    assert dict(engine._rng.bit_generator.state) == baseline_rng
    assert engine._determinism_status == "RNG_RESTORE_FAILED"


def test_successful_load_state_normalizes_stale_determinism_flag(engine):
    state = engine.serialize_state()
    state["determinism_status"] = "RNG_RESTORE_FAILED"
    state["engine_rng_state"] = dict(engine._rng.bit_generator.state)
    engine.load_state(state)
    assert engine._determinism_status == "OK_WITH_HISTORY"


def test_snapshot_roundtrip_preserves_rng_determinism(engine):
    engine.update(_md(ts=1.0, ret=0.001, price=100.0))
    snapshot = {"engine_state": engine.serialize_state(), "_engine_rng_state": dict(engine._rng.bit_generator.state)}

    rng_before = dict(engine._rng.bit_generator.state)
    next_before = int(engine._rng.integers(0, 1_000_000))

    engine._rng.bit_generator.state = rng_before
    engine.load_snapshot(snapshot)
    next_after = int(engine._rng.integers(0, 1_000_000))

    assert next_after == next_before
    assert engine._determinism_status in {"OK", "OK_WITH_HISTORY"}


def test_mtf_missing_base_features_fails_safe(engine):
    out = engine.update(
        {
            "timestamp": 10.0,
            "price": 100.0,
            "return": 0.001,
            "mtf": {"base": {"return": 0.001}},
        }
    )
    assert out["regime_label"] == "UNKNOWN"
    assert out["signal_valid"] is False
    assert out["risk_metrics"]["feed_status"]["primary"] == "MTF_BASE_FEATURES_MISSING"
    assert out["risk_metrics"]["engine_status"] == "OK"


def test_confidence_collapse_suppressed_during_warmup_and_triggers_after(monkeypatch, engine):
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

    # Warm-up suppression.
    for i in range(1, 4):
        out = engine.update(_md(ts=float(i), ret=0.001, price=100.0))
        assert out["regime_label"] != "HALTED"
    assert engine._circuit_breaker_active is False

    # Mature evidence => breaker should fire on true collapse.
    engine._posterior_update_count = engine._CONF_COLLAPSE_WARMUP_UPDATES + 1
    engine._first_posterior_ts = 0.0
    for i in range(200, 210):
        out = engine.update(_md(ts=float(i), ret=0.001, price=100.0))
        if out["regime_label"] == "HALTED":
            break
    assert out["regime_label"] == "HALTED"
    assert engine._circuit_breaker_reason == "CONFIDENCE_COLLAPSE"


def test_determinism_transitions_include_history(engine):
    baseline = engine.serialize_state()
    bad = dict(baseline)
    bad["engine_rng_state"] = {"corrupt": "state"}
    engine.load_state(bad)
    assert engine._determinism_status == "RNG_RESTORE_FAILED"

    good = dict(baseline)
    good["engine_rng_state"] = dict(engine._rng.bit_generator.state)
    engine.load_state(good)
    assert engine._determinism_status == "OK_WITH_HISTORY"


def test_feed_status_and_engine_status_are_separated(engine):
    state = engine.serialize_state()
    state["engine_rng_state"] = {"broken": "rng"}
    engine.load_state(state)
    out = engine.update(_md(ts=1.0, ret=0.001, price=100.0))
    assert "RNG" not in out["risk_metrics"]["feed_status"]["primary"]
    assert out["risk_metrics"]["engine_status"] == "RNG_RESTORE_FAILED"


def test_no_restore_does_not_clear_failure_history(engine):
    baseline = engine.serialize_state()
    bad = dict(baseline)
    bad["engine_rng_state"] = {"invalid": "rng"}
    engine.load_state(bad)
    assert engine._determinism_status == "RNG_RESTORE_FAILED"

    no_restore_state = engine.serialize_state()
    no_restore_state.pop("engine_rng_state", None)
    engine.load_state(no_restore_state)
    assert engine._determinism_status == "OK_WITH_HISTORY"


def test_pnl_mode_locked_and_tick_order_violation_degrades(engine):
    engine.last_signed_position_size = 1.0
    engine.update(_md(ts=None, ret=0.0, price=100.0))
    assert engine._pnl_mode == "TICK"
    # Force non-monotonic anchor tick to trigger explicit violation path.
    engine._last_price_tick_id = engine._tick_id + 5
    out = engine.update(_md(ts=None, ret=0.0, price=101.0))
    assert "PNL_TIMESTAMP_POLICY_BLOCKED" in out["risk_metrics"]["feed_status"]["flags"]
    assert engine._warning_counts.get("pnl_tick_order_violation", 0) >= 1


def test_state_hash_is_validated_on_load_state(engine):
    state = engine.serialize_state()
    state["state_hash"] = "bogus_hash"
    engine._equity = 0.7
    engine.load_state(state)
    assert engine._equity == pytest.approx(0.7)


def test_features_missing_after_validation_emits_failsafe(engine):
    out = engine.update(
        {
            "timestamp": 10.0,
            "return": 0.001,
            "price": 100.0,
            "features": None,
        }
    )
    assert out["risk_metrics"]["feed_status"]["primary"] == "MISSING_DATA"

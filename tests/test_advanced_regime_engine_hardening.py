import numpy as np
import pytest
import threading
import queue
import warnings
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import advanced_regime_engine as module

from advanced_regime_engine import (
    AdvancedRegimeEngine,
    MSGARCH_RiskEngine,
    NHHMM_Engine,
    SparseJumpModel,
    _safe_array,
    _safe_float,
    _safe_int,
    _safe_prob_vector,
    _build_output,
    _normalize_prob_vector,
    _POSITION_SIZE_CAP,
    _validate_output_schema,
    compute_hmm_regime,
)


@pytest.fixture
def engine():
    eng = AdvancedRegimeEngine(n_states=3, n_features=3)
    yield eng
    eng._shutdown_warning_worker()


def _md(ret=0.001, features=None, ts=1.0):
    if features is None:
        features = np.array([0.1, 0.2, 0.3], dtype=float)
    return {
        "timestamp": float(ts),
        "return": ret,
        "features": features,
        "price": 100.0 * (1 + float(ret) if isinstance(ret, (int, float, np.floating)) else 1.0),
    }


def test_update_coerces_string_return_without_crash(engine):
    out = engine.update(_md(ret="bad"))
    assert out["schema_version"] == "1.2.0"
    assert np.isfinite(out["risk_metrics"]["expected_volatility"])

def test_update_with_non_dict_market_data_is_fail_safe(engine):
    out = engine.update(None)
    assert out["schema_version"] == "1.2.0"
    assert np.isfinite(out["position_size"])
    assert out["position_size"] >= 0.0


def test_update_handles_nan_return(engine):
    out = engine.update(_md(ret=float("nan")))
    assert out["signal_valid"] is True
    assert np.isfinite(out["position_size"])


def test_update_dimension_failure_on_bad_feature_shape(engine):
    out = engine.update(_md(ret=0.001, features=np.array([0.1, 0.2])))
    assert out["signal_valid"] is False
    assert out["risk_metrics"]["feed_status"] == "DIMENSION_FAILURE"


def test_mtf_missing_base_raises(engine):
    with pytest.raises(ValueError, match="base"):
        engine.update(
            {
                "timestamp": 1.0,
                "price": 100.0,
                "mtf": {"5m": {"return": 0.001, "features": [0.1, 0.2, 0.3]}},
            }
        )


def test_mtf_non_dict_payload_raises(engine):
    with pytest.raises(ValueError, match="dict"):
        engine.update(
            {
                "timestamp": 1.0,
                "price": 100.0,
                "mtf": ["not", "a", "dict"],
            }
        )


def test_mtf_partial_failure_degrades_not_crash(engine):
    engine._strict_mtf_keys = False
    engine.mtf_weights = {"base": 1.0, "5m": 0.6}
    out = engine.update(
        {
            "timestamp": 1.0,
            "price": 100.0,
            "mtf": {
                "base": {"return": 0.001, "features": [0.1, 0.2, 0.3]},
                "5m": {"return": "bad", "features": [0.1, 0.2, 0.3]},
            },
        }
    )
    assert out["risk_metrics"]["feed_status"] == "MTF_PARTIAL_SURVIVAL"


def test_sjm_non_finite_falls_back_to_last_valid(engine):
    first = engine.update(_md(ret=0.001))
    assert first["signal_valid"] is True
    cached = engine._last_valid_sjm_probs.copy()

    def bad_predict(**kwargs):
        return 0, np.array([np.nan, np.nan, np.nan], dtype=float)

    engine.sjm.online_predict = bad_predict
    out = engine.update(_md(ret=0.002, ts=2.0))
    assert out["signal_valid"] is True
    assert np.allclose(engine._last_valid_sjm_probs, cached)


def test_garch_var_nan_is_repaired(engine):
    engine.garch_var = np.array([np.nan, np.nan], dtype=float)
    out = engine.update(_md())
    assert np.all(np.isfinite(engine.garch_var))
    assert np.isfinite(out["risk_metrics"]["expected_volatility"])


def test_update_handles_extreme_returns_beyond_two_sigma_bounds(engine):
    out_hi = engine.update(_md(ret=2.5, ts=10.0))
    out_lo = engine.update(_md(ret=-2.5, ts=11.0))
    assert _validate_output_schema(out_hi) is True
    assert _validate_output_schema(out_lo) is True


def test_circuit_breaker_preserves_first_reason_and_trigger_tick(engine):
    engine._tick_id = 42
    engine._trigger_circuit_breaker("FIRST")
    engine._trigger_circuit_breaker("SECOND")
    assert engine._circuit_breaker_active is True
    assert engine._circuit_breaker_reason == "FIRST"
    assert engine._circuit_breaker_trigger_tick == 42


def test_self_heal_unknown_error_always_executes_fallback_recovery(engine):
    engine.nhhmm_prior = np.array([np.nan, np.nan, np.nan], dtype=float)
    action = engine._self_heal("E999", {"source": "test"})
    assert action == "SKIP_AND_DEGRADE"
    assert np.isclose(float(np.sum(engine.nhhmm_prior)), 1.0)
    assert np.all(np.isfinite(engine.nhhmm_prior))


def test_load_snapshot_hash_mismatch_rolls_back_state(engine):
    baseline = engine.serialize_state()
    snapshot = {
        "state_hash": "definitely_bad_hash",
        "engine_state": baseline,
        "_engine_rng_state": baseline["engine_rng_state"],
        "_engine_rng_type": type(engine._rng.bit_generator).__name__,
    }
    engine.last_signed_position_size = 0.25
    engine.load_snapshot(snapshot)
    assert np.isclose(engine.last_signed_position_size, 0.25)


def test_compute_hmm_regime_trend_score_bounded_after_scaling():
    scores = compute_hmm_regime(np.array([1.0, 0.0, 0.0], dtype=float))
    assert 0.0 <= scores["trend_score"] <= 1.0


def test_validate_output_schema_requires_nested_fields():
    assert _validate_output_schema({"schema_version": "1.2.0"}) is False


def test_validate_output_schema_rejects_bad_probability_math():
    bad = {
        "schema_version": "1.2.0",
        "regime_idx": 0,
        "regime_label": "TREND",
        "trend_strength": 0.2,
        "risk_level": 0.2,
        "confidence": 0.9,
        "probabilities": {"bull": 0.9, "bear": 0.9, "crisis": 0.1},
        "macro_probs": [0.4, 0.4, 0.2],
        "position_size": 0.5,
        "signed_position_size": 0.5,
        "risk_metrics": {
            "expected_volatility": 0.01,
            "raw_leverage": 0.5,
            "last_valid_vol": 0.02,
            "switch_stability_ema": 1.0,
        },
        "alpha": {"edge_score": 0.1},
    }
    assert _validate_output_schema(bad) is False


def test_validate_output_schema_rejects_invalid_garch_probability_vector():
    bad = {
        "schema_version": "1.2.0",
        "regime_idx": 0,
        "regime_label": "TREND",
        "trend_strength": 0.2,
        "risk_level": 0.2,
        "confidence": 0.9,
        "probabilities": {"bull": 0.6, "bear": 0.3, "crisis": 0.1},
        "macro_probs": [0.4, 0.4, 0.2],
        "position_size": 0.2,
        "signed_position_size": 0.1,
        "risk_metrics": {
            "expected_volatility": 0.01,
            "raw_leverage": 0.5,
            "last_valid_vol": 0.02,
            "switch_stability_ema": 1.0,
            "garch_regime_probs": [0.9, 0.9],
        },
        "alpha": {"edge_score": 0.1},
    }
    assert _validate_output_schema(bad) is False


def test_validate_output_schema_rejects_signed_position_inconsistency():
    bad = {
        "schema_version": "1.2.0",
        "regime_idx": 0,
        "regime_label": "TREND",
        "trend_strength": 0.2,
        "risk_level": 0.2,
        "confidence": 0.9,
        "probabilities": {"bull": 0.6, "bear": 0.3, "crisis": 0.1},
        "macro_probs": [0.4, 0.4, 0.2],
        "position_size": 0.1,
        "signed_position_size": 0.25,
        "risk_metrics": {
            "expected_volatility": 0.01,
            "raw_leverage": 0.5,
            "last_valid_vol": 0.02,
            "switch_stability_ema": 1.0,
            "garch_regime_probs": [0.5, 0.5],
        },
        "alpha": {"edge_score": 0.1},
    }
    assert _validate_output_schema(bad) is False


def test_normalize_prob_vector_non_finite_degrades_to_valid_distribution():
    out = _normalize_prob_vector(np.array([np.nan, np.inf, -np.inf], dtype=float))
    assert np.all(np.isfinite(out))
    assert np.isclose(float(out.sum()), 1.0)


def test_normalize_prob_vector_handles_adversarial_floor():
    out = _normalize_prob_vector(np.array([0.0, 0.0], dtype=float), floor="bad-floor")
    assert np.all(np.isfinite(out))
    assert np.isclose(float(out.sum()), 1.0)


def test_build_output_hardens_corrupt_values_without_crash():
    out = _build_output(
        regime_idx="bad",
        regime_label=None,
        trend_strength=float("nan"),
        risk_level=float("inf"),
        confidence=float("-inf"),
        edge_score="oops",
        probabilities={"bull": np.nan, "bear": None, "crisis": "bad"},
        macro_probs=[np.nan, None, "bad"],
        position_size=float("nan"),
        expected_vol=float("nan"),
        raw_size=float("inf"),
        is_toxic=True,
        garch_regime_probs=[np.nan, np.inf],
        feed_status=None,
        signed_position_size=float("nan"),
        last_valid_vol="x",
        switch_stability_ema=None,
    )
    assert _validate_output_schema(out) is True
    assert out["position_size"] == 0.0
    assert np.isclose(sum(out["macro_probs"]), 1.0)


def test_build_output_fallback_path_never_throws_on_schema_failure(monkeypatch):
    import advanced_regime_engine as module

    monkeypatch.setattr(module, "_validate_output_schema", lambda _out: False)
    out = module._build_output(
        regime_idx=0,
        regime_label="TREND",
        trend_strength=0.5,
        risk_level=0.2,
        confidence=0.9,
        edge_score=0.2,
        probabilities={"bull": 0.8, "bear": 0.1, "crisis": 0.1},
        macro_probs=[0.7, 0.2, 0.1],
        position_size=0.2,
        expected_vol=0.01,
        raw_size=0.3,
        is_toxic=False,
        garch_regime_probs=[0.6, 0.4],
        feed_status="OK",
        last_valid_vol="not-a-number",
        switch_stability_ema=None,
    )
    assert out["risk_metrics"]["feed_status"] == "SCHEMA_FAILURE"
    assert np.isfinite(out["risk_metrics"]["last_valid_vol"])
    assert np.isfinite(out["risk_metrics"]["switch_stability_ema"])
    assert np.isclose(sum(out["macro_probs"]), 1.0)
    assert np.isclose(sum(out["risk_metrics"]["garch_regime_probs"]), 1.0)


def test_build_output_fallback_handles_runtime_float_exceptions(monkeypatch):
    import advanced_regime_engine as module

    class ExplosiveFloat:
        def __float__(self):
            raise RuntimeError("explode")

    monkeypatch.setattr(module, "_validate_output_schema", lambda _out: False)
    out = module._build_output(
        regime_idx=0,
        regime_label="TREND",
        trend_strength=0.5,
        risk_level=0.2,
        confidence=0.9,
        edge_score=0.2,
        probabilities={"bull": 0.8, "bear": 0.1, "crisis": 0.1},
        macro_probs=[0.7, 0.2, 0.1],
        position_size=0.2,
        expected_vol=0.01,
        raw_size=0.3,
        is_toxic=False,
        garch_regime_probs=[0.6, 0.4],
        feed_status="OK",
        last_valid_vol=ExplosiveFloat(),
        switch_stability_ema=ExplosiveFloat(),
    )
    assert out["risk_metrics"]["feed_status"] == "SCHEMA_FAILURE"
    assert np.isfinite(out["risk_metrics"]["last_valid_vol"])
    assert np.isfinite(out["risk_metrics"]["switch_stability_ema"])
    assert np.isclose(sum(out["macro_probs"]), 1.0)


def test_validate_output_schema_rejects_non_finite_position_size():
    bad = {
        "schema_version": "1.2.0",
        "regime_idx": 0,
        "regime_label": "TREND",
        "trend_strength": 0.2,
        "risk_level": 0.2,
        "confidence": 0.9,
        "probabilities": {"bull": 0.6, "bear": 0.3, "crisis": 0.1},
        "macro_probs": [0.4, 0.4, 0.2],
        "position_size": float("nan"),
        "signed_position_size": 0.0,
        "risk_metrics": {
            "expected_volatility": 0.01,
            "raw_leverage": 0.5,
            "last_valid_vol": 0.02,
            "switch_stability_ema": 1.0,
            "garch_regime_probs": [0.5, 0.5],
        },
        "alpha": {"edge_score": 0.1},
    }
    assert _validate_output_schema(bad) is False


def test_load_state_corrupted_scalars_do_not_poison_engine(engine):
    engine.load_state(
        {
            "last_valid_dt": "oops",
            "range_ticks": float("nan"),
            "range_anchor_size": -5.0,
            "last_signed_position_size": float("inf"),
            "last_effective_trend_strength": float("-inf"),
            "last_edge_score": float("nan"),
            "last_valid_vol": 0.0,
            "switch_stability_ema": -1.0,
            "shock_memory": float("nan"),
            "return_ema": float("inf"),
            "abs_return_ema": -1.0,
            "garch_var": [float("nan"), float("inf")],
        }
    )
    assert engine._last_valid_dt > 0.0
    assert engine.range_ticks >= 0.0
    assert engine._range_anchor_size >= 0.0
    assert np.isfinite(engine.last_signed_position_size)
    assert np.isfinite(engine._last_effective_trend_strength)
    assert np.isfinite(engine._last_edge_score)
    assert engine._last_valid_vol > 0.0
    assert engine._switch_stability_ema > 0.0
    assert engine._shock_memory >= 0.0
    assert np.isfinite(engine._return_ema)
    assert engine._abs_return_ema >= 0.0
    assert np.all(np.isfinite(engine.garch_var))


def test_load_state_logs_degrade_fields(engine, caplog):
    caplog.set_level("ERROR")
    engine.load_state(
        {
            "current_regime_idx": "bad",
            "confirmed_regime_idx": 999,
            "loss_streak": "bad",
            "healing_count": -3,
        }
    )
    assert "STATE_LOAD_DEGRADE field=current_regime_idx" in caplog.text
    assert "STATE_LOAD_DEGRADE field=confirmed_regime_idx" in caplog.text
    assert "STATE_LOAD_DEGRADE field=loss_streak" in caplog.text
    assert "STATE_LOAD_DEGRADE field=healing_count" in caplog.text


def test_load_state_signature_and_version_mismatch_do_not_crash(engine):
    engine.current_regime_idx = 2
    engine._circuit_breaker_active = True
    engine._drawdown = 0.9
    engine.load_state({"model_signature": "bad", "state_version": "0.0.0"})
    assert engine.current_regime_idx is None
    assert engine._circuit_breaker_active is False
    assert engine._drawdown == 0.0
    out = engine.update(_md(ret=0.001, ts=1.0))
    assert out["schema_version"] == "1.2.0"


def test_safe_deserialization_helpers_never_raise_and_normalize():
    assert _safe_float("x", default=1.5, min=0.0, max=2.0) == 1.5
    assert _safe_int("x", default=3, min=0, max=5) == 3
    vec = _safe_array([1.0, float("nan"), "bad"], shape=(3,), default=[0.0, 0.0, 0.0])
    assert vec.shape == (3,)
    assert np.all(np.isfinite(vec))
    probs = _safe_prob_vector([1.0, float("nan"), -2.0], 3)
    assert np.isclose(float(np.sum(probs)), 1.0)
    assert np.all(np.isfinite(probs))


def test_load_snapshot_corruption_recovery_no_crash(engine):
    snapshot = {
        "engine_state": {
            "garch_var": [float("nan"), float("nan")],
            "last_valid_vol": float("nan"),
            "switch_stability_ema": 0.0,
            "range_ticks": float("inf"),
            "last_effective_trend_strength": float("nan"),
            "last_edge_score": float("nan"),
        },
        "garch_var": [float("nan"), float("nan")],
        "last_valid_vol": float("nan"),
    }
    engine.load_snapshot(snapshot)
    out = engine.update(_md(ret=0.002, ts=3.0))
    assert out["schema_version"] == "1.2.0"
    assert np.isfinite(out["risk_metrics"]["expected_volatility"])
    assert 0.0 <= out["confidence"] <= 1.0


def test_model_components_normal_flow():
    nhhmm = NHHMM_Engine(n_states=3, n_features=3)
    post, _ = nhhmm.forward_pass_step(0.001, np.array([0.1, 0.2, 0.3]), np.ones(3) / 3)
    assert np.isclose(float(np.sum(post)), 1.0)

    sjm = SparseJumpModel(n_states=3)
    state, probs = sjm.online_predict(
        x_t=np.array([0.001, 0.2, 0.3]),
        expected_n_features=3,
        prev_state=None,
        nhhmm_probs=np.ones(3) / 3,
    )
    assert state in {0, 1, 2}
    assert np.isclose(float(np.sum(probs)), 1.0)

    garch = MSGARCH_RiskEngine(target_volatility=0.02)
    new_var = garch._garch_update(np.array([1e-4, 2e-4]), 0.001)
    new_prob = garch._update_regime_probs(np.array([0.5, 0.5]), np.array([1e-4, 2e-4]), 0.001)
    assert np.all(np.isfinite(new_var))
    assert np.isclose(float(np.sum(new_prob)), 1.0)


def test_deterministic_state_hash_regression():
    inputs = [_md(ret=r, ts=i) for i, r in enumerate([0.001, -0.0005, 0.0007, 0.0], start=1)]
    e1 = AdvancedRegimeEngine(n_states=3, n_features=3)
    e2 = AdvancedRegimeEngine(n_states=3, n_features=3)
    try:
        for md in inputs:
            e1.update(md)
            e2.update(md)
        s1 = e1.serialize_state()
        s2 = e2.serialize_state()
        assert e1._state_hash(s1) == e2._state_hash(s2)
    finally:
        e1._shutdown_warning_worker()
        e2._shutdown_warning_worker()


def test_load_snapshot_logs_structured_error(engine, caplog):
    caplog.set_level("ERROR")
    engine.load_state = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
    engine.load_snapshot({"engine_state": {}})
    assert "Snapshot load failed context_keys=['engine_state']" in caplog.text


def test_circuit_breaker_vol_shock_short_circuits_same_tick(engine):
    engine._VOL_SHOCK_MULTIPLIER = 0.05
    out = engine.update(_md(ret=0.8, ts=1.0))
    assert out["regime_label"] == "HALTED"
    assert out["execution_mode"] == "circuit_breaker"
    assert out["risk_metrics"]["feed_status"] == "CIRCUIT_BREAKER:VOL_SHOCK"
    assert out["signal_valid"] is False
    assert out["position_size"] == 0.0
    assert out["signed_position_size"] == 0.0
    assert out["execution_side"] == "flat"
    assert out["confidence"] == 0.0
    assert out["alpha"]["edge_score"] == 0.0
    assert out["risk_metrics"]["toxic_penalty_applied"] is True
    assert engine._circuit_breaker_active is True


@pytest.mark.parametrize(
    "reason",
    ["MAX_DRAWDOWN", "LOSS_STREAK", "VOL_SHOCK", "CONFIDENCE_COLLAPSE", "MANUAL_TRIGGER"],
)
def test_halted_output_invariants_hold_for_all_breaker_reasons(engine, reason):
    engine._trigger_circuit_breaker(reason)
    out = engine.update(_md(ret=0.001, ts=10.0))
    assert out["regime_label"] == "HALTED"
    assert out["execution_mode"] == "circuit_breaker"
    assert out["signal_valid"] is False
    assert out["position_size"] == 0.0
    assert out["signed_position_size"] == 0.0
    assert out["execution_side"] == "flat"
    assert out["confidence"] == 0.0
    assert out["alpha"]["edge_score"] == 0.0
    assert out["risk_metrics"]["toxic_penalty_applied"] is True


def test_reset_state_clears_breaker_healing_and_price(engine):
    engine._circuit_breaker_active = True
    engine._circuit_breaker_reason = "TEST"
    engine._healing_counter = 9
    engine._last_price = 123.0
    engine._last_valid_sjm_probs = np.array([0.2, 0.2, 0.6], dtype=float)
    engine.reset_state()
    assert engine._circuit_breaker_active is False
    assert engine._circuit_breaker_reason is None
    assert engine._healing_counter == 0
    assert engine._last_price is None
    assert engine._last_valid_sjm_probs is None


def test_signed_position_is_clamped_with_corrupt_prior_state(engine):
    engine.last_signed_position_size = 5.0
    engine._range_anchor_size = 10.0
    out = engine.update(_md(ret=0.001, ts=2.0))
    assert abs(out["signed_position_size"]) <= out["position_size"] + 1e-12


def test_last_signed_position_size_persisted_after_successful_ticks(engine):
    out1 = engine.update(_md(ret=0.001, ts=1.0))
    assert engine.last_signed_position_size == pytest.approx(out1["signed_position_size"])
    out2 = engine.update(_md(ret=-0.001, ts=2.0))
    assert engine.last_signed_position_size == pytest.approx(out2["signed_position_size"])


def test_warning_drop_counter_thread_safe_when_queue_is_full(engine):
    engine._shutdown_warning_worker()
    engine._warning_queue = queue.Queue(maxsize=1)
    engine._warning_queue.put_nowait("occupied")
    engine._warning_drop_count = 0

    n_threads = 16
    threads = []
    for i in range(n_threads):
        t = threading.Thread(
            target=engine._warn_rate_limited,
            args=(f"drop-key-{i}", "message", 0.0),
        )
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    assert engine._warning_drop_count == n_threads


def test_breaker_reason_and_healing_counter_not_overwritten_same_tick(engine):
    engine._last_price = 100.0
    engine.last_signed_position_size = 1.0
    engine._loss_streak = engine._MAX_CONSECUTIVE_LOSSES - 1
    out = engine.update(_md(ret=0.0, ts=1.0) | {"price": 80.0})
    assert out["regime_label"] == "HALTED"
    assert engine._circuit_breaker_reason == "MAX_DRAWDOWN"
    assert engine._healing_counter == 1


def test_self_heal_fallback_mapping_without_errors_module(engine):
    engine._error_category_resolver = None
    engine._errors_module_available = False
    action = engine._self_heal("E200", {"source": "test"})
    assert action == "RESET_NUMERICAL"


def test_rng_seed_and_state_restore_are_deterministic():
    e1 = AdvancedRegimeEngine(n_states=3, n_features=3, seed=123)
    e2 = AdvancedRegimeEngine(n_states=3, n_features=3, seed=123)
    e3 = AdvancedRegimeEngine(n_states=3, n_features=3, seed=999)
    try:
        seq1 = [e1._obs_should_sample() for _ in range(20)]
        seq2 = [e2._obs_should_sample() for _ in range(20)]
        assert seq1 == seq2

        state = e1.get_state()
        e3.load_state(state)
        assert [e1._obs_should_sample() for _ in range(10)] == [e3._obs_should_sample() for _ in range(10)]
    finally:
        e1._shutdown_warning_worker()
        e2._shutdown_warning_worker()
        e3._shutdown_warning_worker()


def test_healing_branch_returns_immediately_after_self_heal(engine, monkeypatch):
    engine._circuit_breaker_active = True
    engine._healing_counter = engine._HEALING_COOLDOWN_TICKS + 1
    monkeypatch.setattr(engine.nhhmm, "forward_pass_step", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("must not run")))
    out = engine.update(_md(ret=0.001, ts=1.0))
    assert out["regime_label"] == "HALTED"


def test_range_decay_is_single_pass_and_monotonic(monkeypatch):
    def _forced_scores(*_args, **_kwargs):
        return {
            "regime": "RANGE",
            "bull": 0.5,
            "bear": 0.49,
            "crisis": 0.01,
            "trend_strength": 0.01,
            "risk_level": 0.01,
            "confidence": 0.5,
            "conviction": 0.6,
            "uncertainty": 0.4,
            "directional_margin": 0.01,
            "directional_label": "TREND",
            "edge_score": 0.8,
            "trend_score": 0.1,
            "range_score": 0.9,
            "toxic_score": 0.0,
        }
    monkeypatch.setattr(module, "compute_hmm_regime", _forced_scores)

    e_fast = AdvancedRegimeEngine(n_states=3, n_features=3, seed=7)
    e_slow = AdvancedRegimeEngine(n_states=3, n_features=3, seed=7)
    try:
        for eng, ticks in ((e_fast, 1.0), (e_slow, 40.0)):
            eng._regime_smoother = None
            eng._confirmed_regime = "RANGE"
            eng._prev_regime = "RANGE"
            eng._range_anchor_size = 0.2
            eng.last_signed_position_size = 0.2
            eng.range_ticks = ticks
        out_fast = e_fast.update(_md(ret=0.001, ts=1.0))
        out_slow = e_slow.update(_md(ret=0.001, ts=1.0))
        assert abs(out_slow["signed_position_size"]) <= abs(out_fast["signed_position_size"])
    finally:
        e_fast._shutdown_warning_worker()
        e_slow._shutdown_warning_worker()


def test_conviction_metric_and_boundary_hysteresis():
    uniform = compute_hmm_regime(np.array([1 / 3, 1 / 3, 1 / 3], dtype=float))
    crisis = compute_hmm_regime(np.array([0.05, 0.05, 0.90], dtype=float))
    assert uniform["conviction"] < crisis["conviction"]
    hold = compute_hmm_regime(
        np.array([0.49, 0.51, 0.0], dtype=float),
        prev_directional_label="TREND",
        direction_switch_gap=0.03,
    )
    assert hold["directional_label"] == "TREND"


def test_last_price_persisted_via_get_state_load_state(engine):
    engine._last_price = 123.45
    state = engine.get_state()
    restored = AdvancedRegimeEngine(n_states=3, n_features=3, seed=7)
    try:
        restored.load_state(state)
        assert restored._last_price == pytest.approx(123.45)
    finally:
        restored._shutdown_warning_worker()


def test_negative_equity_clamped_and_breaker_tripped(engine):
    engine._last_price = 100.0
    engine.last_signed_position_size = 1.0
    engine.update(_md(ret=0.0, ts=1.0) | {"price": 0.0})
    assert engine._equity >= engine._MIN_EQUITY_FLOOR
    assert engine._circuit_breaker_active is True


def test_warning_queue_saturation_fallback_never_recurses(engine, monkeypatch):
    engine._shutdown_warning_worker()
    engine._warning_queue = queue.Queue(maxsize=1)
    engine._warning_queue.put_nowait("full")
    monkeypatch.setattr(warnings, "warn", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("warn fail")))
    engine._warn_rate_limited("k", "msg", 0.0)
    assert engine._warning_drop_count >= 1


@pytest.mark.parametrize("n_features", [1, 2])
def test_sjm_feature_injection_does_not_overwrite_small_vectors(monkeypatch, n_features):
    captured = {}
    eng = AdvancedRegimeEngine(n_states=3, n_features=n_features, seed=1)
    try:
        def _capture(**kwargs):
            captured["x_t"] = np.asarray(kwargs["x_t"], dtype=float).copy()
            return 0, np.array([0.7, 0.2, 0.1], dtype=float)
        monkeypatch.setattr(eng.sjm, "online_predict", _capture)
        feats = np.arange(1, n_features + 1, dtype=float)
        eng.update({"timestamp": 1.0, "return": 0.01, "features": feats, "price": 100.0})
        assert np.allclose(captured["x_t"], feats)
    finally:
        eng._shutdown_warning_worker()


def test_load_snapshot_is_atomic_on_failure(engine):
    engine._last_price = 222.0
    before = engine.serialize_state()
    bad_snapshot = {"engine_state": before, "last_price": float("nan")}
    engine.load_snapshot(bad_snapshot)
    after = engine.serialize_state()
    assert after["last_price"] == before["last_price"]


def test_position_size_cap_single_source_of_truth(engine):
    out = engine.update(_md(ret=0.001, ts=1.0))
    assert module.AdvancedRegimeEngine._MAX_POSITION_SIZE == _POSITION_SIZE_CAP
    assert out["position_size"] <= _POSITION_SIZE_CAP

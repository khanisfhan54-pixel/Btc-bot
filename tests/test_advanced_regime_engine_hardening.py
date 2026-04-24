import numpy as np
import pytest

from advanced_regime_engine import (
    AdvancedRegimeEngine,
    MSGARCH_RiskEngine,
    NHHMM_Engine,
    SparseJumpModel,
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


def test_compute_hmm_regime_trend_score_bounded_after_scaling():
    scores = compute_hmm_regime(np.array([1.0, 0.0, 0.0], dtype=float))
    assert 0.0 <= scores["trend_score"] <= 1.0


def test_validate_output_schema_requires_nested_fields():
    assert _validate_output_schema({"schema_version": "1.2.0"}) is False


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

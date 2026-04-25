import numpy as np
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from advanced_regime_engine import AdvancedRegimeEngine, MSGARCH_RiskEngine


def _single_tf(ts: float, ret, feats=None, price=None):
    payload = {
        "timestamp": float(ts),
        "return": ret,
        "features": np.array(feats if feats is not None else [0.1, 0.2, 0.3], dtype=float),
    }
    if price is not None:
        payload["price"] = float(price)
    return payload


def _base_mtf(ts: float, base_ret: float, base_feats=None, **extra_tfs):
    mtf = {
        "base": {
            "return": base_ret,
            "features": np.array(base_feats if base_feats is not None else [0.1, 0.2, 0.3], dtype=float),
        }
    }
    mtf.update(extra_tfs)
    return {
        "timestamp": float(ts),
        "return": base_ret,
        "features": np.array([0.1, 0.2, 0.3], dtype=float),
        "mtf": mtf,
    }


def test_mtf_default_base_only_does_not_total_fail():
    eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=7)
    heals = []

    def _track_heal(code=None, context=None):
        heals.append((code, dict(context or {})))

    eng._self_heal = _track_heal
    out = eng.update(_base_mtf(ts=1.0, base_ret=0.001))

    assert out["risk_metrics"]["feed_status"] == "OK"
    assert not any(c == "E130" and ctx.get("source") == "mtf_total_failure" for c, ctx in heals)
    eng._shutdown_warning_worker()


def test_mtf_unknown_and_zero_weight_timeframes_degrade_predictably():
    eng = AdvancedRegimeEngine(
        n_states=3,
        n_features=3,
        strict_mtf_keys=False,
        mtf_weights={"5m": 0.0, "15m": 0.0},
        seed=9,
    )
    payload = _base_mtf(
        ts=1.0,
        base_ret=0.001,
        **{
            "5m": {"return": 0.3, "features": np.array([9.0, 9.0, 9.0])},
            "unknown": {"return": 0.8, "features": np.array([8.0, 8.0, 8.0])},
        },
    )
    out = eng.update(payload)

    assert out["risk_metrics"]["feed_status"] == "OK"
    assert out["schema_version"] == "1.2.0"
    eng._shutdown_warning_worker()


def test_garch_update_preserves_ar_input_in_high_vol_regime():
    garch = MSGARCH_RiskEngine(target_volatility=0.02)
    current_var = np.array([1.0, 1.0], dtype=float)
    updated = garch._garch_update(current_var, 0.0)

    # With correct AR term, both regimes hit output ceiling due to large previous variance.
    assert np.allclose(updated, garch._VAR_CEIL)


def test_garch_update_remains_bounded_and_finite_under_large_repeated_returns():
    garch = MSGARCH_RiskEngine(target_volatility=0.02)
    var = np.array([0.01, 0.01], dtype=float)
    for _ in range(200):
        var = garch._garch_update(var, 2.0)
        assert np.all(np.isfinite(var))
        assert np.all(var >= 1e-8)
        assert np.all(var <= garch._VAR_CEIL)


def test_sjm_features_not_overwritten_by_default():
    eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=7)
    captured = {}

    def _capture_predict(**kwargs):
        captured["x_t"] = np.array(kwargs["x_t"], dtype=float)
        return 0, np.array([1.0, 0.0, 0.0], dtype=float)

    eng.sjm.online_predict = _capture_predict
    raw_feats = np.array([9.0, 8.0, 7.0], dtype=float)
    eng.update(_single_tf(ts=1.0, ret=0.01, feats=raw_feats))

    assert np.allclose(captured["x_t"], raw_feats)
    eng._shutdown_warning_worker()


def test_sjm_feature_injection_only_when_indices_reserved():
    eng = AdvancedRegimeEngine(
        n_states=3,
        n_features=3,
        sjm_reserved_feature_indices=(0, 2),
        seed=7,
    )
    captured = {}

    def _capture_predict(**kwargs):
        captured["x_t"] = np.array(kwargs["x_t"], dtype=float)
        return 0, np.array([1.0, 0.0, 0.0], dtype=float)

    eng.sjm.online_predict = _capture_predict
    eng.update(_single_tf(ts=1.0, ret=0.01, feats=[9.0, 8.0, 7.0]))

    assert captured["x_t"][0] == 0.01
    assert captured["x_t"][1] == 8.0
    assert captured["x_t"][2] == 0.01
    eng._shutdown_warning_worker()


def test_mtf_canonical_return_used_for_gating_not_top_level_return():
    eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=7)
    payload = _base_mtf(ts=1.0, base_ret=0.001)
    payload["return"] = 0.2  # intentionally different from base return

    out = eng.update(payload)

    assert out["regime_label"] != "HALTED"
    assert eng._circuit_breaker_active is False
    eng._shutdown_warning_worker()


def test_timestamp_regression_updates_anchor_and_avoids_stale_future_delta():
    eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=7)
    eng.update(_single_tf(ts=100.0, ret=0.001, price=100.0))
    eng.update(_single_tf(ts=90.0, ret=0.001, price=100.1))
    assert eng._last_timestamp == 90.0

    eng.update(_single_tf(ts=95.0, ret=0.001, price=100.2))
    assert eng._last_timestamp == 95.0
    assert eng._last_valid_dt == 5.0
    eng._shutdown_warning_worker()


def test_price_return_mismatch_early_return_updates_timestamp_anchor():
    eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=7)
    eng.update(_single_tf(ts=1.0, ret=0.0, price=100.0))

    mismatch_out = eng.update(_single_tf(ts=2.0, ret=0.0, price=101.0))
    assert mismatch_out["risk_metrics"]["feed_status"] == "PRICE_RETURN_MISMATCH"
    assert eng._last_timestamp == 2.0

    eng.update(_single_tf(ts=3.0, ret=0.001, price=101.101))
    assert eng._last_valid_dt == 1.0
    eng._shutdown_warning_worker()


def test_invalid_scalar_return_fails_safe_with_observable_status():
    eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=7)
    out = eng.update(_single_tf(ts=1.0, ret="not-a-number"))

    assert out["risk_metrics"]["feed_status"] == "INVALID_RETURN_INPUT"
    assert out["execution_mode"] == "fail_safe"
    eng._shutdown_warning_worker()

import numpy as np

import engine
from alpha_liquidity_sweep_predictor import predict_sweep


def test_run_all_engines_nan_inf_and_empty_inputs_safe():
    out = engine.run_all_engines(orderbook={}, trades=[], price=float("nan"), recent_candles=[])
    assert isinstance(out, dict), "run_all_engines should return dict for NaN input"
    assert out.get("allow_trade") is False, "NaN price must fail closed"


def test_predict_sweep_extreme_values_stays_finite():
    output = predict_sweep(
        liquidity={
            "nearest_above": {"distance_points": 1e12, "price": 1e12},
            "nearest_below": {"distance_points": 1e-12, "price": 1e-12},
        },
        market_state={"state": "COMPRESSION", "compression": float("inf"), "volatility": float("nan"), "bias": 1e9},
        volume_intel={"volume_spike": True, "volume_strength": float("inf")},
    )
    output_arr = np.array([
        float(output.get("confidence", 0.0)),
        float(output.get("prob_above", 0.0)),
        float(output.get("prob_below", 0.0)),
    ])
    assert not np.isnan(output_arr).any(), "output must not contain NaN"
    assert not np.isinf(output_arr).any(), "output must not contain Inf"
    assert np.isfinite(output_arr).all(), "output must be finite"


def test_predict_sweep_empty_inputs_returns_safe_fallback():
    output = predict_sweep(liquidity={}, market_state={}, volume_intel={})
    output_arr = np.array([
        float(output.get("confidence", 0.0)),
        float(output.get("prob_above", 0.0)),
        float(output.get("prob_below", 0.0)),
    ])
    assert np.isfinite(output_arr).all(), "empty-input fallback should be finite"


def test_invalid_inputs_raise_or_safe_fallback():
    try:
        output = predict_sweep(liquidity=None, market_state=None, volume_intel=None)
        output_arr = np.array([
            float(output.get("confidence", 0.0)),
            float(output.get("prob_above", 0.0)),
            float(output.get("prob_below", 0.0)),
        ])
        assert np.isfinite(output_arr).all(), "invalid inputs should return safe finite fallback"
    except ValueError:
        assert True, "raising controlled ValueError is acceptable behavior"


def test_detect_liquidity_sweep_invalid_price_fail_closed():
    out = engine.detect_liquidity_sweep(trades=[], price=float("nan"))
    values = np.array([float(out.get("size_usd", 0.0))])
    assert out["sweep"] is False
    assert out["reason"] == "invalid_price"
    assert not np.isnan(values).any()
    assert not np.isinf(values).any()

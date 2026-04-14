# tests/test_alpha.py

import sys
import os

# Fix import path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import alpha_liquidity_sweep_predictor as alpha


# ----------------------------
# 🔹 Helper: sample data
# ----------------------------
def get_sample_candles():
    return {
        "data": [
            {"open": 100, "high": 105, "low": 95, "close": 102, "volume": 10},
            {"open": 102, "high": 110, "low": 100, "close": 108, "volume": 12},
            {"open": 108, "high": 115, "low": 105, "close": 112, "volume": 15},
        ]
    }


# ----------------------------
# 🔹 Test 1: Function runs
# ----------------------------
def test_predict_runs():
    candles = get_sample_candles()
    market_state = {}

    result = alpha.predict_sweep(candles, market_state)

    assert result is not None


# ----------------------------
# 🔹 Test 2: Output structure
# ----------------------------
def test_output_structure():
    candles = get_sample_candles()
    market_state = {}

    result = alpha.predict_sweep(candles, market_state)

    assert isinstance(result, dict), "Output should be dict"


# ----------------------------
# 🔹 Test 3: Output values valid
# ----------------------------
def test_output_values_not_none():
    candles = get_sample_candles()
    market_state = {}

    result = alpha.predict_sweep(candles, market_state)

    for key, value in result.items():
        assert value is not None, f"{key} is None"


# ----------------------------
# 🔹 Test 4: Empty input (edge case)
# ----------------------------
def test_empty_input():
    candles = {"data": []}
    market_state = {}

    result = alpha.predict_sweep(candles, market_state)

    assert result is not None


# ----------------------------
# 🔹 Test 5: Stability (no crash on weird data)
# ----------------------------
def test_invalid_data_handling():
    candles = {
        "data": [
            {"open": None, "high": "bad", "low": -999, "close": 0, "volume": None}
        ]
    }

    market_state = {}

    result = alpha.predict_sweep(candles, market_state)

    assert result is not None


# ----------------------------
# 🔹 Test 6: Consistency check
# ----------------------------
def test_consistency():
    candles = get_sample_candles()
    market_state = {}

    result1 = alpha.predict_sweep(candles, market_state)
    result2 = alpha.predict_sweep(candles, market_state)

    assert result1 == result2, "Function should be deterministic"

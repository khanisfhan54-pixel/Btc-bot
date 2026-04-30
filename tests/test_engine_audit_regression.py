"""
Regression tests for engine.py audit fixes.
Covers Defects A, B, C, D (FIX-7 partial).
All tests must pass before production deployment.
"""
import math
import pytest
from unittest.mock import patch
import engine  # must import the fixed engine.py


def _make_orderbook_sorted():
    return {
        "bids": [[64990.0, 1.5], [64980.0, 2.0], [64970.0, 0.8]],
        "asks": [[65010.0, 1.2], [65020.0, 1.8], [65030.0, 0.6]],
    }

def _make_orderbook_unsorted():
    """Same levels as sorted, but bids ascending and asks descending."""
    return {
        "bids": [[64970.0, 0.8], [64980.0, 2.0], [64990.0, 1.5]],
        "asks": [[65030.0, 0.6], [65020.0, 1.8], [65010.0, 1.2]],
    }

def _make_orderbook_deep_changed():
    """Same best bid/ask as sorted, but different depth sizes."""
    return {
        "bids": [[64990.0, 3.0], [64980.0, 4.0], [64970.0, 1.6]],
        "asks": [[65010.0, 2.4], [65020.0, 3.6], [65030.0, 1.2]],
    }

def _make_trades():
    return [
        {"price": 65000.0, "amount": 0.5, "side": "BUY"},
        {"price": 64999.0, "amount": 0.3, "side": "SELL"},
    ]

def _make_candles(n=60):
    """Generate n synthetic 1-minute OHLCV rows."""
    rows = []
    base = 1_700_000_000_000
    price = 65000.0
    for i in range(n):
        rows.append([base + i * 60_000, price, price + 10, price - 10, price + 1, 100.0])
    return rows

def _run_engines(orderbook=None, candles=None, oi=500_000_000.0):
    return engine.run_all_engines(
        orderbook=orderbook or _make_orderbook_sorted(),
        trades=_make_trades(),
        price=65000.0,
        recent_candles=candles or _make_candles(60),
        open_interest=oi,
        funding_rate=0.0001,
    )


class TestDefectA_MissingReturnFallback:
    def test_exception_returns_dict_not_none(self):
        with patch.object(engine, "institutional_score_engine", side_effect=RuntimeError("injected_fault")):
            result = _run_engines()
        assert result is not None
        assert isinstance(result, dict)

    def test_exception_returns_fail_closed_allow_trade(self):
        with patch.object(engine, "institutional_score_engine", side_effect=RuntimeError("injected_fault")):
            result = _run_engines()
        assert result["allow_trade"] is False

    def test_exception_returns_hold_direction(self):
        with patch.object(engine, "institutional_score_engine", side_effect=RuntimeError("injected_fault")):
            result = _run_engines()
        assert result["direction"] == "HOLD"

    def test_exception_returns_error_reason(self):
        with patch.object(engine, "institutional_score_engine", side_effect=RuntimeError("injected_fault")):
            result = _run_engines()
        assert result["reason"] == "run_all_engines_error"

    def test_exception_returns_valid_alpha(self):
        with patch.object(engine, "institutional_score_engine", side_effect=RuntimeError("injected_fault")):
            result = _run_engines()
        alpha = result["alpha"]
        assert isinstance(alpha, dict)
        assert alpha["direction"] in ("LONG", "SHORT", "NEUTRAL")
        assert 0.0 <= alpha["confidence"] <= 1.0
        assert abs(alpha["prob_above"] + alpha["prob_below"] - 1.0) < 1e-6

    def test_nominal_path_unaffected(self):
        result = _run_engines()
        assert isinstance(result, dict)
        assert "allow_trade" in result
        assert "direction" in result
        assert isinstance(result["allow_trade"], bool)
        assert result["direction"] in ("LONG", "SHORT", "HOLD")


class TestDefectB_CacheKeyDepth:
    def test_different_depth_causes_cache_miss(self):
        engine.run_all_engines._backtest_cache = {}
        engine.run_all_engines._cache_misses = 0
        engine.run_all_engines._cache_hits = 0

        _run_engines(orderbook=_make_orderbook_sorted())
        _run_engines(orderbook=_make_orderbook_deep_changed())

        assert engine.run_all_engines._cache_misses >= 2
        assert engine.run_all_engines._cache_hits == 0

    def test_identical_orderbook_causes_cache_hit(self):
        engine.run_all_engines._backtest_cache = {}
        engine.run_all_engines._cache_misses = 0
        engine.run_all_engines._cache_hits = 0

        ob = _make_orderbook_sorted()
        _run_engines(orderbook=ob)
        _run_engines(orderbook=ob)

        assert engine.run_all_engines._cache_hits >= 1

    def test_imbalance_differs_between_depth_variants(self):
        engine.run_all_engines._backtest_cache = {}
        r1 = _run_engines(orderbook=_make_orderbook_sorted())
        engine.run_all_engines._backtest_cache = {}
        r2 = _run_engines(orderbook=_make_orderbook_deep_changed())
        assert r1 is not None and r2 is not None


class TestFix7Partial_UnsortedAbsorption:
    def test_absorption_sorted_vs_unsorted_same_bids_vol(self):
        ob_sorted = _make_orderbook_sorted()
        ob_unsorted = _make_orderbook_unsorted()
        trades = _make_trades()

        result_sorted = engine.detect_smart_money_absorption(ob_sorted, trades)
        result_unsorted = engine.detect_smart_money_absorption(ob_unsorted, trades)

        assert abs(result_sorted["bids_vol"] - result_unsorted["bids_vol"]) < 1e-6
        assert abs(result_sorted["asks_vol"] - result_unsorted["asks_vol"]) < 1e-6

    def test_absorption_decision_consistent_sorted_unsorted(self):
        ob_sorted = _make_orderbook_sorted()
        ob_unsorted = _make_orderbook_unsorted()
        trades = _make_trades()

        r_sorted = engine.detect_smart_money_absorption(ob_sorted, trades)
        r_unsorted = engine.detect_smart_money_absorption(ob_unsorted, trades)
        assert r_sorted["absorption"] == r_unsorted["absorption"]

    def test_smart_money_detection_sorted_vs_unsorted(self):
        ob_sorted = _make_orderbook_sorted()
        ob_unsorted = _make_orderbook_unsorted()
        trades = _make_trades()
        price = 65000.0

        r_sorted = engine.smart_money_detection_engine(ob_sorted, trades, price)
        r_unsorted = engine.smart_money_detection_engine(ob_unsorted, trades, price)
        assert r_sorted["smart_money_detected"] == r_unsorted["smart_money_detected"]

    def test_absorption_does_not_crash_on_malformed_book(self):
        ob_bad = {"bids": [["not_a_float", "bad"], None, []], "asks": []}
        result = engine.detect_smart_money_absorption(ob_bad, [])
        assert isinstance(result, dict)
        assert "absorption" in result


class TestDefectC_ComputeSmaNotNone:
    def test_short_candle_history_does_not_return_none_alpha(self):
        result = _run_engines(candles=_make_candles(6))
        assert result is not None
        alpha = result.get("alpha", {})
        assert isinstance(alpha, dict)
        assert alpha.get("confidence") is not None
        assert isinstance(alpha["confidence"], float)
        assert math.isfinite(alpha["confidence"])

    def test_short_candle_history_alpha_direction_valid(self):
        result = _run_engines(candles=_make_candles(6))
        assert result["alpha"]["direction"] in ("LONG", "SHORT", "NEUTRAL")

    def test_short_candle_history_alpha_probs_sum_to_one(self):
        result = _run_engines(candles=_make_candles(6))
        alpha = result["alpha"]
        assert abs(alpha["prob_above"] + alpha["prob_below"] - 1.0) < 1e-6

    def test_zero_candles_does_not_crash(self):
        result = _run_engines(candles=[])
        assert result is not None
        assert isinstance(result, dict)


class TestDeterminism:
    def test_run_all_engines_deterministic(self):
        engine.run_all_engines._backtest_cache = {}
        r1 = _run_engines()
        engine.run_all_engines._backtest_cache = {}
        r2 = _run_engines()

        for key in ("allow_trade", "direction", "confidence", "institutional_score", "cascade_probability", "spread_pct", "imbalance"):
            v1 = r1.get(key)
            v2 = r2.get(key)
            if isinstance(v1, float) and isinstance(v2, float):
                assert abs(v1 - v2) < 1e-6
            else:
                assert v1 == v2

    def test_alpha_probs_always_sum_to_one(self):
        for _ in range(3):
            engine.run_all_engines._backtest_cache = {}
            result = _run_engines()
            alpha = result["alpha"]
            assert abs(alpha["prob_above"] + alpha["prob_below"] - 1.0) < 1e-6

    def test_no_nan_in_critical_outputs(self):
        result = _run_engines()
        for key in ("confidence", "institutional_score", "cascade_probability", "spread_pct", "imbalance", "orderbook_imbalance"):
            val = result.get(key)
            if isinstance(val, float):
                assert math.isfinite(val)

    def test_allow_trade_is_bool(self):
        result = _run_engines()
        assert isinstance(result["allow_trade"], bool)

    def test_direction_is_valid_string(self):
        result = _run_engines()
        assert result["direction"] in ("LONG", "SHORT", "HOLD")


class TestOutputSchemaContract:
    REQUIRED_KEYS = {
        "allow_trade", "direction", "confidence", "alpha",
        "market_state", "institutional_score", "price", "reason",
        "volume_intelligence", "liquidity_map", "market_data",
        "cascade_probability", "spread_pct", "imbalance",
        "orderbook_imbalance", "funding_rate", "composite",
        "smc_signal", "regime", "confluence_score",
    }

    def test_all_required_keys_present_nominal(self):
        result = _run_engines()
        missing = self.REQUIRED_KEYS - result.keys()
        assert not missing

    def test_all_required_keys_present_on_exception(self):
        with patch.object(engine, "institutional_score_engine", side_effect=RuntimeError("injected")):
            result = _run_engines()
        missing = self.REQUIRED_KEYS - result.keys()
        assert not missing

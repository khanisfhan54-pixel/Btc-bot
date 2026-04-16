import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import alpha_liquidity_sweep_predictor as alpha

import math
import time


def _make_lsa(history_window=20):
    lsa = alpha.LiquiditySweepAlpha(history_window=history_window)
    lsa.update_liquidity_pools([60100] * 20, [59900] * 20)
    return lsa


def _make_book(price_base=60000, size=1.0, levels=10):
    return {
        'bids': [{'price': price_base - i * 10, 'size': size} for i in range(levels)],
        'asks': [{'price': price_base + i * 10, 'size': size} for i in range(levels)],
    }


def test_active_sweep_uses_standard_sigmoid():
    fast = alpha.LiquiditySweepAlpha()._fast_sigmoid(0.5)
    standard = alpha._standard_sigmoid(0.5)
    assert abs(fast - standard) > 0.01, (
        f"_fast_sigmoid ({fast:.4f}) and _standard_sigmoid ({standard:.4f}) should differ"
    )
    assert abs(fast - 0.6667) < 0.01
    assert abs(standard - 0.6225) < 0.01


def test_get_signal_no_timestamp_uses_zero():
    lsa = _make_lsa()
    book = _make_book()
    md = {
        'price': 60050,
        'prev_book': book,
        'curr_book': book,
        'trades_count': 10,
        'atr': 150,
    }
    lsa.get_signal(md)
    assert abs(lsa.last_trade_time - time.time()) > 100, (
        f"last_trade_time ({lsa.last_trade_time}) should NOT be wall-clock time"
    )


def test_predict_sweep_has_confidence_key():
    result = alpha.predict_sweep(
        {"nearest_above": {"distance_points": 50, "price": 60100},
         "nearest_below": {"distance_points": 100, "price": 59900}},
        {"state": "COMPRESSION", "compression": 0.5, "volatility": 0.005, "bias": 0.1},
    )
    assert "probability" in result, "Missing 'probability' key"
    assert "confidence" in result, "Missing 'confidence' key"
    assert result["confidence"] == result["probability"]


def test_predict_sweep_confidence_equals_probability():
    inputs = [
        ({}, {}),
        ({"nearest_above": {"distance_points": 50, "price": 60100}}, {"state": "TRENDING", "bias": 0.5}),
        ({"nearest_below": {"distance_points": 30, "price": 59900}}, {"state": "COMPRESSION", "compression": 0.8}),
        (
            {"nearest_above": {"distance_points": 10, "price": 60100},
             "nearest_below": {"distance_points": 200, "price": 59900}},
            {"state": "CHOPPY", "volatility": 0.01, "bias": -0.3},
        ),
        (
            {"nearest_above": {"distance_points": 100, "price": 60100},
             "nearest_below": {"distance_points": 100, "price": 59900}},
            {"state": "COMPRESSION", "volatility": 0.002, "compression": 0.0001, "bias": 0.0},
        ),
    ]
    for liq, ms in inputs:
        result = alpha.predict_sweep(liq, ms)
        assert result["confidence"] == result["probability"], (
            f"confidence != probability for input {liq}, {ms}"
        )


def test_vol_adj_block_fires_for_normal_btc_vol():
    liq = {
        "nearest_above": {"distance_points": 30, "price": 60100},
        "nearest_below": {"distance_points": 70, "price": 59900},
    }
    ms_vol = {"state": "CHOPPY", "volatility": 0.002, "compression": 0.04, "bias": 0.1}
    ms_no_vol = {"state": "CHOPPY", "volatility": 0.0, "compression": 0.04, "bias": 0.1}
    r_vol = alpha.predict_sweep(liq, ms_vol)
    r_no_vol = alpha.predict_sweep(liq, ms_no_vol)
    differs = (
        r_vol["prob_above"] != r_no_vol["prob_above"] or
        r_vol["prob_below"] != r_no_vol["prob_below"]
    )
    assert differs, "vol_adj block should affect output for volatility=0.002"


def test_safe_output_no_zero_probability():
    pa_clamped = alpha._clamp(-0.1, alpha.EPS, 1.0 - alpha.EPS)
    pb_clamped = alpha._clamp(1.1, alpha.EPS, 1.0 - alpha.EPS)
    assert pa_clamped > 0.0, f"clamped prob_above should be > 0, got {pa_clamped}"
    assert pb_clamped > 0.0, f"clamped prob_below should be > 0, got {pb_clamped}"
    total = pa_clamped + pb_clamped
    assert pa_clamped / total > 0.0, "normalized prob_above should be > 0 before rounding"
    result = alpha._safe_output({
        "prob_above": -0.1,
        "prob_below": 1.1,
        "action": "HOLD",
        "confidence": 0.5,
        "state": "NORMAL",
    })
    assert result["prob_above"] >= 0.0
    assert result["prob_below"] >= 0.0
    assert abs(result["prob_above"] + result["prob_below"] - 1.0) < 1e-4


def test_safe_output_no_one_probability():
    pa_clamped = alpha._clamp(1.1, alpha.EPS, 1.0 - alpha.EPS)
    pb_clamped = alpha._clamp(-0.1, alpha.EPS, 1.0 - alpha.EPS)
    assert pa_clamped < 1.0, f"clamped prob_above should be < 1, got {pa_clamped}"
    assert pb_clamped < 1.0, f"clamped prob_below should be < 1, got {pb_clamped}"
    total = pa_clamped + pb_clamped
    assert pa_clamped / total < 1.0, "normalized prob_above should be < 1 before rounding"
    result = alpha._safe_output({
        "prob_above": 1.1,
        "prob_below": -0.1,
        "action": "HOLD",
        "confidence": 0.5,
        "state": "NORMAL",
    })
    assert result["prob_above"] <= 1.0
    assert result["prob_below"] <= 1.0
    assert abs(result["prob_above"] + result["prob_below"] - 1.0) < 1e-4


def test_safe_output_garbage_input_still_bounded():
    garbage_cases = [
        {"prob_above": float('nan'), "prob_below": float('nan')},
        {"prob_above": None, "prob_below": None},
        {"prob_above": -999.0, "prob_below": -999.0},
        {"prob_above": 1e15, "prob_below": 1e15},
        {"prob_above": float('inf'), "prob_below": float('-inf')},
        {},
    ]
    for case in garbage_cases:
        case.setdefault("action", "HOLD")
        case.setdefault("confidence", 0.5)
        case.setdefault("state", "NORMAL")
        result = alpha._safe_output(case)
        pa = result["prob_above"]
        pb = result["prob_below"]
        assert 0.0 < pa < 1.0, f"prob_above {pa} not in (0,1) for {case}"
        assert 0.0 < pb < 1.0, f"prob_below {pb} not in (0,1) for {case}"
        assert abs(pa + pb - 1.0) < 1e-6, f"probs don't sum to 1.0: {pa}+{pb} for {case}"


def test_determinism_30_ticks():
    def run_30_ticks():
        lsa = _make_lsa(history_window=50)
        book = _make_book()
        ts = 1700000000.0
        results = []
        for j in range(30):
            curr = _make_book(size=1.0 + j * 0.1)
            r = lsa.get_signal({
                'price': 60050 + j * 2,
                'close_price': 60050 + j * 2,
                'prev_book': book,
                'curr_book': curr,
                'timestamp': ts + j * 1.0,
                'trades_count': 10 + j,
                'curr_depth': 100,
                'atr': 150,
                'ema_fast': 60080,
                'ema_slow': 60050,
            })
            results.append(r)
            book = curr
        return results

    r1 = run_30_ticks()
    r2 = run_30_ticks()
    for i, (a, b) in enumerate(zip(r1, r2)):
        assert a == b, f"Tick {i} differs: {a} vs {b}"


def test_extreme_values_no_crash():
    lsa = _make_lsa()
    book = _make_book()
    md = {
        'price': 1e-6,
        'close_price': 1e-6,
        'prev_book': book,
        'curr_book': book,
        'timestamp': 1700000000.0,
        'trades_count': int(1e6),
        'curr_depth': 100,
        'atr': 1e6,
        'ema_fast': 1e-6,
        'ema_slow': 1e-6,
        'pre_sweep_depth': 1e15,
    }
    r = lsa.get_signal(md)
    assert isinstance(r, dict)
    for key in ("action", "confidence", "state", "prob_above", "prob_below"):
        assert key in r, f"Missing key: {key}"
    assert 0.0 <= r["confidence"] <= 1.0
    assert abs(r["prob_above"] + r["prob_below"] - 1.0) < 1e-4

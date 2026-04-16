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


def _warm_up_lsa(lsa, ticks=25, ts_start=1700000000.0):
    book = _make_book()
    for j in range(ticks):
        curr = _make_book(size=1.0 + j * 0.1)
        lsa.get_signal({
            'price': 60050 + j * 2,
            'close_price': 60050 + j * 2,
            'prev_book': book,
            'curr_book': curr,
            'timestamp': ts_start + j * 1.0,
            'trades_count': 10 + j,
            'curr_depth': 100,
            'atr': 150,
            'ema_fast': 60080,
            'ema_slow': 60050,
        })
        book = curr
    return book


# =====================================================
# Patch 1: Missing liquidity distance tests
# =====================================================

def test_missing_distance_above_treated_as_missing():
    result = alpha.predict_sweep(
        {"nearest_above": {"price": 60100}, "nearest_below": {"distance_points": 100, "price": 59900}},
        {}
    )
    assert result["prob_above"] < 0.8, (
        f"Missing dist_above should not cause extreme bias, got prob_above={result['prob_above']}"
    )


def test_missing_distance_below_treated_as_missing():
    result = alpha.predict_sweep(
        {"nearest_above": {"distance_points": 100, "price": 60100}, "nearest_below": {"price": 59900}},
        {}
    )
    assert result["prob_below"] < 0.8, (
        f"Missing dist_below should not cause extreme bias, got prob_below={result['prob_below']}"
    )


def test_missing_both_distances_neutral():
    result = alpha.predict_sweep(
        {"nearest_above": {"price": 60100}, "nearest_below": {"price": 59900}},
        {}
    )
    assert 0.4 <= result["prob_above"] <= 0.6, (
        f"Both distances missing should be neutral, got prob_above={result['prob_above']}"
    )
    assert 0.4 <= result["prob_below"] <= 0.6, (
        f"Both distances missing should be neutral, got prob_below={result['prob_below']}"
    )


def test_nan_distance_treated_as_missing():
    result = alpha.predict_sweep(
        {"nearest_above": {"distance_points": float('nan'), "price": 60100},
         "nearest_below": {"distance_points": 100, "price": 59900}},
        {}
    )
    assert result["prob_above"] < 0.8, (
        f"NaN dist_above should not cause extreme bias, got prob_above={result['prob_above']}"
    )


def test_string_distance_treated_as_missing():
    result = alpha.predict_sweep(
        {"nearest_above": {"distance_points": "bad", "price": 60100},
         "nearest_below": {"distance_points": 100, "price": 59900}},
        {}
    )
    assert result["prob_above"] < 0.8, (
        f"String dist_above should not cause extreme bias, got prob_above={result['prob_above']}"
    )


def test_valid_zero_distance_still_works():
    result = alpha.predict_sweep(
        {"nearest_above": {"distance_points": 0.0, "price": 60100},
         "nearest_below": {"distance_points": 100, "price": 59900}},
        {}
    )
    assert result["prob_above"] > 0.7, (
        f"Valid zero distance should produce directional bias, got prob_above={result['prob_above']}"
    )


def test_try_float_returns_none_for_invalid():
    assert alpha._try_float(None) is None
    assert alpha._try_float("bad") is None
    assert alpha._try_float(float('nan')) is None
    assert alpha._try_float(float('inf')) is None
    assert alpha._try_float(42.0) == 42.0
    assert alpha._try_float(0) == 0.0
    assert alpha._try_float(-5.0) == -5.0


# =====================================================
# Patch 2: predict_sweep EPS floor tests
# =====================================================

def test_predict_sweep_prob_never_exact_zero():
    result = alpha.predict_sweep(
        {"nearest_above": {"distance_points": 0.001, "price": 60100},
         "nearest_below": {"distance_points": 99999, "price": 59900}},
        {}
    )
    assert result["prob_below"] > 0, (
        f"prob_below should never be exactly 0, got {result['prob_below']}"
    )


def test_predict_sweep_prob_never_exact_one():
    result = alpha.predict_sweep(
        {"nearest_above": {"distance_points": 0.001, "price": 60100},
         "nearest_below": {"distance_points": 99999, "price": 59900}},
        {}
    )
    assert result["prob_above"] < 1.0, (
        f"prob_above should never be exactly 1.0, got {result['prob_above']}"
    )


# =====================================================
# Patch 3: Compression dynamic range test
# =====================================================

def test_compression_has_dynamic_range():
    lsa = _make_lsa(history_window=50)
    book = _warm_up_lsa(lsa, ticks=25)

    base_md = {
        'price': 60050,
        'close_price': 60050,
        'curr_book': book,
        'prev_book': book,
        'timestamp': 1700000030.0,
        'trades_count': 40,
        'curr_depth': 100,
        'ema_fast': 60080,
        'ema_slow': 60050,
        'bid_depth': 50.0,
        'ask_depth': 50.0,
    }

    ofi_z = 0.5
    hawkes = 1.0
    hawkes_delta = 0.1

    md_low_atr = {**base_md, 'atr': 50}
    md_high_atr = {**base_md, 'atr': 300}

    result_low = lsa._predict_next_sweep(md_low_atr, ofi_z, hawkes, hawkes_delta)
    result_high = lsa._predict_next_sweep(md_high_atr, ofi_z, hawkes, hawkes_delta)

    diff = abs(result_low["prob_up"] - result_high["prob_up"])
    assert diff > 0.01, (
        f"Compression should discriminate ATR 50 vs 300, got diff={diff:.6f}"
    )


# =====================================================
# Patch 4: reaction_score full range test
# =====================================================

def test_reaction_score_full_range():
    low = alpha._standard_sigmoid((0.0 - 0.5) * 4.0)
    high = alpha._standard_sigmoid((1.0 - 0.5) * 4.0)
    assert low < 0.15, f"All features at 0 should give sigmoid < 0.15, got {low:.4f}"
    assert high > 0.85, f"All features at 1 should give sigmoid > 0.85, got {high:.4f}"


# =====================================================
# Patch 5: Warmup additive penalty test
# =====================================================

def test_warmup_penalty_additive():
    ensemble_score = 0.8
    warmup_factor = 0.5
    result = max(0.0, ensemble_score - 0.15 * (1.0 - warmup_factor))
    assert result > 0.7, (
        f"Additive warmup penalty should preserve score > 0.7, got {result}"
    )
    old_result = ensemble_score * warmup_factor
    assert old_result < 0.7, (
        f"Old multiplicative should be < 0.7 for comparison, got {old_result}"
    )


# =====================================================
# Patch 6: Harden update_liquidity_pools tests
# =====================================================

def test_update_pools_none_in_list_no_crash():
    lsa = alpha.LiquiditySweepAlpha()
    lsa.update_liquidity_pools([60100, None, 60050], [59900, None, 59850])
    assert lsa.liquidity_pools['high'] == 60100
    assert lsa.liquidity_pools['low'] == 59850


def test_update_pools_string_in_list_no_crash():
    lsa = alpha.LiquiditySweepAlpha()
    lsa.update_liquidity_pools([60100, "bad", 60050], [59900, "bad", 59850])
    assert lsa.liquidity_pools['high'] == 60100
    assert lsa.liquidity_pools['low'] == 59850


def test_update_pools_inf_filtered():
    lsa = alpha.LiquiditySweepAlpha()
    lsa.update_liquidity_pools([60100, float('inf')], [59900, float('-inf')])
    assert lsa.liquidity_pools['high'] == 60100
    assert lsa.liquidity_pools['low'] == 59900


def test_update_pools_nan_only_no_update():
    lsa = alpha.LiquiditySweepAlpha()
    lsa.update_liquidity_pools([float('nan')], [float('nan')])
    assert lsa.liquidity_pools['high'] is None
    assert lsa.liquidity_pools['low'] is None


def test_update_pools_mixed_garbage():
    lsa = alpha.LiquiditySweepAlpha()
    lsa.update_liquidity_pools(
        [None, float('nan'), "x", float('inf'), 60100],
        [None, float('nan'), "x", float('-inf'), 59900]
    )
    assert lsa.liquidity_pools['high'] == 60100
    assert lsa.liquidity_pools['low'] == 59900


# =====================================================
# Patch 7: ML sweep probability volatility discrimination
# =====================================================

def test_ml_sweep_prob_volatility_discriminates():
    lsa = alpha.LiquiditySweepAlpha()
    low_vol = lsa._ml_sweep_probability({
        "ofi": 1.0, "hawkes": 0.5, "volatility": 0.002, "depth": 100
    })
    high_vol = lsa._ml_sweep_probability({
        "ofi": 1.0, "hawkes": 0.5, "volatility": 0.05, "depth": 100
    })
    diff = abs(low_vol - high_vol)
    assert diff >= 0.01, (
        f"ML sweep prob should discriminate vol 0.002 vs 0.05, got diff={diff:.6f}"
    )


# =====================================================
# Integration tests
# =====================================================

def test_30_tick_determinism_v2():
    def run_30_ticks():
        lsa = _make_lsa(history_window=50)
        book = _make_book()
        ts = 1700000000.0
        results = []
        for j in range(30):
            curr = _make_book(size=1.0 + j * 0.1)
            md = {
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
            }
            if j % 7 == 0:
                md['macro_liquidity'] = {
                    "nearest_above": {"price": 60200},
                    "nearest_below": {"distance_points": 80, "price": 59800},
                }
            r = lsa.get_signal(md)
            results.append(r)
            book = curr
        return results

    r1 = run_30_ticks()
    r2 = run_30_ticks()
    for i, (a, b) in enumerate(zip(r1, r2)):
        assert a == b, f"Tick {i} differs: {a} vs {b}"


def test_extreme_values_after_v2_patches():
    lsa = _make_lsa()
    book = _make_book()

    extreme_cases = [
        {
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
        },
        {
            'price': 60000,
            'prev_book': book,
            'curr_book': book,
            'timestamp': 1700000001.0,
            'trades_count': 5,
            'atr': 150,
            'macro_liquidity': {
                "nearest_above": {"distance_points": float('nan'), "price": 60100},
                "nearest_below": {"distance_points": "bad", "price": 59900},
            },
        },
        {
            'price': 60000,
            'prev_book': book,
            'curr_book': book,
            'timestamp': 1700000002.0,
            'trades_count': 5,
            'atr': 150,
        },
    ]

    for md in extreme_cases:
        r = lsa.get_signal(md)
        assert isinstance(r, dict), f"Output should be dict for {md.get('price')}"
        for key in ("action", "confidence", "state", "prob_above", "prob_below"):
            assert key in r, f"Missing key: {key}"
        assert 0.0 <= r["confidence"] <= 1.0, f"confidence out of range: {r['confidence']}"
        assert abs(r["prob_above"] + r["prob_below"] - 1.0) < 1e-3, (
            f"probs don't sum to 1: {r['prob_above']} + {r['prob_below']}"
        )
        assert r["action"] in ("BUY", "SELL", "HOLD")

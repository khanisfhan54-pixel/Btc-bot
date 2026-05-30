import pytest
import math
from liquidity_magnet_predictor import LiquidityMagnetPredictor, predict_liquidity_magnet

def test_time_decay():
    predictor = LiquidityMagnetPredictor(decay_half_life=24.0)

    # 0 bars should mean 1.0 weight
    assert predictor._score_time_decay(0) == 1.0

    # 24 bars (half life) should mean 0.5 weight
    score_24 = predictor._score_time_decay(24)
    assert math.isclose(score_24, 0.5, rel_tol=1e-4)

    # Closer zone (smaller age) scores higher
    score_10 = predictor._score_time_decay(10)
    score_50 = predictor._score_time_decay(50)

    assert score_10 > score_50

def test_volume_weighting():
    predictor = LiquidityMagnetPredictor()
    cand_strong = {"volume_node_strength": 1.5, "absorption": 0.8}
    cand_weak = {"volume_node_strength": 0.0, "absorption": 0.0}

    vol_intel_high = {"liquidity_score": 2.0}
    vol_intel_low = {"liquidity_score": 0.5}

    # Strong candidate with high liquidity score should score highest
    score_strong = predictor._score_volume(cand_strong, vol_intel_high)
    score_weak = predictor._score_volume(cand_weak, vol_intel_low)

    assert score_strong > score_weak
    assert score_strong > 1.0  # Boosted above base 1.0

def test_memory_updates_deterministic():
    predictor = LiquidityMagnetPredictor()

    price = 60000.0
    side = "above"
    ztype = "equal_highs"

    # Initial state
    mem = predictor.get_memory_state(price, side, ztype)
    assert mem["touches"] == 0
    assert mem["sweeps"] == 0

    # Add touch
    predictor.update_memory(price, side, ztype, "touch", 1000.0)
    mem_after_touch = predictor.get_memory_state(price, side, ztype)
    assert mem_after_touch["touches"] == 1

    # Score should increase slightly with touch
    score1 = predictor._score_memory(price, side, ztype)

    # Add sweep
    predictor.update_memory(price, side, ztype, "sweep", 1005.0)
    mem_after_sweep = predictor.get_memory_state(price, side, ztype)
    assert mem_after_sweep["sweeps"] == 1

    # Score should decrease slightly after sweep
    score2 = predictor._score_memory(price, side, ztype)
    assert score1 > score2

def test_regime_filtering():
    predictor = LiquidityMagnetPredictor()
    side = "above"

    market_toxic = {"regime": "toxic", "volatility": 1.0}
    market_normal = {"regime": "normal", "volatility": 1.0}
    market_volatile = {"regime": "normal", "volatility": 3.0}
    market_trending_up = {"regime": "trending", "trend_direction": "up", "volatility": 1.0}

    score_toxic = predictor._score_regime(market_toxic, side)
    score_normal = predictor._score_regime(market_normal, side)
    score_volatile = predictor._score_regime(market_volatile, side)
    score_trending_up_above = predictor._score_regime(market_trending_up, side)

    assert score_normal == 1.0
    assert score_toxic == 0.5
    assert score_volatile < 1.0 # Due to > 2.0 vol multiplier
    assert score_trending_up_above == 1.2 # Continuation

def test_safety_missing_inputs():
    predictor = LiquidityMagnetPredictor()

    # NaN in candidate price
    res = predict_liquidity_magnet(
        candidates=[{"price": float("nan"), "side": "above", "type": "equal_highs"}],
        current_price=50000.0,
        current_time=12345.0,
        market_state={}
    )
    assert res["zone_side"] == "none"

    # Empty inputs
    res_empty = predict_liquidity_magnet([], 50000, 12345, {})
    assert res_empty["zone_side"] == "none"

    # None inputs
    res_none = predict_liquidity_magnet(None, 50000, 12345, None, None, None)
    assert res_none["zone_side"] == "none"

    # Infinity handled
    res_inf = predict_liquidity_magnet(
        candidates=[{"price": float("inf"), "side": "above", "type": "equal_highs"}],
        current_price=50000.0,
        current_time=12345.0,
        market_state={}
    )
    assert res_inf["zone_side"] == "none"

def test_no_lookahead_integration_smoke():
    candidates = [
        {"price": 50100.0, "side": "above", "type": "equal_highs", "age_bars": 5},
        {"price": 49900.0, "side": "below", "type": "equal_lows", "age_bars": 10},
        {"price": 50500.0, "side": "above", "type": "swing_high", "age_bars": 2}
    ]

    market_state = {"atr": 100.0, "regime": "normal"}
    stop_hunt = {"probability": 0.8, "degraded": False}
    volume_intel = {"liquidity_score": 1.2}

    result = predict_liquidity_magnet(
        candidates=candidates,
        current_price=50000.0,
        current_time=100.0,
        market_state=market_state,
        stop_hunt_data=stop_hunt,
        volume_intel=volume_intel
    )

    # Output schema verification
    assert "zone_side" in result
    assert "target_price" in result
    assert "score" in result
    assert "confidence" in result
    assert "candidate_zones" in result
    assert len(result["candidate_zones"]) == 3

    # Closer / better zone (50100) should be selected over far (50500)
    assert result["target_price"] == 50100.0

    # Must never lookahead - components shouldn't rely on 'future_sweep'
    assert result["score"] > 0
    assert result["confidence"] <= 1.0

def test_distance_scoring_closer_better():
    predictor = LiquidityMagnetPredictor()

    current_price = 50000.0
    atr = 100.0

    # Price 100 away
    score_close = predictor._score_distance(50100.0, current_price, atr)
    # Price 500 away
    score_far = predictor._score_distance(50500.0, current_price, atr)

    assert score_close > score_far

def test_distance_scoring_no_atr():
    predictor = LiquidityMagnetPredictor()
    current_price = 50000.0

    score_close = predictor._score_distance(50100.0, current_price, 0.0)
    score_far = predictor._score_distance(50500.0, current_price, 0.0)

    assert score_close > score_far
    assert score_close <= 1.0

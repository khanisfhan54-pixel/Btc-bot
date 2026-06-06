import pytest
import math
import threading
from liquidity_magnet_predictor import (
    LiquidityMagnetPredictor,
    MagnetPrediction,
    predict_liquidity_magnet,
)

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
    score1 = predictor._score_memory(price, side, ztype, 1000.0)

    # Add sweep
    predictor.update_memory(price, side, ztype, "sweep", 1005.0)
    mem_after_sweep = predictor.get_memory_state(price, side, ztype)
    assert mem_after_sweep["sweeps"] == 1

    # Score should decrease slightly after sweep
    score2 = predictor._score_memory(price, side, ztype, 1005.0)
    assert score1 > score2

def test_regime_filtering():
    predictor = LiquidityMagnetPredictor()
    side = "above"

    market_toxic = {"regime": "toxic", "volatility": 1.0, "trend_direction": "none"}
    market_normal = {"regime": "normal", "volatility": 1.0, "trend_direction": "none"}
    market_volatile = {"regime": "normal", "volatility": 3.0, "trend_direction": "none"}
    market_trending_up = {"regime": "trending", "trend_direction": "up", "volatility": 1.0}

    score_toxic = predictor._score_regime(market_toxic, side)
    score_normal = predictor._score_regime(market_normal, side)
    score_volatile = predictor._score_regime(market_volatile, side)
    score_trending_up_above = predictor._score_regime(market_trending_up, side)

    assert score_normal == 1.0
    assert score_toxic == 0.0
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

    # Regression protection for singleton-only usage: direct helper scoring must
    # receive an explicit persistent predictor instance instead of creating a
    # stateless fallback.
    result = predict_liquidity_magnet(
        candidates=candidates,
        current_price=50000.0,
        current_time=100.0,
        market_state=market_state,
        stop_hunt_data=stop_hunt,
        volume_intel=volume_intel,
        predictor_instance=LiquidityMagnetPredictor(),
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


def _full_market_state(**overrides):
    state = {"atr": 100.0, "regime": "normal", "volatility": 1.0, "trend_direction": "none"}
    state.update(overrides)
    return state


def _candidate(**overrides):
    cand = {
        "price": 50100.0,
        "side": "above",
        "type": "equal_highs",
        "age_bars": 0.0,
        "base_strength": 1.0,
    }
    cand.update(overrides)
    return cand


def test_fix01_memory_eviction_bounded_and_oldest_first():
    predictor = LiquidityMagnetPredictor(memory_maxlen=2, zone_price_bucket=1.0)

    predictor.update_memory(60000.0, "above", "equal_highs", "touch", 1.0)
    predictor.update_memory(60001.0, "above", "equal_highs", "touch", 2.0)
    predictor.update_memory(60002.0, "above", "equal_highs", "touch", 3.0)

    assert len(predictor.zone_memory) == 2
    assert "above_equal_highs_60000.00" not in predictor.zone_memory
    assert list(predictor.zone_memory.keys()) == [
        "above_equal_highs_60001.00",
        "above_equal_highs_60002.00",
    ]


def test_fix02_valid_interactions_increment_and_unknown_raises():
    predictor = LiquidityMagnetPredictor(zone_price_bucket=1.0)
    price = 60000.0

    for interaction, key in [
        ("touch", "touches"),
        ("rejection", "rejections"),
        ("sweep", "sweeps"),
        ("breakout", "breakouts"),
    ]:
        predictor.update_memory(price, "above", "equal_highs", interaction, 1.0)
        assert predictor.get_memory_state(price, "above", "equal_highs")[key] == 1

    with pytest.raises(ValueError, match="Unknown interaction"):
        predictor.update_memory(price, "above", "equal_highs", "fake", 2.0)


def test_fix03_warning_when_no_persistent_instance(caplog):
    caplog.set_level("WARNING")

    result = predict_liquidity_magnet(
        [_candidate()],
        50000.0,
        1.0,
        _full_market_state(),
    )

    # Critical singleton-only fix: before this helper silently instantiated a
    # fresh stateless predictor; after it fails closed and requires callers to
    # pass the singleton-managed persistent instance.
    assert result["zone_side"] == "none"
    assert "fail-closed neutral prediction" in caplog.text


def test_fix03_concurrent_update_memory_calls_do_not_raise():
    predictor = LiquidityMagnetPredictor(memory_maxlen=1000, zone_price_bucket=1.0)
    errors = []

    def worker(offset):
        try:
            for i in range(50):
                predictor.update_memory(60000.0 + offset + i, "above", "equal_highs", "touch", float(i))
        except Exception as exc:  # pragma: no cover - assertion inspects collection
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n * 100,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(predictor.zone_memory) <= predictor.memory_maxlen


def test_fix04_atr_zero_fallback_is_configurable_and_warns(caplog):
    predictor = LiquidityMagnetPredictor(fallback_pct_scale=50.0)
    caplog.set_level("WARNING")

    score_close = predictor._score_distance(50100.0, 50000.0, 0.0)
    score_far = predictor._score_distance(50500.0, 50000.0, 0.0)

    assert score_close > score_far
    assert "ATR is zero" in caplog.text


def test_fix05_memory_score_saturates_and_empty_is_neutral():
    predictor = LiquidityMagnetPredictor(zone_price_bucket=1.0)

    assert predictor._score_memory(60000.0, "above", "equal_highs", 1.0) == 1.0

    for i in range(100):
        predictor.update_memory(60000.0, "above", "equal_highs", "touch", float(i))

    assert predictor._score_memory(60000.0, "above", "equal_highs", 99.0) < 2.0


def test_fix06_confidence_normalized_and_should_trade_absent():
    predictor = LiquidityMagnetPredictor()
    typical = predictor.predict([_candidate()], 50000.0, 1.0, _full_market_state())
    extreme = predictor.predict(
        [_candidate(base_strength=1000.0, volume_node_strength=1000.0, absorption=1000.0)],
        50100.0,
        1.0,
        _full_market_state(regime="trending", trend_direction="up"),
        stop_hunt_data={"probability": 1.0},
        volume_intel={"liquidity_score": 2.0},
    )

    assert 0.0 <= typical["confidence"] <= 1.0
    assert 0.0 <= extreme["confidence"] <= 1.0
    assert "should_trade" not in typical
    assert "should_trade" not in extreme


def test_fix07_memory_effect_time_decays():
    fresh = LiquidityMagnetPredictor(zone_price_bucket=1.0)
    stale = LiquidityMagnetPredictor(zone_price_bucket=1.0)

    for predictor, interaction_time in [(fresh, 200.0), (stale, 0.0)]:
        predictor.update_memory(60000.0, "above", "equal_highs", "touch", interaction_time)
        predictor.update_memory(60000.0, "above", "equal_highs", "rejection", interaction_time)

    assert fresh._score_memory(60000.0, "above", "equal_highs", 200.0) > stale._score_memory(
        60000.0, "above", "equal_highs", 200.0
    )


def test_fix08_zone_price_bucket_groups_nearby_prices():
    predictor = LiquidityMagnetPredictor(zone_price_bucket=10.0)

    assert predictor._get_zone_key(60000.01, "above", "equal_highs") == predictor._get_zone_key(
        60003.0, "above", "equal_highs"
    )
    assert predictor._get_zone_key(60000.0, "above", "equal_highs") != predictor._get_zone_key(
        60015.0, "above", "equal_highs"
    )


def test_fix09_volume_intel_zone_backfill_increases_volume_effect():
    predictor = LiquidityMagnetPredictor()
    base = predictor.predict([_candidate(price=50100.0)], 50000.0, 1.0, _full_market_state())
    boosted = predictor.predict(
        [_candidate(price=50100.0)],
        50000.0,
        1.0,
        _full_market_state(),
        volume_intel={
            "liquidity_score": 1.0,
            "zones": {"50100.0": {"volume_node_strength": 1.0, "absorption": 1.0}},
        },
    )

    assert boosted["components"]["volume_effect"] > base["components"]["volume_effect"]


def test_fix10_horizon_bars_is_capped_and_atr_zero_is_finite():
    predictor = LiquidityMagnetPredictor(max_horizon_bars=5)
    capped = predictor.predict([_candidate(price=60000.0)], 50000.0, 1.0, _full_market_state(atr=1.0))
    atr_zero = predictor.predict([_candidate(price=60000.0)], 50000.0, 1.0, _full_market_state(atr=0.0))

    assert capped["horizon_bars"] <= 5
    assert atr_zero["horizon_bars"] in {1, 5}


def test_fix11_missing_trend_direction_warns_and_regime_effect_neutral(caplog):
    predictor = LiquidityMagnetPredictor()
    caplog.set_level("WARNING")

    result = predictor.predict(
        [_candidate()],
        50000.0,
        1.0,
        {"atr": 100.0, "regime": "trending", "volatility": 1.0},
    )

    assert "trend_direction" in caplog.text
    assert result["components"]["regime_effect"] == 1.0


def test_fix12_probability_removed_and_sweep_likelihood_present():
    predictor = LiquidityMagnetPredictor()
    result = predictor.predict([_candidate()], 50000.0, 1.0, _full_market_state())

    assert "probability" not in result
    assert "sweep_likelihood_estimate" in result
    assert 0.0 <= result["sweep_likelihood_estimate"] <= 1.0


def test_fix13_none_base_strength_defaults_to_neutral():
    predictor = LiquidityMagnetPredictor()
    neutral = predictor.predict([_candidate(base_strength=1.0)], 50000.0, 1.0, _full_market_state())
    none_strength = predictor.predict([_candidate(base_strength=None)], 50000.0, 1.0, _full_market_state())

    assert none_strength["score"] == neutral["score"]


def test_fix14_prediction_and_empty_prediction_return_typed_keys():
    predictor = LiquidityMagnetPredictor()
    required = set(MagnetPrediction.__annotations__)

    prediction = predictor.predict([_candidate()], 50000.0, 1.0, _full_market_state())
    empty = predictor._empty_prediction()

    assert set(prediction) == required
    assert set(empty) == required


def test_fix15_predict_emits_debug_logs(caplog):
    predictor = LiquidityMagnetPredictor()
    caplog.set_level("DEBUG")

    predictor.predict([_candidate()], 50000.0, 1.0, _full_market_state())

    assert "predict liquidity magnet start" in caplog.text
    assert "predict liquidity magnet end" in caplog.text


def test_fix16_memory_state_fetched_once(monkeypatch):
    """get_memory_state must be called exactly once for the top candidate in predict."""
    predictor = LiquidityMagnetPredictor()
    call_count = {"n": 0}
    original = predictor.get_memory_state

    def counting_get_memory_state(price, side, zone_type):
        call_count["n"] += 1
        return original(price, side, zone_type)

    monkeypatch.setattr(predictor, "get_memory_state", counting_get_memory_state)
    candidates = [{"price": 60000.0, "side": "above", "type": "swing_high", "age_bars": 5, "base_strength": 1.0}]
    market_state = {"regime": "normal", "volatility": 1.0, "trend_direction": "up", "atr": 100.0}
    predictor.predict(candidates, 59900.0, 100.0, market_state)
    assert call_count["n"] == 1, f"Expected 1 call to get_memory_state, got {call_count['n']}"

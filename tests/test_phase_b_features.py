import inspect
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import alpha_liquidity_sweep_predictor as alpha


def _book(price=100.0, bid_size=10.0, ask_size=10.0, levels=10):
    return {
        "bids": [
            {"price": price - i, "size": bid_size}
            for i in range(levels)
        ],
        "asks": [
            {"price": price + i, "size": ask_size}
            for i in range(levels)
        ],
    }


def _level_bid_delta_books(level, delta, price=100.0):
    prev = _book(price=price)
    curr = _book(price=price)
    curr["bids"][level]["size"] += delta
    return prev, curr


def _opposing_delta_books(price=100.0):
    prev = _book(price=price)
    curr = _book(price=price)
    curr["bids"][0]["size"] += 2.0
    curr["asks"][5]["size"] += 6.0
    return prev, curr


def _market_data(price=100.0, timestamp=1_700_000_000.0):
    return {
        "price": price,
        "close_price": price,
        "prev_book": _book(price=price),
        "curr_book": _book(price=price),
        "timestamp": timestamp,
        "trades_count": 0,
        "pre_sweep_depth": 100.0,
        "curr_depth": 100.0,
        "sweep_time_elapsed": 1.0,
        "atr": 1.0,
        "ema_fast": price,
        "ema_slow": price,
    }


def _warm_ofi(alpha_model, count=20):
    prev = _book()
    curr = _book()
    for _ in range(count):
        alpha_model.calculate_ofi_zscore(prev, curr)


def test_level_weighted_ofi_direction():
    level0 = alpha.LiquiditySweepAlpha(history_window=100)
    _warm_ofi(level0)
    prev0, curr0 = _level_bid_delta_books(level=0, delta=10.0)
    z0 = level0.calculate_ofi_zscore(prev0, curr0)
    contrib0 = level0.ofi_history[-1]

    level9 = alpha.LiquiditySweepAlpha(history_window=100)
    _warm_ofi(level9)
    prev9, curr9 = _level_bid_delta_books(level=9, delta=10.0)
    z9 = level9.calculate_ofi_zscore(prev9, curr9)
    contrib9 = level9.ofi_history[-1]

    assert z0 > 0.0
    assert z9 > 0.0
    assert contrib9 / contrib0 == pytest.approx(0.1, rel=0.01)


def test_level_weighted_ofi_magnitude_ordering():
    model = alpha.LiquiditySweepAlpha(history_window=100)
    _warm_ofi(model, count=20)
    prev, curr = _opposing_delta_books()

    cumulative_z = 0.0
    for _ in range(10):
        cumulative_z += model.calculate_ofi_zscore(prev, curr)

    assert model.ofi_history[-1] > 0.0
    assert cumulative_z > 0.0


def test_ofi_weighting_label_in_metrics():
    metrics = alpha.LiquiditySweepAlpha().get_state_metrics()

    assert metrics["ofi_level_weighting"] == "cont_2014_decay"


def test_pool_age_tracked_on_update():
    model = alpha.LiquiditySweepAlpha()
    for i in range(50):
        model.get_signal(_market_data(timestamp=1_700_000_000.0 + i))

    model.update_liquidity_pools([100.0, 101.0], [90.0, 89.0])
    metrics = model.get_state_metrics()

    assert metrics["pool_age_high_bars"] == 0


def test_stale_pool_expires_to_normal():
    model = alpha.LiquiditySweepAlpha(pool_max_age_bars=10)
    model.update_liquidity_pools([100.0], [50.0])

    for i in range(11):
        model.get_signal(_market_data(price=80.0, timestamp=1_700_000_000.0 + i))

    assert model.detect_sweep_state(price=99.5, atr=1.0, hawkes_intensity=100.0) == "NORMAL"
    assert model.liquidity_pools["high"] is None
    assert model.liquidity_pools["low"] is None


def test_pool_bar_reset_on_atr_expiry():
    model = alpha.LiquiditySweepAlpha()
    model.update_liquidity_pools([100.0], [50.0])

    assert model._pool_set_bar["high"] is not None
    assert model.detect_sweep_state(price=120.0, atr=1.0, hawkes_intensity=0.0) == "NORMAL"
    assert model._pool_set_bar["high"] is None


def test_pool_max_age_bars_validation():
    with pytest.raises(ValueError):
        alpha.LiquiditySweepAlpha(pool_max_age_bars=5)


def test_pool_age_in_metrics():
    model = alpha.LiquiditySweepAlpha()
    model.update_liquidity_pools([100.0], [50.0])
    for i in range(7):
        model.get_signal(_market_data(price=75.0, timestamp=1_700_000_000.0 + i))

    metrics = model.get_state_metrics()

    assert metrics["pool_age_high_bars"] == 7
    assert metrics["pool_age_low_bars"] == 7


def test_constructor_pool_max_age_exposed():
    model = alpha.LiquiditySweepAlpha(pool_max_age_bars=150)

    assert model.get_state_metrics()["pool_max_age_bars"] == 150


def test_phase_a_items_still_present():
    assert "branching_ratio" in alpha.LiquiditySweepAlpha().get_state_metrics()
    assert hasattr(alpha, "_shrink_prob")
    assert "hawkes_decay" in inspect.signature(alpha.LiquiditySweepAlpha).parameters

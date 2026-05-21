from stop_hunt_engine.data.candle_store import Candle
from stop_hunt_engine.features.pool_distance import compute_pool_distance


def test_pool_distance_at_bar_zero() -> None:
    candles = [Candle(timestamp=0, open=100, high=110, low=90, close=105, volume=10)]
    result = compute_pool_distance(0, candles)
    assert result.stale is False
    assert result.dist_to_high_pool_pct >= 0.0
    assert result.dist_to_low_pool_pct >= 0.0


def test_pool_distance_empty_candles() -> None:
    result = compute_pool_distance(0, [])
    assert result.stale is True

import pytest

from stop_hunt_engine.data.candle_store import Candle
from stop_hunt_engine.data.l2_snapshot import BookLevel, L2Snapshot
from stop_hunt_engine.integrations.feature_pipeline import PipelineInput, build_feature_vector


def _candles(n: int = 5):
    return [
        Candle(timestamp=1_700_000_000 + i * 300, open=50000, high=50010, low=49990, close=50000, volume=100)
        for i in range(n)
    ]


def test_clock_skew_raises_value_error() -> None:
    candles = _candles()
    stale_snap = L2Snapshot(
        timestamp=candles[-1].timestamp - 9999,
        bids=(BookLevel(49999, 1.0),),
        asks=(BookLevel(50001, 1.0),),
    )
    payload = PipelineInput(candles, [stale_snap], [], [], [], {})
    with pytest.raises(ValueError, match="timestamp mismatch"):
        build_feature_vector(payload, len(candles) - 1, max_clock_skew_sec=60)

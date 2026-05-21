"""Proves regime stale flag fires through the full pipeline path."""
from stop_hunt_engine.data.candle_store import Candle
from stop_hunt_engine.integrations.feature_pipeline import PipelineInput, build_feature_vector


def _candles(n: int = 5) -> list:
    return [
        Candle(
            timestamp=1_700_000_000.0 + i * 300,
            open=50000,
            high=50010,
            low=49990,
            close=50000,
            volume=100,
        )
        for i in range(n)
    ]


def test_regime_stale_flag_fires_through_pipeline() -> None:
    """Stale regime must propagate through map_regime_output -> compute_feature_vector."""
    candles = _candles()
    bar_ts = candles[-1].timestamp
    stale_regime = {
        "regime_label": "range",
        "confidence": 0.8,
        "timestamp": bar_ts - 600,
    }
    payload = PipelineInput(candles, [], [], [], [], stale_regime)
    fv = build_feature_vector(payload, len(candles) - 1)
    assert fv.regime.stale is True


def test_fresh_regime_not_stale_through_pipeline() -> None:
    candles = _candles()
    bar_ts = candles[-1].timestamp
    fresh_regime = {
        "regime_label": "range",
        "confidence": 0.8,
        "timestamp": bar_ts - 60,
    }
    payload = PipelineInput(candles, [], [], [], [], fresh_regime)
    fv = build_feature_vector(payload, len(candles) - 1)
    assert fv.regime.stale is False


def test_missing_regime_is_stale_through_pipeline() -> None:
    candles = _candles()
    payload = PipelineInput(candles, [], [], [], [], {})
    fv = build_feature_vector(payload, len(candles) - 1)
    assert fv.regime.stale is True

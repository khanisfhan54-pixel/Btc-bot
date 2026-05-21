"""Tests SHPE disable path works safely."""
from stop_hunt_engine.data.candle_store import Candle
from stop_hunt_engine.integrations.feature_pipeline import PipelineInput
from stop_hunt_engine.integrations.signal_adapter import get_shpe_probability


def test_shpe_disabled_via_env(monkeypatch) -> None:
    monkeypatch.setenv("SHPE_ENABLED", "false")
    candles = [Candle(timestamp=1_700_000_000.0, open=1, high=1, low=1, close=1, volume=1)]
    payload = PipelineInput(candles, [], [], [], [], {})
    out = get_shpe_probability(None, payload, 0)
    assert out["probability"] == 0.5
    assert out["degraded"] is True
    assert out["regime_used"] == "<disabled>"

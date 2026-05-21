from unittest.mock import MagicMock

from stop_hunt_engine.integrations.feature_pipeline import PipelineInput
from stop_hunt_engine.integrations.signal_adapter import get_shpe_probability


def test_fallback_on_exception() -> None:
    engine = MagicMock()
    payload = PipelineInput([], [], [], [], [], {})
    result = get_shpe_probability(engine, payload, bar_index=0)
    assert result["probability"] == 0.5
    assert result["degraded"] is True
    assert result["regime_used"] == "<error>"

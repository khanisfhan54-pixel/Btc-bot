"""Proves map_regime_output does not strip the timestamp key."""
from stop_hunt_engine.integrations.regime_adapter import map_regime_output


def test_timestamp_preserved_when_present() -> None:
    result = map_regime_output({"regime_label": "range", "timestamp": 1_700_000_000.0})
    assert "timestamp" in result
    assert result["timestamp"] == 1_700_000_000.0


def test_timestamp_defaults_to_zero_when_absent() -> None:
    result = map_regime_output({"regime_label": "range"})
    assert result.get("timestamp", -1) == 0.0


def test_empty_payload_has_timestamp_zero() -> None:
    result = map_regime_output(None)
    assert result.get("timestamp", -1) == 0.0

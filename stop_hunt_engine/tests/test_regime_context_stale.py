from stop_hunt_engine.features.regime_context import project_regime_context


def test_stale_when_regime_missing() -> None:
    result = project_regime_context(None, as_of_ts=1_700_000_000.0)
    assert result.stale is True


def test_stale_when_regime_timestamp_old() -> None:
    as_of = 1_700_010_000.0
    payload = {"regime_label": "range", "confidence": 0.8, "timestamp": as_of - 600}
    result = project_regime_context(payload, as_of_ts=as_of, stale_seconds=300)
    assert result.stale is True


def test_fresh_when_regime_recent() -> None:
    as_of = 1_700_010_000.0
    payload = {"regime_label": "range", "confidence": 0.8, "timestamp": as_of - 100}
    result = project_regime_context(payload, as_of_ts=as_of, stale_seconds=300)
    assert result.stale is False

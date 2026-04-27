import time

from alpha_orchestrator import AlphaOrchestrator, OrchestratorConfig, AlphaSignal


def test_signal_within_latency_buffer_is_accepted():
    orch = AlphaOrchestrator(OrchestratorConfig(signal_weights={"s": 1.0}, signal_ttl_seconds=1.0, pipeline_latency_buffer_ms=250.0))
    now = time.time()
    sig = AlphaSignal(source_id="s", direction=1, conviction=0.9, expected_edge_bps=10.0, timestamp=now - 1.1, timeframe="1m")
    valid, metrics, _ = orch._validate_and_prune([sig], now)
    assert len(valid) == 1
    assert metrics["stale"] == 0


def test_signal_beyond_latency_buffer_is_rejected():
    orch = AlphaOrchestrator(OrchestratorConfig(signal_weights={"s": 1.0}, signal_ttl_seconds=1.0, pipeline_latency_buffer_ms=100.0))
    now = time.time()
    sig = AlphaSignal(source_id="s", direction=1, conviction=0.9, expected_edge_bps=10.0, timestamp=now - 1.2, timeframe="1m")
    valid, metrics, _ = orch._validate_and_prune([sig], now)
    assert len(valid) == 0
    assert metrics["stale"] == 1

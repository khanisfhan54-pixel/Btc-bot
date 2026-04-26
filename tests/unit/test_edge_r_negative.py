import pytest
from alpha_orchestrator import AlphaOrchestrator, OrchestratorConfig, AlphaPerformanceStats

def test_edge_r_negative():
    o=AlphaOrchestrator(OrchestratorConfig(signal_weights={"s":1.0}))
    s=AlphaPerformanceStats(source_id="s")
    s.ema_win_rate=0.5
    s.expected_edge_bps=5.0
    s.avg_realized_edge_bps=-5.0
    neg=o._calc_score_block(s)
    s.avg_realized_edge_bps=0.0
    neu=o._calc_score_block(s)
    assert (-5.0/5.0) == pytest.approx(-1.0)
    assert neg < neu

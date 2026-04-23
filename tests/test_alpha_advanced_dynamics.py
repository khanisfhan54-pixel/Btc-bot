import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from alpha_orchestration import (
    AlphaOrchestrator,
    OrchestratorConfig,
    RegimeContext,
    FeatureQuality,
    ExecutionState,
)


@pytest.fixture
def config():
    return OrchestratorConfig(
        signal_weights={
            "alpha_good": 1.0,
            "alpha_mid": 1.0,
            "alpha1": 1.0,
            **{f"clone_{i}": 1.0 for i in range(10)},
        },
        feedback_enabled=True,
        regime_feedback_enabled=True,
        allow_unknown_sources=True,
    )


@pytest.fixture
def orchestrator(config):
    return AlphaOrchestrator(config)


@pytest.fixture
def fq():
    return FeatureQuality(staleness_ratio=0.0, missing_data_ratio=0.0)


@pytest.fixture
def exec_state():
    return ExecutionState(
        current_exposure_usd=0,
        max_exposure_usd=100000,
        current_drawdown_pct=0.0,
    )


@pytest.fixture
def regime_ctx():
    return RegimeContext("trend", 0.3, 0.8)


def test_alpha_competition(orchestrator):
    for _ in range(50):
        orchestrator.update_performance(
            {
                "source_id": "alpha_good",
                "realized_pnl": 100,
                "realized_edge_bps": 50,
                "expected_edge_bps": 50,
            }
        )

    for _ in range(50):
        orchestrator.update_performance(
            {
                "source_id": "alpha_mid",
                "realized_pnl": 10,
                "realized_edge_bps": 5,
                "expected_edge_bps": 50,
            }
        )

    good = orchestrator.performance_stats["alpha_good"]
    mid = orchestrator.performance_stats["alpha_mid"]

    assert good.current_multiplier > mid.current_multiplier


def test_correlated_alpha_does_not_dominate(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_000_000.0
    signals = [
        {
            "source_id": f"clone_{i}",
            "direction": 1,
            "conviction": 0.9,
            "expected_edge_bps": 50,
            "timestamp": ts,
            "timeframe": "1m",
        }
        for i in range(10)
    ]

    result = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)

    assert result.net_conviction < 0.9


def test_delayed_alpha_decay(orchestrator):
    for _ in range(30):
        orchestrator.update_performance(
            {
                "source_id": "alpha1",
                "realized_pnl": 100,
                "realized_edge_bps": 50,
                "expected_edge_bps": 50,
            }
        )

    for _ in range(30):
        orchestrator.update_performance(
            {
                "source_id": "alpha1",
                "realized_pnl": -100,
                "realized_edge_bps": -50,
                "expected_edge_bps": 50,
            }
        )

    stats = orchestrator.performance_stats["alpha1"]

    assert stats.current_multiplier < 1.0

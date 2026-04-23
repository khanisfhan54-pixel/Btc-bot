"""Targeted deterministic feedback + regime safety tests for AlphaOrchestrator."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from alpha_orchestrator import (
    AlphaOrchestrator,
    ExecutionState,
    FeatureQuality,
    OrchestratorConfig,
    RegimeContext,
)

FIXED_TS = 1_700_000_000.0


@pytest.fixture
def config() -> OrchestratorConfig:
    return OrchestratorConfig(
        signal_weights={"good_alpha": 1.0, "bad_alpha": 1.0},
        feedback_enabled=True,
        regime_feedback_enabled=True,
    )


@pytest.fixture
def orchestrator(config: OrchestratorConfig) -> AlphaOrchestrator:
    return AlphaOrchestrator(config)


@pytest.fixture
def fq() -> FeatureQuality:
    return FeatureQuality(staleness_ratio=0.0, missing_data_ratio=0.0)


@pytest.fixture
def exec_state() -> ExecutionState:
    return ExecutionState(
        current_exposure_usd=0.0,
        max_exposure_usd=100000.0,
        current_drawdown_pct=0.0,
    )


def strong_bullish_signal() -> dict:
    return {
        "source_id": "good_alpha",
        "direction": 1,
        "conviction": 1.0,
        "expected_edge_bps": 50,
        "timestamp": FIXED_TS,
        "timeframe": "1m",
    }


def test_bad_alpha_gets_killed(orchestrator: AlphaOrchestrator):
    for _ in range(50):
        orchestrator.update_performance(
            {
                "source_id": "bad_alpha",
                "realized_pnl": -100,
                "realized_edge_bps": -50,
                "expected_edge_bps": 50,
            }
        )

    stats = orchestrator.performance_stats["bad_alpha"]

    assert 0.5 <= stats.current_multiplier <= 1.0
    assert stats.current_multiplier < 0.8


def test_good_alpha_gets_promoted(orchestrator: AlphaOrchestrator):
    for _ in range(50):
        orchestrator.update_performance(
            {
                "source_id": "good_alpha",
                "realized_pnl": 100,
                "realized_edge_bps": 50,
                "expected_edge_bps": 50,
            }
        )

    stats = orchestrator.performance_stats["good_alpha"]

    assert 1.0 <= stats.current_multiplier <= 1.5
    assert stats.current_multiplier > 1.1


def test_no_buy_in_toxic_regime(
    orchestrator: AlphaOrchestrator,
    fq: FeatureQuality,
    exec_state: ExecutionState,
):
    signals = [strong_bullish_signal()]
    toxic = RegimeContext("toxic", 0.95, 0.1)

    result = orchestrator.orchestrate(signals, toxic, fq, exec_state, current_time=FIXED_TS)

    assert result.action.name != "BUY"
    assert result.net_conviction < 0.7

"""Stress tests for alpha orchestration engine.

These tests focus on deterministic, fail-safe behaviour under production-like
stress scenarios (MTF conflict, drawdown hard stops, malformed data,
feedback-loop adaptation, and extreme values).
"""

from __future__ import annotations

import math
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from alpha_orchestrator import (
    Action,
    AlphaOrchestrator,
    ExecutionState,
    FeatureQuality,
    OrchestratorConfig,
    RegimeContext,
)


def valid_test_config(**overrides) -> OrchestratorConfig:
    base = dict(
        signal_weights={"alpha1": 1.0, "alpha2": 1.0},
        action_threshold=0.3,
        score_deadband=0.05,
        min_liquidity_threshold=0.1,
        max_missing_data_ratio=0.8,
        max_drawdown_pct=0.15,
        risk_gamma=2.0,
        signal_ttl_seconds=120.0,
        allow_unknown_sources=False,
        default_unknown_weight=0.0,
        feedback_enabled=False,
        feedback_min_trades=2,
        feedback_max_multiplier=1.5,
        feedback_min_multiplier=0.5,
        timeframe_weights={"1m": 1.0, "5m": 1.0, "1h": 1.0, "default": 1.0},
        timeframe_order=["1m", "5m", "1h", "default"],
        higher_tf_dominance=True,
    )
    base.update(overrides)
    return OrchestratorConfig(**base)


@pytest.fixture
def orchestrator() -> AlphaOrchestrator:
    return AlphaOrchestrator(valid_test_config())


@pytest.fixture
def regime_ctx() -> RegimeContext:
    return RegimeContext("trend", 0.3, 0.8)


@pytest.fixture
def fq() -> FeatureQuality:
    return FeatureQuality(0.1, 0.1)


@pytest.fixture
def exec_state() -> ExecutionState:
    return ExecutionState(0.0, 100000.0, 0.0)


def valid_signal_bullish() -> dict:
    return {
        "source_id": "alpha1",
        "direction": 1,
        "conviction": 0.8,
        "expected_edge_bps": 50,
        "timestamp": time.time(),
        "timeframe": "1m",
    }


def strong_bullish_signal() -> dict:
    s = valid_signal_bullish()
    s["conviction"] = 1.0
    return s


def test_mtf_conflict_allows_unknown_sources_and_resolves_to_htf(regime_ctx, fq, exec_state):
    now = time.time()
    orch = AlphaOrchestrator(
        valid_test_config(
            signal_weights={"alpha1": 1.0},
            allow_unknown_sources=True,
            default_unknown_weight=1.0,
            higher_tf_dominance=True,
            timeframe_weights={"1m": 1.0, "1h": 1.0, "default": 1.0},
            timeframe_order=["1m", "1h", "default"],
        )
    )

    bullish_ltf = valid_signal_bullish()
    bullish_ltf["timestamp"] = now
    bearish_htf_unknown = {
        "source_id": "external_alpha",
        "direction": -1,
        "conviction": 1.0,
        "expected_edge_bps": 45,
        "timestamp": now,
        "timeframe": "1h",
    }

    result = orch.orchestrate([bullish_ltf, bearish_htf_unknown], regime_ctx, fq, exec_state, now)

    assert result.action == Action.SELL
    assert "external_alpha" in result.meta_info["source_policy_summary"]["unknown_sources_accepted"]


def test_feedback_loop_multiplier_respects_bounds_and_adapts(regime_ctx, fq, exec_state):
    now = time.time()
    orch = AlphaOrchestrator(
        valid_test_config(
            feedback_enabled=True,
            feedback_min_trades=1,
            feedback_max_multiplier=1.6,
            feedback_min_multiplier=0.5,
        )
    )

    for _ in range(6):
        orch.update_performance(
            {
                "source_id": "alpha1",
                "realized_pnl": 120.0,
                "realized_edge_bps": 35.0,
                "expected_edge_bps": 20.0,
                "expected_win_rate": 0.55,
            },
            fq,
            regime_ctx,
        )

    result = orch.orchestrate([strong_bullish_signal() | {"timestamp": now}], regime_ctx, fq, exec_state, now)
    multiplier = result.meta_info["alpha_performance"]["stats"]["alpha1"]["current_multiplier"]

    assert 0.5 <= multiplier <= 1.6
    assert multiplier > 1.0


def test_risk_override_drawdown_breach_forces_hold(regime_ctx, fq):
    now = time.time()
    orch = AlphaOrchestrator(valid_test_config(max_drawdown_pct=0.1))
    dd_breached_state = ExecutionState(0.0, 100000.0, 0.35)

    result = orch.orchestrate([strong_bullish_signal() | {"timestamp": now}], regime_ctx, fq, dd_breached_state, now)

    assert result.action == Action.HOLD
    assert result.net_conviction == pytest.approx(0.0)
    assert result.meta_info.get("rationale") == "dd_breach"


def test_orchestration_is_deterministic_on_stable_fields(orchestrator, regime_ctx, fq, exec_state):
    now = time.time()
    sig = strong_bullish_signal()
    sig["timestamp"] = now

    first = orchestrator.orchestrate([sig], regime_ctx, fq, exec_state, now)
    second = orchestrator.orchestrate([sig], regime_ctx, fq, exec_state, now)

    assert second.action == first.action
    assert abs(second.net_conviction - first.net_conviction) < 1e-6
    assert second.expected_edge_bps == first.expected_edge_bps


def test_extreme_values_are_clamped_and_finite(regime_ctx, fq, exec_state):
    now = time.time()
    orch = AlphaOrchestrator(valid_test_config())

    extreme = {
        "source_id": "alpha1",
        "direction": 1,
        "conviction": 10.0,
        "expected_edge_bps": 1e12,
        "timestamp": now,
        "timeframe": "1m",
    }
    invalid_nan = {
        "source_id": "alpha2",
        "direction": 1,
        "conviction": float("nan"),
        "expected_edge_bps": float("inf"),
        "timestamp": now,
        "timeframe": "5m",
    }

    result = orch.orchestrate([extreme, invalid_nan], regime_ctx, fq, exec_state, now)

    assert math.isfinite(result.net_conviction)
    assert math.isfinite(result.expected_edge_bps)
    assert 0.0 <= abs(result.expected_edge_bps) <= 1000.0


def test_invalid_signal_batch_fails_safe_to_hold(orchestrator, regime_ctx, fq, exec_state):
    now = time.time()
    bad_signals = [
        {"source_id": "", "direction": 1, "conviction": 0.5, "expected_edge_bps": 10.0, "timestamp": now, "timeframe": "1m"},
        {"source_id": "alpha1", "direction": 99, "conviction": 0.5, "expected_edge_bps": 10.0, "timestamp": now, "timeframe": "1m"},
        {"source_id": "alpha1", "direction": 1, "conviction": 0.5, "expected_edge_bps": 10.0, "timestamp": now + 10_000, "timeframe": "1m"},
    ]

    result = orchestrator.orchestrate(bad_signals, regime_ctx, fq, exec_state, now)

    assert result.action == Action.HOLD
    assert result.meta_info.get("rationale") == "no_valid_signals"


def test_regime_crash_liquidity_guard_prefers_hold(exec_state, fq):
    now = time.time()
    crash_regime = RegimeContext("crash", 0.95, 0.02)
    orch = AlphaOrchestrator(valid_test_config(min_liquidity_threshold=0.15))

    result = orch.orchestrate([strong_bullish_signal() | {"timestamp": now}], crash_regime, fq, exec_state, now)

    assert result.action == Action.HOLD
    assert result.meta_info.get("rationale") == "insufficient_liquidity"

import os
import sys
import math

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
            "strong_htf": 1.0,
            "alpha_v1": 1.0,
            "alpha_v2": 1.0,
            **{f"clone_{i}": 1.0 for i in range(60)},
            **{f"alpha_clone_{i}": 1.0 for i in range(60)},
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

    assert orchestrator.performance_stats["alpha_good"].current_multiplier > orchestrator.performance_stats["alpha_mid"].current_multiplier


def test_double_penalty_detection(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_000.0
    signals = [
        {
            "source_id": "alpha_v1",
            "direction": 1,
            "conviction": 1.0,
            "expected_edge_bps": 50,
            "timestamp": ts,
            "timeframe": "1m",
        },
        {
            "source_id": "alpha_v2",
            "direction": 1,
            "conviction": 1.0,
            "expected_edge_bps": 50,
            "timestamp": ts,
            "timeframe": "1m",
        },
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    rows = out.meta_info["per_signal_breakdown"]
    w = rows[0]["final_weight_contribution"]
    # Single application at weight level: 1.0 * (1/sqrt(2)).
    assert w == pytest.approx(1.0 / math.sqrt(2), rel=1e-6)


def test_low_conviction_spam_is_grouped(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_100.0
    signals = [
        {
            "source_id": f"alpha_clone_{i}",
            "direction": 1,
            "conviction": 0.49,
            "expected_edge_bps": 30,
            "timestamp": ts,
            "timeframe": "1m",
        }
        for i in range(50)
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    groups = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]["groups"]
    assert any(g["size"] == 50 for g in groups)
    assert any(g["penalty"] < 1.0 for g in groups)
    assert out.net_conviction < 0.8


def test_mixed_conviction_cluster_penalized(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_200.0
    signals = [
        {
            "source_id": f"alpha_clone_{i}",
            "direction": 1,
            "conviction": 1.0 if i < 5 else 0.3,
            "expected_edge_bps": 40,
            "timestamp": ts,
            "timeframe": "1m",
        }
        for i in range(10)
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    groups = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]["groups"]
    grp = next(g for g in groups if g["key"][2] == "alpha_clone")
    assert grp["size"] == 10
    assert grp["penalty"] == pytest.approx(1.0 / math.sqrt(10), rel=1e-6)
    assert out.net_conviction < 0.8


def test_grouping_correctness(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_300.0
    signals = [
        {"source_id": "alpha_v1", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_v2", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "clone_1", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "clone_2", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    groups = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]["groups"]
    keyed = {tuple(g["key"]): g["size"] for g in groups}
    assert keyed[(1, "1m", "alpha_v")] == 2
    assert keyed[(1, "1m", "clone")] == 2


def test_denom_consistency(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_400.0
    signals = [
        {"source_id": "alpha_v1", "direction": 1, "conviction": 1.0, "expected_edge_bps": 40, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "clone_1", "direction": -1, "conviction": 0.8, "expected_edge_bps": 20, "timestamp": ts, "timeframe": "1m"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    rows = out.meta_info["per_signal_breakdown"]
    denom = sum(r["final_weight_contribution"] for r in rows)
    weighted_sum = sum(r["final_weight_contribution"] * r["direction"] for r in rows)
    tf_score = out.meta_info["tf_fusion_breakdown"]["1m"]["net_score"]
    attenuation = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]["attenuation_factor"]
    assert denom > 0.0
    assert tf_score == pytest.approx((weighted_sum / denom) * attenuation, rel=1e-7)


def test_clone_spam_reduces_conviction_vs_single(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_450.0
    single = [
        {"source_id": "alpha_clone_0", "direction": 1, "conviction": 1.0, "expected_edge_bps": 40, "timestamp": ts, "timeframe": "1m"}
    ]
    spam = [
        {"source_id": f"alpha_clone_{i}", "direction": 1, "conviction": 1.0, "expected_edge_bps": 40, "timestamp": ts, "timeframe": "1m"}
        for i in range(30)
    ]
    out_single = orchestrator.orchestrate(single, regime_ctx, fq, exec_state, current_time=ts)
    out_spam = orchestrator.orchestrate(spam, regime_ctx, fq, exec_state, current_time=ts)
    assert out_spam.net_conviction < out_single.net_conviction


def test_low_conviction_not_boosted(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_460.0
    weak = [
        {"source_id": "alpha_clone_1", "direction": 1, "conviction": 0.49, "expected_edge_bps": 40, "timestamp": ts, "timeframe": "1m"}
    ]
    strong = [
        {"source_id": "alpha_clone_2", "direction": 1, "conviction": 1.0, "expected_edge_bps": 40, "timestamp": ts, "timeframe": "1m"}
    ]
    out_weak = orchestrator.orchestrate(weak, regime_ctx, fq, exec_state, current_time=ts)
    out_strong = orchestrator.orchestrate(strong, regime_ctx, fq, exec_state, current_time=ts)
    weak_w = out_weak.meta_info["per_signal_breakdown"][0]["final_weight_contribution"]
    strong_w = out_strong.meta_info["per_signal_breakdown"][0]["final_weight_contribution"]
    assert weak_w < strong_w


def test_numerical_stability(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_500.0
    signals = [
        {
            "source_id": f"alpha_clone_{i}",
            "direction": 1,
            "conviction": 1.0,
            "expected_edge_bps": 1e9,
            "timestamp": ts,
            "timeframe": "1m",
        }
        for i in range(60)
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    assert math.isfinite(out.net_conviction)
    assert math.isfinite(out.expected_edge_bps)


def test_single_signal_baseline(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_600.0
    signals = [
        {"source_id": "alpha_good", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m"}
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    assert out.net_conviction > 0.95


def test_cross_tf_safe(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_700.0
    signals = [
        {"source_id": "alpha_v1", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_v2", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1h"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    assert out.net_conviction > 0.6


def test_opposite_direction_reduces_toward_neutral(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_750.0
    signals = [
        {"source_id": "alpha_v1", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_v2", "direction": -1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    assert abs(out.net_conviction) < 0.2


def test_correlation_summary_observability(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_770.0
    signals = [
        {"source_id": f"alpha_clone_{i}", "direction": 1, "conviction": 1.0, "expected_edge_bps": 30, "timestamp": ts, "timeframe": "1m"}
        for i in range(5)
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    summary = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    assert "groups" in summary
    assert "largest_group" in summary
    assert "total_groups" in summary
    assert "attenuation_factor" in summary


def test_invalid_source_id_safe(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_800.0
    signals = [
        {"source_id": None, "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    assert out.action.name == "HOLD"


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

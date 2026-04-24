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
        correlation_min_conviction=0.5,
        correlation_min_group_size=3,
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
    assert w == pytest.approx(1.0, rel=1e-6)
    assert out.net_conviction > 0.95


def test_low_conviction_signals_do_not_trigger_group_penalty(orchestrator, regime_ctx, fq, exec_state):
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
    assert any(r["similar_signal_count"] > 1 for r in out.meta_info["per_signal_breakdown"])
    assert all(r["correlation_penalty"] == 1.0 for r in out.meta_info["per_signal_breakdown"])


def test_large_correlated_cluster_is_penalized(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_200.0
    signals = [
        {
            "source_id": f"alpha_clone_{i}",
            "direction": 1,
            "conviction": 1.0,
            "expected_edge_bps": 40,
            "timestamp": ts,
            "timeframe": "1m",
            "correlation_group_id": "cluster_x",
        }
        for i in range(10)
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    groups = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]["groups"]
    grp = next(g for g in groups if g["key"][2] == "cluster_x")
    assert grp["size"] == 10
    assert grp["penalty"] == pytest.approx(1.0 / math.sqrt(1.0 + 0.5 * (10 - 1)), rel=1e-6)
    rows = out.meta_info["per_signal_breakdown"]
    assert all(r["effective_weight_contribution"] < r["raw_weight_contribution"] for r in rows)


def test_same_family_source_ids_are_grouped_by_default(orchestrator, regime_ctx, fq, exec_state):
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
    assert keyed[(1, "1m", "alpha")] == 2
    assert keyed[(1, "1m", "clone")] == 2


def test_unrelated_sources_not_over_penalized(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_301.0
    signals = [
        {"source_id": "alpha_good", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_mid", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    summary = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    assert summary["attenuation_factor"] == pytest.approx(1.0, rel=1e-7)
    assert out.expected_edge_bps == pytest.approx(50.0, rel=1e-6)


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
    assert denom > 0.0
    assert tf_score == pytest.approx(weighted_sum / denom, rel=1e-7)


def test_clone_spam_reduces_conviction_vs_single(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_450.0
    single = [
        {"source_id": "alpha_clone_0", "direction": 1, "conviction": 1.0, "expected_edge_bps": 40, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "cluster_x"}
    ]
    spam = [
        {"source_id": f"alpha_clone_{i}", "direction": 1, "conviction": 1.0, "expected_edge_bps": 40, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "cluster_x"}
        for i in range(30)
    ]
    out_single = orchestrator.orchestrate(single, regime_ctx, fq, exec_state, current_time=ts)
    out_spam = orchestrator.orchestrate(spam, regime_ctx, fq, exec_state, current_time=ts)
    single_row = out_single.meta_info["per_signal_breakdown"][0]
    spam_rows = out_spam.meta_info["per_signal_breakdown"]
    assert single_row["correlation_penalty"] == pytest.approx(1.0, rel=1e-8)
    assert all(r["correlation_penalty"] < 1.0 for r in spam_rows)


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
    assert out.expected_edge_bps == pytest.approx(50.0, rel=1e-6)


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
        {"source_id": f"alpha_clone_{i}", "direction": 1, "conviction": 1.0, "expected_edge_bps": 30, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "cluster_x"}
        for i in range(5)
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    summary = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    assert "groups" in summary
    assert "largest_group_size" in summary
    assert "total_groups" in summary
    assert "attenuation_factor" in summary
    assert "raw_blended_edge_bps" in summary
    assert "attenuated_blended_edge_bps" in summary
    assert "penalty_logic" in summary
    assert "conviction_gate_active" in summary


def test_explicit_correlation_group_id_is_respected(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_780.0
    signals = [
        {"source_id": "alpha_v1", "direction": 1, "conviction": 1.0, "expected_edge_bps": 30, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "explicit_a"},
        {"source_id": "alpha_v2", "direction": 1, "conviction": 1.0, "expected_edge_bps": 30, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "explicit_a"},
        {"source_id": "clone_1", "direction": 1, "conviction": 1.0, "expected_edge_bps": 30, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "explicit_b"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    groups = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]["groups"]
    keyed = {tuple(g["key"]): g["size"] for g in groups}
    assert keyed[(1, "1m", "explicit_a")] == 2
    assert keyed[(1, "1m", "explicit_b")] == 1


def test_metadata_contains_canonical_correlation_summary(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_790.0
    signals = [
        {"source_id": "alpha_v1", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_v2", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    summary = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    assert "largest_group_size" in summary
    assert "largest_group" not in summary
    assert summary["attenuated_blended_edge_bps"] == pytest.approx(out.expected_edge_bps, rel=1e-6)
    row = out.meta_info["per_signal_breakdown"][0]
    expected_fields = {
        "source_id",
        "source_policy",
        "perf_multiplier",
        "direction",
        "timeframe",
        "expected_edge_bps",
        "base_weight",
        "regime_alignment_weight",
        "raw_weight_contribution",
        "effective_weight_contribution",
        "final_weight_contribution",
        "fallback_active",
        "drift_active",
        "dominance_cap_active",
        "stress_attenuation",
        "correlation_penalty",
        "similar_signal_count",
        "correlation_group_id",
        "correlation_group_key",
    }
    assert expected_fields.issubset(row.keys())
    assert row["effective_weight_contribution"] <= row["raw_weight_contribution"] + 1e-12


def test_non_correlated_signals_do_not_suppress_edge(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_795.0
    signals = [
        {"source_id": "alpha_good", "direction": 1, "conviction": 1.0, "expected_edge_bps": 20, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_mid", "direction": 1, "conviction": 1.0, "expected_edge_bps": 80, "timestamp": ts, "timeframe": "1m"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    summary = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    assert summary["attenuation_factor"] == pytest.approx(1.0, rel=1e-8)
    assert isinstance(summary["groups"], list)
    assert all("size" in g and "key" in g for g in summary["groups"])
    assert out.expected_edge_bps == pytest.approx(50.0, rel=1e-6)


def test_blended_edge_matches_score_attenuation_factor(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_796.0
    signals = [
        {"source_id": f"alpha_clone_{i}", "direction": 1, "conviction": 1.0, "expected_edge_bps": 60, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "cluster_x"}
        for i in range(9)
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    summary = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    attenuation = summary["attenuation_factor"]
    raw_edge = summary["raw_blended_edge_bps"]
    attenuated_edge = summary["attenuated_blended_edge_bps"]
    assert attenuation <= 1.0
    assert abs(attenuated_edge) <= abs(raw_edge) + 1e-12
    if abs(raw_edge) > 1e-12:
        assert attenuated_edge == pytest.approx(raw_edge * attenuation, rel=1e-6)
    else:
        assert attenuation == pytest.approx(1.0, rel=1e-9)
    assert out.expected_edge_bps == pytest.approx(attenuated_edge, rel=1e-6)


def test_low_conviction_bypass_regression_closed(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_797.0
    single = [
        {"source_id": "alpha_clone_0", "direction": 1, "conviction": 0.2, "expected_edge_bps": 60, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "cluster_low"}
    ]
    spam = [
        {"source_id": f"alpha_clone_{i}", "direction": 1, "conviction": 0.2, "expected_edge_bps": 60, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "cluster_low"}
        for i in range(40)
    ]
    out_single = orchestrator.orchestrate(single, regime_ctx, fq, exec_state, current_time=ts)
    out_spam = orchestrator.orchestrate(spam, regime_ctx, fq, exec_state, current_time=ts)
    spam_rows = out_spam.meta_info["per_signal_breakdown"]
    single_row = out_single.meta_info["per_signal_breakdown"][0]
    groups = out_spam.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]["groups"]
    assert any(g["size"] == 40 for g in groups)
    assert single_row["correlation_penalty"] == pytest.approx(1.0, rel=1e-9)
    assert all(
        r["effective_weight_contribution"] <= r["raw_weight_contribution"] + 1e-12
        for r in spam_rows
    )
    assert all(r["correlation_penalty"] == 1.0 for r in spam_rows)


def test_correlation_not_double_applied(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_7972.0
    signals = [
        {"source_id": f"alpha_clone_{i}", "direction": 1, "conviction": 1.0, "expected_edge_bps": 70, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "cluster_y"}
        for i in range(12)
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    rows = out.meta_info["per_signal_breakdown"]
    denom = sum(r["final_weight_contribution"] for r in rows)
    weighted_sum = sum(r["final_weight_contribution"] * r["direction"] for r in rows)
    weighted_edge = sum(r["final_weight_contribution"] * r["direction"] * r["expected_edge_bps"] for r in rows)
    tf_meta = out.meta_info["tf_fusion_breakdown"]["1m"]
    assert tf_meta["net_score"] == pytest.approx(weighted_sum / denom, rel=1e-7)
    assert out.expected_edge_bps == pytest.approx(weighted_edge / denom, rel=1e-7)


def test_zero_denom_correlation_summary_safety(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_7973.0
    signals = [
        {"source_id": "alpha_good", "direction": 0, "conviction": 1.0, "expected_edge_bps": 70, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_mid", "direction": 0, "conviction": 1.0, "expected_edge_bps": 70, "timestamp": ts, "timeframe": "1m"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    assert out.action.name == "HOLD"
    assert out.expected_edge_bps == 0.0


def test_legacy_signal_without_timeframe_and_correlation_group_id(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_7975.0
    signals = [
        {"source_id": "alpha_v1", "direction": 1, "conviction": 1.0, "expected_edge_bps": 20, "timestamp": ts},
        {"source_id": "alpha_v2", "direction": 1, "conviction": 1.0, "expected_edge_bps": 20, "timestamp": ts},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    summary = out.meta_info["tf_fusion_breakdown"]["default"]["fusion_meta"]["correlation_summary"]
    groups = {tuple(g["key"]): g["size"] for g in summary["groups"]}
    assert groups[(1, "default", "alpha")] == 2
    assert out.action.name in {"BUY", "HOLD"}


def test_hold_on_invalid_correlation_group_id_format(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_798.0
    signals = [
        {"source_id": "alpha_v1", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "bad id"},
        {"source_id": "alpha_v2", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "also bad"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    assert out.action.name == "HOLD"


def test_hold_on_malformed_correlation_input(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_799.0
    signals = [
        {"source_id": "alpha_v1", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "!bad"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    assert out.action.name == "HOLD"


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

import os
import sys
import math
import random

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from alpha_orchestration import (
    AlphaOrchestrator,
    AlphaSignal,
    OrchestratorConfig,
    RegimeContext,
    FeatureQuality,
    ExecutionState,
)


def _is_unsafe_aggregation(out, timeframe="1m"):
    tf_meta = out.meta_info.get("tf_fusion_breakdown", {}).get(timeframe, {})
    summary = tf_meta.get("fusion_meta", {}).get("correlation_summary", {})
    denom = summary.get("total_adjusted_weight")
    if denom is None:
        return True
    return bool(summary.get("low_aggregate_weight")) or float(denom) <= 1e-12


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
    if _is_unsafe_aggregation(out):
        assert out.action.name == "HOLD"
        assert w == 0.0
    else:
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
    assert grp["penalty"] == grp["correlation_penalty_model"]
    assert grp["correlation_penalty_model"] == pytest.approx(1.0 / math.sqrt(1.0 + 0.5 * (10 - 1)), rel=1e-6)
    assert grp["realized_attenuation"] == pytest.approx(grp["adjusted_weight"] / grp["raw_weight"], rel=1e-7)
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
    if _is_unsafe_aggregation(out):
        assert summary["edge_attenuation_factor"] == pytest.approx(0.0, rel=1e-7)
        assert out.expected_edge_bps == 0.0
    else:
        assert summary["edge_attenuation_factor"] == pytest.approx(1.0, rel=1e-7)
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
    if denom <= 1e-12:
        assert out.action.name == "HOLD"
        assert tf_score == 0.0
    else:
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
    if _is_unsafe_aggregation(out_weak) and _is_unsafe_aggregation(out_strong):
        assert weak_w == 0.0
        assert strong_w == 0.0
    else:
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
    if _is_unsafe_aggregation(out):
        assert out.action.name == "HOLD"
        assert out.net_conviction == 0.0
        assert out.expected_edge_bps == 0.0
    else:
        assert out.net_conviction > 0.95
        assert out.expected_edge_bps == pytest.approx(50.0, rel=1e-6)


def test_cross_tf_safe(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_700.0
    signals = [
        {"source_id": "alpha_v1", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_v2", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1h"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    if _is_unsafe_aggregation(out):
        assert out.action.name == "HOLD"
        assert out.net_conviction == 0.0
    else:
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
    assert "edge_attenuation_factor" in summary
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
        "conviction",
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
        "conviction_gate_applies",
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
    if _is_unsafe_aggregation(out):
        assert summary["edge_attenuation_factor"] == pytest.approx(0.0, rel=1e-8)
    else:
        assert summary["edge_attenuation_factor"] == pytest.approx(1.0, rel=1e-8)
    assert isinstance(summary["groups"], list)
    assert all("size" in g and "key" in g for g in summary["groups"])
    if _is_unsafe_aggregation(out):
        assert out.expected_edge_bps == 0.0
    else:
        assert out.expected_edge_bps == pytest.approx(50.0, rel=1e-6)


def test_blended_edge_matches_score_attenuation_factor(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_796.0
    signals = [
        {"source_id": f"alpha_clone_{i}", "direction": 1, "conviction": 1.0, "expected_edge_bps": 60, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "cluster_x"}
        for i in range(9)
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    summary = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    attenuation = summary["edge_attenuation_factor"]
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


def test_raw_edge_uses_matching_raw_denominator(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_7974.0
    signals = [
        {"source_id": "alpha_v1", "direction": 1, "conviction": 1.0, "expected_edge_bps": 80, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_v2", "direction": -1, "conviction": 0.75, "expected_edge_bps": 20, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_good", "direction": 0, "conviction": 1.0, "expected_edge_bps": 99, "timestamp": ts, "timeframe": "1m"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    rows = out.meta_info["per_signal_breakdown"]
    raw_denom = sum(r["raw_weight_contribution"] for r in rows if r["direction"] in (-1, 1))
    raw_num = sum(
        r["raw_weight_contribution"] * r["direction"] * r["expected_edge_bps"]
        for r in rows
        if r["direction"] in (-1, 1)
    )
    summary = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    assert raw_denom > 1e-12
    assert summary["raw_blended_edge_bps"] == pytest.approx(raw_num / raw_denom, rel=1e-7)


def test_score_and_edge_attenuation_are_observable_and_consistent(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_79745.0
    signals = [
        {"source_id": f"alpha_clone_{i}", "direction": 1, "conviction": 1.0, "expected_edge_bps": 60, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "cluster_obs"}
        for i in range(8)
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    summary = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    assert summary["adjusted_score"] == pytest.approx(out.meta_info["tf_fusion_breakdown"]["1m"]["net_score"], rel=1e-7)
    assert summary["attenuation_mode"] == "score_and_edge"
    assert abs(summary["adjusted_score"]) <= abs(summary["raw_score"]) + 1e-12
    if abs(summary["raw_score"]) > 1e-12:
        assert summary["score_attenuation_factor"] == pytest.approx(
            abs(summary["adjusted_score"]) / abs(summary["raw_score"]),
            rel=1e-7,
        )
    assert abs(summary["attenuated_blended_edge_bps"]) <= abs(summary["raw_blended_edge_bps"]) + 1e-12
    assert summary["penalty_condition"].startswith("conviction>=")


def test_score_attenuation_near_zero_raw_score_is_stable(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_797451.0
    signals = [
        {"source_id": "alpha_good", "direction": 1, "conviction": 1.0, "expected_edge_bps": 30, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_mid", "direction": -1, "conviction": 1.0, "expected_edge_bps": 30, "timestamp": ts, "timeframe": "1m"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    summary = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    assert abs(summary["raw_score"]) < 1e-12
    assert abs(summary["adjusted_score"]) < 1e-12
    assert summary["score_attenuation_factor"] == pytest.approx(1.0, rel=1e-12)


def test_conviction_gate_summary_matches_execution_behavior(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_79746.0
    high_conviction = [
        {"source_id": f"alpha_clone_{i}", "direction": 1, "conviction": 1.0, "expected_edge_bps": 30, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "cluster_gate"}
        for i in range(5)
    ]
    low_conviction = [
        {"source_id": f"alpha_clone_{i+10}", "direction": 1, "conviction": 0.2, "expected_edge_bps": 30, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "cluster_gate_low"}
        for i in range(5)
    ]
    out_high = orchestrator.orchestrate(high_conviction, regime_ctx, fq, exec_state, current_time=ts)
    out_low = orchestrator.orchestrate(low_conviction, regime_ctx, fq, exec_state, current_time=ts)
    high_summary = out_high.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    low_summary = out_low.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    assert high_summary["conviction_gate_active"] is True
    assert low_summary["conviction_gate_active"] is False
    high_group = high_summary["groups"][0]
    low_group = low_summary["groups"][0]
    assert high_group["any_signal_gated"] is True
    assert high_group["penalty_applied"] is True
    assert high_group["all_signals_gated"] is True
    assert high_group["gated_signal_count"] == high_group["signal_count"]
    assert low_group["any_signal_gated"] is False
    assert low_group["penalty_applied"] is False
    assert low_group["all_signals_gated"] is False
    assert low_group["gated_signal_count"] == 0
    assert any(r["conviction_gate_applies"] for r in out_high.meta_info["per_signal_breakdown"])
    assert all(not r["conviction_gate_applies"] for r in out_low.meta_info["per_signal_breakdown"])


def test_mixed_conviction_group_reports_partial_gating(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_797461.0
    signals = [
        {"source_id": "alpha_clone_0", "direction": 1, "conviction": 1.0, "expected_edge_bps": 30, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "mixed_gate"},
    ] + [
        {"source_id": f"alpha_clone_{i+1}", "direction": 1, "conviction": 0.1, "expected_edge_bps": 30, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "mixed_gate"}
        for i in range(9)
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    summary = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    grp = next(g for g in summary["groups"] if g["key"][2] == "mixed_gate")
    assert grp["signal_count"] == 10
    assert grp["gated_signal_count"] == 1
    assert grp["any_signal_gated"] is True
    assert grp["all_signals_gated"] is False


def test_model_penalty_matches_helper_output(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_7974615.0
    signals = [
        {"source_id": f"alpha_clone_{i}", "direction": 1, "conviction": 1.0, "expected_edge_bps": 15, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "penalty_match"}
        for i in range(6)
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    summary = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    grp = next(g for g in summary["groups"] if g["key"][2] == "penalty_match")
    expected_penalty, _ = orchestrator._correlation_penalty(1.0, 6)
    assert grp["correlation_penalty_model"] == pytest.approx(expected_penalty, rel=1e-9)
    assert grp["realized_attenuation"] == pytest.approx(
        grp["adjusted_weight"] / grp["raw_weight"], rel=1e-9
    )


def test_dominance_distortion_is_observable():
    cfg = OrchestratorConfig(
        signal_weights={"heavy": 10.0, "light_a": 1.0, "light_b": 1.0},
        allow_unknown_sources=True,
        correlation_min_conviction=0.5,
        correlation_min_group_size=3,
    )
    orch = AlphaOrchestrator(cfg)
    ts = 1_700_010_7974616.0
    signals = [
        {"source_id": "heavy", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "dom_cluster"},
        {"source_id": "light_a", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "dom_cluster"},
        {"source_id": "light_b", "direction": 1, "conviction": 1.0, "expected_edge_bps": 50, "timestamp": ts, "timeframe": "1m", "correlation_group_id": "dom_cluster"},
    ]
    out = orch.orchestrate(
        signals,
        RegimeContext("trend", 0.3, 0.8),
        FeatureQuality(0.0, 0.0),
        ExecutionState(0.0, 100000.0, 0.0),
        current_time=ts,
    )
    summary = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    grp = summary["groups"][0]
    assert grp["dominance_cap_effect"] > 0.0
    assert grp["realized_attenuation"] < grp["correlation_penalty_model"]
    assert grp["realized_attenuation"] <= grp["correlation_penalty_model"] + 1e-12
    assert summary["total_dominance_cap_impact"] > 0.0
    assert summary["total_correlation_impact"] > 0.0
    assert summary["total_raw_weight"] >= summary["total_adjusted_weight"]
    assert all(g["adjusted_weight"] <= g["raw_weight"] + 1e-12 for g in summary["groups"])


def test_raw_observability_pre_filter_independence(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_797462.0
    signals = [
        {"source_id": "alpha_good", "direction": 1, "conviction": 1.0, "expected_edge_bps": 25, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_mid", "direction": -1, "conviction": 0.9, "expected_edge_bps": 10, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_clone_1", "direction": 1, "conviction": 1e-9, "expected_edge_bps": 99, "timestamp": ts, "timeframe": "1m"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    rows = out.meta_info["per_signal_breakdown"]
    directional_rows = [r for r in rows if r["direction"] in (-1, 1)]
    summary = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    raw_denom = sum(r["raw_weight_contribution"] for r in directional_rows)
    assert raw_denom > 0.0
    assert summary["raw_score"] == pytest.approx(
        sum(r["raw_weight_contribution"] * r["direction"] for r in directional_rows) / raw_denom,
        rel=1e-7,
    )
    assert math.isfinite(summary["adjusted_score"])


def test_dominance_cap_changes_score(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_797463.0
    signals = [
        {"source_id": "alpha_good", "direction": 1, "conviction": 1.0, "expected_edge_bps": 30, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_mid", "direction": -1, "conviction": 0.2, "expected_edge_bps": 30, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_clone_1", "direction": -1, "conviction": 0.2, "expected_edge_bps": 30, "timestamp": ts, "timeframe": "1m"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    summary = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    rows = out.meta_info["per_signal_breakdown"]
    assert any(r["dominance_cap_active"] for r in rows)
    assert summary["adjusted_score"] != pytest.approx(summary["raw_score"], rel=1e-7)


def test_adjusted_score_uses_weighted_sum_contributors(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_797464.0
    signals = [
        {"source_id": "alpha_good", "direction": 1, "conviction": 0.9, "expected_edge_bps": 40, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_mid", "direction": -1, "conviction": 0.7, "expected_edge_bps": 30, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_clone_1", "direction": 1, "conviction": 0.4, "expected_edge_bps": 20, "timestamp": ts, "timeframe": "1m"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    rows = out.meta_info["per_signal_breakdown"]
    denom = sum(r["final_weight_contribution"] for r in rows)
    weighted_sum = sum(r["final_weight_contribution"] * r["direction"] for r in rows)
    tf_score = out.meta_info["tf_fusion_breakdown"]["1m"]["net_score"]
    assert denom > 1e-12
    assert tf_score == pytest.approx(weighted_sum / denom, rel=1e-9)


def test_fuse_signals_weighted_sum_regression_no_name_error():
    cfg = OrchestratorConfig(
        signal_weights={"alpha_good": 1.0, "alpha_mid": 1.0},
        min_aggregate_weight=0.0,
        allow_unknown_sources=True,
    )
    orch = AlphaOrchestrator(cfg)
    score, edge, meta = orch._fuse_signals(
        signals=[
            AlphaSignal("alpha_good", 1, 1.0, 40.0, 1_700_010_7974641.0, timeframe="1m"),
            AlphaSignal("alpha_mid", -1, 0.5, 20.0, 1_700_010_7974642.0, timeframe="1m"),
        ],
        regime_name="trend",
        safe_dd=0.0,
        perf_snapshot={},
        regime_assessment=None,
    )
    rows = meta["breakdown"]
    denom = sum(r["final_weight_contribution"] for r in rows)
    weighted_sum = sum(r["final_weight_contribution"] * r["direction"] for r in rows)
    weighted_edge = sum(
        r["final_weight_contribution"] * r["direction"] * r["expected_edge_bps"] for r in rows
    )
    if denom <= 1e-12:
        assert score == 0.0
        assert edge == 0.0
    else:
        assert score == pytest.approx(weighted_sum / denom, rel=1e-9)
        assert edge == pytest.approx(weighted_edge / denom, rel=1e-9)


def test_directionless_signals_do_not_dilute_adjusted_metrics():
    cfg = OrchestratorConfig(
        signal_weights={"alpha_good": 1.0, "alpha_mid": 1.0, "alpha_v1": 1.0},
        min_aggregate_weight=0.0,
        allow_unknown_sources=True,
    )
    orch = AlphaOrchestrator(cfg)
    score, edge, meta = orch._fuse_signals(
        signals=[
            AlphaSignal("alpha_good", 1, 1.0, 50.0, 1_700_010_7974643.0, timeframe="1m"),
            AlphaSignal("alpha_mid", -1, 0.5, 10.0, 1_700_010_7974644.0, timeframe="1m"),
            AlphaSignal("alpha_v1", 0, 1.0, 999.0, 1_700_010_7974645.0, timeframe="1m"),
        ],
        regime_name="trend",
        safe_dd=0.0,
        perf_snapshot={},
        regime_assessment=None,
    )
    directional = [r for r in meta["breakdown"] if r["direction"] in (-1, 1)]
    neutral = [r for r in meta["breakdown"] if r["direction"] == 0]
    assert len(neutral) == 1
    assert neutral[0]["final_weight_contribution"] == 0.0
    denom = sum(r["final_weight_contribution"] for r in directional)
    weighted_sum = sum(r["final_weight_contribution"] * r["direction"] for r in directional)
    weighted_edge = sum(
        r["final_weight_contribution"] * r["direction"] * r["expected_edge_bps"] for r in directional
    )
    if denom <= 1e-12:
        assert score == 0.0
        assert edge == 0.0
    else:
        assert score == pytest.approx(weighted_sum / denom, rel=1e-9)
        assert edge == pytest.approx(weighted_edge / denom, rel=1e-9)


def test_zero_denom_blended_edge_is_guarded():
    cfg = OrchestratorConfig(
        signal_weights={"alpha_good": 1.0},
        min_aggregate_weight=0.0,
        allow_unknown_sources=True,
    )
    orch = AlphaOrchestrator(cfg)
    signals = [
        AlphaSignal(
            source_id="alpha_good",
            direction=0,
            conviction=1.0,
            expected_edge_bps=50.0,
            timestamp=1_700_010_79747.0,
            timeframe="1m",
        )
    ]
    score, edge, meta = orch._fuse_signals(
        signals=signals,
        regime_name="trend",
        safe_dd=0.0,
        perf_snapshot={},
        regime_assessment=None,
    )
    assert score == 0.0
    assert edge == 0.0
    assert "correlation_summary" in meta


def test_raw_and_adjusted_identity_fields_are_exact(orchestrator, regime_ctx, fq, exec_state):
    ts = 1_700_010_797471.0
    signals = [
        {"source_id": "alpha_good", "direction": 1, "conviction": 1.0, "expected_edge_bps": 60, "timestamp": ts, "timeframe": "1m"},
        {"source_id": "alpha_mid", "direction": -1, "conviction": 0.4, "expected_edge_bps": 20, "timestamp": ts, "timeframe": "1m"},
    ]
    out = orchestrator.orchestrate(signals, regime_ctx, fq, exec_state, current_time=ts)
    summary = out.meta_info["tf_fusion_breakdown"]["1m"]["fusion_meta"]["correlation_summary"]
    rows = out.meta_info["per_signal_breakdown"]
    directional = [r for r in rows if r["direction"] in (-1, 1)]
    raw_denom = sum(r["raw_weight_contribution"] for r in directional)
    raw_weighted_sum = sum(r["raw_weight_contribution"] * r["direction"] for r in directional)
    raw_weighted_edge = sum(
        r["raw_weight_contribution"] * r["direction"] * r["expected_edge_bps"] for r in directional
    )
    adjusted_denom = sum(r["final_weight_contribution"] for r in directional)
    adjusted_weighted_sum = sum(r["final_weight_contribution"] * r["direction"] for r in directional)
    adjusted_weighted_edge = sum(
        r["final_weight_contribution"] * r["direction"] * r["expected_edge_bps"] for r in directional
    )
    assert summary["raw_score"] == pytest.approx(raw_weighted_sum / raw_denom, rel=1e-9)
    assert summary["raw_blended_edge_bps"] == pytest.approx(raw_weighted_edge / raw_denom, rel=1e-9)
    if adjusted_denom <= 1e-12:
        assert summary["adjusted_score"] == 0.0
        assert summary["attenuated_blended_edge_bps"] == 0.0
    else:
        assert summary["adjusted_score"] == pytest.approx(adjusted_weighted_sum / adjusted_denom, rel=1e-9)
        assert summary["attenuated_blended_edge_bps"] == pytest.approx(adjusted_weighted_edge / adjusted_denom, rel=1e-9)
    assert summary["total_raw_weight"] == pytest.approx(raw_denom, rel=1e-9)
    assert summary["total_adjusted_weight"] == pytest.approx(adjusted_denom, rel=1e-9)


def test_dominance_cap_closed_form_constraint_holds():
    cfg = OrchestratorConfig(
        signal_weights={"a": 10.0, "b": 1.0, "c": 1.0, "d": 1.0},
        min_aggregate_weight=0.0,
        allow_unknown_sources=True,
    )
    orch = AlphaOrchestrator(cfg)
    score, edge, meta = orch._fuse_signals(
        signals=[
            AlphaSignal("a", 1, 1.0, 50.0, 1_700_010_797472.0, timeframe="1m", correlation_group_id="g"),
            AlphaSignal("b", 1, 1.0, 50.0, 1_700_010_797473.0, timeframe="1m", correlation_group_id="g"),
            AlphaSignal("c", 1, 1.0, 50.0, 1_700_010_797474.0, timeframe="1m", correlation_group_id="g"),
            AlphaSignal("d", 1, 1.0, 50.0, 1_700_010_797475.0, timeframe="1m", correlation_group_id="g"),
        ],
        regime_name="trend",
        safe_dd=0.0,
        perf_snapshot={},
        regime_assessment=None,
    )
    assert math.isfinite(score)
    assert math.isfinite(edge)
    directional = [r for r in meta["breakdown"] if r["direction"] in (-1, 1)]
    denom = sum(r["final_weight_contribution"] for r in directional)
    assert denom > 1e-12
    for r in directional:
        assert r["final_weight_contribution"] <= 0.4 * denom + 1e-8


def test_multiple_dominant_signals_are_all_capped():
    cfg = OrchestratorConfig(
        signal_weights={"a": 8.0, "b": 8.0, "c": 1.0, "d": 1.0, "e": 1.0},
        min_aggregate_weight=0.0,
        allow_unknown_sources=True,
    )
    orch = AlphaOrchestrator(cfg)
    _, _, meta = orch._fuse_signals(
        signals=[
            AlphaSignal("a", 1, 1.0, 20.0, 1_700_010_797476.0, timeframe="1m", correlation_group_id="g"),
            AlphaSignal("b", 1, 1.0, 20.0, 1_700_010_797477.0, timeframe="1m", correlation_group_id="g"),
            AlphaSignal("c", 1, 1.0, 20.0, 1_700_010_797478.0, timeframe="1m", correlation_group_id="g"),
            AlphaSignal("d", 1, 1.0, 20.0, 1_700_010_797479.0, timeframe="1m", correlation_group_id="g"),
            AlphaSignal("e", 1, 1.0, 20.0, 1_700_010_7974791.0, timeframe="1m", correlation_group_id="g"),
        ],
        regime_name="trend",
        safe_dd=0.0,
        perf_snapshot={},
        regime_assessment=None,
    )
    directional = [r for r in meta["breakdown"] if r["direction"] in (-1, 1)]
    denom = sum(r["final_weight_contribution"] for r in directional)
    assert denom > 0.0
    assert sum(1 for r in directional if r["dominance_cap_active"]) >= 2
    assert all(r["final_weight_contribution"] <= 0.4 * denom + 1e-8 for r in directional)


def test_fusion_is_deterministic_over_100_runs():
    cfg = OrchestratorConfig(
        signal_weights={f"s{i}": 1.0 + (i % 3) for i in range(12)},
        min_aggregate_weight=0.0,
        allow_unknown_sources=True,
    )
    orch = AlphaOrchestrator(cfg)
    base_signals = [
        AlphaSignal(
            source_id=f"s{i}",
            direction=1 if i % 2 == 0 else -1,
            conviction=0.2 + 0.07 * (i % 5),
            expected_edge_bps=10.0 + 3.0 * i,
            timestamp=1_700_010_797480.0 + i,
            timeframe="1m",
            correlation_group_id=f"grp_{i % 3}",
        )
        for i in range(12)
    ]
    first = None
    for _ in range(100):
        out = orch._fuse_signals(
            signals=base_signals,
            regime_name="trend",
            safe_dd=0.0,
            perf_snapshot={},
            regime_assessment=None,
        )
        if first is None:
            first = out
        assert out == first


def test_monte_carlo_stress_no_nan_or_inf():
    rnd = random.Random(42)
    cfg = OrchestratorConfig(
        signal_weights={f"m{i}": 0.5 + (i % 7) * 0.3 for i in range(40)},
        min_aggregate_weight=0.0,
        allow_unknown_sources=True,
    )
    orch = AlphaOrchestrator(cfg)
    for k in range(10_000):
        n = rnd.randint(1, 35)
        signals = []
        for i in range(n):
            sid = f"m{rnd.randint(0, 39)}"
            d = rnd.choice([-1, 0, 1])
            conv = rnd.random()
            edge = rnd.uniform(0.0, 200.0)
            tf = rnd.choice(["1m", "5m", "1h", "default"])
            gid = f"g{rnd.randint(0, 8)}"
            signals.append(
                AlphaSignal(
                    source_id=sid,
                    direction=d,
                    conviction=conv,
                    expected_edge_bps=edge,
                    timestamp=1_700_010_797500.0 + i + k * 0.1,
                    timeframe=tf,
                    correlation_group_id=gid,
                )
            )
        score, edge, meta = orch._fuse_signals(
            signals=signals,
            regime_name="trend",
            safe_dd=0.0,
            perf_snapshot={},
            regime_assessment=None,
        )
        assert math.isfinite(score)
        assert math.isfinite(edge)
        summary = meta["correlation_summary"]
        assert math.isfinite(summary["raw_score"])
        assert math.isfinite(summary["adjusted_score"])
        assert math.isfinite(summary["raw_blended_edge_bps"])
        assert math.isfinite(summary["attenuated_blended_edge_bps"])
        assert summary["total_pre_cap_weight"] <= summary["total_raw_weight"] + 1e-8
        assert summary["total_adjusted_weight"] <= summary["total_pre_cap_weight"] + 1e-8
        for r in meta["breakdown"]:
            assert r["raw_weight_contribution"] >= 0.0
            assert r["effective_weight_contribution"] >= 0.0
            assert r["final_weight_contribution"] >= 0.0


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

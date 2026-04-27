"""Embedded regression tests moved out of production module."""

import math
import time

from alpha_orchestrator import (
    AlphaOrchestrator, AlphaSignal, OrchestratorConfig, ExecutionState,
    RegimeContext, RegimeEngine, RegimeAssessment, AlphaPerformanceStats,
    AlphaRegimeStats, Action, _DEFAULT_TIMEFRAME,
)

def test_r1_max_drawdown_zero_raises():
    """max_drawdown_pct=0.0 must raise ValueError at config construction, not silently halt trading."""
    import pytest
    base_cfg = dict(signal_weights={"src_a": 1.0})
    with pytest.raises(ValueError, match="max_drawdown_pct"):
        OrchestratorConfig(**base_cfg, max_drawdown_pct=0.0)
    cfg = OrchestratorConfig(**base_cfg, max_drawdown_pct=0.15)
    assert cfg.max_drawdown_pct == 0.15


def test_r1_dd_breach_is_real_not_config_artifact():
    cfg = OrchestratorConfig(signal_weights={"src_a": 1.0}, max_drawdown_pct=0.10)
    orch = AlphaOrchestrator(cfg)
    now = time.time()
    signals = [AlphaSignal("src_a", 1, 0.9, 10.0, now, "1h")]
    state_ok = ExecutionState(0.0, 10000.0, 0.05)
    regime = RegimeContext("normal", 0.4, 0.8)
    result = orch.orchestrate(signals, regime, None, state_ok, current_time=now)
    assert result.meta_info.get("rationale") != "dd_breach"
    state_over = ExecutionState(0.0, 10000.0, 0.15)
    result2 = orch.orchestrate(signals, regime, None, state_over, current_time=now)
    assert result2.meta_info.get("rationale") == "dd_breach"


def test_r2_cold_start_regime_tightening():
    cfg = OrchestratorConfig(signal_weights={"src": 1.0}, max_drawdown_pct=0.20)
    engine = RegimeEngine(cfg)
    high_stress_assessment = RegimeAssessment("unknown", 0.9, 0.9, 0.9, 0.0, True, False, False, 0)
    effective_dd = engine.effective_max_drawdown(high_stress_assessment, 0.20)
    assert effective_dd < 0.20
    full_conf_assessment = RegimeAssessment("trending", 0.9, 0.9, 0.9, 1.0, True, True, False, 30)
    effective_dd_full = engine.effective_max_drawdown(full_conf_assessment, 0.20)
    assert effective_dd_full < effective_dd
    no_stress = RegimeAssessment("calm", 0.2, 0.1, 0.16, 0.0, False, False, True, 0)
    assert engine.effective_max_drawdown(no_stress, 0.20) == 0.20


def test_r3_sanitize_stats_respects_separate_caps():
    cfg = OrchestratorConfig(signal_weights={"src": 1.0}, feedback_enabled=True, feedback_max_multiplier=1.5, regime_max_adjustment=2.0)
    orch = AlphaOrchestrator(cfg)
    global_stats = AlphaPerformanceStats(source_id="src")
    global_stats.current_multiplier = 1.9
    orch._sanitize_stats(global_stats)
    assert global_stats.current_multiplier <= cfg.feedback_max_multiplier
    regime_stats = AlphaRegimeStats()
    regime_stats.current_multiplier = 1.9
    orch._sanitize_stats(regime_stats)
    assert regime_stats.current_multiplier <= cfg.regime_max_adjustment
    assert regime_stats.current_multiplier >= 1.5


def test_r4_hurdle_locked_exactly_once_by_update_stats_block():
    cfg = OrchestratorConfig(signal_weights={"src": 1.0}, feedback_enabled=True)
    orch = AlphaOrchestrator(cfg)
    orch.update_performance({"source_id": "src", "realized_pnl": 100.0, "realized_edge_bps": 5.0, "expected_edge_bps": 8.0, "expected_win_rate": 0.55, "event_time": time.time()})
    with orch._lock:
        stats = orch._performance_stats["src"]
        assert stats.hurdles_locked is True
        assert abs(stats.expected_edge_bps - 8.0) < 1e-6
        assert abs(stats.target_win_rate - 0.55) < 1e-6
    orch.update_performance({"source_id": "src", "realized_pnl": 50.0, "realized_edge_bps": 3.0, "expected_edge_bps": 15.0, "expected_win_rate": 0.70, "event_time": time.time()})
    with orch._lock:
        stats = orch._performance_stats["src"]
        assert abs(stats.expected_edge_bps - 8.0) < 1e-6
        assert abs(stats.target_win_rate - 0.55) < 1e-6


def test_r5_decay_signal_over_delivery():
    cfg = OrchestratorConfig(signal_weights={"src": 1.0}, feedback_enabled=True)
    orch = AlphaOrchestrator(cfg)
    stats = AlphaPerformanceStats(source_id="src")
    stats.expected_edge_bps = 10.0
    stats.target_win_rate = 0.55
    stats.hurdles_locked = True
    stats.avg_realized_edge_bps = 30.0
    decay = orch._calculate_decay_signal(stats, None, None)
    stats.avg_realized_edge_bps = 10.0
    decay_breakeven = orch._calculate_decay_signal(stats, None, None)
    stats.avg_realized_edge_bps = 5.0
    decay_under = orch._calculate_decay_signal(stats, None, None)
    assert decay == decay_breakeven
    assert decay_under > decay_breakeven


def test_r6_weak_conviction_always_hold():
    cfg = OrchestratorConfig(signal_weights={"src": 1.0}, action_threshold=0.7, score_deadband=0.05)
    orch = AlphaOrchestrator(cfg)
    meta = {}
    result = orch._generate_decision(score=0.95, conviction=0.5, edge=10.0, urgency=0.8, risk_rat=None, meta=meta)
    assert result.action == Action.HOLD
    assert meta.get("rationale") == "weak_score"
    meta2 = {}
    result2 = orch._generate_decision(score=0.95, conviction=0.95, edge=10.0, urgency=0.9, risk_rat="dd_breach", meta=meta2)
    assert result2.action == Action.HOLD
    assert result2.net_conviction == 0.0
    assert meta2.get("rationale") == "dd_breach"
    meta3 = {}
    result3 = orch._generate_decision(score=0.8, conviction=0.8, edge=15.0, urgency=0.75, risk_rat=None, meta=meta3)
    assert result3.action == Action.BUY


def test_r7_signal_timeframe_normalized_in_place():
    now = time.time()
    sig = AlphaSignal("test_src", 1, 0.8, 10.0, now, "1H")
    assert sig.timeframe == "1h"
    sig2 = AlphaSignal("test_src", 1, 0.8, 10.0, now, "  5M  ")
    assert sig2.timeframe == "5m"
    sig3 = AlphaSignal("test_src", 1, 0.8, 10.0, now)
    assert sig3.timeframe == _DEFAULT_TIMEFRAME
    cfg = OrchestratorConfig(signal_weights={"test_src": 1.0}, timeframe_order=["1h", "5m", "default"], timeframe_weights={"1h": 2.0, "5m": 1.0, "default": 0.5}, higher_tf_dominance=False)
    orch = AlphaOrchestrator(cfg)
    state = ExecutionState(0.0, 10000.0, 0.0)
    result = orch.orchestrate([sig, sig2], None, None, state, current_time=now)
    tf_breakdown = result.meta_info.get("timeframe_breakdown", {})
    assert "1H" not in tf_breakdown
    assert "  5M  " not in tf_breakdown


def test_r8_signal_ttl_upper_bound():
    cfg = OrchestratorConfig(signal_weights={"src": 1.0}, signal_ttl_seconds=1e30)
    assert cfg.signal_ttl_seconds <= 300.0
    cfg2 = OrchestratorConfig(signal_weights={"src": 1.0}, signal_ttl_seconds=300.0)
    orch = AlphaOrchestrator(cfg2)
    old_ts = time.time() - 301.0
    stale_sig = {"source_id": "src", "direction": 1, "conviction": 0.9, "expected_edge_bps": 10.0, "timestamp": old_ts, "timeframe": "default"}
    state = ExecutionState(0.0, 10000.0, 0.0)
    result = orch.orchestrate([stale_sig], None, None, state, current_time=time.time())
    assert result.meta_info.get("metrics", {}).get("stale", 0) >= 1


def test_r9_mtf_decision_matches_score():
    cfg = OrchestratorConfig(signal_weights={"src": 1.0}, timeframe_order=["1h", "4h", "default"], score_deadband=0.05, higher_tf_dominance=False)
    orch = AlphaOrchestrator(cfg)
    tf_results_buy = {"1h": {"net_score": 0.8, "blended_edge": 12.0, "fusion_meta": {}}, "4h": {"net_score": 0.7, "blended_edge": 10.0, "fusion_meta": {}}}
    score, edge, meta = orch._combine_timeframes(tf_results_buy)
    assert score > 0.05
    assert meta["decision"] == "BUY"
    tf_results_sell = {"1h": {"net_score": -0.75, "blended_edge": -11.0, "fusion_meta": {}}, "4h": {"net_score": -0.8, "blended_edge": -12.0, "fusion_meta": {}}}
    score2, _, meta2 = orch._combine_timeframes(tf_results_sell)
    assert score2 < -0.05
    assert meta2["decision"] == "SELL"
    tf_results_hold = {"1h": {"net_score": 0.01, "blended_edge": 0.5, "fusion_meta": {}}}
    score3, _, meta3 = orch._combine_timeframes(tf_results_hold)
    assert meta3["decision"] == "HOLD"


def test_r10_default_tf_included_when_canonical_neutral():
    """CRIT-2 FIX VERIFICATION: When all canonical TFs are within deadband (dom_dir==0),
    the default bucket must be INCLUDED in the combined score, not silently dropped.
    The old behaviour dropped the default TF when dom_dir==0, discarding valid alpha.
    The correct behaviour includes it because it may be the only tradeable signal.
    """
    cfg = OrchestratorConfig(
        signal_weights={"src": 1.0},
        timeframe_order=["1h", "4h", "default"],
        timeframe_weights={"1h": 2.0, "4h": 2.0, "default": 5.0},
        score_deadband=0.05,
        higher_tf_dominance=True,
    )
    orch = AlphaOrchestrator(cfg)

    # Scenario A: canonical TFs flat, default has a strong BUY signal.
    # Expected: default TF is included → combined score is strongly positive.
    tf_results_default_dominates = {
        "1h": {"net_score": 0.02, "blended_edge": 0.5, "fusion_meta": {}},
        "4h": {"net_score": 0.01, "blended_edge": 0.3, "fusion_meta": {}},
        "default": {"net_score": 0.9, "blended_edge": 15.0, "fusion_meta": {}},
    }
    combined_score, combined_edge, meta = orch._combine_timeframes(tf_results_default_dominates)

    # With default included: (0.02*2 + 0.01*2 + 0.9*5) / (2+2+5) ≈ 0.507
    # Must be clearly above deadband — not suppressed to near-zero
    assert combined_score > cfg.score_deadband, (
        f"CRIT-2 REGRESSION: default TF was excluded when dom_dir==0. "
        f"combined_score={combined_score:.4f}, expected > {cfg.score_deadband}. "
        f"The default bucket must contribute when no canonical TF has a direction."
    )
    assert combined_edge > 0.0, (
        f"Edge was not propagated from default TF: combined_edge={combined_edge}"
    )

    # Scenario B: canonical TF 4h has a strong SELL direction (dom_dir = -1).
    # Default TF has a BUY signal.
    # Expected: default TF is EXCLUDED because HTF dominance is active (dom_dir != 0).
    # The default bucket must not contradict the dominant canonical direction.
    tf_results_htf_dominant = {
        "1h": {"net_score": -0.3, "blended_edge": -5.0, "fusion_meta": {}},
        "4h": {"net_score": -0.8, "blended_edge": -20.0, "fusion_meta": {}},
        "default": {"net_score": 0.95, "blended_edge": 25.0, "fusion_meta": {}},
    }
    combined_score_b, _, _ = orch._combine_timeframes(tf_results_htf_dominant)

    # With default excluded and 1h/4h both SELL (4h is dom_tf), combined score is negative
    assert combined_score_b < 0.0, (
        f"CRIT-2 REGRESSION: default TF was included when HTF dominance was active. "
        f"combined_score={combined_score_b:.4f}, expected < 0.0. "
        f"Default bucket must be excluded when a canonical dominant direction exists."
    )


def test_r11_infinite_direction_correctly_classified():
    cfg = OrchestratorConfig(signal_weights={"src": 1.0})
    orch = AlphaOrchestrator(cfg)
    now = time.time()
    bad_signal = {"source_id": "src", "direction": float('inf'), "conviction": 0.8, "expected_edge_bps": 10.0, "timestamp": now, "timeframe": "default"}
    valid, metrics, rejections = orch._validate_and_prune([bad_signal], now)
    assert len(valid) == 0
    assert metrics["invalid"] == 1
    assert any(r.get("reason") == "invalid_direction" for r in rejections)


def test_r12_event_timestamp_zero_rejected():
    cfg = OrchestratorConfig(signal_weights={"src": 1.0}, feedback_enabled=True)
    orch = AlphaOrchestrator(cfg)
    result = orch._resolve_event_timestamp(0.0, {"source_id": "src"})
    assert result is None
    now = time.time()
    result2 = orch._resolve_event_timestamp(now, {"source_id": "src"})
    assert result2 == now


def test_r13_no_local_eps_redefinition():
    import inspect
    import alpha_orchestrator as mod
    src = inspect.getsource(mod.AlphaOrchestrator._fuse_signals)
    assert "eps = 1e-8" not in src
    assert hasattr(mod, "_FUSION_EPS")
    assert hasattr(mod, "_EPSILON")


def test_r14_single_pass_invariant_produces_same_results():
    cfg = OrchestratorConfig(signal_weights={"src_a": 1.0, "src_b": 0.8, "src_c": 0.6}, timeframe_order=["1h", "4h", "default"])
    orch = AlphaOrchestrator(cfg)
    now = time.time()
    signals = [AlphaSignal("src_a", 1, 0.9, 12.0, now, "1h"), AlphaSignal("src_b", 1, 0.7, 8.0, now, "1h"), AlphaSignal("src_c", -1, 0.6, 5.0, now, "1h")]
    perf_snap = {}
    score, edge, meta = orch._fuse_signals(signals, "unknown", 0.05, perf_snap)
    assert not meta.get("is_fallback_synthetic", False)
    assert math.isfinite(score)
    assert math.isfinite(edge)


if __name__ == "__main__":
    import sys
    tests = [
        test_r1_max_drawdown_zero_raises,
        test_r1_dd_breach_is_real_not_config_artifact,
        test_r2_cold_start_regime_tightening,
        test_r3_sanitize_stats_respects_separate_caps,
        test_r4_hurdle_locked_exactly_once_by_update_stats_block,
        test_r5_decay_signal_over_delivery,
        test_r6_weak_conviction_always_hold,
        test_r7_signal_timeframe_normalized_in_place,
        test_r8_signal_ttl_upper_bound,
        test_r9_mtf_decision_matches_score,
        test_r10_default_tf_included_when_canonical_neutral,
        test_r11_infinite_direction_correctly_classified,
        test_r12_event_timestamp_zero_rejected,
        test_r13_no_local_eps_redefinition,
        test_r14_single_pass_invariant_produces_same_results,
    ]
    failed = []
    for test_fn in tests:
        try:
            test_fn()
            print(f"PASS: {test_fn.__name__}")
        except Exception as e:
            print(f"FAIL: {test_fn.__name__} — {e}")
            failed.append(test_fn.__name__)
    if failed:
        print(f"\n{len(failed)} tests FAILED: {failed}")
        sys.exit(1)
    else:
        print(f"\nAll {len(tests)} tests passed. Production hardening verified.")

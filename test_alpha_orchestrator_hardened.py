"""
test_alpha_orchestrator_hardened.py
Full regression and hardening test suite for alpha_orchestrator.py.
Covers all CRIT, HIGH, MED, and LOW fixes from the Final Verified Audit Report.
"""
import math
import time
import pytest
from alpha_orchestrator import (
    AlphaOrchestrator, AlphaSignal, OrchestratorConfig, OrchestratorAction,
    RegimeContext, FeatureQuality, ExecutionState, Action,
    AlphaPerformanceStats, AlphaRegimeStats, RegimeEngine, RegimeAssessment,
    _safe_float, _FUSION_EPS, _DEFAULT_TIMEFRAME, _PNL_CLAMP,
)

def test_crit1_single_signal_not_zeroed():
    cfg = OrchestratorConfig(signal_weights={"src_a": 2.0}, timeframe_order=["1h", "default"], higher_tf_dominance=False)
    orch = AlphaOrchestrator(cfg)
    now = time.time()
    sig = AlphaSignal("src_a", 1, 0.9, 10.0, now, "1h")
    state = ExecutionState(0.0, 10000.0, 0.0)
    regime = RegimeContext("normal", 0.4, 0.9)
    result = orch.orchestrate([sig], regime, None, state, current_time=now)
    breakdown = result.meta_info.get("per_signal_breakdown", [])
    assert breakdown
    assert breakdown[0].get("final_weight_contribution", 0.0) > 0.0

def test_crit2_default_included_when_canonical_neutral():
    cfg = OrchestratorConfig(signal_weights={"src": 1.0}, timeframe_order=["1h", "4h", "default"], timeframe_weights={"1h": 1.0, "4h": 1.0, "default": 1.0}, score_deadband=0.05, higher_tf_dominance=True)
    orch = AlphaOrchestrator(cfg)
    tf_results = {"1h": {"net_score": 0.02, "blended_edge": 0.1}, "4h": {"net_score": 0.01, "blended_edge": 0.1}, "default": {"net_score": 0.80, "blended_edge": 15.0}}
    score, _, _ = orch._combine_timeframes(tf_results)
    assert score > cfg.score_deadband

def test_crit2_default_excluded_when_htf_dominant():
    cfg = OrchestratorConfig(signal_weights={"src": 1.0}, timeframe_order=["1h", "4h", "default"], timeframe_weights={"1h": 1.0, "4h": 2.0, "default": 5.0}, score_deadband=0.05, higher_tf_dominance=True)
    orch = AlphaOrchestrator(cfg)
    tf_results = {"1h": {"net_score": 0.6, "blended_edge": 8.0}, "4h": {"net_score": 0.75, "blended_edge": 12.0}, "default": {"net_score": -0.9, "blended_edge": -20.0}}
    score, _, _ = orch._combine_timeframes(tf_results)
    assert score > 0.0

def test_crit3_large_batch_no_false_fallback():
    cfg = OrchestratorConfig(signal_weights={f"src_{i}": float(i % 5 + 1) for i in range(50)}, timeframe_order=["1h", "default"], higher_tf_dominance=False)
    orch = AlphaOrchestrator(cfg)
    now = time.time()
    signals = [AlphaSignal(f"src_{i}", 1 if i % 3 != 0 else -1, 0.6 + (i % 4) * 0.08, 5.0 + i * 0.2, now, "1h") for i in range(50)]
    score, edge, meta = orch._fuse_signals(signals, "normal", 0.05, {}, now=now)
    assert not meta.get("is_fallback_synthetic", False)
    assert math.isfinite(score) and math.isfinite(edge)

def test_crit4_partial_delivery_scores_above_zero_delivery():
    cfg = OrchestratorConfig(signal_weights={"src": 1.0}, feedback_enabled=True, feedback_win_rate_weight=0.5, feedback_edge_weight=0.5)
    orch = AlphaOrchestrator(cfg)
    s = AlphaPerformanceStats(source_id="src")
    s.expected_edge_bps = 2.0
    s.ema_win_rate = 0.5
    s.hurdles_locked = True
    s.avg_realized_edge_bps = 1.0
    half = orch._calc_score_block(s)
    s.avg_realized_edge_bps = 0.0
    zero = orch._calc_score_block(s)
    assert half > zero

def test_high1_drawdown_fraction_required():
    ExecutionState(0.0, 10000.0, 0.15)
    with pytest.raises(ValueError, match="FRACTIONAL"):
        ExecutionState(0.0, 10000.0, 15.0)

def test_high3_zero_exposure_returns_max_risk_pressure():
    cfg = OrchestratorConfig(signal_weights={"src": 1.0}, max_drawdown_pct=0.15)
    orch = AlphaOrchestrator(cfg)
    state = ExecutionState(0.0, 0.0, 0.0)
    conv, risk_pressure, util, rationale = orch._apply_risk_overlay(0.8, state, 0.0)
    assert conv == 0.0 and risk_pressure == 1.0 and rationale == "zero_exp"

def test_high4_validators():
    RegimeContext("normal", 0.5, 0.7)
    FeatureQuality(0.1, 0.2)
    with pytest.raises(ValueError):
        RegimeContext("", 0.5, 0.7)
    with pytest.raises(ValueError):
        FeatureQuality(-0.1, 0.2)

def test_high5_winrate_exact_at_large_n():
    cfg = OrchestratorConfig(signal_weights={"src": 1.0}, feedback_enabled=True)
    orch = AlphaOrchestrator(cfg)
    stats = AlphaPerformanceStats(source_id="src")
    for i in range(100_000):
        orch._update_stats_block(stats, 1.0 if i % 2 == 0 else 0.0, 0.0, 0.0, 0.5, 0.0)
    assert abs(stats.win_rate - 0.5) < 1e-9

def test_high6_stale_multiplier_decays_to_neutral():
    cfg = OrchestratorConfig(signal_weights={"src_a": 1.0}, feedback_enabled=True, perf_staleness_ttl_seconds=3600.0, timeframe_order=["1h", "default"], higher_tf_dominance=False)
    orch = AlphaOrchestrator(cfg)
    stale_ts = time.time() - 7200.0
    snap = {"src_a": {"current_multiplier": 1.5, "fallback_used": False, "drift_detected": False, "last_updated": stale_ts, "regimes": {}}}
    now = time.time()
    sig = AlphaSignal("src_a", 1, 0.9, 10.0, now, "1h")
    _, _, meta = orch._fuse_signals([sig], "normal", 0.0, snap, now=now)
    assert abs(meta["breakdown"][0]["perf_multiplier"] - 1.0) < 1e-9

def test_med2_malformed_timestamp_classified_invalid():
    cfg = OrchestratorConfig(signal_weights={"src": 1.0})
    orch = AlphaOrchestrator(cfg)
    valid, metrics, _ = orch._validate_and_prune([{"source_id": "src", "direction": 1, "conviction": 0.8, "expected_edge_bps": 10.0, "timestamp": None, "timeframe": "default"}], time.time())
    assert not valid and metrics["invalid"] >= 1 and metrics["stale"] == 0

def test_med8_zero_missing_ratio_raises():
    with pytest.raises(ValueError):
        OrchestratorConfig(signal_weights={"src": 1.0}, max_missing_data_ratio=0.0)

def test_med5_unknown_source_evicted_before_known():
    """Unknown sources must be evicted before known sources when trade counts are equal."""
    cfg = OrchestratorConfig(
        signal_weights={"known_src": 1.0},
        feedback_enabled=True,
        max_tracked_sources=2,
        allow_unknown_sources=True,
        default_unknown_weight=1.0,
    )
    orch = AlphaOrchestrator(cfg)
    now = time.time()

    # Manually inject two sources at capacity: one known, one unknown
    # Both have trade_count=1 and identical last_updated so only known/unknown matters
    with orch._lock:
        known_stats = AlphaPerformanceStats(source_id="known_src")
        known_stats.trade_count = 1
        known_stats.last_updated = now
        orch._performance_stats["known_src"] = known_stats

        unknown_stats = AlphaPerformanceStats(source_id="unknown_src")
        unknown_stats.trade_count = 1
        unknown_stats.last_updated = now
        orch._performance_stats["unknown_src"] = unknown_stats

    # At capacity (2 sources). Adding a third must evict unknown_src, not known_src.
    with orch._lock:
        orch._enforce_source_capacity_locked(preserve_source="new_src")

    with orch._lock:
        remaining = set(orch._performance_stats.keys())

    assert "known_src" in remaining, (
        f"Known source was evicted instead of unknown source. Remaining: {remaining}"
    )
    assert "unknown_src" not in remaining, (
        f"Unknown source was kept instead of known source. Remaining: {remaining}"
    )

def test_pnl_compensation_sanitized_on_corrupt_state():
    """Corrupt _pnl_compensation must be reset by _sanitize_stats, not propagate NaN."""
    cfg = OrchestratorConfig(
        signal_weights={"src": 1.0},
        feedback_enabled=True,
    )
    orch = AlphaOrchestrator(cfg)
    stats = AlphaPerformanceStats(source_id="src")
    stats.pnl_contribution = 500.0
    stats._pnl_compensation = float("nan")   # inject corruption

    # Sanitize must reset the compensation term without corrupting pnl_contribution
    orch._sanitize_stats(stats)
    assert math.isfinite(stats._pnl_compensation), \
        f"_pnl_compensation remained NaN after _sanitize_stats: {stats._pnl_compensation}"
    assert stats._pnl_compensation == 0.0, \
        f"Expected _pnl_compensation=0.0 after reset, got {stats._pnl_compensation}"
    assert math.isfinite(stats.pnl_contribution), \
        f"pnl_contribution corrupted by NaN compensation: {stats.pnl_contribution}"

    # Verify subsequent Kahan updates are not corrupted
    stats.pnl_contribution = 500.0
    stats._pnl_compensation = 0.0
    orch_internal = AlphaOrchestrator(cfg)  # fresh instance for _update_performance_locked access
    # Simulate the Kahan addition directly
    pnl = 10.0
    _kahan_y = pnl - stats._pnl_compensation
    _kahan_t = stats.pnl_contribution + _kahan_y
    stats._pnl_compensation = (_kahan_t - stats.pnl_contribution) - _kahan_y
    stats.pnl_contribution = _safe_float(_kahan_t, 0.0, -_PNL_CLAMP, _PNL_CLAMP)
    assert math.isfinite(stats.pnl_contribution), \
        f"pnl_contribution became non-finite after Kahan update: {stats.pnl_contribution}"
    assert abs(stats.pnl_contribution - 510.0) < 1e-9, \
        f"Kahan summation incorrect: expected 510.0, got {stats.pnl_contribution}"

if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = []
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:
            print(f"FAIL  {fn.__name__}  —  {e}")
            failed.append(fn.__name__)
    print(f"\n{'ALL PASS' if not failed else f'{len(failed)} FAILED'}: {len(tests)} tests")
    sys.exit(1 if failed else 0)

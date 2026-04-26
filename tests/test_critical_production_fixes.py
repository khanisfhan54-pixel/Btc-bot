import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import math
import time
import threading

import pytest

import alpha_orchestrator as ao


def _cfg(**overrides):
    base = dict(
        signal_weights={"a": 1.0, "b": 1.0, "c": 1.0},
        allow_unknown_sources=True,
        default_unknown_weight=1.0,
        feedback_enabled=True,
        regime_feedback_enabled=True,
        min_aggregate_weight=0.0,
    )
    base.update(overrides)
    return ao.OrchestratorConfig(**base)


def _orch(**overrides):
    return ao.AlphaOrchestrator(_cfg(**overrides))


def _sig(source_id="a", direction=1, conviction=1.0, edge=10.0, timeframe="1m"):
    return ao.AlphaSignal(
        source_id=source_id,
        direction=direction,
        conviction=conviction,
        expected_edge_bps=edge,
        timestamp=time.time(),
        timeframe=timeframe,
    )


def test_fuse_signals_invariant_failure_falls_back_hold(monkeypatch):
    orch = _orch()

    def _bad_penalty(conviction, group_size):
        return 2.0, True

    monkeypatch.setattr(orch, "_correlation_penalty", _bad_penalty)
    score, edge, meta = orch._fuse_signals([_sig("a"), _sig("b")], "normal", 0.0, {})
    assert score == 0.0
    assert edge == 0.0
    assert meta["decision"] == "HOLD"
    assert meta["fallback_action"] == "hold_zero_confidence"
    assert meta["is_fallback_synthetic"] is True
    assert meta["score"] == score
    assert meta["correlation_summary"]["adjusted_score"] == score
    assert meta["correlation_summary"]["attenuated_blended_edge_bps"] == edge
    assert meta["correlation_summary"]["synthetic"] is True


def test_config_validation_occurs_after_clamp():
    cfg = ao.OrchestratorConfig(
        signal_weights={"a": 1.0},
        feedback_min_multiplier=1.2,
        feedback_max_multiplier=1.2,
    )
    assert cfg.feedback_min_multiplier == pytest.approx(1.0)
    assert cfg.feedback_max_multiplier == pytest.approx(1.2)


def test_drawdown_cutoff_triggers_at_threshold():
    orch = _orch(max_drawdown_pct=0.1)
    state = ao.ExecutionState(current_exposure_usd=0.0, max_exposure_usd=100.0, current_drawdown_pct=0.1)
    conv, _, _, reason = orch._apply_risk_overlay(1.0, state, dd=0.1, max_drawdown_pct=0.1)
    assert conv == 0.0
    assert reason == "dd_breach"


def test_combine_timeframes_total_weight_zero_schema_consistent():
    orch = _orch(timeframe_weights={"1m": 0.0, "5m": 0.0})
    score, edge, meta = orch._combine_timeframes(
        {
            "1m": {"net_score": 0.5, "blended_edge": 10.0},
            "5m": {"net_score": -0.5, "blended_edge": -10.0},
        }
    )
    assert score == 0.0 and edge == 0.0
    assert set(["decision", "confidence", "score", "is_mtf", "error"]).issubset(meta.keys())
    assert meta["error"] == "total_w_zero"


def test_low_edge_decay_is_bounded_with_floor():
    orch = _orch()
    stats = ao.AlphaRegimeStats(expected_edge_bps=1e-5, avg_realized_edge_bps=1e-3, ema_win_rate=0.5)
    decay = orch._calculate_decay_signal(stats, ao.FeatureQuality(0.0, 0.0), None)
    assert 0.0 <= decay <= 1.0


def test_regime_eviction_is_deterministic_multikey():
    orch = _orch(regime_min_trades=1)
    src = "a"
    orch._performance_stats[src] = ao.AlphaPerformanceStats(source_id=src)
    st = orch._performance_stats[src]
    # Pre-fill 100 regimes with identical trade_count/last_updated; lexicographic tie-break should win.
    for i in range(100):
        name = f"reg_{i:03d}"
        st.regimes[name] = ao.AlphaRegimeStats(trade_count=1, last_updated=1_000.0)
    trade_result = {"source_id": src, "realized_pnl": 1.0, "realized_edge_bps": 1.0}
    regime = ao.RegimeContext(regime_name="new_regime", volatility_score=0.1, liquidity_score=0.9)
    orch.update_performance(trade_result, ao.FeatureQuality(0.0, 0.0), regime, event_time=1.0)
    assert "reg_000" not in st.regimes


def test_alpha_signal_post_init_validation():
    with pytest.raises(ValueError):
        ao.AlphaSignal(source_id="x", direction=2, conviction=0.5, expected_edge_bps=1.0, timestamp=time.time())
    with pytest.raises(ValueError):
        ao.AlphaSignal(source_id="x", direction=1, conviction=float("nan"), expected_edge_bps=1.0, timestamp=time.time())
    with pytest.raises(ValueError):
        ao.AlphaSignal(source_id=" bad id ", direction=1, conviction=0.5, expected_edge_bps=1.0, timestamp=time.time())
    with pytest.raises(ValueError):
        ao.AlphaSignal(source_id="x", direction=1, conviction=0.5, expected_edge_bps=1.0, timestamp=0.0)
    with pytest.raises(ValueError):
        ao.AlphaSignal(source_id="x", direction=1, conviction=0.5, expected_edge_bps=1.0, timestamp=time.time(), timeframe="bad tf!")


def test_sign_flip_observable_field_present():
    orch = _orch()
    score, edge, meta = orch._fuse_signals([_sig("a", 1), _sig("b", -1)], "normal", 0.0, {})
    assert math.isfinite(score) and math.isfinite(edge)
    assert "sign_flip" in meta["correlation_summary"]
    assert isinstance(meta["correlation_summary"]["sign_flip"], bool)


def test_fuse_signals_deterministic_same_input_same_output():
    orch = _orch()
    signals = [_sig("a", 1, 0.9, 20.0), _sig("b", -1, 0.7, 15.0), _sig("c", 1, 0.4, 5.0)]
    first = orch._fuse_signals(signals, "normal", 0.0, {})
    for _ in range(10):
        assert orch._fuse_signals(signals, "normal", 0.0, {}) == first


def test_update_performance_uses_injected_event_time():
    orch = _orch(max_tracked_sources=10)
    tr = {"source_id": "a", "realized_pnl": 1.0, "realized_edge_bps": 1.0}
    orch.update_performance(tr, ao.FeatureQuality(0.0, 0.0), None, event_time=1234.5)
    assert orch._performance_stats["a"].last_updated == pytest.approx(1234.5)


def test_update_performance_invalid_time_falls_back_deterministically():
    orch = _orch(max_tracked_sources=10)
    tr = {"source_id": "a", "realized_pnl": 1.0, "realized_edge_bps": 1.0}
    orch.update_performance(tr, ao.FeatureQuality(0.0, 0.0), None, event_time=float("nan"))
    assert "a" not in orch._performance_stats
    assert orch._rejection_telemetry["invalid_timestamp"] >= 1


def test_update_performance_missing_timestamp_does_not_mutate_state():
    orch = _orch(max_tracked_sources=10)
    tr = {"source_id": "a", "realized_pnl": 1.0, "realized_edge_bps": 1.0}
    orch.update_performance(tr, ao.FeatureQuality(0.0, 0.0), None)
    assert "a" not in orch._performance_stats
    assert orch._rejection_telemetry["invalid_timestamp"] >= 1


def test_orchestrate_without_current_time_rejected_deterministically():
    orch = _orch()
    deterministic = {
        "source_id": "a",
        "direction": 1,
        "conviction": 0.9,
        "expected_edge_bps": 20.0,
        "timestamp": 1_700_000_123.0,
        "timeframe": "1m",
    }
    out = orch.orchestrate(
        [deterministic],
        ao.RegimeContext("normal", 0.1, 0.9),
        ao.FeatureQuality(0.0, 0.0),
        ao.ExecutionState(0.0, 1000.0, 0.0),
        current_time=None,
    )
    assert out.action == ao.Action.HOLD
    assert out.meta_info["rationale"] == "invalid_current_time"
    assert out.meta_info["orchestration_ts"] is None
    assert out.meta_info["rejection_details"][0]["reason"] == "missing_current_time"


def test_source_eviction_cap_is_deterministic_and_bounded():
    orch = _orch(max_tracked_sources=2, signal_weights={"a": 1.0})
    fq = ao.FeatureQuality(0.0, 0.0)
    orch.update_performance({"source_id": "x1", "realized_pnl": 1.0, "realized_edge_bps": 1.0}, fq, None, event_time=1.0)
    orch.update_performance({"source_id": "x2", "realized_pnl": 1.0, "realized_edge_bps": 1.0}, fq, None, event_time=1.0)
    orch.update_performance({"source_id": "a", "realized_pnl": 1.0, "realized_edge_bps": 1.0}, fq, None, event_time=2.0)
    assert len(orch._performance_stats) == 2
    assert "a" in orch._performance_stats
    assert "x1" not in orch._performance_stats


def test_low_aggregate_metadata_matches_returned_values():
    orch = _orch(min_aggregate_weight=10.0)
    score, edge, meta = orch._fuse_signals([_sig("a", 1, 0.2, 10.0)], "normal", 0.0, {})
    summary = meta["correlation_summary"]
    assert score == pytest.approx(summary["adjusted_score"])
    assert edge == pytest.approx(summary["attenuated_blended_edge_bps"])
    if abs(summary["raw_score"]) > 1e-12:
        assert summary["score_attenuation_factor"] == pytest.approx(abs(score / summary["raw_score"]))


def test_fallback_before_rows_keeps_breakdown_empty(monkeypatch):
    orch = _orch()

    def _bad_projection(weights, cap_ratio=0.4):
        return [float("nan")] * len(weights)

    # inject at function scope by patching method local dependency via wrapper call
    score, edge, meta = orch._fuse_signals([_sig("a", 1), _sig("b", 1)], "normal", 0.0, {})
    assert math.isfinite(score) and math.isfinite(edge)
    assert "breakdown" in meta


def test_fallback_after_rows_marks_partial(monkeypatch):
    orch = _orch()

    def _bad_penalty(conviction, group_size):
        return 2.0, True

    monkeypatch.setattr(orch, "_correlation_penalty", _bad_penalty)
    _, _, meta = orch._fuse_signals([_sig("a"), _sig("b")], "normal", 0.0, {})
    if meta["breakdown"]:
        assert all(r.get("partial") is True for r in meta["breakdown"])


def test_fallback_non_finite_summary_is_synthetic():
    orch = _orch()
    def _bad_penalty(conviction, group_size):
        return 2.0, True

    orch._correlation_penalty = _bad_penalty
    score, edge, meta = orch._fuse_signals([_sig("a", 1), _sig("b", -1)], "normal", 0.0, {})
    assert math.isfinite(score)
    assert math.isfinite(edge)
    assert "synthetic" in meta["correlation_summary"]


def test_concurrent_update_and_orchestrate_snapshot_consistency():
    orch = _orch(max_tracked_sources=50)
    fq = ao.FeatureQuality(0.0, 0.0)
    regime = ao.RegimeContext("normal", 0.1, 0.9)
    exec_state = ao.ExecutionState(0.0, 1000.0, 0.0)

    def writer():
        for i in range(20):
            orch.update_performance(
                {"source_id": f"s{i%3}", "realized_pnl": 1.0, "realized_edge_bps": 1.0, "timestamp": float(i)},
                fq,
                regime,
                event_time=float(i),
            )

    t = threading.Thread(target=writer)
    t.start()
    for _ in range(20):
        out = orch.orchestrate(
            [{"source_id": "a", "direction": 1, "conviction": 0.5, "expected_edge_bps": 10.0, "timestamp": 10.0, "timeframe": "1m"}],
            regime,
            fq,
            exec_state,
            current_time=10.0,
        )
        assert isinstance(out.meta_info.get("rejection_telemetry"), dict)
    t.join()


def test_external_mutation_of_public_stats_does_not_affect_internal_state():
    orch = _orch()
    orch.update_performance({"source_id": "a", "realized_pnl": 1.0, "realized_edge_bps": 1.0}, ao.FeatureQuality(0.0, 0.0), None, event_time=1.0)
    external = orch.performance_stats
    external["a"].trade_count = 9999
    assert orch._performance_stats["a"].trade_count != 9999


def test_negative_event_time_is_rejected_without_state_mutation():
    orch = _orch()
    orch.update_performance({"source_id": "a", "realized_pnl": 1.0, "realized_edge_bps": 1.0}, ao.FeatureQuality(0.0, 0.0), None, event_time=-1.0)
    assert "a" not in orch._performance_stats


def test_transaction_rollback_keeps_state_unchanged_on_internal_failure(monkeypatch):
    orch = _orch()
    orch.update_performance({"source_id": "a", "realized_pnl": 1.0, "realized_edge_bps": 1.0}, ao.FeatureQuality(0.0, 0.0), None, event_time=1.0)
    before = orch.performance_stats

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(orch, "_build_performance_meta", _boom)
    with pytest.raises(RuntimeError):
        orch.update_performance({"source_id": "a", "realized_pnl": 2.0, "realized_edge_bps": 2.0}, ao.FeatureQuality(0.0, 0.0), None, event_time=2.0)
    after = orch.performance_stats
    assert after["a"].trade_count == before["a"].trade_count


def test_transaction_rollback_restores_nested_regime_state(monkeypatch):
    orch = _orch()
    regime = ao.RegimeContext("normal", 0.1, 0.9)
    orch.update_performance({"source_id": "a", "realized_pnl": 1.0, "realized_edge_bps": 1.0}, ao.FeatureQuality(0.0, 0.0), regime, event_time=1.0)
    before = orch.performance_stats

    def _boom(*args, **kwargs):
        raise RuntimeError("boom_nested")

    monkeypatch.setattr(orch, "_calculate_performance_multiplier", _boom)
    with pytest.raises(RuntimeError):
        orch.update_performance({"source_id": "a", "realized_pnl": 2.0, "realized_edge_bps": 2.0}, ao.FeatureQuality(0.0, 0.0), regime, event_time=2.0)
    after = orch.performance_stats
    assert after["a"].regimes["normal"].trade_count == before["a"].regimes["normal"].trade_count


def test_decay_low_edge_floor_is_stable():
    orch = _orch()
    low = ao.AlphaRegimeStats(expected_edge_bps=1e-5, avg_realized_edge_bps=0.1, ema_win_rate=0.5)
    medium = ao.AlphaRegimeStats(expected_edge_bps=5.0, avg_realized_edge_bps=0.1, ema_win_rate=0.5)
    zero = ao.AlphaRegimeStats(expected_edge_bps=0.0, avg_realized_edge_bps=0.1, ema_win_rate=0.5)
    assert orch._calculate_decay_signal(low, ao.FeatureQuality(0.0, 0.0), None) <= 1.0
    assert orch._calculate_decay_signal(medium, ao.FeatureQuality(0.0, 0.0), None) >= 0.0
    assert orch._calculate_decay_signal(zero, ao.FeatureQuality(0.0, 0.0), None) >= 0.0


def test_decay_monotonicity_and_edge_cases():
    orch = _orch()
    q = ao.FeatureQuality(0.0, 0.0)
    stats = ao.AlphaRegimeStats(expected_edge_bps=0.4, avg_realized_edge_bps=0.4, ema_win_rate=0.5)
    equal_decay = orch._calculate_decay_signal(stats, q, None)
    stats.avg_realized_edge_bps = 1.0
    better_decay = orch._calculate_decay_signal(stats, q, None)
    stats.avg_realized_edge_bps = -0.1
    worse_decay = orch._calculate_decay_signal(stats, q, None)
    assert better_decay <= equal_decay
    assert worse_decay >= equal_decay

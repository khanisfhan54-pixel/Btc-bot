import copy

from alpha_orchestrator import (
    Action,
    AlphaOrchestrator,
    AlphaSignal,
    ExecutionState,
    FeatureQuality,
    OrchestratedAction,
    OrchestratorConfig,
    RegimeContext,
)


def _orchestrator() -> AlphaOrchestrator:
    cfg = OrchestratorConfig(signal_weights={"src_a": 1.0}, signal_ttl_seconds=60.0)
    return AlphaOrchestrator(cfg)


def _exec_state() -> ExecutionState:
    return ExecutionState(current_exposure_usd=0.0, max_exposure_usd=1000.0, current_drawdown_pct=0.01)


def _regime(liq: float = 0.9) -> RegimeContext:
    return RegimeContext(regime_name="normal", volatility_score=0.2, liquidity_score=liq)


def _signals(now: float) -> list[AlphaSignal]:
    return [AlphaSignal(source_id="src_a", direction=1, conviction=0.9, expected_edge_bps=5.0, timestamp=now)]


def _assert_hold_schema(meta_info: dict) -> None:
    assert "final_conviction" in meta_info
    assert "risk_metrics" in meta_info
    assert "quality_metrics" in meta_info
    assert meta_info["risk_metrics"]["risk_pressure"] == 0.0
    assert meta_info["quality_metrics"]["combined_multiplier"] == 1.0
    assert meta_info["quality_metrics"]["conviction_post_quality"] == 0.0
    assert meta_info["quality_metrics"]["conviction_pre_quality"] == 0.0


def test_hold_meta_schema_consistent_across_all_hold_paths() -> None:
    now = 1_700_000_000.0

    poor_feature_quality = _orchestrator().orchestrate(
        signals=_signals(now),
        regime=_regime(),
        feature_quality=FeatureQuality(staleness_ratio=0.0, missing_data_ratio=0.95),
        exec_state=_exec_state(),
        current_time=now,
    ).meta_info

    insufficient_liquidity = _orchestrator().orchestrate(
        signals=_signals(now),
        regime=_regime(liq=0.01),
        feature_quality=FeatureQuality(staleness_ratio=0.0, missing_data_ratio=0.0),
        exec_state=_exec_state(),
        current_time=now,
    ).meta_info

    no_valid_signals = _orchestrator().orchestrate(
        signals=[AlphaSignal(source_id="src_a", direction=1, conviction=0.9, expected_edge_bps=5.0, timestamp=now - 10_000.0)],
        regime=_regime(),
        feature_quality=FeatureQuality(staleness_ratio=0.0, missing_data_ratio=0.0),
        exec_state=_exec_state(),
        current_time=now,
    ).meta_info

    invalid_current_time = _orchestrator().orchestrate(
        signals=_signals(now),
        regime=_regime(),
        feature_quality=FeatureQuality(staleness_ratio=0.0, missing_data_ratio=0.0),
        exec_state=_exec_state(),
        current_time=None,
    ).meta_info

    all_holds = [poor_feature_quality, insufficient_liquidity, no_valid_signals, invalid_current_time]
    for hold_meta in all_holds:
        _assert_hold_schema(hold_meta)

    key_sets = [set(m.keys()) for m in all_holds]
    assert key_sets[0] == key_sets[1] == key_sets[2] == key_sets[3]


def test_success_path_meta_info_unchanged_for_same_input() -> None:
    orch = _orchestrator()
    now = 1_700_000_000.0
    kwargs = dict(
        signals=_signals(now),
        regime=_regime(),
        feature_quality=FeatureQuality(staleness_ratio=0.0, missing_data_ratio=0.0),
        exec_state=_exec_state(),
        current_time=now,
    )

    baseline_success_meta = copy.deepcopy(orch.orchestrate(**kwargs).meta_info)
    new_success_meta = copy.deepcopy(orch.orchestrate(**kwargs).meta_info)

    assert baseline_success_meta == new_success_meta


def test_hold_schema_enforced_at_finalization_boundary_for_direct_hold_action() -> None:
    orch = _orchestrator()
    raw_hold = OrchestratedAction(Action.HOLD, 0.0, 0.0, 0.0, {})
    finalized = orch._finalize_action(raw_hold)
    _assert_hold_schema(finalized.meta_info)


def test_hold_finalization_is_deterministic() -> None:
    orch = _orchestrator()
    raw_hold = OrchestratedAction(Action.HOLD, 0.0, 0.0, 0.0, {"environmental_context": {"stale_ratio": 0.2, "missing_ratio": 0.1}})
    run1 = orch._finalize_action(raw_hold)
    run2 = orch._finalize_action(raw_hold)
    assert run1.meta_info == run2.meta_info

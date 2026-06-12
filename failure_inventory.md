# Failure Inventory

## Cluster 1 — Regime Engine
### Failing Tests
- test_refactor.py::test_pnl_baseline_anchoring
- tests/integration/test_regime_engine_integration.py::test_regime_engine_integration
- tests/test_5m_l2_strict_and_report.py::TestComputeHmmRegimeNormalization::test_score_map_sums_to_one[alpha0]
- tests/test_5m_l2_strict_and_report.py::TestComputeHmmRegimeNormalization::test_score_map_sums_to_one[alpha1]
- tests/test_5m_l2_strict_and_report.py::TestComputeHmmRegimeNormalization::test_score_map_sums_to_one[alpha2]
- tests/test_5m_l2_strict_and_report.py::TestComputeHmmRegimeNormalization::test_score_map_sums_to_one[alpha3]
- tests/test_5m_l2_strict_and_report.py::TestComputeHmmRegimeNormalization::test_score_map_sums_to_one[alpha4]
- tests/test_5m_l2_strict_and_report.py::TestComputeHmmRegimeNormalization::test_score_map_sums_to_one[alpha5]
- tests/test_5m_l2_strict_and_report.py::TestComputeHmmRegimeNormalization::test_score_map_sums_to_one[alpha6]
- tests/test_5m_l2_strict_and_report.py::TestComputeHmmRegimeNormalization::test_degenerate_near_zero_sum_falls_back_to_uniform
- tests/test_advanced_regime_engine.py::test_bull_bias
- tests/test_advanced_regime_engine.py::test_range_presence
- tests/test_advanced_regime_engine_hardening.py::test_update_handles_nan_return
- tests/test_advanced_regime_engine_hardening.py::test_update_dimension_failure_on_bad_feature_shape
- tests/test_advanced_regime_engine_hardening.py::test_mtf_partial_failure_degrades_not_crash
- tests/test_advanced_regime_engine_hardening.py::test_sjm_non_finite_falls_back_to_last_valid
- tests/test_advanced_regime_engine_hardening.py::test_update_handles_extreme_returns_beyond_two_sigma_bounds
- tests/test_advanced_regime_engine_hardening.py::test_build_output_fallback_path_never_throws_on_schema_failure
- tests/test_advanced_regime_engine_hardening.py::test_build_output_fallback_handles_runtime_float_exceptions
- tests/test_advanced_regime_engine_hardening.py::test_schema_failure_metrics
- tests/test_advanced_regime_engine_hardening.py::test_load_state_logs_degrade_fields
- tests/test_advanced_regime_engine_hardening.py::test_regime_markov_smoother_directional_evidence_is_symmetric_and_not_suppressed
- tests/test_advanced_regime_engine_hardening.py::test_snapshot_path_no_tautological_consistency_loop
- tests/test_advanced_regime_engine_hardening.py::test_load_snapshot_logs_structured_error
- tests/test_advanced_regime_engine_hardening.py::test_circuit_breaker_vol_shock_short_circuits_same_tick
- tests/test_advanced_regime_engine_hardening.py::test_breaker_reason_and_healing_counter_not_overwritten_same_tick
- tests/test_advanced_regime_engine_hardening.py::test_healing_branch_returns_immediately_after_self_heal
- tests/test_advanced_regime_engine_hardening.py::test_negative_equity_clamped_and_breaker_tripped
- tests/test_advanced_regime_engine_hardening.py::test_price_return_mismatch_emits_fail_safe_without_pnl_state_contamination
- tests/test_advanced_regime_engine_hardening.py::test_breaker_cooldown_initialization_consistent_across_trigger_paths
- tests/test_advanced_regime_engine_hardening.py::test_warning_worker_does_not_keep_engine_alive_strongly
- tests/test_advanced_regime_engine_hardening.py::test_full_self_heal_resets_last_price_reference
- tests/test_advanced_regime_engine_live_risks.py::test_timestamp_less_pnl_policy_enabled_updates_equity
- tests/test_advanced_regime_engine_live_risks.py::test_timestamp_less_pnl_policy_disabled_marks_degraded
- tests/test_advanced_regime_engine_live_risks.py::test_shock_warmup_transition_is_smooth
- tests/test_advanced_regime_engine_live_risks.py::test_time_aware_ema_and_range_are_rate_consistent
- tests/test_advanced_regime_engine_live_risks.py::test_macro_only_fallback_bypasses_shock_memory_modulation
- tests/test_advanced_regime_engine_live_risks.py::test_mtf_missing_base_features_fails_safe
- tests/test_advanced_regime_engine_live_risks.py::test_pnl_mode_locked_and_tick_order_violation_degrades
- tests/test_advanced_regime_engine_mission_critical_refactor.py::test_mtf_graceful_degradation_survives_base_feature_corruption
- tests/test_advanced_regime_engine_production_fixes.py::test_pnl_tracking_subpaths_and_breakers
- tests/test_advanced_regime_engine_production_fixes.py::test_edge_sizing_single_modulation_path
- tests/test_advanced_regime_engine_production_fixes.py::test_alpha_override_cannot_bypass_directional_edge_gate
- tests/test_advanced_regime_engine_production_fixes.py::test_range_from_flat_has_deterministic_nonzero_signed_size
- tests/test_advanced_regime_engine_verified_fixes.py::test_mtf_default_base_only_does_not_total_fail
- tests/test_advanced_regime_engine_verified_fixes.py::test_mtf_unknown_and_zero_weight_timeframes_degrade_predictably
- tests/test_advanced_regime_engine_verified_fixes.py::test_price_return_mismatch_early_return_updates_timestamp_anchor
- tests/test_advanced_regime_engine_verified_fixes.py::test_invalid_scalar_return_fails_safe_with_observable_status
- tests/test_monte_carlo_stress.py::test_mc_bull_trend_recognized
- tests/test_regime_accuracy.py::test_accuracy_trend_recall
- tests/test_regime_calibration_gate.py::test_uncalibrated_weights_fail_closed
- tests/test_regime_engine_full_audit.py::TestRegimeClassification::test_strong_bull_returns_trend
- tests/test_regime_engine_full_audit.py::TestRegimeClassification::test_strong_bear_returns_bear
- tests/unit/test_edge_r_negative.py::test_edge_r_negative
- tests/unit/test_ema_decay.py::test_ema_decay_long_gap_lower
### Root Cause
Regime failures cluster around degraded calibration/provenance gating, score normalization expectations, RANGE/TREND/BEAR classification recall, PnL timestamp anchoring, circuit-breaker/self-heal behavior, and EMA/range dynamics. The Stage 1 removal deliberately changed strategy behavior for RANGE labels that previously could be overridden by EMA hints, so tests expecting that override now fail.
### Estimated Repair Complexity: High
### Production Risk: Critical
### Recommended Repair Order: 1

## Cluster 2 — Signal Engine
### Failing Tests
- tests/integration/test_signal_engine_candles.py::test_signal_engine_candles_non_hold
- tests/test_phase_a_surgical.py::test_ml_prob_not_called_in_active_sweep
- tests/test_phase_a_surgical.py::test_shrink_prob_rename
- tests/test_phase_a_surgical.py::test_shrink_prob_values
- tests/test_phase_b_features.py::test_level_weighted_ofi_direction
- tests/test_phase_b_features.py::test_level_weighted_ofi_magnitude_ordering
- tests/test_phase_b_features.py::test_pool_bar_reset_on_atr_expiry
- tests/test_phase_b_features.py::test_phase_a_items_still_present
- tests/test_phase_c_regime.py::test_active_sweep_hold_in_trending_up_high_sweep
- tests/test_phase_c_regime.py::test_active_sweep_hold_in_trending_down_low_sweep
- tests/test_pipeline_contract_regressions.py::test_engine_smc_cache_key_distinguishes_same_second_market_changes
### Root Cause
Signal failures cluster around ACTIVE_SWEEP behavior, legacy phase assertions, OFI level weighting, pool expiry accounting, and SMC cache contracts. The Stage 2 removal deliberately changed behavior for trend-aligned fake sweeps from HOLD suppression back to actionable BUY/SELL.
### Estimated Repair Complexity: Medium
### Production Risk: High
### Recommended Repair Order: 2

## Cluster 3 — Execution Layer
### Failing Tests
- tests/integration/test_bracket_failure_compensation.py::test_bracket_failure_compensation_calls_alert
### Root Cause
Bracket placement failure handling submits emergency close but does not trigger the expected alert counter/callback path in the test.
### Estimated Repair Complexity: Low
### Production Risk: Critical
### Recommended Repair Order: 3

## Cluster 4 — Risk Layer
### Failing Tests
- tests/test_circuit_breaker_reason_update.py::test_circuit_breaker_reason_updates_when_active
- tests/test_main_invariants.py::test_invariant_fallback_engine_allow_trade_false
- tests/test_main_invariants.py::test_fix_a3_fallback_feature_engine_triggers_failsafe
- tests/test_main_invariants.py::test_no_regression_fallback_engine_stubs
- tests/test_main_regression.py::TestIssueC_ConstantValidation::test_defaults_pass_validation
### Root Cause
Risk-layer failures cluster around circuit-breaker reason updates, fallback engine fail-safe invariants, and boot/default validation constants.
### Estimated Repair Complexity: Medium
### Production Risk: High
### Recommended Repair Order: 4

## Cluster 5 — Replay Layer
### Failing Tests
- tests/test_replay_engine.py::test_snapshot_restore_failure_rolls_back_engine_state
- tests/test_replay_engine.py::test_unsafe_replay_is_faster_than_deepcopy_replay
- tests/test_replay_engine.py::test_snapshot_replay_isolation_replay_twice_same_result
- tests/test_replay_engine.py::test_snapshot_restore_fast_path_not_timeout
- tests/test_replay_engine.py::test_fsm_error_resets_between_snapshot_replays
- tests/test_replay_engine.py::test_snapshot_rollback_uses_schema_safe_snapshot_fallback
- tests/test_replay_engine.py::test_self_heal_event_replay_runs_under_engine_lock
### Root Cause
Replay failures cluster around snapshot rollback/isolation, timeout/performance expectations, FSM error reset semantics, schema-safe fallback snapshots, and self-heal lock behavior.
### Estimated Repair Complexity: High
### Production Risk: High
### Recommended Repair Order: 5

## Cluster 6 — Orchestration Layer
### Failing Tests
- tests/stress/test_alpha_orchestration_stress.py::test_feedback_loop_multiplier_respects_bounds_and_adapts
- tests/test_alpha_feedback_behavior.py::test_bad_alpha_gets_killed
- tests/test_alpha_feedback_behavior.py::test_good_alpha_gets_promoted
- tests/test_alpha_orchestration_comprehensive.py::TestRegimeIntegration::test_regime_cold_start_no_adjustment
- tests/test_alpha_orchestration_comprehensive.py::TestPerformanceFeedback::test_multiplier_never_exceeds_max
- tests/test_alpha_orchestration_comprehensive.py::TestPerformanceFeedback::test_multiplier_never_below_min
- tests/test_alpha_orchestration_comprehensive.py::TestPerformanceFeedback::test_decay_score_bounded
- tests/test_alpha_orchestration_comprehensive.py::TestRegimeFeedback::test_regime_stats_tracked
- tests/test_alpha_orchestration_comprehensive.py::TestRegimeFeedback::test_regime_multiplier_bounded
- tests/test_alpha_orchestration_comprehensive.py::TestRegimeFeedback::test_regime_fallback_on_cold_start
- tests/test_alpha_orchestration_comprehensive.py::TestConfigValidation::test_min_multiplier_greater_than_max_rejected
- tests/test_alpha_orchestrator_meta_schema.py::test_hold_schema_enforced_at_finalization_boundary_for_direct_hold_action
- tests/test_alpha_orchestrator_meta_schema.py::test_hold_finalization_is_deterministic
- tests/test_main_invariants.py::test_run_all_engines_is_deterministic
- tests/test_regime_wiring_audit.py::test_main_does_not_shadow_engine_symbols
- tests/test_regime_wiring_audit.py::test_main_signal_pipeline_engine_constructed
- tests/test_regime_wiring_audit.py::test_sniper_execution_engine_signal_only_does_not_execute
- tests/test_regression_baseline.py::TestOrchestrationParity::test_orchestration_degraded_labels_non_production
### Root Cause
Orchestration failures cluster around missing/None alpha performance metadata, multiplier bounds/decay, regime feedback stats, meta schema finalization, deterministic engine runs, and wiring audits.
### Estimated Repair Complexity: High
### Production Risk: High
### Recommended Repair Order: 6

## Cluster 7 — Integration Layer
### Failing Tests
- tests/system/test_boot_validation.py::test_boot_validation
- tests/test_5m_l2_strict_and_report.py::TestRunValidationReportHonesty::test_report_comparison_fields_present
- tests/test_5m_l2_strict_and_report.py::TestRunValidationReportHonesty::test_report_contains_run_status_field
- tests/test_5m_l2_strict_and_report.py::TestRunValidationReportHonesty::test_report_calibration_section_present
- tests/test_5m_l2_strict_and_report.py::TestRunValidationReportHonesty::test_unavailable_metrics_field_present
- tests/test_5m_l2_strict_and_report.py::TestRunValidationReportHonesty::test_unavailable_metrics_not_blocker
### Root Cause
Integration failures cluster around system boot validation and research/validation report schema honesty fields.
### Estimated Repair Complexity: Medium
### Production Risk: Medium
### Recommended Repair Order: 7

## Summary
- Total failures: 103
- Tests passed: 1140
- Strategy behavior changes detected: [Removed unsafe EMA-based RANGE-to-TREND/BEAR regime overrides; removed trend-aligned ACTIVE_SWEEP HOLD suppression so qualifying fake sweeps can emit BUY/SELL]
- Remaining production risks: [Regime classification/normalization instability, calibration/provenance gating failures, active sweep compatibility failures, execution alerting gap for unprotected positions, replay snapshot isolation failures, orchestration metadata/fallback inconsistencies]
- Recommended next repair sequence: [1. Regime Engine, 2. Signal Engine, 3. Execution Layer, 4. Risk Layer, 5. Replay Layer, 6. Orchestration Layer, 7. Integration Layer]

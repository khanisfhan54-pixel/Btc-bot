# Failure Cluster Analysis

## Executive finding

The 46 reproduced failures collapse into roughly 12 root-cause clusters. The minimum high-leverage defect set is smaller than the raw failure count: lock ownership, regime classification/calibration, feed-status schema drift, and circuit-breaker precedence explain most failures.

## Cluster A — Regime classification / TREND-RANGE recall collapse

**Tests**

- `tests/test_advanced_regime_engine.py::test_bull_bias`
- `tests/test_advanced_regime_engine.py::test_range_presence`
- `tests/test_regime_engine_full_audit.py::TestRegimeClassification::test_strong_bull_returns_trend`
- `tests/test_regime_engine_full_audit.py::TestRegimeClassification::test_flat_returns_range`
- `tests/test_regime_accuracy.py::test_accuracy_trend_recall`
- Related integration symptom: `tests/integration/test_regime_engine_integration.py::test_regime_engine_integration`

**Root cause**

`compute_hmm_regime()` maps 3-state probabilities into 4 labels using heuristic, non-normalized scores. The current weight artifact is synthetic and 3-state. There is no trained RANGE state, and bull streams can be smoothed/heuristically mapped away from TREND.

**Classification mix**: REAL_BUG.

**Estimated fix count**: 1 architectural/model fix, plus calibration validation gates.

## Cluster B — Feed-status schema drift

**Tests**

- `test_update_dimension_failure_on_bad_feature_shape`
- `test_mtf_partial_failure_degrades_not_crash`
- `test_circuit_breaker_vol_shock_short_circuits_same_tick`
- `test_mtf_default_base_only_does_not_total_fail`
- `test_mtf_unknown_and_zero_weight_timeframes_degrade_predictably`
- `test_price_return_mismatch_early_return_updates_timestamp_anchor`
- `test_invalid_scalar_return_fails_safe_with_observable_status`

**Root cause**

`_build_output()` now canonicalizes feed status into `risk_metrics.feed_status = {"primary": ..., "flags": [...]}`. Tests still assert strings.

**Classification mix**: STALE_TEST.

**Estimated fix count**: 1 contract/test update.

## Cluster C — Self-heal lock ownership and concurrent healing

**Tests**

- `test_self_heal_unknown_error_always_executes_fallback_recovery`
- `test_self_heal_fallback_mapping_without_errors_module`
- `test_full_self_heal_resets_last_price_reference`
- `test_concurrent_self_heal`
- `test_equity_persistence`
- `test_lock_safety_concurrent_self_heal_and_update`

**Root cause**

`_self_heal()` unconditionally releases `self._lock` to run side effects, assuming the current thread owns the lock. Direct calls do not own the lock; concurrent calls can release un-acquired locks.

**Classification mix**: REAL_BUG.

**Estimated fix count**: 1 core concurrency fix.

## Cluster D — Circuit-breaker precedence/reason policy

**Tests**

- `test_circuit_breaker_preserves_first_reason_and_trigger_tick`
- `test_breaker_reason_and_healing_counter_not_overwritten_same_tick`
- `test_breaker_cooldown_initialization_consistent_across_trigger_paths`

**Root cause**

`_trigger_circuit_breaker()` assigns `_circuit_breaker_reason` before checking whether a breaker is already active or already triggered in the same tick. This means latest reason wins in stored state, even when activation state does not change.

**Classification mix**: ARCHITECTURE_MISMATCH.

**Estimated fix count**: 1 policy decision + implementation.

## Cluster E — Price validation versus fail-safe expectations

**Tests**

- `test_update_handles_nan_return`
- `test_update_handles_extreme_returns_beyond_two_sigma_bounds`
- `test_negative_equity_clamped_and_breaker_tripped`
- `test_time_aware_ema_and_range_are_rate_consistent`
- `test_pnl_tracking_subpaths_and_breakers`

**Root cause**

`update()` now fail-fast raises `ValueError` for non-numeric, non-finite, non-positive price before downstream fail-safe paths. Several tests intend to exercise return/equity/EMA behavior but pass invalid price values.

**Classification mix**: WRONG_EXPECTATION.

**Estimated fix count**: 1 test fixture/contract correction.

## Cluster F — Schema guard monkeypatch / Prometheus labels

**Tests**

- `test_build_output_fallback_path_never_throws_on_schema_failure`
- `test_build_output_fallback_handles_runtime_float_exceptions`
- `test_schema_failure_metrics`
- `TestRegimeOutputSchema::test_build_output_failsafe`

**Root cause**

Tests rely on older `_validate_output_schema` call signature/metric labels or expect invalid schema inputs to pass unchanged.

**Classification mix**: mostly STALE_TEST / WRONG_EXPECTATION.

**Estimated fix count**: 1 test/schema contract cleanup.

## Cluster G — MTF degradation semantics

**Tests**

- `test_mtf_missing_base_features_fails_safe`
- `test_mtf_graceful_degradation_survives_base_feature_corruption`

**Root cause**

Current MTF paths distinguish `NO_FEATURES`, macro-only fallback, and explicit structured flags. Tests expect older or less specific statuses.

**Classification mix**: ARCHITECTURE_MISMATCH.

**Estimated fix count**: 1 MTF contract decision.

## Cluster H — Price/return mismatch path reliability

**Tests**

- `test_price_return_mismatch_emits_fail_safe_without_pnl_state_contamination`

**Root cause**

The mismatch check is gated by anchor freshness/policy; in the observed sequence it can miss the fail-safe path and continue to `range_mean_revert`.

**Classification mix**: REAL_BUG.

**Estimated fix count**: 1 reconciliation path fix.

## Cluster I — Warning worker lifecycle

**Tests**

- `test_warning_worker_does_not_keep_engine_alive_strongly`

**Root cause**

Worker/finalizer/thread lifecycle retains the engine object or delays collection despite weakref intent.

**Classification mix**: REAL_BUG.

**Estimated fix count**: 1 worker lifecycle fix.

## Cluster J — Shock warmup monotonicity

**Tests**

- `test_shock_warmup_transition_is_smooth`

**Root cause**

`_shock_threshold()`/`_warmup_progress()` can produce equal early thresholds (`t0 == t1`), not the expected monotonic transition.

**Classification mix**: REAL_BUG.

**Estimated fix count**: 1 warmup formula fix.

## Cluster K — Main/engine integration surface

**Tests**

- `test_main_does_not_shadow_engine_symbols`
- `test_main_signal_pipeline_engine_constructed`
- `test_sniper_execution_engine_signal_only_does_not_execute`

**Root cause**

The import/bootstrap/live-mode contract changed: legacy exports are missing, pipeline is not constructed, and signal-only construction is blocked by live-mode guard.

**Classification mix**: ARCHITECTURE_MISMATCH and REAL_BUG.

**Estimated fix count**: 2 integration contract decisions/fixes.

## Cluster L — State/snapshot logging and replay instrumentation

**Tests**

- `test_load_state_logs_degrade_fields`
- `test_snapshot_path_no_tautological_consistency_loop`
- `test_load_snapshot_logs_structured_error`

**Root cause**

Logging and replay payload expectations are stale or mismatched against current load/snapshot behavior.

**Classification mix**: ARCHITECTURE_MISMATCH.

**Estimated fix count**: 1 observability contract cleanup.

## Minimum defect set

If fixing actual defects first, highest leverage appears to be:

1. `_self_heal()` lock ownership/concurrency: resolves 6 direct failures and removes critical runtime thread-safety risk.
2. Regime classification/calibration alignment: resolves 5 direct classification/accuracy failures and the largest production validity risk.
3. Circuit-breaker precedence policy: resolves 3 direct failures and reduces risk-control ambiguity.
4. Feed-status schema compatibility: resolves 7 failures, but mostly stale tests; lower production risk than the above.

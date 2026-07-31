# Current Regime Failure Report

Generated: 2026-06-12 (UTC)

## Scope

Regime-related pytest files were selected by filename/path (`regime`, `advanced_regime_engine`, calibration gate, and stale-regime integration), plus the calibration pipeline E2E test because it exercises active regime weights.

## Command

```bash
pytest -vv tests/test_phase_c_regime.py tests/test_regime_accuracy.py tests/test_advanced_regime_engine_self_heal_contention.py tests/test_advanced_regime_engine_production_fixes.py tests/test_regime_calibration_gate.py tests/test_advanced_regime_engine_verified_fixes.py tests/test_regime_wiring_audit.py tests/test_advanced_regime_engine_live_risks.py tests/test_advanced_regime_engine.py tests/test_advanced_regime_engine_mission_critical_refactor.py tests/test_regime_engine_full_audit.py tests/test_advanced_regime_engine_production_hardening.py tests/test_calibrate_regime_weights.py tests/test_advanced_regime_engine_hardening.py tests/integration/test_stale_regime_halt.py tests/integration/test_regime_engine_integration.py tests/test_calibration_pipeline_e2e.py stop_hunt_engine/tests/test_regime_staleness_integration.py stop_hunt_engine/tests/test_regime_context_stale.py stop_hunt_engine/tests/test_regime_adapter_preserves_timestamp.py
```

## Result

- Exit code: `1`
- Collected: `270`
- Passed: `205`
- Failed: `65`
- Warnings: `2`
- Runtime: `30.11s`

## Short failure summary

```text
=========================== short test summary info ============================
FAILED tests/test_phase_c_regime.py::test_detect_regime_returns_valid_vocab - AssertionError: assert 'UPTREND' == 'TRENDING_UP'
  
  - TRENDING_UP
  + UPTREND
FAILED tests/test_phase_c_regime.py::test_volatile_in_valid_regimes - AssertionError: assert 'UPTREND' not in frozenset({'UNKNOWN', 'RANGING', 'TRENDING_UP', 'VOLATILE', 'UPTREND', 'DOWNTREND', 'TRENDING_DOWN'})
FAILED tests/test_phase_c_regime.py::test_active_sweep_hold_in_trending_up_high_sweep - AttributeError: 'LiquiditySweepAlpha' object has no attribute '_bar_count'. Did you mean: '_ofi_count'?
FAILED tests/test_phase_c_regime.py::test_active_sweep_hold_in_trending_down_low_sweep - AttributeError: 'LiquiditySweepAlpha' object has no attribute '_bar_count'. Did you mean: '_ofi_count'?
FAILED tests/test_phase_c_regime.py::test_active_sweep_not_blocked_in_ranging - AttributeError: 'LiquiditySweepAlpha' object has no attribute '_bar_count'. Did you mean: '_ofi_count'?
FAILED tests/test_phase_c_regime.py::test_volatile_gate_suppresses_pre_sweep - AttributeError: 'LiquiditySweepAlpha' object has no attribute '_bar_count'. Did you mean: '_ofi_count'?
FAILED tests/test_phase_c_regime.py::test_volatile_gate_suppresses_active_sweep - AttributeError: 'LiquiditySweepAlpha' object has no attribute '_bar_count'. Did you mean: '_ofi_count'?
FAILED tests/test_phase_c_regime.py::test_volatile_gate_count_increments - AttributeError: 'LiquiditySweepAlpha' object has no attribute '_bar_count'. Did you mean: '_ofi_count'?
FAILED tests/test_phase_c_regime.py::test_volatile_gate_count_in_metrics - AssertionError: assert 'volatile_gate_count' in {'ofi_count': 0, 'ofi_M2': 0.0, 'hawkes_history_len': 0, 'hawkes_baseline': 0.0, 'liquidity_pools': {'high': None, 'low': None}, 'neutral_predict_count': 0, 'state_invalid_count': 0, 'active_sweep_fired_count': 0, 'pre_sweep_fired_count': 0, 'last_ofi_levels_used': 0, 'active_sweep_lookback_bars': 30, 'direction_mode': 'continuation', 'pool_reset_atr_mult': 5.0, 'atr_expiry_mult': 3.0, 'vol_ratio_threshold': 0.015, 'pool_expired_age_count': 0, 'pool_expired_atr_count': 0, 'fake_breakout_ofi_required_count': 0, 'calibration_status': {'calibrated': False, 'n_samples': 0, 'brier_score': nan}, 'gate_fire_log_tail': [], 'gate_counts': {'VOLATILE': 0, 'LOW_LIQUIDITY': 0, 'WARMUP': 0, 'NO_EDGE': 0, 'POOL_UNSET': 0, 'TREND_ALIGNED': 0, 'INVALID_PRICE': 0}, 'regime_history_tail': [], 'bar_idx': 0}
FAILED tests/test_phase_c_regime.py::test_regime_output_field_is_valid_vocab - AssertionError: assert 'UPTREND' == 'TRENDING_UP'
  
  - TRENDING_UP
  + UPTREND
FAILED tests/test_phase_c_regime.py::test_confidence_scaled_in_trending_up_no_external_context - AttributeError: 'LiquiditySweepAlpha' object has no attribute '_bar_count'. Did you mean: '_ofi_count'?
FAILED tests/test_phase_c_regime.py::test_confidence_scaled_in_trending_down_no_external_context - AttributeError: 'LiquiditySweepAlpha' object has no attribute '_bar_count'. Did you mean: '_ofi_count'?
FAILED tests/test_phase_c_regime.py::test_phase_b_items_still_present - AssertionError: assert 'ofi_level_weighting' in {'ofi_count': 0, 'ofi_M2': 0.0, 'hawkes_history_len': 0, 'hawkes_baseline': 0.0, 'liquidity_pools': {'high': None, 'low': None}, 'neutral_predict_count': 0, 'state_invalid_count': 0, 'active_sweep_fired_count': 0, 'pre_sweep_fired_count': 0, 'last_ofi_levels_used': 0, 'active_sweep_lookback_bars': 30, 'direction_mode': 'continuation', 'pool_reset_atr_mult': 5.0, 'atr_expiry_mult': 3.0, 'vol_ratio_threshold': 0.015, 'pool_expired_age_count': 0, 'pool_expired_atr_count': 0, 'fake_breakout_ofi_required_count': 0, 'calibration_status': {'calibrated': False, 'n_samples': 0, 'brier_score': nan}, 'gate_fire_log_tail': [], 'gate_counts': {'VOLATILE': 0, 'LOW_LIQUIDITY': 0, 'WARMUP': 0, 'NO_EDGE': 0, 'POOL_UNSET': 0, 'TREND_ALIGNED': 0, 'INVALID_PRICE': 0}, 'regime_history_tail': [], 'bar_idx': 0}
FAILED tests/test_regime_accuracy.py::test_accuracy_trend_recall - AssertionError: assert 0.0 > 0.3
 +  where 0.0 = <built-in method get of dict object at 0x7f0784738580>('TREND', 0.0)
 +    where <built-in method get of dict object at 0x7f0784738580> = {'BEAR': 0.0, 'HALTED': 0.0, 'RANGE': 0.0, 'TOXIC': 0.0, 'TREND': 0.0, 'UNCALIBRATED': 0.0, 'UNKNOWN': 0.0}.get
FAILED tests/test_regime_accuracy.py::test_accuracy_bear_recall - AssertionError: assert 0.0 > 0.2
 +  where 0.0 = <built-in method get of dict object at 0x7f0784738580>('BEAR', 0.0)
 +    where <built-in method get of dict object at 0x7f0784738580> = {'BEAR': 0.0, 'HALTED': 0.0, 'RANGE': 0.0, 'TOXIC': 0.0, 'TREND': 0.0, 'UNCALIBRATED': 0.0, 'UNKNOWN': 0.0}.get
FAILED tests/test_regime_accuracy.py::test_accuracy_no_regime_collapse - assert 0.9621621621621622 < 0.8
FAILED tests/test_advanced_regime_engine_production_fixes.py::test_pnl_tracking_subpaths_and_breakers - assert False is True
FAILED tests/test_advanced_regime_engine_production_fixes.py::test_edge_sizing_single_modulation_path - assert 0.0 > 0.0
FAILED tests/test_advanced_regime_engine_production_fixes.py::test_alpha_override_cannot_bypass_directional_edge_gate - AssertionError: assert 'range_mean_revert' == 'flat'
  
  - flat
  + range_mean_revert
FAILED tests/test_advanced_regime_engine_production_fixes.py::test_range_from_flat_has_deterministic_nonzero_signed_size - assert 0.0 != 0.0 \xb1 1.0e-12
 +  where 0.0 \xb1 1.0e-12 = <function approx at 0x7f07ade01c70>(0.0)
 +    where <function approx at 0x7f07ade01c70> = pytest.approx
FAILED tests/test_advanced_regime_engine_verified_fixes.py::test_mtf_default_base_only_does_not_total_fail - AssertionError: assert {'primary': 'OK', 'flags': []} == 'OK'
FAILED tests/test_advanced_regime_engine_verified_fixes.py::test_mtf_unknown_and_zero_weight_timeframes_degrade_predictably - AssertionError: assert {'primary': 'OK', 'flags': []} == 'OK'
FAILED tests/test_advanced_regime_engine_verified_fixes.py::test_price_return_mismatch_early_return_updates_timestamp_anchor - AssertionError: assert {'primary': 'PRICE_RETURN_MISMATCH', 'flags': []} == 'PRICE_RETURN_MISMATCH'
FAILED tests/test_advanced_regime_engine_verified_fixes.py::test_invalid_scalar_return_fails_safe_with_observable_status - AssertionError: assert {'primary': 'INVALID_RETURN_INPUT', 'flags': []} == 'INVALID_RETURN_INPUT'
FAILED tests/test_regime_wiring_audit.py::test_main_does_not_shadow_engine_symbols - AssertionError: main missing detect_entry_trigger
assert False
 +  where False = hasattr(<module 'main' from '/workspace/Btc-bot/main.py'>, 'detect_entry_trigger')
FAILED tests/test_regime_wiring_audit.py::test_main_signal_pipeline_engine_constructed - AssertionError: main._signal_pipeline_engine should be constructed when engine imports OK
assert None is not None
 +  where None = <module 'main' from '/workspace/Btc-bot/main.py'>._signal_pipeline_engine
FAILED tests/test_regime_wiring_audit.py::test_sniper_execution_engine_signal_only_does_not_execute - ImportError: SniperExecutionEngine requires BTCBOT_LIVE_MODE=1.
FAILED tests/test_advanced_regime_engine_live_risks.py::test_shock_warmup_transition_is_smooth - assert 0.045 < 0.045
FAILED tests/test_advanced_regime_engine_live_risks.py::test_time_aware_ema_and_range_are_rate_consistent - ValueError: price must be numeric, got None
FAILED tests/test_advanced_regime_engine_live_risks.py::test_mtf_missing_base_features_fails_safe - AssertionError: assert 'NO_FEATURES' == 'OK'
  
  - OK
  + NO_FEATURES
FAILED tests/test_advanced_regime_engine.py::test_shock_triggers_toxic - assert 0.0 > 0.05
FAILED tests/test_advanced_regime_engine.py::test_bull_bias - assert 0 > 0
FAILED tests/test_advanced_regime_engine.py::test_bear_bias - assert 0 > 0
FAILED tests/test_advanced_regime_engine.py::test_range_presence - assert 0 > 0
FAILED tests/test_advanced_regime_engine_mission_critical_refactor.py::test_mtf_graceful_degradation_survives_base_feature_corruption - AssertionError: assert {'primary': 'MTF_FUSED_BASE_FEATURE_INVALID_MACRO_ONLY', 'flags': ['MACRO_ONLY_FALLBACK']} == 'MTF_FUSED_BASE_FEATURE_INVALID'
FAILED tests/test_advanced_regime_engine_mission_critical_refactor.py::test_range_persistence_after_five_minutes - AssertionError: assert 'UNCALIBRATED' == 'RANGE'
  
  - RANGE
  + UNCALIBRATED
FAILED tests/test_regime_engine_full_audit.py::TestRegimeClassification::test_strong_bull_returns_trend - AssertionError: Expected at least one TREND in bull market
assert 0 > 0
FAILED tests/test_regime_engine_full_audit.py::TestRegimeClassification::test_strong_bear_returns_bear - AssertionError: Expected at least one BEAR in bear market
assert 0 > 0
FAILED tests/test_regime_engine_full_audit.py::TestRegimeClassification::test_flat_returns_range - AssertionError: Expected at least one RANGE in range market
assert 0 > 0
FAILED tests/test_regime_engine_full_audit.py::TestRegimeClassification::test_shock_returns_toxic - AssertionError: Expected at least one TOXIC/HALTED on shock market
assert 0 > 0
FAILED tests/test_regime_engine_full_audit.py::TestRegimeClassification::test_no_contradictory_states - AssertionError: Invalid regime label: UNCALIBRATED
assert 'UNCALIBRATED' in ('TREND', 'RANGE', 'BEAR', 'TOXIC', 'HALTED', 'UNKNOWN')
FAILED tests/test_regime_engine_full_audit.py::TestRegimeOutputSchema::test_build_output_failsafe - AssertionError: assert 'UNKNOWN' == 'TREND'
  
  - TREND
  + UNKNOWN
FAILED tests/test_regime_engine_full_audit.py::TestNumericalSafety::test_extreme_returns_no_crash - AssertionError: assert 'UNCALIBRATED' in ('TREND', 'RANGE', 'BEAR', 'TOXIC', 'HALTED', 'UNKNOWN')
FAILED tests/test_regime_engine_full_audit.py::TestRegimeLabelNormalization::test_advanced_regime_engine_labels - AssertionError: Unknown regime label: UNCALIBRATED
assert 'UNCALIBRATED' in {'UNKNOWN', 'TOXIC', 'BEAR', 'TREND', 'HALTED', 'RANGE'}
FAILED tests/test_advanced_regime_engine_hardening.py::test_update_handles_nan_return - ValueError: price must be finite, got nan
FAILED tests/test_advanced_regime_engine_hardening.py::test_update_dimension_failure_on_bad_feature_shape - AssertionError: assert {'primary': 'DIMENSION_FAILURE', 'flags': []} == 'DIMENSION_FAILURE'
FAILED tests/test_advanced_regime_engine_hardening.py::test_mtf_partial_failure_degrades_not_crash - AssertionError: assert {'primary': 'MTF_PARTIAL_SURVIVAL', 'flags': []} == 'MTF_PARTIAL_SURVIVAL'
FAILED tests/test_advanced_regime_engine_hardening.py::test_sjm_non_finite_falls_back_to_last_valid - assert False is True
FAILED tests/test_advanced_regime_engine_hardening.py::test_update_handles_extreme_returns_beyond_two_sigma_bounds - ValueError: price must be positive, got -150.0
FAILED tests/test_advanced_regime_engine_hardening.py::test_build_output_fallback_path_never_throws_on_schema_failure - TypeError: test_build_output_fallback_path_never_throws_on_schema_failure.<locals>.<lambda>() got an unexpected keyword argument 'engine_id'
FAILED tests/test_advanced_regime_engine_hardening.py::test_build_output_fallback_handles_runtime_float_exceptions - TypeError: test_build_output_fallback_handles_runtime_float_exceptions.<locals>.<lambda>() got an unexpected keyword argument 'engine_id'
FAILED tests/test_advanced_regime_engine_hardening.py::test_schema_failure_metrics - ValueError: Incorrect label names
FAILED tests/test_advanced_regime_engine_hardening.py::test_load_state_logs_degrade_fields - assert 'STATE_LOAD_DEGRADE field=current_regime_idx' in "ERROR    advanced_regime_engine:advanced_regime_engine.py:3484 load_state: neither smoother nor state probs found. Using uniform prior.\nCRITICAL advanced_regime_engine:advanced_regime_engine.py:3565 load_state: NHHMM parameter restore INCOMPLETE. Restored: []. Failed/missing: ['nhhmm_beta', 'nhhmm_mu', 'nhhmm_sigma']. Regime posteriors will be INCOHERENT \u2014 emission distribution is using default parameters, not trained values. Engine requires retrain or a valid snapshot before live trading.\n"
 +  where "ERROR    advanced_regime_engine:advanced_regime_engine.py:3484 load_state: neither smoother nor state probs found. Using uniform prior.\nCRITICAL advanced_regime_engine:advanced_regime_engine.py:3565 load_state: NHHMM parameter restore INCOMPLETE. Restored: []. Failed/missing: ['nhhmm_beta', 'nhhmm_mu', 'nhhmm_sigma']. Regime posteriors will be INCOHERENT \u2014 emission distribution is using default parameters, not trained values. Engine requires retrain or a valid snapshot before live trading.\n" = <_pytest.logging.LogCaptureFixture object at 0x7f07846f1a90>.text
FAILED tests/test_advanced_regime_engine_hardening.py::test_regime_markov_smoother_directional_evidence_is_symmetric_and_not_suppressed - assert np.float64(0.03125) > 0.1
FAILED tests/test_advanced_regime_engine_hardening.py::test_snapshot_path_no_tautological_consistency_loop - assert 0 == 1
 +  where 0 = len([])
 +    where [] = <tests.test_advanced_regime_engine_hardening._ReplayCapture object at 0x7f077c72b380>.payloads
FAILED tests/test_advanced_regime_engine_hardening.py::test_load_snapshot_logs_structured_error - assert "Snapshot load failed context_keys=['engine_state']" in "CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.\nERROR    advanced_regime_engine:advanced_regime_engine.py:3484 load_state: neither smoother nor state probs found. Using uniform prior.\nCRITICAL advanced_regime_engine:advanced_regime_engine.py:3565 load_state: NHHMM parameter restore INCOMPLETE. Restored: []. Failed/missing: ['nhhmm_beta', 'nhhmm_mu', 'nhhmm_sigma']. Regime posteriors will be INCOHERENT \u2014 emission distribution is using default parameters, not trained values. Engine requires retrain or a valid snapshot before live trading.\nCRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.\nCRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.\n"
 +  where "CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.\nERROR    advanced_regime_engine:advanced_regime_engine.py:3484 load_state: neither smoother nor state probs found. Using uniform prior.\nCRITICAL advanced_regime_engine:advanced_regime_engine.py:3565 load_state: NHHMM parameter restore INCOMPLETE. Restored: []. Failed/missing: ['nhhmm_beta', 'nhhmm_mu', 'nhhmm_sigma']. Regime posteriors will be INCOHERENT \u2014 emission distribution is using default parameters, not trained values. Engine requires retrain or a valid snapshot before live trading.\nCRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.\nCRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.\n" = <_pytest.logging.LogCaptureFixture object at 0x7f07ad1b4910>.text
FAILED tests/test_advanced_regime_engine_hardening.py::test_circuit_breaker_vol_shock_short_circuits_same_tick - AssertionError: assert {'primary': 'CIRCUIT_BREAKER:VOL_SHOCK', 'flags': []} == 'CIRCUIT_BREAKER:VOL_SHOCK'
FAILED tests/test_advanced_regime_engine_hardening.py::test_breaker_reason_and_healing_counter_not_overwritten_same_tick - AssertionError: assert 'VOL_SHOCK' == 'MAX_DRAWDOWN'
  
  - MAX_DRAWDOWN
  + VOL_SHOCK
FAILED tests/test_advanced_regime_engine_hardening.py::test_healing_branch_returns_immediately_after_self_heal - AssertionError: assert 'UNCALIBRATED' == 'HALTED'
  
  - HALTED
  + UNCALIBRATED
FAILED tests/test_advanced_regime_engine_hardening.py::test_negative_equity_clamped_and_breaker_tripped - ValueError: price must be positive, got 0.0
FAILED tests/test_advanced_regime_engine_hardening.py::test_price_return_mismatch_emits_fail_safe_without_pnl_state_contamination - AssertionError: assert 'halt' == 'fail_safe'
  
  - fail_safe
  + halt
FAILED tests/test_advanced_regime_engine_hardening.py::test_breaker_cooldown_initialization_consistent_across_trigger_paths - AssertionError: assert 'VOL_SHOCK' == 'MAX_DRAWDOWN'
  
  - MAX_DRAWDOWN
  + VOL_SHOCK
FAILED tests/test_advanced_regime_engine_hardening.py::test_warning_worker_does_not_keep_engine_alive_strongly - AssertionError: assert <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f075c71bcb0> is None
 +  where <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f075c71bcb0> = <weakref at 0x7f075c2623e0; to 'advanced_regime_engine.AdvancedRegimeEngine' at 0x7f075c71bcb0>()
FAILED tests/test_advanced_regime_engine_hardening.py::test_full_self_heal_resets_last_price_reference - assert 50.0 is None
 +  where 50.0 = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f075c245010>._last_price
FAILED tests/integration/test_regime_engine_integration.py::test_regime_engine_integration - AssertionError: assert 'UNKNOWN' != 'UNKNOWN'
================= 65 failed, 205 passed, 2 warnings in 30.11s ==================
```

## Exact pytest failure sections / stack traces

```text
=================================== FAILURES ===================================
____________________ test_detect_regime_returns_valid_vocab ____________________

    def test_detect_regime_returns_valid_vocab():
        model = alpha.LiquiditySweepAlpha()
    
>       assert model._detect_regime(101.0, 100.0) == "TRENDING_UP"
E       AssertionError: assert 'UPTREND' == 'TRENDING_UP'
E         
E         - TRENDING_UP
E         + UPTREND

tests/test_phase_c_regime.py:72: AssertionError
________________________ test_volatile_in_valid_regimes ________________________

    def test_volatile_in_valid_regimes():
        assert "VOLATILE" in _VALID_REGIMES
        assert "TRENDING_UP" in _VALID_REGIMES
        assert "TRENDING_DOWN" in _VALID_REGIMES
>       assert "UPTREND" not in _VALID_REGIMES
E       AssertionError: assert 'UPTREND' not in frozenset({'UNKNOWN', 'RANGING', 'TRENDING_UP', 'VOLATILE', 'UPTREND', 'DOWNTREND', 'TRENDING_DOWN'})

tests/test_phase_c_regime.py:88: AssertionError
_______________ test_active_sweep_hold_in_trending_up_high_sweep _______________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f0784570640>

    def test_active_sweep_hold_in_trending_up_high_sweep(monkeypatch):
        model = alpha.LiquiditySweepAlpha()
        _warm_model(model)
>       _seed_pools(model, high=100.0, low=90.0)

tests/test_phase_c_regime.py:95: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

model = <alpha_liquidity_sweep_predictor.LiquiditySweepAlpha object at 0x7f07845f6a50>
high = 100.0, low = 90.0

    def _seed_pools(model, high=100.0, low=90.0):
        model.liquidity_pools["high"] = high
        model.liquidity_pools["low"] = low
>       model._pool_set_bar["high"] = model._bar_count
                                      ^^^^^^^^^^^^^^^^
E       AttributeError: 'LiquiditySweepAlpha' object has no attribute '_bar_count'. Did you mean: '_ofi_count'?

tests/test_phase_c_regime.py:50: AttributeError
______________ test_active_sweep_hold_in_trending_down_low_sweep _______________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f0784570510>

    def test_active_sweep_hold_in_trending_down_low_sweep(monkeypatch):
        model = alpha.LiquiditySweepAlpha()
        _warm_model(model)
>       _seed_pools(model, high=110.0, low=100.0)

tests/test_phase_c_regime.py:108: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

model = <alpha_liquidity_sweep_predictor.LiquiditySweepAlpha object at 0x7f07845f6ba0>
high = 110.0, low = 100.0

    def _seed_pools(model, high=100.0, low=90.0):
        model.liquidity_pools["high"] = high
        model.liquidity_pools["low"] = low
>       model._pool_set_bar["high"] = model._bar_count
                                      ^^^^^^^^^^^^^^^^
E       AttributeError: 'LiquiditySweepAlpha' object has no attribute '_bar_count'. Did you mean: '_ofi_count'?

tests/test_phase_c_regime.py:50: AttributeError
___________________ test_active_sweep_not_blocked_in_ranging ___________________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f0784775250>

    def test_active_sweep_not_blocked_in_ranging(monkeypatch):
        model = alpha.LiquiditySweepAlpha()
        _warm_model(model)
>       _seed_pools(model, high=100.0, low=90.0)

tests/test_phase_c_regime.py:121: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

model = <alpha_liquidity_sweep_predictor.LiquiditySweepAlpha object at 0x7f07845f6cf0>
high = 100.0, low = 90.0

    def _seed_pools(model, high=100.0, low=90.0):
        model.liquidity_pools["high"] = high
        model.liquidity_pools["low"] = low
>       model._pool_set_bar["high"] = model._bar_count
                                      ^^^^^^^^^^^^^^^^
E       AttributeError: 'LiquiditySweepAlpha' object has no attribute '_bar_count'. Did you mean: '_ofi_count'?

tests/test_phase_c_regime.py:50: AttributeError
___________________ test_volatile_gate_suppresses_pre_sweep ____________________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f07845dae00>

    def test_volatile_gate_suppresses_pre_sweep(monkeypatch):
        model = alpha.LiquiditySweepAlpha()
        _warm_model(model)
>       _seed_pools(model)

tests/test_phase_c_regime.py:133: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

model = <alpha_liquidity_sweep_predictor.LiquiditySweepAlpha object at 0x7f07845f6e40>
high = 100.0, low = 90.0

    def _seed_pools(model, high=100.0, low=90.0):
        model.liquidity_pools["high"] = high
        model.liquidity_pools["low"] = low
>       model._pool_set_bar["high"] = model._bar_count
                                      ^^^^^^^^^^^^^^^^
E       AttributeError: 'LiquiditySweepAlpha' object has no attribute '_bar_count'. Did you mean: '_ofi_count'?

tests/test_phase_c_regime.py:50: AttributeError
__________________ test_volatile_gate_suppresses_active_sweep __________________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f07845daf10>

    def test_volatile_gate_suppresses_active_sweep(monkeypatch):
        model = alpha.LiquiditySweepAlpha()
        _warm_model(model)
>       _seed_pools(model)

tests/test_phase_c_regime.py:146: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

model = <alpha_liquidity_sweep_predictor.LiquiditySweepAlpha object at 0x7f07845f6f90>
high = 100.0, low = 90.0

    def _seed_pools(model, high=100.0, low=90.0):
        model.liquidity_pools["high"] = high
        model.liquidity_pools["low"] = low
>       model._pool_set_bar["high"] = model._bar_count
                                      ^^^^^^^^^^^^^^^^
E       AttributeError: 'LiquiditySweepAlpha' object has no attribute '_bar_count'. Did you mean: '_ofi_count'?

tests/test_phase_c_regime.py:50: AttributeError
_____________________ test_volatile_gate_count_increments ______________________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f0784354050>

    def test_volatile_gate_count_increments(monkeypatch):
        model = alpha.LiquiditySweepAlpha()
        _warm_model(model)
>       _seed_pools(model)

tests/test_phase_c_regime.py:159: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

model = <alpha_liquidity_sweep_predictor.LiquiditySweepAlpha object at 0x7f07845f70e0>
high = 100.0, low = 90.0

    def _seed_pools(model, high=100.0, low=90.0):
        model.liquidity_pools["high"] = high
        model.liquidity_pools["low"] = low
>       model._pool_set_bar["high"] = model._bar_count
                                      ^^^^^^^^^^^^^^^^
E       AttributeError: 'LiquiditySweepAlpha' object has no attribute '_bar_count'. Did you mean: '_ofi_count'?

tests/test_phase_c_regime.py:50: AttributeError
_____________________ test_volatile_gate_count_in_metrics ______________________

    def test_volatile_gate_count_in_metrics():
        metrics = alpha.LiquiditySweepAlpha().get_state_metrics()
    
>       assert "volatile_gate_count" in metrics
E       AssertionError: assert 'volatile_gate_count' in {'ofi_count': 0, 'ofi_M2': 0.0, 'hawkes_history_len': 0, 'hawkes_baseline': 0.0, 'liquidity_pools': {'high': None, 'low': None}, 'neutral_predict_count': 0, 'state_invalid_count': 0, 'active_sweep_fired_count': 0, 'pre_sweep_fired_count': 0, 'last_ofi_levels_used': 0, 'active_sweep_lookback_bars': 30, 'direction_mode': 'continuation', 'pool_reset_atr_mult': 5.0, 'atr_expiry_mult': 3.0, 'vol_ratio_threshold': 0.015, 'pool_expired_age_count': 0, 'pool_expired_atr_count': 0, 'fake_breakout_ofi_required_count': 0, 'calibration_status': {'calibrated': False, 'n_samples': 0, 'brier_score': nan}, 'gate_fire_log_tail': [], 'gate_counts': {'VOLATILE': 0, 'LOW_LIQUIDITY': 0, 'WARMUP': 0, 'NO_EDGE': 0, 'POOL_UNSET': 0, 'TREND_ALIGNED': 0, 'INVALID_PRICE': 0}, 'regime_history_tail': [], 'bar_idx': 0}

tests/test_phase_c_regime.py:171: AssertionError
___________________ test_regime_output_field_is_valid_vocab ____________________

    def test_regime_output_field_is_valid_vocab():
        model = alpha.LiquiditySweepAlpha()
        out = model.get_signal(_market_data(price=100.0, ema_fast=100.0, ema_slow=100.0))
        assert out["regime"] in _VALID_REGIMES
    
        out = model.get_signal(_market_data(price=100.0, timestamp=1_700_000_001.0, ema_fast=103.0, ema_slow=100.0))
        assert out["regime"] in _VALID_REGIMES
>       assert out["regime"] == "TRENDING_UP"
E       AssertionError: assert 'UPTREND' == 'TRENDING_UP'
E         
E         - TRENDING_UP
E         + UPTREND

tests/test_phase_c_regime.py:182: AssertionError
__________ test_confidence_scaled_in_trending_up_no_external_context ___________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f0784355750>

    def test_confidence_scaled_in_trending_up_no_external_context(monkeypatch):
        model = alpha.LiquiditySweepAlpha(direction_mode="fade")
        _warm_model(model)
>       _seed_pools(model, high=100.0, low=90.0)

tests/test_phase_c_regime.py:188: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

model = <alpha_liquidity_sweep_predictor.LiquiditySweepAlpha object at 0x7f07845f7380>
high = 100.0, low = 90.0

    def _seed_pools(model, high=100.0, low=90.0):
        model.liquidity_pools["high"] = high
        model.liquidity_pools["low"] = low
>       model._pool_set_bar["high"] = model._bar_count
                                      ^^^^^^^^^^^^^^^^
E       AttributeError: 'LiquiditySweepAlpha' object has no attribute '_bar_count'. Did you mean: '_ofi_count'?

tests/test_phase_c_regime.py:50: AttributeError
_________ test_confidence_scaled_in_trending_down_no_external_context __________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f078435e4e0>

    def test_confidence_scaled_in_trending_down_no_external_context(monkeypatch):
        model = alpha.LiquiditySweepAlpha(direction_mode="fade")
        _warm_model(model)
>       _seed_pools(model, high=110.0, low=100.0)

tests/test_phase_c_regime.py:205: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

model = <alpha_liquidity_sweep_predictor.LiquiditySweepAlpha object at 0x7f07845f74d0>
high = 110.0, low = 100.0

    def _seed_pools(model, high=100.0, low=90.0):
        model.liquidity_pools["high"] = high
        model.liquidity_pools["low"] = low
>       model._pool_set_bar["high"] = model._bar_count
                                      ^^^^^^^^^^^^^^^^
E       AttributeError: 'LiquiditySweepAlpha' object has no attribute '_bar_count'. Did you mean: '_ofi_count'?

tests/test_phase_c_regime.py:50: AttributeError
_______________________ test_phase_b_items_still_present _______________________

    def test_phase_b_items_still_present():
        metrics = alpha.LiquiditySweepAlpha().get_state_metrics()
    
>       assert "ofi_level_weighting" in metrics
E       AssertionError: assert 'ofi_level_weighting' in {'ofi_count': 0, 'ofi_M2': 0.0, 'hawkes_history_len': 0, 'hawkes_baseline': 0.0, 'liquidity_pools': {'high': None, 'low': None}, 'neutral_predict_count': 0, 'state_invalid_count': 0, 'active_sweep_fired_count': 0, 'pre_sweep_fired_count': 0, 'last_ofi_levels_used': 0, 'active_sweep_lookback_bars': 30, 'direction_mode': 'continuation', 'pool_reset_atr_mult': 5.0, 'atr_expiry_mult': 3.0, 'vol_ratio_threshold': 0.015, 'pool_expired_age_count': 0, 'pool_expired_atr_count': 0, 'fake_breakout_ofi_required_count': 0, 'calibration_status': {'calibrated': False, 'n_samples': 0, 'brier_score': nan}, 'gate_fire_log_tail': [], 'gate_counts': {'VOLATILE': 0, 'LOW_LIQUIDITY': 0, 'WARMUP': 0, 'NO_EDGE': 0, 'POOL_UNSET': 0, 'TREND_ALIGNED': 0, 'INVALID_PRICE': 0}, 'regime_history_tail': [], 'bar_idx': 0}

tests/test_phase_c_regime.py:222: AssertionError
__________________________ test_accuracy_trend_recall __________________________

accuracy_results = {'confusion': {'BEAR': Counter({'UNCALIBRATED': 370}), 'RANGE': Counter({'UNCALIBRATED': 370}), 'TOXIC': Counter({'UNC...': 1424, 'HALTED': 53, 'UNKNOWN': 3}), 'precision': {'BEAR': 0.0, 'HALTED': 0.0, 'RANGE': 0.0, 'TOXIC': 0.0, ...}, ...}

    def test_accuracy_trend_recall(accuracy_results):
>       assert accuracy_results["recall"].get("TREND", 0.0) > 0.30
E       AssertionError: assert 0.0 > 0.3
E        +  where 0.0 = <built-in method get of dict object at 0x7f0784738580>('TREND', 0.0)
E        +    where <built-in method get of dict object at 0x7f0784738580> = {'BEAR': 0.0, 'HALTED': 0.0, 'RANGE': 0.0, 'TOXIC': 0.0, 'TREND': 0.0, 'UNCALIBRATED': 0.0, 'UNKNOWN': 0.0}.get

tests/test_regime_accuracy.py:108: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833481
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835183
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841076
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842731
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843035
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841645
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843590
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840271
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.830268
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.830126
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840747
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840614
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843102
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843576
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842385
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839578
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842802
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843706
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840773
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841162
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835410
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842552
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842713
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.828994
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840144
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842555
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839338
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835681
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843183
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838917
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842565
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842083
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842872
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840505
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842980
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841827
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837408
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842181
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841468
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843094
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843663
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836801
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837317
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841905
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841949
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.832171
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842426
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841941
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842215
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833094
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838978
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841102
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842755
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838330
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837334
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841267
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841383
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.831620
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841951
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843277
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839976
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842522
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843833
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843140
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842086
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841419
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843498
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842189
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843099
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836945
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840854
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843078
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841191
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834815
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.825951
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.832604
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838925
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841890
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843541
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841230
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834844
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837043
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841618
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834259
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833751
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842403
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838031
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843469
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.831126
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.820553
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.829492
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840177
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840622
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.830013
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840059
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840603
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842784
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843882
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844386
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843434
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842859
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843330
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844306
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844694
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838316
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843186
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835519
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841731
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836423
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833760
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840420
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.818439
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839123
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840548
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836160
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833906
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.818961
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835887
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838976
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840385
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Snapshot dropped: _lock held by update() thread.
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840794
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843410
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843958
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843958
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836848
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842101
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838707
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842184
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843135
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.824660
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835104
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839776
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842036
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841569
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841820
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833983
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839215
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837665
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842321
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842020
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842584
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843968
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840008
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843440
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844738
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843652
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844200
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.828944
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841966
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842902
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836964
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840964
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842028
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842486
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.821026
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837501
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.825165
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.817632
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837234
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840458
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841503
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841706
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841825
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843484
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844042
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835589
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843129
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842461
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844259
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844257
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841289
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839618
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842515
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843286
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844235
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844264
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845086
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844774
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842863
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844084
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841566
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837778
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838949
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841102
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842117
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844163
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843907
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843430
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843490
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840615
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842413
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834125
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841469
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838829
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843516
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834857
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.825990
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833123
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840763
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841690
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836268
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842962
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837908
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841590
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843052
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838402
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842400
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843023
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844099
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841193
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840939
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839860
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842315
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840406
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842969
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.827989
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837236
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841122
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840668
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842717
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843983
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844437
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.820519
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840651
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839294
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841893
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835957
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835325
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840645
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839558
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.828457
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840464
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836886
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841977
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.829159
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841888
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842395
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843403
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843372
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841621
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841513
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835916
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843151
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843958
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844294
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838020
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.827307
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839879
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840226
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838458
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840497
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837345
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841890
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840329
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.820645
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837216
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840110
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841131
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834782
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841115
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841962
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.830655
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836307
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841249
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843152
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839336
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835312
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841290
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838996
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841374
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843041
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.829322
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840741
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842868
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841155
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843274
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844085
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842340
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843781
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835888
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841767
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842648
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841846
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840516
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842903
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.830888
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840428
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842076
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842797
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843365
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843502
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843199
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843510
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834542
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833791
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840762
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834494
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842852
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843505
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844150
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839848
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842767
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.872359
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.829673
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837342
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839657
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841300
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.830910
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838508
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842367
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843895
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.829068
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839202
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841914
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.830141
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835083
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837104
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841063
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842354
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839417
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842732
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842113
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843847
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.820814
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.831893
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.817926
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839407
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839221
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841178
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843343
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.822940
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836736
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838606
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842282
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843394
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843156
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834362
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840518
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842422
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834641
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841429
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842785
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841917
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843416
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843671
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.818084
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835874
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840027
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842303
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837397
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841543
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842710
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.816327
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840249
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840985
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.829500
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.824848
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.832310
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837300
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.823305
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840022
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842473
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838273
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842469
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843246
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835622
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842409
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843433
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843095
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842400
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843879
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843292
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844287
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840167
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842089
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842996
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843482
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.827010
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840229
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837973
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841822
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842517
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842538
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844286
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843622
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841316
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838496
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842902
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844455
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.829681
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.819262
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839980
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841408
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842496
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843284
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843006
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.830288
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840279
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841473
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843663
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844485
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844917
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839647
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.827348
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835714
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.824966
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839092
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842502
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841687
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843987
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.816114
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838501
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.832488
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838035
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842400
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843303
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842057
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844536
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844988
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839861
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838129
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841421
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836835
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836931
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840158
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835202
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843100
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.818495
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840573
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836137
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837524
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.824326
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838807
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839512
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842488
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837732
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842296
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837666
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841965
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837254
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843422
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841829
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844616
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845032
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839365
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.816620
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835819
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841253
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838634
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843136
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844019
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837612
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842534
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839604
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842569
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842997
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843393
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.832070
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840075
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840684
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843290
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.832601
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.819486
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.831991
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841615
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842938
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.820041
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.815712
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836174
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838731
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840634
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843495
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842611
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843128
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844479
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.829463
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.827811
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840981
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842992
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843815
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844329
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844861
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844763
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.814080
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833041
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841404
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842801
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.820649
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840305
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837850
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840176
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843205
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844516
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.822544
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840900
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841034
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834635
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839708
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.823354
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.832586
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839761
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835373
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839442
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841337
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842882
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843800
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842675
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840698
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844271
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834622
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837865
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842559
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.818990
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839585
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842089
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841910
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841645
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.830362
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842150
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843277
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833235
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.821485
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840670
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839773
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.825587
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834125
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836423
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840358
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.816585
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.831575
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839309
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840395
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836797
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.825990
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841445
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842799
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842900
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839379
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843431
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844197
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835881
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843181
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.821300
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.824400
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837300
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839869
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.828162
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841293
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842779
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843531
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843550
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844684
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844696
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.828597
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839084
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842158
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.828670
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840438
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839029
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.832710
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.830806
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840963
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842744
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843452
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.822237
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837660
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841881
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843272
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844050
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844592
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843402
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844330
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834008
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841348
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835434
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.829711
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.815326
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834193
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.828955
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.825173
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.832991
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838900
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840017
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842676
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840612
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842223
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842341
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843871
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842283
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844405
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Snapshot dropped: _lock held by update() thread.
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843526
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841103
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843801
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844193
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836044
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842796
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841408
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836654
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.816226
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.825925
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839750
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.828603
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841601
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839213
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843629
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844374
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834571
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839585
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842447
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843129
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842799
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840163
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844219
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842020
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843594
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835079
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842675
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842430
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844210
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837408
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839358
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841417
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.832916
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839287
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837273
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839751
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842318
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837273
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836387
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.824802
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841340
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842485
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843442
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840705
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841905
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839845
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844183
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.827807
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833358
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839379
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836365
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.814100
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836847
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834341
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842645
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841448
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842056
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.816676
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836779
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.832174
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842417
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838974
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842099
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835156
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839959
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842796
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833339
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841800
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.824641
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840867
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841811
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840661
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842888
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.813749
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839965
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841406
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837783
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833789
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834481
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842051
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.832587
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.826760
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.831726
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.822871
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834770
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.832531
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838777
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842097
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841438
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843251
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844126
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843538
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844293
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837706
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.823137
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833566
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837588
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842219
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840585
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842407
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842554
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.825849
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839820
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842539
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843571
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844189
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843318
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843881
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843398
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.815378
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835232
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839062
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841644
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843461
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841779
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837041
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833797
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841141
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839993
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842944
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840960
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.822623
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837847
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.821321
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.822193
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837983
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840938
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842258
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842031
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842407
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844321
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844118
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844135
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844712
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.829575
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838082
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835097
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841709
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842181
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834601
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842941
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843810
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836197
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838188
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842628
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.823810
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834426
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839853
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837804
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.818674
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836619
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840266
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841850
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841372
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834235
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841097
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842104
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835984
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839628
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843940
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844208
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.831597
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837894
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839199
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841339
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841042
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843396
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839403
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.857499
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834258
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838164
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.814100
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841451
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836759
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.829429
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841100
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841684
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841060
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843905
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843647
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844992
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844664
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844482
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844826
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844834
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.824034
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838002
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840199
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839493
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.818670
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835131
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841526
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842167
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840443
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836824
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838852
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837226
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840012
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836851
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.815264
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839265
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841439
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842321
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.829609
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839305
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837312
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839939
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842731
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836592
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843390
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843806
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844645
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844886
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843873
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844218
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844137
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845215
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845394
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845009
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845236
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845327
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845346
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844762
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845040
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841266
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844086
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844146
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844617
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844883
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843672
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844329
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844655
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844418
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843504
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844713
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842255
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844698
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844965
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845029
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844805
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844064
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844813
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844977
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845160
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844824
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842916
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845040
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842345
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844029
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844130
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845299
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845325
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845551
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845597
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844378
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845485
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844517
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844543
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845212
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845025
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845005
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845251
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845176
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845266
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844178
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844053
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844444
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844449
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845422
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844509
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844449
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845315
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844849
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845103
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838206
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842359
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842827
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843011
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844382
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843884
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843944
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843884
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845250
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845192
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845397
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844356
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845212
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845571
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840939
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841841
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844760
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845121
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844557
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843519
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844761
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843060
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844461
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844156
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844445
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844842
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844611
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845272
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844554
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842668
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844324
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844809
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845337
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844310
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844782
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842206
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844248
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843845
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843773
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843514
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842848
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844605
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844108
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Snapshot dropped: _lock held by update() thread.
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845182
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845282
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845345
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845343
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844876
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843753
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844248
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843261
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844609
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844395
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843476
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844526
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842577
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843497
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844526
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844835
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844875
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844341
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844924
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845063
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839939
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842344
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843877
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844166
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844358
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844056
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845086
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844838
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845467
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842524
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844800
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843570
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843794
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844852
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844848
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844916
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844963
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844925
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844601
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845471
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845460
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844518
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845538
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845574
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843861
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844943
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841619
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843557
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843931
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842556
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843161
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838980
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840905
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842109
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844618
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845066
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840079
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844240
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842955
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844874
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845268
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845462
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845370
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845595
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843208
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844977
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845063
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845122
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842335
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843864
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844417
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844142
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844499
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842798
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839819
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844162
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844622
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845069
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844694
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845396
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843779
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844349
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843274
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844442
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844549
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843340
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838580
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839595
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842143
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844626
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844727
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844186
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842802
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844579
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844844
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844641
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845124
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844169
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843904
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839382
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843753
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843148
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844967
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844740
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845375
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845497
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845497
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845050
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842512
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844956
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844719
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845355
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844306
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844830
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843600
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839793
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842761
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844688
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843233
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844663
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844067
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844680
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843663
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845214
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845400
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845307
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845324
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845605
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842196
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844557
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843662
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845192
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845436
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843852
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845345
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845452
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845352
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845323
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844925
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845108
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845520
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845515
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844871
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839362
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843400
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840596
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843512
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844622
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844800
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845106
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845177
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837838
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841975
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844263
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844364
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844514
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841896
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843326
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840128
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844028
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844301
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844866
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842934
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844285
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845032
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844415
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842304
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844431
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844857
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844920
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843887
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845301
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845074
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842864
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844777
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844895
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845238
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841312
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843319
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844838
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845201
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845246
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845323
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845387
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845130
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844069
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844546
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844102
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845214
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840669
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844030
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844065
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844784
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845345
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845289
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842278
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844170
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844012
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845053
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845487
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843639
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845300
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844680
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844446
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845312
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842230
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843975
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845080
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841381
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843753
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843697
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844859
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844874
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844218
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845141
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838453
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843805
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844277
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844642
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839290
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843853
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843915
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838453
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842752
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843994
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844874
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844965
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844610
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845520
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845323
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844426
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843500
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845001
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845248
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843858
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843777
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844235
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844612
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844834
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843807
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843876
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844776
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845153
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845154
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845301
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845362
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845009
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845048
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844665
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845504
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845440
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844003
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844139
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844734
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844696
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844851
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845177
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845386
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842918
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842133
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844624
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845166
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844421
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841304
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844063
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841627
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843275
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844502
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845078
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844494
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844275
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844915
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844722
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842759
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841717
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843945
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844221
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844834
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845043
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844330
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843363
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844792
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844649
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843319
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844715
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843085
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843232
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844123
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843275
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844148
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840783
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836268
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.819608
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839644
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841562
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842859
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843967
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843486
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841396
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833329
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838601
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842537
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.832508
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840593
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842279
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841945
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844063
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.830814
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840359
CRITICAL advanced_regime_engine:advanced_regime_engine.py:5762 [CIRCUIT BREAKER TRIGGERED] Reason=VOL_SHOCK
WARNING  advanced_regime_engine:advanced_regime_engine.py:6017 [SELF HEALING INITIATED]
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839965
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844152
CRITICAL advanced_regime_engine:advanced_regime_engine.py:5762 [CIRCUIT BREAKER TRIGGERED] Reason=VOL_SHOCK
WARNING  advanced_regime_engine:advanced_regime_engine.py:6017 [SELF HEALING INITIATED]
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843765
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845268
CRITICAL advanced_regime_engine:advanced_regime_engine.py:5762 [CIRCUIT BREAKER TRIGGERED] Reason=VOL_SHOCK
WARNING  advanced_regime_engine:advanced_regime_engine.py:6017 [SELF HEALING INITIATED]
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837767
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843665
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135368
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.112115
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.830328
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834679
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836448
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.829347
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.818170
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.823163
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.829190
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833824
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836856
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838430
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839773
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842238
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842583
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842727
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838683
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843276
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844492
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Price/return mismatch detected (|Δ|=0.059974 > 0.001000); degrading to fail-safe output and freezing risk-state mutation.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Snapshot dropped: _lock held by update() thread.
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843339
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.845000
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833552
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842413
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843453
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844328
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844730
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844326
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840684
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.132132
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.107561
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.830175
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.822515
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.823838
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.825411
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833315
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837058
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836445
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835602
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834794
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842890
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842465
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842893
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843771
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840898
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843365
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836853
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836209
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842937
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843159
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844535
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844115
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842245
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841714
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.103736
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.161990
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.129889
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.108035
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.102656
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.156231
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.126382
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.105350
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841310
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833975
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.833706
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838400
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841032
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842337
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840374
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.131634
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.108916
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138741
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.113984
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.108058
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150381
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.122721
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.103151
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.827888
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.832789
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.828258
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838813
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841303
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842395
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841935
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843679
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844429
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842488
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843496
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.103161
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.161526
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.129555
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.109586
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.823063
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.829592
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.832291
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.836350
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839142
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839132
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841416
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843268
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842934
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844265
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844390
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840499
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838697
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838536
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841571
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842909
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.843695
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.128696
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.109672
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.826163
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.825676
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834682
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.837793
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.839855
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841367
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.842592
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.103889
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.160785
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.129704
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.107583
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.822215
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.821314
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.826504
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.831944
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.835391
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.834311
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.838370
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841116
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.841714
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.840207
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844123
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844625
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=0.844839
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.125132
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.101225
__________________________ test_accuracy_bear_recall ___________________________

accuracy_results = {'confusion': {'BEAR': Counter({'UNCALIBRATED': 370}), 'RANGE': Counter({'UNCALIBRATED': 370}), 'TOXIC': Counter({'UNC...': 1424, 'HALTED': 53, 'UNKNOWN': 3}), 'precision': {'BEAR': 0.0, 'HALTED': 0.0, 'RANGE': 0.0, 'TOXIC': 0.0, ...}, ...}

    def test_accuracy_bear_recall(accuracy_results):
>       assert accuracy_results["recall"].get("BEAR", 0.0) > 0.20
E       AssertionError: assert 0.0 > 0.2
E        +  where 0.0 = <built-in method get of dict object at 0x7f0784738580>('BEAR', 0.0)
E        +    where <built-in method get of dict object at 0x7f0784738580> = {'BEAR': 0.0, 'HALTED': 0.0, 'RANGE': 0.0, 'TOXIC': 0.0, 'TREND': 0.0, 'UNCALIBRATED': 0.0, 'UNKNOWN': 0.0}.get

tests/test_regime_accuracy.py:112: AssertionError
_______________________ test_accuracy_no_regime_collapse _______________________

accuracy_results = {'confusion': {'BEAR': Counter({'UNCALIBRATED': 370}), 'RANGE': Counter({'UNCALIBRATED': 370}), 'TOXIC': Counter({'UNC...': 1424, 'HALTED': 53, 'UNKNOWN': 3}), 'precision': {'BEAR': 0.0, 'HALTED': 0.0, 'RANGE': 0.0, 'TOXIC': 0.0, ...}, ...}

    def test_accuracy_no_regime_collapse(accuracy_results):
>       assert accuracy_results["dominant_ratio"] < 0.80
E       assert 0.9621621621621622 < 0.8

tests/test_regime_accuracy.py:124: AssertionError
___________________ test_pnl_tracking_subpaths_and_breakers ____________________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f07845f7a10>

    def test_pnl_tracking_subpaths_and_breakers(engine):
        engine.last_signed_position_size = 1.0
        out1 = engine.update(_md(ts=1.0, ret=0.0, price=100.0))
>       assert out1["signal_valid"] is True
E       assert False is True

tests/test_advanced_regime_engine_production_fixes.py:34: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
___________________ test_edge_sizing_single_modulation_path ____________________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f07846f1400>
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f07846716d0>

    def test_edge_sizing_single_modulation_path(engine, monkeypatch):
        engine._regime_smoother = None
        engine._EDGE_MIN_DIRECTIONAL_CONFIDENCE = 0.0
        engine._EDGE_MIN_ACTIVE = 0.0
    
        def _scores(edge):
            return {
                "regime": "TREND",
                "bull": 0.8,
                "bear": 0.1,
                "crisis": 0.1,
                "trend_strength": 0.6,
                "risk_level": 0.2,
                "confidence": 0.9,
                "conviction": 0.9,
                "uncertainty": 0.1,
                "directional_margin": 0.6,
                "directional_label": "TREND",
                "edge_score": edge,
                "trend_score": 0.9,
                "range_score": 0.1,
                "toxic_score": 0.0,
            }
    
        monkeypatch.setattr(module, "compute_hmm_regime", lambda *_a, **_k: _scores(0.4))
        low = engine.update(_md(ts=1.0, ret=0.001, price=100.0))["position_size"]
    
        monkeypatch.setattr(module, "compute_hmm_regime", lambda *_a, **_k: _scores(0.95))
        high = engine.update(_md(ts=2.0, ret=0.001, price=100.1))["position_size"]
    
        assert 0.0 <= low <= module._POSITION_SIZE_CAP
        assert 0.0 <= high <= module._POSITION_SIZE_CAP
>       assert high > low
E       assert 0.0 > 0.0

tests/test_advanced_regime_engine_production_fixes.py:274: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
___________ test_alpha_override_cannot_bypass_directional_edge_gate ____________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f07846f1160>
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f078434c260>

    def test_alpha_override_cannot_bypass_directional_edge_gate(engine, monkeypatch):
        monkeypatch.setattr(
            module,
            "compute_hmm_regime",
            lambda *_a, **_k: {
                "regime": "RANGE",
                "bull": 0.5,
                "bear": 0.4,
                "crisis": 0.1,
                "trend_strength": 0.5,
                "risk_level": 0.2,
                "confidence": 0.8,
                "conviction": 0.9,
                "uncertainty": 0.2,
                "directional_margin": 0.4,
                "directional_label": "TREND",
                "edge_score": 0.2,
                "trend_score": 0.8,
                "range_score": 0.2,
                "toxic_score": 0.0,
            },
        )
        monkeypatch.setattr(
            engine.nhhmm,
            "forward_pass_step",
            lambda *_a, **_k: (np.array([0.85, 0.05, 0.10], dtype=float), None),
        )
        monkeypatch.setattr(
            engine.sjm,
            "online_predict",
            lambda **_k: (0, np.array([0.4, 0.4, 0.2], dtype=float)),
        )
    
        out = engine.update(_md(ts=1.0, ret=0.001, price=100.0))
>       assert out["execution_side"] == "flat"
E       AssertionError: assert 'range_mean_revert' == 'flat'
E         
E         - flat
E         + range_mean_revert

tests/test_advanced_regime_engine_production_fixes.py:324: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
__________ test_range_from_flat_has_deterministic_nonzero_signed_size __________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f07846f16a0>
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f0784540cb0>

    def test_range_from_flat_has_deterministic_nonzero_signed_size(engine, monkeypatch):
        engine.last_signed_position_size = 0.0
        engine._last_edge_score = 1.0
        monkeypatch.setattr(
            module,
            "compute_hmm_regime",
            lambda *_a, **_k: {
                "regime": "RANGE",
                "bull": 0.45,
                "bear": 0.45,
                "crisis": 0.10,
                "trend_strength": 0.0,
                "risk_level": 0.2,
                "confidence": 0.7,
                "conviction": 0.5,
                "uncertainty": 0.3,
                "directional_margin": 0.3,
                "directional_label": "TREND",
                "edge_score": 0.95,
                "trend_score": 0.6,
                "range_score": 0.7,
                "toxic_score": 0.0,
            },
        )
        monkeypatch.setattr(
            engine.nhhmm,
            "forward_pass_step",
            lambda *_a, **_k: (np.array([0.34, 0.33, 0.33], dtype=float), None),
        )
        monkeypatch.setattr(
            engine.sjm,
            "online_predict",
            lambda **_k: (0, np.array([0.33, 0.33, 0.34], dtype=float)),
        )
        out = engine.update(_md(ts=1.0, ret=0.0, price=100.0))
        assert out["execution_side"] == "range_mean_revert"
>       assert out["signed_position_size"] != pytest.approx(0.0)
E       assert 0.0 != 0.0 \xb1 1.0e-12
E        +  where 0.0 \xb1 1.0e-12 = <function approx at 0x7f07ade01c70>(0.0)
E        +    where <function approx at 0x7f07ade01c70> = pytest.approx

tests/test_advanced_regime_engine_production_fixes.py:374: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
________________ test_mtf_default_base_only_does_not_total_fail ________________

    def test_mtf_default_base_only_does_not_total_fail():
        eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=7)
        heals = []
    
        def _track_heal(code=None, context=None):
            heals.append((code, dict(context or {})))
    
        eng._self_heal = _track_heal
        out = eng.update(_base_mtf(ts=1.0, base_ret=0.001))
    
>       assert out["risk_metrics"]["feed_status"] == "OK"
E       AssertionError: assert {'primary': 'OK', 'flags': []} == 'OK'

tests/test_advanced_regime_engine_verified_fixes.py:46: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
_______ test_mtf_unknown_and_zero_weight_timeframes_degrade_predictably ________

    def test_mtf_unknown_and_zero_weight_timeframes_degrade_predictably():
        eng = AdvancedRegimeEngine(
            n_states=3,
            n_features=3,
            strict_mtf_keys=False,
            mtf_weights={"5m": 0.0, "15m": 0.0},
            seed=9,
        )
        payload = _base_mtf(
            ts=1.0,
            base_ret=0.001,
            **{
                "5m": {"return": 0.3, "features": np.array([9.0, 9.0, 9.0])},
                "unknown": {"return": 0.8, "features": np.array([8.0, 8.0, 8.0])},
            },
        )
        out = eng.update(payload)
    
>       assert out["risk_metrics"]["feed_status"] == "OK"
E       AssertionError: assert {'primary': 'OK', 'flags': []} == 'OK'

tests/test_advanced_regime_engine_verified_fixes.py:69: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  advanced_regime_engine:advanced_regime_engine.py:1855 Ignoring non-positive/invalid MTF weight for '5m': 0.0
WARNING  advanced_regime_engine:advanced_regime_engine.py:1855 Ignoring non-positive/invalid MTF weight for '15m': 0.0
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
_______ test_price_return_mismatch_early_return_updates_timestamp_anchor _______

    def test_price_return_mismatch_early_return_updates_timestamp_anchor():
        eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=7)
        eng.update(_single_tf(ts=1.0, ret=0.0, price=100.0))
    
        mismatch_out = eng.update(_single_tf(ts=2.0, ret=0.0, price=101.0))
>       assert mismatch_out["risk_metrics"]["feed_status"] == "PRICE_RETURN_MISMATCH"
E       AssertionError: assert {'primary': 'PRICE_RETURN_MISMATCH', 'flags': []} == 'PRICE_RETURN_MISMATCH'

tests/test_advanced_regime_engine_verified_fixes.py:160: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Price/return mismatch detected (|Δ|=0.010000 > 0.001000); degrading to fail-safe output and freezing risk-state mutation.
_________ test_invalid_scalar_return_fails_safe_with_observable_status _________

    def test_invalid_scalar_return_fails_safe_with_observable_status():
        eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=7)
        out = eng.update(_single_tf(ts=1.0, ret="not-a-number"))
    
>       assert out["risk_metrics"]["feed_status"] == "INVALID_RETURN_INPUT"
E       AssertionError: assert {'primary': 'INVALID_RETURN_INPUT', 'flags': []} == 'INVALID_RETURN_INPUT'

tests/test_advanced_regime_engine_verified_fixes.py:172: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Invalid canonical return input (single_return_non_numeric); emitting fail-safe output.
___________________ test_main_does_not_shadow_engine_symbols ___________________

    def test_main_does_not_shadow_engine_symbols():
        """Regression: main.py was shadowing engine symbols with fallback stubs."""
        import engine as engine_mod
        import main as main_mod
    
        for name in (
            "run_all_engines",
            "analyze_volume_intelligence",
            "detect_entry_trigger",
            "build_trade_plan",
            "compute_score",
            "get_cascade_probability",
            "MarketStateDetector",
            "evaluate_smc_sniper",
            "evaluate_meta_filter",
            "apply_meta_to_decision",
        ):
>           assert hasattr(main_mod, name), f"main missing {name}"
E           AssertionError: main missing detect_entry_trigger
E           assert False
E            +  where False = hasattr(<module 'main' from '/workspace/Btc-bot/main.py'>, 'detect_entry_trigger')

tests/test_regime_wiring_audit.py:317: AssertionError
_________________ test_main_signal_pipeline_engine_constructed _________________

    def test_main_signal_pipeline_engine_constructed():
        import main as main_mod
    
>       assert main_mod._signal_pipeline_engine is not None, (
            "main._signal_pipeline_engine should be constructed when engine imports OK"
        )
E       AssertionError: main._signal_pipeline_engine should be constructed when engine imports OK
E       assert None is not None
E        +  where None = <module 'main' from '/workspace/Btc-bot/main.py'>._signal_pipeline_engine

tests/test_regime_wiring_audit.py:327: AssertionError
__________ test_sniper_execution_engine_signal_only_does_not_execute ___________

    def test_sniper_execution_engine_signal_only_does_not_execute():
        from engine import MarketSnapshot, SniperExecutionEngine
    
>       eng = SniperExecutionEngine(symbol="BTCUSDT", config={"signal_only_mode": True})
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/test_regime_wiring_audit.py:386: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <engine.SniperExecutionEngine object at 0x7f0784364910>
symbol = 'BTCUSDT', strategy_bias_provider = None, on_signal = None
regime_engine = None, feature_engine = None, predictor = None
config = {'signal_only_mode': True}

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        strategy_bias_provider: Optional[Callable[[], str]] = None,
        on_signal: Optional[Callable[[SniperSignal], None]] = None,
        regime_engine: Optional[Any] = None,
        feature_engine: Optional[Any] = None,
        predictor: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        import os as _os  # AUDIT FIX ISSUE-A
        if _os.environ.get("BTCBOT_LIVE_MODE") != "1":
>           raise ImportError(
                "SniperExecutionEngine requires BTCBOT_LIVE_MODE=1."
            )
E           ImportError: SniperExecutionEngine requires BTCBOT_LIVE_MODE=1.

engine.py:5658: ImportError
____________________ test_shock_warmup_transition_is_smooth ____________________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f07846f1e80>

    def test_shock_warmup_transition_is_smooth(engine):
        engine._valid_return_count = 1
        t0, _ = engine._shock_threshold(baseline_vol=0.01, current_ts=1.0)
        engine._valid_return_count = engine._shock_warmup_ticks // 2
        t1, _ = engine._shock_threshold(baseline_vol=0.01, current_ts=30.0)
        engine._valid_return_count = engine._shock_warmup_ticks * 4
        t2, _ = engine._shock_threshold(baseline_vol=0.01, current_ts=600.0)
>       assert t0 < t1 < t2
E       assert 0.045 < 0.045

tests/test_advanced_regime_engine_live_risks.py:119: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
______________ test_time_aware_ema_and_range_are_rate_consistent _______________

self = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f07846f1be0>
market_data = {'features': array([0.1, 0.2, 0.3]), 'price': None, 'return': 0.001, 'timestamp': 0.0}

    @_synchronized
    def update(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(market_data, dict):
            market_data = {}
            self._obs_observe("input_validation", "high", {"reason": "market_data_not_dict"})
            self._warn_rate_limited(
                key="market_data_not_dict",
                message="update() received non-dict market_data; degraded to fail-safe defaults.",
                cooldown_s=30.0,
            )
        if "price" in market_data:
            try:
>               market_data["price"] = float(market_data["price"])
                                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^
E               TypeError: float() argument must be a string or a real number, not 'NoneType'

advanced_regime_engine.py:3822: TypeError

The above exception was the direct cause of the following exception:

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f07aceaebd0>

    def test_time_aware_ema_and_range_are_rate_consistent(monkeypatch):
        def always_range(*_a, **_k):
            return {
                "regime": "RANGE",
                "bull": 0.34,
                "bear": 0.33,
                "crisis": 0.33,
                "trend_strength": 0.0,
                "risk_level": 0.2,
                "confidence": 0.8,
                "conviction": 0.7,
                "uncertainty": 0.2,
                "directional_margin": 0.0,
                "directional_label": "TREND",
                "edge_score": 0.4,
                "trend_score": 0.2,
                "range_score": 0.8,
                "toxic_score": 0.0,
            }
    
        monkeypatch.setattr(module, "compute_hmm_regime", always_range)
        fast = AdvancedRegimeEngine(n_states=3, n_features=3, seed=1)
        slow = AdvancedRegimeEngine(n_states=3, n_features=3, seed=1)
        try:
>           fast.update(_md(ts=0.0, ret=0.001, price=None))

tests/test_advanced_regime_engine_live_risks.py:146: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
advanced_regime_engine.py:112: in wrapper
    return method(self, *args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f07846f1be0>
market_data = {'features': array([0.1, 0.2, 0.3]), 'price': None, 'return': 0.001, 'timestamp': 0.0}

    @_synchronized
    def update(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(market_data, dict):
            market_data = {}
            self._obs_observe("input_validation", "high", {"reason": "market_data_not_dict"})
            self._warn_rate_limited(
                key="market_data_not_dict",
                message="update() received non-dict market_data; degraded to fail-safe defaults.",
                cooldown_s=30.0,
            )
        if "price" in market_data:
            try:
                market_data["price"] = float(market_data["price"])
            except Exception as exc:
>               raise ValueError(f"price must be numeric, got {market_data.get('price')!r}") from exc
E               ValueError: price must be numeric, got None

advanced_regime_engine.py:3824: ValueError
------------------------------ Captured log call -------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
__________________ test_mtf_missing_base_features_fails_safe ___________________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f07846f2cf0>

    def test_mtf_missing_base_features_fails_safe(engine):
        out = engine.update(
            {
                "timestamp": 10.0,
                "price": 100.0,
                "return": 0.001,
                "mtf": {"base": {"return": 0.001}},
            }
        )
        assert out["regime_label"] == "UNKNOWN"
        assert out["signal_valid"] is False
        assert out["risk_metrics"]["feed_status"]["primary"] == "MTF_BASE_FEATURES_MISSING"
>       assert out["risk_metrics"]["engine_status"] == "OK"
E       AssertionError: assert 'NO_FEATURES' == 'OK'
E         
E         - OK
E         + NO_FEATURES

tests/test_advanced_regime_engine_live_risks.py:264: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
------------------------------ Captured log call -------------------------------
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 MTF base timeframe is missing features; emitting fail-safe output.
__________________________ test_shock_triggers_toxic ___________________________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f07846f3cb0>

    def test_shock_triggers_toxic(engine):
        outputs = run_engine(engine, shock_market())
        toxic_ratio = sum(o["regime_label"] == "TOXIC" for o in outputs) / len(outputs)
>       assert toxic_ratio > 0.05
E       assert 0.0 > 0.05

tests/test_advanced_regime_engine.py:82: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
------------------------------ Captured log call -------------------------------
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.322207
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.207766
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.191442
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.183270
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.174523
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.160006
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.154487
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146331
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.155708
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151933
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143298
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143787
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139193
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147316
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149018
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144803
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142896
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137895
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144738
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141334
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135873
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.152257
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141824
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135872
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150258
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138737
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144027
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149042
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139956
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139449
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143796
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150596
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140522
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136880
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144802
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139982
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146635
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150483
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140533
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140330
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144827
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141657
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149288
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144767
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137708
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147533
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146889
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138292
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143362
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149071
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149346
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149429
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138647
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140613
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144427
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140451
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146155
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142676
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140873
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137254
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148414
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146811
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141953
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148301
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148432
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138015
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147853
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143358
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143853
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140024
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136187
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151568
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148787
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136257
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135936
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149481
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143147
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138549
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136099
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151562
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147797
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137826
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146184
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140773
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141854
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142152
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143546
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141803
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151816
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137700
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142457
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137795
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139075
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149507
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148553
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147182
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143703
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148738
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148838
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141861
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.346771
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.238535
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.212432
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.197461
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.188652
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.173485
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.169955
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.165484
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.156815
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.154209
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150784
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.153478
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141274
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151384
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135838
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147954
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144897
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150502
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136279
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149991
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147972
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143550
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137360
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139051
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141564
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143662
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136847
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140887
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141700
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141075
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146805
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149667
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140718
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148135
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144438
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138080
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140392
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151504
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148607
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146242
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141368
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.145939
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149615
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147847
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143248
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144856
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138484
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137266
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143861
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149425
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143577
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141067
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136284
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140364
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138228
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140091
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150222
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.145682
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141043
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140224
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147686
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139914
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146987
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137265
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144939
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135996
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148757
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146760
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142139
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144806
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135869
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136074
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144909
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151537
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141835
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144557
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148609
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149510
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148547
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147760
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142792
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141116
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142483
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138398
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140172
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146333
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137489
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.145806
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149287
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139748
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141397
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149631
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138685
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141313
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147395
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146306
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137942
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150921
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150394
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147415
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.346771
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.235139
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.209918
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.201897
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.177339
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.174994
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.163625
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.162576
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.145696
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150033
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.153564
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146022
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146986
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150663
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150045
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143423
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139445
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149827
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140334
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142520
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142452
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150196
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149681
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140088
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143214
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142350
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136416
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144608
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139376
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143497
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144298
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143694
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135932
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147064
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147011
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138535
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142282
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148638
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143970
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.145024
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140956
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140786
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138049
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138196
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137670
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151124
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149649
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146523
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140088
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136064
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Snapshot dropped: _lock held by update() thread.
________________________________ test_bull_bias ________________________________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f075c3d46e0>

    def test_bull_bias(engine):
        outputs = run_engine(engine, bull_market())
        trend_count = sum(o["regime_label"] == "TREND" for o in outputs)
>       assert trend_count > 0
E       assert 0 > 0

tests/test_advanced_regime_engine.py:101: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
------------------------------ Captured log call -------------------------------
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Snapshot dropped: _lock held by update() thread.
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.153344
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150103
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138070
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147190
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.145499
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137188
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136175
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150589
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150441
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136623
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141521
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136988
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139823
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144099
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139985
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150127
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141817
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137449
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142788
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135908
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142203
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139067
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142770
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135859
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.162015
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.155084
________________________________ test_bear_bias ________________________________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f075c3d4830>

    def test_bear_bias(engine):
        outputs = run_engine(engine, bear_market())
        bear_count = sum(o["regime_label"] == "BEAR" for o in outputs)
>       assert bear_count > 0
E       assert 0 > 0

tests/test_advanced_regime_engine.py:107: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
------------------------------ Captured log call -------------------------------
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
_____________________________ test_range_presence ______________________________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f075c3d4ad0>

    def test_range_presence(engine):
        outputs = run_engine(engine, range_market())
        range_count = sum(o["regime_label"] == "RANGE" for o in outputs)
>       assert range_count > 0
E       assert 0 > 0

tests/test_advanced_regime_engine.py:113: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
------------------------------ Captured log call -------------------------------
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Snapshot dropped: _lock held by update() thread.
________ test_mtf_graceful_degradation_survives_base_feature_corruption ________

    def test_mtf_graceful_degradation_survives_base_feature_corruption():
        eng = _base_engine()
    
        payload = {
            "timestamp": 1_700_000_000.0,
            "price": 100.0,
            "return": 0.001,
            "mtf": {
                "base": {"return": 0.001, "features": [np.nan, np.nan]},
                "1m": {"return": 0.002, "features": [0.1, 0.2, 0.3]},
                "5m": {"return": 0.0015, "features": [0.2, 0.1, 0.0]},
            },
        }
    
        out = eng.update(payload)
    
        assert out["regime_label"] != "UNKNOWN"
>       assert out["risk_metrics"]["feed_status"] == "MTF_FUSED_BASE_FEATURE_INVALID"
E       AssertionError: assert {'primary': 'MTF_FUSED_BASE_FEATURE_INVALID_MACRO_ONLY', 'flags': ['MACRO_ONLY_FALLBACK']} == 'MTF_FUSED_BASE_FEATURE_INVALID'

tests/test_advanced_regime_engine_mission_critical_refactor.py:39: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
__________________ test_range_persistence_after_five_minutes ___________________

    def test_range_persistence_after_five_minutes():
        eng = _base_engine()
    
        def _forced_range(*args, **kwargs):
            return 0, np.array([0.5, 0.5, 0.0], dtype=float)
    
        eng.sjm.online_predict = _forced_range  # type: ignore[assignment]
        eng.last_signed_position_size = 0.08
        eng._range_anchor_size = 0.08
    
        out = None
        ts0 = 1_700_100_000.0
        for i in range(301):
            out = eng.update(
                {
                    "timestamp": ts0 + i,
                    "price": 100.0 + 0.01 * i,
                    "return": 0.0,
                    "features": [0.0, 0.0, 0.0],
                }
            )
    
        assert out is not None
>       assert out["regime_label"] == "RANGE"
E       AssertionError: assert 'UNCALIBRATED' == 'RANGE'
E         
E         - RANGE
E         + UNCALIBRATED

tests/test_advanced_regime_engine_mission_critical_refactor.py:66: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Snapshot dropped: _lock held by update() thread.
___________ TestRegimeClassification.test_strong_bull_returns_trend ____________

self = <tests.test_regime_engine_full_audit.TestRegimeClassification object at 0x7f07992b0910>

    def test_strong_bull_returns_trend(self):
        """Sustained positive returns should produce TREND regime."""
        outputs = _run_regime_engine(_bull_market())
        trend_count = sum(o["regime_label"] == "TREND" for o in outputs)
>       assert trend_count > 0, "Expected at least one TREND in bull market"
E       AssertionError: Expected at least one TREND in bull market
E       assert 0 > 0

tests/test_regime_engine_full_audit.py:129: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.153344
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150103
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138070
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147190
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.145499
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137188
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136175
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150589
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150441
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136623
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141521
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136988
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139823
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144099
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139985
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150127
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141817
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137449
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142788
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135908
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142203
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139067
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142770
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135859
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.162015
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.155084
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Snapshot dropped: _lock held by update() thread.
____________ TestRegimeClassification.test_strong_bear_returns_bear ____________

self = <tests.test_regime_engine_full_audit.TestRegimeClassification object at 0x7f07992b0a50>

    def test_strong_bear_returns_bear(self):
        """Sustained negative returns should produce BEAR regime."""
        outputs = _run_regime_engine(_bear_market())
        bear_count = sum(o["regime_label"] == "BEAR" for o in outputs)
>       assert bear_count > 0, "Expected at least one BEAR in bear market"
E       AssertionError: Expected at least one BEAR in bear market
E       assert 0 > 0

tests/test_regime_engine_full_audit.py:135: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Snapshot dropped: _lock held by update() thread.
_______________ TestRegimeClassification.test_flat_returns_range _______________

self = <tests.test_regime_engine_full_audit.TestRegimeClassification object at 0x7f079960ad70>

    def test_flat_returns_range(self):
        """Near-zero returns should produce RANGE regime."""
        outputs = _run_regime_engine(_range_market())
        range_count = sum(o["regime_label"] == "RANGE" for o in outputs)
>       assert range_count > 0, "Expected at least one RANGE in range market"
E       AssertionError: Expected at least one RANGE in range market
E       assert 0 > 0

tests/test_regime_engine_full_audit.py:141: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Snapshot dropped: _lock held by update() thread.
______________ TestRegimeClassification.test_shock_returns_toxic _______________

self = <tests.test_regime_engine_full_audit.TestRegimeClassification object at 0x7f079960aea0>

    def test_shock_returns_toxic(self):
        """Large sudden moves should trigger TOXIC regime."""
        outputs = _run_regime_engine(_shock_market())
        toxic_count = sum(o["regime_label"] in ("TOXIC", "HALTED") for o in outputs)
>       assert toxic_count > 0, "Expected at least one TOXIC/HALTED on shock market"
E       AssertionError: Expected at least one TOXIC/HALTED on shock market
E       assert 0 > 0

tests/test_regime_engine_full_audit.py:147: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.322207
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.207766
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.191442
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.183270
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.174523
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.160006
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.154487
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146331
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.155708
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151933
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143298
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143787
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139193
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147316
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149018
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144803
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142896
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137895
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144738
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141334
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135873
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.152257
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141824
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135872
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150258
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138737
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144027
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149042
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139956
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139449
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143796
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150596
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140522
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136880
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144802
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139982
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146635
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150483
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140533
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140330
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144827
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141657
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149288
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144767
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137708
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147533
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146889
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138292
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143362
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149071
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149346
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149429
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138647
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140613
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144427
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140451
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146155
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142676
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140873
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137254
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148414
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146811
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141953
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148301
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148432
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138015
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147853
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143358
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143853
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140024
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136187
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151568
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148787
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136257
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135936
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149481
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143147
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138549
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136099
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151562
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147797
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137826
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146184
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140773
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141854
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142152
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143546
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141803
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151816
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137700
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142457
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137795
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139075
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149507
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148553
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147182
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143703
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148738
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148838
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141861
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.346771
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.238535
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.212432
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.197461
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.188652
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.173485
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.169955
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.165484
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.156815
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.154209
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150784
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.153478
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141274
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151384
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135838
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147954
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144897
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150502
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136279
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149991
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147972
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143550
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137360
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139051
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141564
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143662
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136847
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140887
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141700
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141075
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146805
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149667
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140718
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148135
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144438
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138080
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140392
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151504
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148607
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146242
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141368
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.145939
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149615
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147847
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143248
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144856
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138484
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137266
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143861
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149425
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143577
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141067
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136284
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140364
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138228
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140091
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150222
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.145682
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141043
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140224
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147686
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139914
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146987
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137265
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144939
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135996
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148757
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146760
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142139
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144806
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135869
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136074
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144909
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151537
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141835
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144557
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148609
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149510
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148547
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147760
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142792
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141116
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142483
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138398
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140172
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146333
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137489
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.145806
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149287
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139748
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141397
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149631
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138685
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141313
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147395
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146306
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137942
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150921
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150394
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147415
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.346771
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.235139
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.209918
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.201897
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.177339
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.174994
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.163625
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.162576
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.145696
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150033
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.153564
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146022
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146986
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150663
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150045
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143423
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139445
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149827
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140334
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142520
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Snapshot dropped: _lock held by update() thread.
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142452
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150196
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149681
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140088
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143214
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142350
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136416
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144608
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139376
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143497
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144298
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143694
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135932
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147064
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147011
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138535
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142282
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148638
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143970
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.145024
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140956
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140786
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138049
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138196
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137670
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151124
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149649
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146523
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140088
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136064
____________ TestRegimeClassification.test_no_contradictory_states _____________

self = <tests.test_regime_engine_full_audit.TestRegimeClassification object at 0x7f079976f1d0>

    def test_no_contradictory_states(self):
        """A single update should never return contradictory regime info."""
        outputs = _run_regime_engine(_range_market(n=100, seed=77))
        for o in outputs:
            label = o["regime_label"]
>           assert label in ("TREND", "RANGE", "BEAR", "TOXIC", "HALTED", "UNKNOWN"), \
                f"Invalid regime label: {label}"
E           AssertionError: Invalid regime label: UNCALIBRATED
E           assert 'UNCALIBRATED' in ('TREND', 'RANGE', 'BEAR', 'TOXIC', 'HALTED', 'UNKNOWN')

tests/test_regime_engine_full_audit.py:154: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
______________ TestRegimeOutputSchema.test_build_output_failsafe _______________

self = <tests.test_regime_engine_full_audit.TestRegimeOutputSchema object at 0x7f079960afd0>

    def test_build_output_failsafe(self):
        """_build_output with valid params should pass schema validation."""
        out = _build_output(
            regime_idx=0,
            regime_label="TREND",
            trend_strength=0.5,
            risk_level=0.3,
            confidence=0.7,
            edge_score=0.5,
            probabilities={"bull": 0.6, "bear": 0.3, "crisis": 0.1},
            macro_probs=[0.6, 0.3, 0.1],
            position_size=0.1,
            expected_vol=0.02,
            raw_size=0.5,
            is_toxic=False,
            garch_regime_probs=[0.8, 0.2],
            feed_status="OK",
        )
>       assert out["regime_label"] == "TREND"
E       AssertionError: assert 'UNKNOWN' == 'TREND'
E         
E         - TREND
E         + UNKNOWN

tests/test_regime_engine_full_audit.py:203: AssertionError
------------------------------ Captured log call -------------------------------
ERROR    advanced_regime_engine:advanced_regime_engine.py:331 [SCHEMA VIOLATION] Invalid execution_mode:  | output={'schema_version': '1.2.0', 'regime_idx': 0, 'regime_label': 'TREND', 'trend_strength': 0.5, 'risk_level': 0.3, 'confidence': 0.7, 'conviction': 0.0, 'probabilities': {'bull': 0.6000000000000001, 'bear': 0.30000000000000004, 'crisis': 0.10000000000000002}, 'macro_probs': [0.6000000000000001, 0.30000000000000004, 0.10000000000000002], 'position_size': 0.1, 'execution_mode': '', 'execution_side': '', 'signed_position_size': 0.0, 'signal_valid': True, 'weights_loaded': False, 'calibration_valid': F
______________ TestNumericalSafety.test_extreme_returns_no_crash _______________

self = <tests.test_regime_engine_full_audit.TestNumericalSafety object at 0x7f07992b0f50>

    def test_extreme_returns_no_crash(self):
        """Extreme positive/negative returns should not crash or produce NaN."""
        outputs = _run_regime_engine(_shock_market())
        for o in outputs:
            assert math.isfinite(o["confidence"])
            assert math.isfinite(o["trend_strength"])
>           assert o["regime_label"] in ("TREND", "RANGE", "BEAR", "TOXIC", "HALTED", "UNKNOWN")
E           AssertionError: assert 'UNCALIBRATED' in ('TREND', 'RANGE', 'BEAR', 'TOXIC', 'HALTED', 'UNKNOWN')

tests/test_regime_engine_full_audit.py:235: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.322207
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.207766
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.191442
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.183270
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.174523
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.160006
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.154487
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146331
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.155708
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151933
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143298
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143787
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139193
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147316
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149018
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144803
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142896
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137895
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144738
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141334
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135873
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.152257
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141824
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135872
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150258
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138737
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144027
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149042
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139956
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139449
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143796
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150596
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140522
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136880
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144802
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139982
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146635
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150483
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140533
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140330
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144827
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141657
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149288
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144767
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137708
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147533
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146889
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138292
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143362
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149071
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149346
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149429
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138647
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140613
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144427
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140451
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146155
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142676
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140873
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137254
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148414
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146811
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141953
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148301
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148432
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138015
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147853
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143358
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143853
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140024
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136187
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151568
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148787
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136257
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135936
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149481
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143147
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138549
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136099
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151562
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147797
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137826
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146184
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140773
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141854
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142152
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143546
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141803
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151816
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137700
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142457
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137795
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139075
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149507
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148553
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147182
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143703
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148738
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148838
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141861
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.346771
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.238535
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.212432
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.197461
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.188652
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.173485
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.169955
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.165484
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.156815
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.154209
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150784
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.153478
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141274
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151384
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135838
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147954
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144897
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150502
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136279
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149991
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147972
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143550
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137360
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139051
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141564
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143662
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136847
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140887
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141700
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141075
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146805
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149667
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140718
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148135
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Snapshot dropped: _lock held by update() thread.
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144438
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138080
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140392
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151504
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148607
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146242
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141368
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.145939
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149615
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147847
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143248
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144856
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138484
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137266
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143861
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149425
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143577
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141067
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136284
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140364
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138228
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140091
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150222
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.145682
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141043
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140224
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147686
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139914
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146987
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137265
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144939
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135996
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148757
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146760
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142139
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144806
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135869
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136074
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144909
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151537
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141835
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144557
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148609
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149510
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148547
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147760
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142792
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141116
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142483
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138398
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140172
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146333
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137489
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.145806
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149287
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139748
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141397
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149631
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138685
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.141313
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147395
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146306
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137942
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150921
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150394
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147415
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.346771
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.235139
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.209918
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.201897
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.177339
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.174994
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.163625
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.162576
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.145696
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150033
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.153564
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146022
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146986
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150663
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150045
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143423
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139445
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149827
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140334
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142520
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142452
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.150196
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149681
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140088
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143214
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142350
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136416
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144608
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.139376
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143497
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.144298
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143694
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.135932
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147064
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.147011
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138535
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.142282
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.148638
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.143970
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.145024
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140956
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140786
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138049
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.138196
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.137670
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.151124
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.149649
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.146523
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.140088
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.136064
_______ TestRegimeLabelNormalization.test_advanced_regime_engine_labels ________

self = <tests.test_regime_engine_full_audit.TestRegimeLabelNormalization object at 0x7f07992b2710>

    def test_advanced_regime_engine_labels(self):
        """AdvancedRegimeEngine should only emit known labels."""
        valid_labels = {"TREND", "RANGE", "BEAR", "TOXIC", "HALTED", "UNKNOWN"}
        outputs = _run_regime_engine(_range_market(n=100, seed=42))
        for o in outputs:
>           assert o["regime_label"] in valid_labels, \
                f"Unknown regime label: {o['regime_label']}"
E           AssertionError: Unknown regime label: UNCALIBRATED
E           assert 'UNCALIBRATED' in {'UNKNOWN', 'TOXIC', 'BEAR', 'TREND', 'HALTED', 'RANGE'}

tests/test_regime_engine_full_audit.py:841: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
________________________ test_update_handles_nan_return ________________________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f077c72af90>

    def test_update_handles_nan_return(engine):
>       out = engine.update(_md(ret=float("nan")))
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/test_advanced_regime_engine_hardening.py:64: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
advanced_regime_engine.py:112: in wrapper
    return method(self, *args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f077c72af90>
market_data = {'features': array([0.1, 0.2, 0.3]), 'price': nan, 'return': nan, 'timestamp': 1.0}

    @_synchronized
    def update(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(market_data, dict):
            market_data = {}
            self._obs_observe("input_validation", "high", {"reason": "market_data_not_dict"})
            self._warn_rate_limited(
                key="market_data_not_dict",
                message="update() received non-dict market_data; degraded to fail-safe defaults.",
                cooldown_s=30.0,
            )
        if "price" in market_data:
            try:
                market_data["price"] = float(market_data["price"])
            except Exception as exc:
                raise ValueError(f"price must be numeric, got {market_data.get('price')!r}") from exc
            if not np.isfinite(market_data["price"]):
>               raise ValueError(f"price must be finite, got {market_data.get('price')}")
E               ValueError: price must be finite, got nan

advanced_regime_engine.py:3826: ValueError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
______________ test_update_dimension_failure_on_bad_feature_shape ______________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f077c72b620>

    def test_update_dimension_failure_on_bad_feature_shape(engine):
        out = engine.update(_md(ret=0.001, features=np.array([0.1, 0.2])))
        assert out["signal_valid"] is False
>       assert out["risk_metrics"]["feed_status"] == "DIMENSION_FAILURE"
E       AssertionError: assert {'primary': 'DIMENSION_FAILURE', 'flags': []} == 'DIMENSION_FAILURE'

tests/test_advanced_regime_engine_hardening.py:72: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
------------------------------ Captured log call -------------------------------
WARNING  advanced_regime_engine:advanced_regime_engine.py:6017 [SELF HEALING INITIATED]
_________________ test_mtf_partial_failure_degrades_not_crash __________________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f077c72bb60>

    def test_mtf_partial_failure_degrades_not_crash(engine):
        engine._strict_mtf_keys = False
        engine.mtf_weights = {"base": 1.0, "5m": 0.6}
        out = engine.update(
            {
                "timestamp": 1.0,
                "price": 100.0,
                "mtf": {
                    "base": {"return": 0.001, "features": [0.1, 0.2, 0.3]},
                    "5m": {"return": "bad", "features": [0.1, 0.2, 0.3]},
                },
            }
        )
>       assert out["risk_metrics"]["feed_status"] == "MTF_PARTIAL_SURVIVAL"
E       AssertionError: assert {'primary': 'MTF_PARTIAL_SURVIVAL', 'flags': []} == 'MTF_PARTIAL_SURVIVAL'

tests/test_advanced_regime_engine_hardening.py:110: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
------------------------------ Captured log call -------------------------------
WARNING  advanced_regime_engine:advanced_regime_engine.py:6017 [SELF HEALING INITIATED]
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 MTF fusion failed, falling back to SAFE base timeframe
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Self-healing used built-in fallback category mapping.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 MTF degradation summary: invalid_return=1, partial_survival=1, telemetry_partial_survival=1
_________________ test_sjm_non_finite_falls_back_to_last_valid _________________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f07846f38c0>

    def test_sjm_non_finite_falls_back_to_last_valid(engine):
        first = engine.update(_md(ret=0.001))
>       assert first["signal_valid"] is True
E       assert False is True

tests/test_advanced_regime_engine_hardening.py:115: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
------------------------------ Captured log call -------------------------------
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
_________ test_update_handles_extreme_returns_beyond_two_sigma_bounds __________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f07846f2ba0>

    def test_update_handles_extreme_returns_beyond_two_sigma_bounds(engine):
        out_hi = engine.update(_md(ret=2.5, ts=10.0))
>       out_lo = engine.update(_md(ret=-2.5, ts=11.0))
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/test_advanced_regime_engine_hardening.py:136: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
advanced_regime_engine.py:112: in wrapper
    return method(self, *args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f07846f2ba0>
market_data = {'features': array([0.1, 0.2, 0.3]), 'price': -150.0, 'return': -2.5, 'timestamp': 11.0}

    @_synchronized
    def update(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(market_data, dict):
            market_data = {}
            self._obs_observe("input_validation", "high", {"reason": "market_data_not_dict"})
            self._warn_rate_limited(
                key="market_data_not_dict",
                message="update() received non-dict market_data; degraded to fail-safe defaults.",
                cooldown_s=30.0,
            )
        if "price" in market_data:
            try:
                market_data["price"] = float(market_data["price"])
            except Exception as exc:
                raise ValueError(f"price must be numeric, got {market_data.get('price')!r}") from exc
            if not np.isfinite(market_data["price"]):
                raise ValueError(f"price must be finite, got {market_data.get('price')}")
            if market_data["price"] <= 0.0:
>               raise ValueError(f"price must be positive, got {market_data['price']!r}")
E               ValueError: price must be positive, got -150.0

advanced_regime_engine.py:3828: ValueError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
------------------------------ Captured log call -------------------------------
CRITICAL advanced_regime_engine:advanced_regime_engine.py:5762 [CIRCUIT BREAKER TRIGGERED] Reason=VOL_SHOCK
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
________ test_build_output_fallback_path_never_throws_on_schema_failure ________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f079909fd90>

    def test_build_output_fallback_path_never_throws_on_schema_failure(monkeypatch):
        import advanced_regime_engine as module
    
        monkeypatch.setattr(module, "_validate_output_schema", lambda _out: False)
>       out = module._build_output(
            regime_idx=0,
            regime_label="TREND",
            trend_strength=0.5,
            risk_level=0.2,
            confidence=0.9,
            edge_score=0.2,
            probabilities={"bull": 0.8, "bear": 0.1, "crisis": 0.1},
            macro_probs=[0.7, 0.2, 0.1],
            position_size=0.2,
            expected_vol=0.01,
            raw_size=0.3,
            is_toxic=False,
            garch_regime_probs=[0.6, 0.4],
            feed_status="OK",
            last_valid_vol="not-a-number",
            switch_stability_ema=None,
        )

tests/test_advanced_regime_engine_hardening.py:292: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

    def _build_output(
        *,
        regime_idx: int,
        regime_label: str,
        trend_strength: float,
        risk_level: float,
        confidence: float,
        conviction: float = 0.0,
        edge_score: float,
        probabilities: Dict[str, float],
        macro_probs: List[float],
        position_size: float,
        expected_vol: float,
        raw_size: float,
        is_toxic: bool,
        garch_regime_probs: List[float],
        feed_status: Any,
        engine_status: str = "OK",
        signed_position_size: float = 0.0,
        last_valid_vol: float = 0.0,
        switch_stability_ema: float = 1.0,
        execution_mode: str = "",
        execution_side: str = "",
        extended_schema: bool = False,
        range_ticks: int = 0,
        signal_valid: bool = True,
        include_signal_valid: bool = True,
        weights_loaded: bool = False,
        calibration_valid: bool = False,
        production_valid: bool = False,
        research_mode: bool = False,
        calibration_status: str = "uncalibrated",
        engine_id: str = "unknown",
    ) -> Dict[str, Any]:
        """
        Single authoritative output constructor for AdvancedRegimeEngine.update().
        Centralising schema construction here ensures both the normal and feed-failure
        paths emit identical key sets, eliminating downstream KeyError risks from
        schema divergence between code paths.
        """
        safe_expected_vol = safe_float(expected_vol, default=0.0, min=0.0)
        safe_last_valid_vol = safe_float(last_valid_vol, default=max(safe_expected_vol, 1e-12), min=1e-12)
        safe_switch_stability = safe_float(switch_stability_ema, default=1.0, min=1e-6)
        safe_raw_size = safe_float(raw_size, default=0.0, min=0.0, max=10.0)
        safe_position_size = safe_float(position_size, default=0.0, min=0.0, max=_POSITION_SIZE_CAP)
        safe_signed_position = safe_float(
            signed_position_size,
            default=0.0,
            min=-safe_position_size,
            max=safe_position_size,
        )
        safe_risk_level = safe_float(risk_level, default=1.0, min=0.0, max=1.0)
        # confidence := max probability mass, conviction := entropy-derived certainty.
        safe_confidence = safe_float(confidence, default=0.0, min=0.0, max=1.0)
        safe_conviction = safe_float(conviction, default=0.0, min=0.0, max=1.0)
        safe_trend_strength = safe_float(trend_strength, default=0.0, min=-1.0, max=1.0)
        safe_edge_score = safe_float(edge_score, default=0.0, min=0.0, max=1.0)
        safe_regime_idx = int(safe_float(regime_idx, default=-1, min=-1, max=4))
        probabilities = probabilities if isinstance(probabilities, dict) else {}
        safe_prob_values = _normalize_prob_vector(np.asarray([
            safe_float(probabilities.get("bull", 1.0 / 3.0), default=1.0 / 3.0, min=0.0),
            safe_float(probabilities.get("bear", 1.0 / 3.0), default=1.0 / 3.0, min=0.0),
            safe_float(probabilities.get("crisis", 1.0 / 3.0), default=1.0 / 3.0, min=0.0),
        ], dtype=float))
        safe_probabilities = {
            "bull": float(safe_prob_values[0]),
            "bear": float(safe_prob_values[1]),
            "crisis": float(safe_prob_values[2]),
        }
        macro_probs = macro_probs if isinstance(macro_probs, list) else []
        safe_macro_probs = _normalize_prob_vector(np.asarray([
            safe_float(macro_probs[i] if len(macro_probs) > i else 1.0 / 3.0, default=1.0 / 3.0, min=0.0)
            for i in range(3)
        ], dtype=float)).tolist()
        safe_garch_probs = _normalize_prob_vector(np.asarray([
            safe_float(garch_regime_probs[i] if isinstance(garch_regime_probs, list) and len(garch_regime_probs) > i else 0.5, default=0.5, min=0.0)
            for i in range(2)
        ], dtype=float)).tolist()
        if isinstance(feed_status, dict):
            primary_status = str(feed_status.get("primary", "UNKNOWN"))
            raw_flags = feed_status.get("flags", [])
            if not isinstance(raw_flags, list):
                raw_flags = []
            status_flags = [str(v)[:64] for v in raw_flags[:8]]
        else:
            primary_status = str(feed_status or "UNKNOWN")
            status_flags = []
    
        out = {
            'schema_version': _OUTPUT_SCHEMA_VERSION,
            'regime_idx': safe_regime_idx,
            'regime_label': str(regime_label or "UNKNOWN"),
            'trend_strength': safe_trend_strength,
            'risk_level': safe_risk_level,
            'confidence': safe_confidence,
            'conviction': safe_conviction,
            'probabilities': safe_probabilities,
            'macro_probs': safe_macro_probs,
            'position_size': safe_position_size,
            'execution_mode': execution_mode,
            'execution_side': execution_side,
            'signed_position_size': safe_signed_position,
            'signal_valid': bool(signal_valid),
            'weights_loaded': bool(weights_loaded),
            'calibration_valid': bool(calibration_valid),
            'production_valid': bool(production_valid),
            'research_mode': bool(research_mode),
            'calibration_status': str(calibration_status or "uncalibrated"),
            'engine_status': str(engine_status or "UNKNOWN"),
    
            # --- NEW: forward compatibility anchor ---
            'schema_compat': {
                "version": _OUTPUT_SCHEMA_VERSION,
                "backward_compatible": True
            },
    
            'risk_metrics': {
                'expected_volatility': safe_expected_vol,
                'raw_leverage': safe_raw_size,
                'last_valid_vol': safe_last_valid_vol,
                'switch_stability_ema': safe_switch_stability,
                'toxic_penalty_applied': bool(is_toxic),
                'garch_regime_probs': safe_garch_probs,
                'feed_status': {"primary": primary_status, "flags": status_flags},
                'engine_status': str(engine_status or "UNKNOWN"),
                'range_ticks': int(safe_float(range_ticks, default=0.0, min=0.0, max=1e9)),
            },
            # ==========================================
            # EDGE OUTPUT (NEW - FIXES SCHEMA GAP)
            # ==========================================
            'alpha': {
                'edge_score': safe_edge_score
            },
        }
    
        # Centralized DEGRADED enforcement (defense-in-depth).
        if str(engine_status or "OK") == "DEGRADED":
            out["signal_valid"] = False
    
        # --- HARD GUARD (fail-safe, NON-BREAKING) ---
>       if not _validate_output_schema(out, engine_id=engine_id):
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       TypeError: test_build_output_fallback_path_never_throws_on_schema_failure.<locals>.<lambda>() got an unexpected keyword argument 'engine_id'

advanced_regime_engine.py:513: TypeError
_________ test_build_output_fallback_handles_runtime_float_exceptions __________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f079909f1c0>

    def test_build_output_fallback_handles_runtime_float_exceptions(monkeypatch):
        import advanced_regime_engine as module
    
        class ExplosiveFloat:
            def __float__(self):
                raise RuntimeError("explode")
    
        monkeypatch.setattr(module, "_validate_output_schema", lambda _out: False)
>       out = module._build_output(
            regime_idx=0,
            regime_label="TREND",
            trend_strength=0.5,
            risk_level=0.2,
            confidence=0.9,
            edge_score=0.2,
            probabilities={"bull": 0.8, "bear": 0.1, "crisis": 0.1},
            macro_probs=[0.7, 0.2, 0.1],
            position_size=0.2,
            expected_vol=0.01,
            raw_size=0.3,
            is_toxic=False,
            garch_regime_probs=[0.6, 0.4],
            feed_status="OK",
            last_valid_vol=ExplosiveFloat(),
            switch_stability_ema=ExplosiveFloat(),
        )

tests/test_advanced_regime_engine_hardening.py:325: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

    def _build_output(
        *,
        regime_idx: int,
        regime_label: str,
        trend_strength: float,
        risk_level: float,
        confidence: float,
        conviction: float = 0.0,
        edge_score: float,
        probabilities: Dict[str, float],
        macro_probs: List[float],
        position_size: float,
        expected_vol: float,
        raw_size: float,
        is_toxic: bool,
        garch_regime_probs: List[float],
        feed_status: Any,
        engine_status: str = "OK",
        signed_position_size: float = 0.0,
        last_valid_vol: float = 0.0,
        switch_stability_ema: float = 1.0,
        execution_mode: str = "",
        execution_side: str = "",
        extended_schema: bool = False,
        range_ticks: int = 0,
        signal_valid: bool = True,
        include_signal_valid: bool = True,
        weights_loaded: bool = False,
        calibration_valid: bool = False,
        production_valid: bool = False,
        research_mode: bool = False,
        calibration_status: str = "uncalibrated",
        engine_id: str = "unknown",
    ) -> Dict[str, Any]:
        """
        Single authoritative output constructor for AdvancedRegimeEngine.update().
        Centralising schema construction here ensures both the normal and feed-failure
        paths emit identical key sets, eliminating downstream KeyError risks from
        schema divergence between code paths.
        """
        safe_expected_vol = safe_float(expected_vol, default=0.0, min=0.0)
        safe_last_valid_vol = safe_float(last_valid_vol, default=max(safe_expected_vol, 1e-12), min=1e-12)
        safe_switch_stability = safe_float(switch_stability_ema, default=1.0, min=1e-6)
        safe_raw_size = safe_float(raw_size, default=0.0, min=0.0, max=10.0)
        safe_position_size = safe_float(position_size, default=0.0, min=0.0, max=_POSITION_SIZE_CAP)
        safe_signed_position = safe_float(
            signed_position_size,
            default=0.0,
            min=-safe_position_size,
            max=safe_position_size,
        )
        safe_risk_level = safe_float(risk_level, default=1.0, min=0.0, max=1.0)
        # confidence := max probability mass, conviction := entropy-derived certainty.
        safe_confidence = safe_float(confidence, default=0.0, min=0.0, max=1.0)
        safe_conviction = safe_float(conviction, default=0.0, min=0.0, max=1.0)
        safe_trend_strength = safe_float(trend_strength, default=0.0, min=-1.0, max=1.0)
        safe_edge_score = safe_float(edge_score, default=0.0, min=0.0, max=1.0)
        safe_regime_idx = int(safe_float(regime_idx, default=-1, min=-1, max=4))
        probabilities = probabilities if isinstance(probabilities, dict) else {}
        safe_prob_values = _normalize_prob_vector(np.asarray([
            safe_float(probabilities.get("bull", 1.0 / 3.0), default=1.0 / 3.0, min=0.0),
            safe_float(probabilities.get("bear", 1.0 / 3.0), default=1.0 / 3.0, min=0.0),
            safe_float(probabilities.get("crisis", 1.0 / 3.0), default=1.0 / 3.0, min=0.0),
        ], dtype=float))
        safe_probabilities = {
            "bull": float(safe_prob_values[0]),
            "bear": float(safe_prob_values[1]),
            "crisis": float(safe_prob_values[2]),
        }
        macro_probs = macro_probs if isinstance(macro_probs, list) else []
        safe_macro_probs = _normalize_prob_vector(np.asarray([
            safe_float(macro_probs[i] if len(macro_probs) > i else 1.0 / 3.0, default=1.0 / 3.0, min=0.0)
            for i in range(3)
        ], dtype=float)).tolist()
        safe_garch_probs = _normalize_prob_vector(np.asarray([
            safe_float(garch_regime_probs[i] if isinstance(garch_regime_probs, list) and len(garch_regime_probs) > i else 0.5, default=0.5, min=0.0)
            for i in range(2)
        ], dtype=float)).tolist()
        if isinstance(feed_status, dict):
            primary_status = str(feed_status.get("primary", "UNKNOWN"))
            raw_flags = feed_status.get("flags", [])
            if not isinstance(raw_flags, list):
                raw_flags = []
            status_flags = [str(v)[:64] for v in raw_flags[:8]]
        else:
            primary_status = str(feed_status or "UNKNOWN")
            status_flags = []
    
        out = {
            'schema_version': _OUTPUT_SCHEMA_VERSION,
            'regime_idx': safe_regime_idx,
            'regime_label': str(regime_label or "UNKNOWN"),
            'trend_strength': safe_trend_strength,
            'risk_level': safe_risk_level,
            'confidence': safe_confidence,
            'conviction': safe_conviction,
            'probabilities': safe_probabilities,
            'macro_probs': safe_macro_probs,
            'position_size': safe_position_size,
            'execution_mode': execution_mode,
            'execution_side': execution_side,
            'signed_position_size': safe_signed_position,
            'signal_valid': bool(signal_valid),
            'weights_loaded': bool(weights_loaded),
            'calibration_valid': bool(calibration_valid),
            'production_valid': bool(production_valid),
            'research_mode': bool(research_mode),
            'calibration_status': str(calibration_status or "uncalibrated"),
            'engine_status': str(engine_status or "UNKNOWN"),
    
            # --- NEW: forward compatibility anchor ---
            'schema_compat': {
                "version": _OUTPUT_SCHEMA_VERSION,
                "backward_compatible": True
            },
    
            'risk_metrics': {
                'expected_volatility': safe_expected_vol,
                'raw_leverage': safe_raw_size,
                'last_valid_vol': safe_last_valid_vol,
                'switch_stability_ema': safe_switch_stability,
                'toxic_penalty_applied': bool(is_toxic),
                'garch_regime_probs': safe_garch_probs,
                'feed_status': {"primary": primary_status, "flags": status_flags},
                'engine_status': str(engine_status or "UNKNOWN"),
                'range_ticks': int(safe_float(range_ticks, default=0.0, min=0.0, max=1e9)),
            },
            # ==========================================
            # EDGE OUTPUT (NEW - FIXES SCHEMA GAP)
            # ==========================================
            'alpha': {
                'edge_score': safe_edge_score
            },
        }
    
        # Centralized DEGRADED enforcement (defense-in-depth).
        if str(engine_status or "OK") == "DEGRADED":
            out["signal_valid"] = False
    
        # --- HARD GUARD (fail-safe, NON-BREAKING) ---
>       if not _validate_output_schema(out, engine_id=engine_id):
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       TypeError: test_build_output_fallback_handles_runtime_float_exceptions.<locals>.<lambda>() got an unexpected keyword argument 'engine_id'

advanced_regime_engine.py:513: TypeError
_________________________ test_schema_failure_metrics __________________________

    def test_schema_failure_metrics():
        if not module._PROM_AVAILABLE:
            pytest.skip("prometheus_client not available")
    
>       schema_counter = module.REGIME_SCHEMA_VIOLATIONS.labels(
            engine_id="default",
            reason="schema_validation_failed",
        )

tests/test_advanced_regime_engine_hardening.py:355: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = prometheus_client.metrics.Counter(regime_schema_violations)
labelvalues = ()
labelkwargs = {'engine_id': 'default', 'reason': 'schema_validation_failed'}

    def labels(self: T, *labelvalues: Any, **labelkwargs: Any) -> T:
        """Return the child for the given labelset.
    
        All metrics can have labels, allowing grouping of related time series.
        Taking a counter as an example:
    
            from prometheus_client import Counter
    
            c = Counter('my_requests_total', 'HTTP Failures', ['method', 'endpoint'])
            c.labels('get', '/').inc()
            c.labels('post', '/submit').inc()
    
        Labels can also be provided as keyword arguments:
    
            from prometheus_client import Counter
    
            c = Counter('my_requests_total', 'HTTP Failures', ['method', 'endpoint'])
            c.labels(method='get', endpoint='/').inc()
            c.labels(method='post', endpoint='/submit').inc()
    
        See the best practices on [naming](http://prometheus.io/docs/practices/naming/)
        and [labels](http://prometheus.io/docs/practices/instrumentation/#use-labels).
        """
        if not self._labelnames:
            raise ValueError('No label names were set when constructing %s' % self)
    
        if self._labelvalues:
            raise ValueError('{} already has labels set ({}); can not chain calls to .labels()'.format(
                self,
                dict(zip(self._labelnames, self._labelvalues))
            ))
    
        if labelvalues and labelkwargs:
            raise ValueError("Can't pass both *args and **kwargs")
    
        if labelkwargs:
            if sorted(labelkwargs) != sorted(self._labelnames):
>               raise ValueError('Incorrect label names')
E               ValueError: Incorrect label names

/root/.pyenv/versions/3.14.4/lib/python3.14/site-packages/prometheus_client/metrics.py:175: ValueError
_____________________ test_load_state_logs_degrade_fields ______________________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f07846f23c0>
caplog = <_pytest.logging.LogCaptureFixture object at 0x7f07846f1a90>

    def test_load_state_logs_degrade_fields(engine, caplog):
        caplog.set_level("ERROR")
        engine.load_state(
            {
                "current_regime_idx": "bad",
                "confirmed_regime_idx": 999,
                "loss_streak": "bad",
                "healing_count": -3,
            }
        )
>       assert "STATE_LOAD_DEGRADE field=current_regime_idx" in caplog.text
E       assert 'STATE_LOAD_DEGRADE field=current_regime_idx' in "ERROR    advanced_regime_engine:advanced_regime_engine.py:3484 load_state: neither smoother nor state probs found. Using uniform prior.\nCRITICAL advanced_regime_engine:advanced_regime_engine.py:3565 load_state: NHHMM parameter restore INCOMPLETE. Restored: []. Failed/missing: ['nhhmm_beta', 'nhhmm_mu', 'nhhmm_sigma']. Regime posteriors will be INCOHERENT \u2014 emission distribution is using default parameters, not trained values. Engine requires retrain or a valid snapshot before live trading.\n"
E        +  where "ERROR    advanced_regime_engine:advanced_regime_engine.py:3484 load_state: neither smoother nor state probs found. Using uniform prior.\nCRITICAL advanced_regime_engine:advanced_regime_engine.py:3565 load_state: NHHMM parameter restore INCOMPLETE. Restored: []. Failed/missing: ['nhhmm_beta', 'nhhmm_mu', 'nhhmm_sigma']. Regime posteriors will be INCOHERENT \u2014 emission distribution is using default parameters, not trained values. Engine requires retrain or a valid snapshot before live trading.\n" = <_pytest.logging.LogCaptureFixture object at 0x7f07846f1a90>.text

tests/test_advanced_regime_engine_hardening.py:470: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
------------------------------ Captured log call -------------------------------
ERROR    advanced_regime_engine:advanced_regime_engine.py:3484 load_state: neither smoother nor state probs found. Using uniform prior.
CRITICAL advanced_regime_engine:advanced_regime_engine.py:3565 load_state: NHHMM parameter restore INCOMPLETE. Restored: []. Failed/missing: ['nhhmm_beta', 'nhhmm_mu', 'nhhmm_sigma']. Regime posteriors will be INCOHERENT — emission distribution is using default parameters, not trained values. Engine requires retrain or a valid snapshot before live trading.
_ test_regime_markov_smoother_directional_evidence_is_symmetric_and_not_suppressed _

    def test_regime_markov_smoother_directional_evidence_is_symmetric_and_not_suppressed():
        smoother = RegimeMarkovSmoother()
        scores = {"bull": 0.9, "bear": 0.1, "trend_score": 0.2, "range_score": 0.2, "toxic_score": 0.2}
        evidence = smoother._scores_to_evidence(scores)
        assert np.isclose(float(np.sum(evidence)), 1.0)
        assert evidence[smoother.state_to_idx["TREND"]] > 0.35
>       assert evidence[smoother.state_to_idx["BEAR"]] > 0.10
E       assert np.float64(0.03125) > 0.1

tests/test_advanced_regime_engine_hardening.py:547: AssertionError
_____________ test_snapshot_path_no_tautological_consistency_loop ______________

    def test_snapshot_path_no_tautological_consistency_loop():
        eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=7)
        replay = _ReplayCapture()
        warn_keys = []
        try:
            eng._replay_engine = replay
            eng._tick_id = 99
            eng._warn_rate_limited = lambda key, *_a, **_k: warn_keys.append(key)
            eng.update(_md(ret=0.001, ts=1.0))
>           assert len(replay.payloads) == 1
E           assert 0 == 1
E            +  where 0 = len([])
E            +    where [] = <tests.test_advanced_regime_engine_hardening._ReplayCapture object at 0x7f077c72b380>.payloads

tests/test_advanced_regime_engine_hardening.py:592: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
___________________ test_load_snapshot_logs_structured_error ___________________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f077c72aba0>
caplog = <_pytest.logging.LogCaptureFixture object at 0x7f07ad1b4910>

    def test_load_snapshot_logs_structured_error(engine, caplog):
        caplog.set_level("ERROR")
        engine.load_state = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
        engine.load_snapshot({"engine_state": {}})
>       assert "Snapshot load failed context_keys=['engine_state']" in caplog.text
E       assert "Snapshot load failed context_keys=['engine_state']" in "CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.\nERROR    advanced_regime_engine:advanced_regime_engine.py:3484 load_state: neither smoother nor state probs found. Using uniform prior.\nCRITICAL advanced_regime_engine:advanced_regime_engine.py:3565 load_state: NHHMM parameter restore INCOMPLETE. Restored: []. Failed/missing: ['nhhmm_beta', 'nhhmm_mu', 'nhhmm_sigma']. Regime posteriors will be INCOHERENT \u2014 emission distribution is using default parameters, not trained values. Engine requires retrain or a valid snapshot before live trading.\nCRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.\nCRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.\n"
E        +  where "CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.\nERROR    advanced_regime_engine:advanced_regime_engine.py:3484 load_state: neither smoother nor state probs found. Using uniform prior.\nCRITICAL advanced_regime_engine:advanced_regime_engine.py:3565 load_state: NHHMM parameter restore INCOMPLETE. Restored: []. Failed/missing: ['nhhmm_beta', 'nhhmm_mu', 'nhhmm_sigma']. Regime posteriors will be INCOHERENT \u2014 emission distribution is using default parameters, not trained values. Engine requires retrain or a valid snapshot before live trading.\nCRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.\nCRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.\n" = <_pytest.logging.LogCaptureFixture object at 0x7f07ad1b4910>.text

tests/test_advanced_regime_engine_hardening.py:648: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
------------------------------ Captured log call -------------------------------
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
ERROR    advanced_regime_engine:advanced_regime_engine.py:3484 load_state: neither smoother nor state probs found. Using uniform prior.
CRITICAL advanced_regime_engine:advanced_regime_engine.py:3565 load_state: NHHMM parameter restore INCOMPLETE. Restored: []. Failed/missing: ['nhhmm_beta', 'nhhmm_mu', 'nhhmm_sigma']. Regime posteriors will be INCOHERENT — emission distribution is using default parameters, not trained values. Engine requires retrain or a valid snapshot before live trading.
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
___________ test_circuit_breaker_vol_shock_short_circuits_same_tick ____________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f077c72bcb0>

    def test_circuit_breaker_vol_shock_short_circuits_same_tick(engine):
        engine._VOL_SHOCK_MULTIPLIER = 0.05
        out = engine.update(_md(ret=0.8, ts=1.0))
        assert out["regime_label"] == "HALTED"
        assert out["execution_mode"] == "circuit_breaker"
>       assert out["risk_metrics"]["feed_status"] == "CIRCUIT_BREAKER:VOL_SHOCK"
E       AssertionError: assert {'primary': 'CIRCUIT_BREAKER:VOL_SHOCK', 'flags': []} == 'CIRCUIT_BREAKER:VOL_SHOCK'

tests/test_advanced_regime_engine_hardening.py:656: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
------------------------------ Captured log call -------------------------------
CRITICAL advanced_regime_engine:advanced_regime_engine.py:5762 [CIRCUIT BREAKER TRIGGERED] Reason=VOL_SHOCK
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
______ test_breaker_reason_and_healing_counter_not_overwritten_same_tick _______

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f075c718c20>

    def test_breaker_reason_and_healing_counter_not_overwritten_same_tick(engine):
        engine._VOL_SHOCK_MULTIPLIER = 100.0
        engine._last_price = 100.0
        engine._last_price_timestamp = 0.0
        engine.last_signed_position_size = 1.0
        engine._loss_streak = engine._MAX_CONSECUTIVE_LOSSES - 1
        out = engine.update(_md(ret=-0.2, ts=1.0) | {"price": 80.0})
        assert out["regime_label"] == "HALTED"
>       assert engine._circuit_breaker_reason == "MAX_DRAWDOWN"
E       AssertionError: assert 'VOL_SHOCK' == 'MAX_DRAWDOWN'
E         
E         - MAX_DRAWDOWN
E         + VOL_SHOCK

tests/test_advanced_regime_engine_hardening.py:742: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
------------------------------ Captured log call -------------------------------
CRITICAL advanced_regime_engine:advanced_regime_engine.py:5762 [CIRCUIT BREAKER TRIGGERED] Reason=VOL_SHOCK
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
___________ test_healing_branch_returns_immediately_after_self_heal ____________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f075c719010>
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f07ad243b60>

    def test_healing_branch_returns_immediately_after_self_heal(engine, monkeypatch):
        engine._circuit_breaker_active = True
        engine._healing_counter = engine._HEALING_COOLDOWN_TICKS + 1
        monkeypatch.setattr(engine.nhhmm, "forward_pass_step", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("must not run")))
        out = engine.update(_md(ret=0.001, ts=1.0))
>       assert out["regime_label"] == "HALTED"
E       AssertionError: assert 'UNCALIBRATED' == 'HALTED'
E         
E         - HALTED
E         + UNCALIBRATED

tests/test_advanced_regime_engine_hardening.py:776: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
------------------------------ Captured log call -------------------------------
WARNING  advanced_regime_engine:advanced_regime_engine.py:6017 [SELF HEALING INITIATED]
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:766 compute_hmm_regime: score sum out-of-band sum=1.112915
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Single-TF NHHMM forward pass failed; using uniform posterior. error=must not run
_______________ test_negative_equity_clamped_and_breaker_tripped _______________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f075c7196a0>

    def test_negative_equity_clamped_and_breaker_tripped(engine):
        engine._last_price = 100.0
        engine._last_price_timestamp = 0.0
        engine.last_signed_position_size = 1.0
>       engine.update(_md(ret=-1.0, ts=1.0) | {"price": 0.0})

tests/test_advanced_regime_engine_hardening.py:846: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
advanced_regime_engine.py:112: in wrapper
    return method(self, *args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f075c7196a0>
market_data = {'features': array([0.1, 0.2, 0.3]), 'price': 0.0, 'return': -1.0, 'timestamp': 1.0}

    @_synchronized
    def update(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(market_data, dict):
            market_data = {}
            self._obs_observe("input_validation", "high", {"reason": "market_data_not_dict"})
            self._warn_rate_limited(
                key="market_data_not_dict",
                message="update() received non-dict market_data; degraded to fail-safe defaults.",
                cooldown_s=30.0,
            )
        if "price" in market_data:
            try:
                market_data["price"] = float(market_data["price"])
            except Exception as exc:
                raise ValueError(f"price must be numeric, got {market_data.get('price')!r}") from exc
            if not np.isfinite(market_data["price"]):
                raise ValueError(f"price must be finite, got {market_data.get('price')}")
            if market_data["price"] <= 0.0:
>               raise ValueError(f"price must be positive, got {market_data['price']!r}")
E               ValueError: price must be positive, got 0.0

advanced_regime_engine.py:3828: ValueError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
__ test_price_return_mismatch_emits_fail_safe_without_pnl_state_contamination __

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f075c71ae40>

    def test_price_return_mismatch_emits_fail_safe_without_pnl_state_contamination(engine):
        engine._last_price = 100.0
        engine._last_price_timestamp = 1.0
        engine.last_signed_position_size = 1.0
        engine._equity = 1.0
        engine._loss_streak = 2
        out = engine.update(_md(ret=0.0, ts=2.0) | {"price": 110.0})
>       assert out["execution_mode"] == "fail_safe"
E       AssertionError: assert 'halt' == 'fail_safe'
E         
E         - fail_safe
E         + halt

tests/test_advanced_regime_engine_hardening.py:936: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
------------------------------ Captured log call -------------------------------
WARNING  advanced_regime_engine:advanced_regime_engine.py:960 SparseJumpModel fallback initialized with symmetric zero centroids; load_weights() is recommended for production inference.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 PnL tracking requires timestamp anchors but feed is timestamp-less or mixed; PnL update skipped and feed marked degraded.
_____ test_breaker_cooldown_initialization_consistent_across_trigger_paths _____

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f075c71b4d0>

    def test_breaker_cooldown_initialization_consistent_across_trigger_paths(engine):
        engine._VOL_SHOCK_MULTIPLIER = 0.05
        out_shock = engine.update(_md(ret=0.8, ts=1.0))
        assert out_shock["regime_label"] == "HALTED"
        assert engine._circuit_breaker_reason == "VOL_SHOCK"
        assert engine._healing_counter == 0
    
        engine.reset_state()
        engine._VOL_SHOCK_MULTIPLIER = 100.0
        engine._last_price = 100.0
        engine._last_price_timestamp = 1.0
        engine.last_signed_position_size = 1.0
        out_dd = engine.update(_md(ret=-0.2, ts=2.0) | {"price": 80.0})
        assert out_dd["regime_label"] == "HALTED"
>       assert engine._circuit_breaker_reason == "MAX_DRAWDOWN"
E       AssertionError: assert 'VOL_SHOCK' == 'MAX_DRAWDOWN'
E         
E         - MAX_DRAWDOWN
E         + VOL_SHOCK

tests/test_advanced_regime_engine_hardening.py:959: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
------------------------------ Captured log call -------------------------------
CRITICAL advanced_regime_engine:advanced_regime_engine.py:5762 [CIRCUIT BREAKER TRIGGERED] Reason=VOL_SHOCK
CRITICAL advanced_regime_engine:advanced_regime_engine.py:5762 [CIRCUIT BREAKER TRIGGERED] Reason=VOL_SHOCK
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
___________ test_warning_worker_does_not_keep_engine_alive_strongly ____________

    def test_warning_worker_does_not_keep_engine_alive_strongly():
        eng = AdvancedRegimeEngine(n_states=3, n_features=3, seed=99)
        worker = eng._warning_worker
        stop_event = eng._warning_stop_event
        eng_ref = weakref.ref(eng)
        del eng
        for _ in range(20):
            gc.collect()
            if eng_ref() is None:
                break
            time.sleep(0.05)
>       assert eng_ref() is None
E       AssertionError: assert <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f075c71bcb0> is None
E        +  where <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f075c71bcb0> = <weakref at 0x7f075c2623e0; to 'advanced_regime_engine.AdvancedRegimeEngine' at 0x7f075c71bcb0>()

tests/test_advanced_regime_engine_hardening.py:987: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
_______________ test_full_self_heal_resets_last_price_reference ________________

engine = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f075c245010>

    def test_full_self_heal_resets_last_price_reference(engine):
        engine._last_price = 50.0
        engine._last_price_timestamp = 1.0
        engine.last_signed_position_size = 1.0
        engine._equity = 1.0
        action = engine._self_heal()
        assert action == "RESET_FULL"
>       assert engine._last_price is None
E       assert 50.0 is None
E        +  where 50.0 = <advanced_regime_engine.AdvancedRegimeEngine object at 0x7f075c245010>._last_price

tests/test_advanced_regime_engine_hardening.py:1064: AssertionError
------------------------------ Captured log setup ------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
------------------------------ Captured log call -------------------------------
WARNING  advanced_regime_engine:advanced_regime_engine.py:6017 [SELF HEALING INITIATED]
________________________ test_regime_engine_integration ________________________

    def test_regime_engine_integration():
        e=AdvancedRegimeEngine()
        out=e.update({"return":0.001,"features":np.array([0.1,1000.0,950.0,50.0]),"price":50000.0,"orderbook":{},"open_interest":0.0,"funding_rate":0.0})
>       assert out["regime_label"] != "UNKNOWN"
E       AssertionError: assert 'UNKNOWN' != 'UNKNOWN'

tests/integration/test_regime_engine_integration.py:7: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  model_weights:model_weights.py:39 [WEIGHTS] Weight file not found for model 'advanced_regime': /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz
CRITICAL advanced_regime_engine:advanced_regime_engine.py:2263 [REGIME] Missing trained weights at /tmp/pytest-of-root/pytest-0/test_uncalibrated_weights_fail0/missing_weights.npz; blocking regime engine until calibration artifacts are available.
WARNING  advanced_regime_engine:advanced_regime_engine.py:6017 [SELF HEALING INITIATED]
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 update() called while engine_status=DEGRADED. NHHMM parameters may be incoherent. Signal reliability is reduced. Reload a valid snapshot or retrain.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Single-TF features invalid; using deterministic uniform posterior fallback.
WARNING  advanced_regime_engine:advanced_regime_engine.py:2174 Self-healing used built-in fallback category mapping.
=============================== warnings summary ===============================
tests/test_advanced_regime_engine_production_fixes.py::test_warning_queue_saturation_safe
  /workspace/Btc-bot/tests/test_advanced_regime_engine_production_fixes.py:114: RuntimeWarning: Warning queue saturated; dropping warning messages.
    engine._warn_rate_limited(f"drop-{i}", "msg", cooldown_s=0.0)

tests/test_advanced_regime_engine_hardening.py::test_warning_drop_counter_thread_safe_when_queue_is_full
  /root/.pyenv/versions/3.14.4/lib/python3.14/threading.py:1024: RuntimeWarning: Warning queue saturated; dropping warning messages.
    self._target(*self._args, **self._kwargs)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
```

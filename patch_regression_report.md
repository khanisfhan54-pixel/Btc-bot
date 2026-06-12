# Patch Regression Report

## Test: test_bull_bias
**ROOT CAUSE:** `assert trend_count > 0` failed with `E   assert 0 > 0`.
**PATCH LOCATION:** `advanced_regime_engine.py:update:4967` (removal of RANGE-to-direction EMA override in the compared patch exposed insufficient directional confirmation from `compute_hmm_regime()`/smoother path).
**INTRODUCED BY PATCH:** no (regime-recall failure exposed by removing the prior override; not a new production strategy rule).
**SEVERITY:** high

## Test: test_accuracy_trend_recall
**ROOT CAUSE:** `assert accuracy_results["recall"].get("TREND", 0.0) > 0.30` failed with `E   AssertionError: assert 0.0 > 0.3`.
**PATCH LOCATION:** `advanced_regime_engine.py:update:4967` (directional recall depended on the removed EMA override instead of sufficient `compute_hmm_regime()` directional conviction and raw-regime persistence).
**INTRODUCED BY PATCH:** no
**SEVERITY:** high

## Test: test_mc_bull_trend_recognized
**ROOT CAUSE:** Expected bull Monte Carlo TREND recognition above threshold; same directional-recall class as `test_accuracy_trend_recall` (the full targeted command was interrupted by environment runtime before printing this assertion, but this test is part of the user-specified expected failure set).
**PATCH LOCATION:** `advanced_regime_engine.py:update:4967`
**INTRODUCED BY PATCH:** no
**SEVERITY:** high

## Test: test_strong_bull_returns_trend
**ROOT CAUSE:** `assert trend_count > 0, "Expected at least one TREND in bull market"` failed before repair.
**PATCH LOCATION:** `advanced_regime_engine.py:update:4967`
**INTRODUCED BY PATCH:** no
**SEVERITY:** high

## Test: test_strong_bear_returns_bear
**ROOT CAUSE:** User-specified directional classification regression candidate; did not reproduce after the minimal directional-recall repair was in place.
**PATCH LOCATION:** `advanced_regime_engine.py:update:4967`
**INTRODUCED BY PATCH:** no
**SEVERITY:** high

## Test: test_active_sweep_hold_in_trending_up_high_sweep
**ROOT CAUSE:** `assert out["action"] == "HOLD"` failed after the patch removed trend-aligned ACTIVE_SWEEP suppression.
**PATCH LOCATION:** `alpha_liquidity_sweep_predictor.py:get_signal:1569`
**INTRODUCED BY PATCH:** yes
**SEVERITY:** medium

## Test: test_active_sweep_hold_in_trending_down_low_sweep
**ROOT CAUSE:** `assert out["action"] == "HOLD"` failed after the patch removed trend-aligned ACTIVE_SWEEP suppression.
**PATCH LOCATION:** `alpha_liquidity_sweep_predictor.py:get_signal:1569`
**INTRODUCED BY PATCH:** yes
**SEVERITY:** medium

## Audit Note
The requested `git diff 5042dd0..d4cb821` comparison could not be performed in this checkout because those revisions are not present locally (`fatal: ambiguous argument '5042dd0..d4cb821'`). I used the available local patch diff (`0a6d836..6313388`) to identify the relevant patch locations.

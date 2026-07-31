# Top 10 Remaining Production Blockers

Generated: 2026-06-12 (UTC)

Ranking criteria: (1) production risk, (2) information gain, (3) number of failures explained. No regime logic was modified.

## 1. Active default regime weights are synthetic and fail-closed

- Classification: **ARCHITECTURE DECISION** with production deployment risk.
- Evidence: active provenance is `data_source=synthetic`, `production_valid=false`; default engine emits `calibration_status=not_production_valid`, `signal_valid=False`, `regime_label=UNCALIBRATED`, `execution_mode=halt`.
- Failures explained: broad collapse of regime accuracy/integration tests to `UNCALIBRATED`, including TREND/BEAR/RANGE/TOXIC recall failures and many output-vocabulary tests.
- Recommendation: obtain/load a real BTC `production_valid=true` artifact before evaluating classifier logic.

## 2. Research-mode override can make synthetic/non-production weights signal-valid

- Classification: **ARCHITECTURE DECISION** with **REAL BUG** potential if production environment can enable it.
- Evidence: `REGIME_RESEARCH_MODE=1` with active synthetic weights produced `production_valid=False`, `calibration_status=research`, `signal_valid=True`, `regime_label=RANGE`, `execution_mode=range_mean_revert`.
- Failures explained: not a current pytest failure, but it is the highest-risk governance gap if live runtime permits research mode.
- Recommendation: later add deployment guard preventing research mode in live trading.

## 3. End-to-end regime classification metrics are zero for all requested classes under current artifact

- Classification: **RESEARCH GAP** caused by current governance state.
- Evidence: validation support was 370 per class; precision/recall were 0.0000 for TREND/RANGE/BEAR/TOXIC.
- Failures explained: `test_accuracy_trend_recall`, `test_accuracy_bear_recall`, `test_accuracy_no_regime_collapse`, and full-audit classification failures.
- Recommendation: rerun with real/prod-valid artifact before changing thresholds, confidence formulas, alpha logic, or smoother logic.

## 4. Tests still expect legacy scalar `feed_status` strings, while current engine emits structured feed-status dicts

- Classification: **STALE TEST** unless downstream contracts require scalar strings.
- Evidence: failures compare `{'primary': 'OK', 'flags': []}` to `'OK'`, `{'primary': 'PRICE_RETURN_MISMATCH', 'flags': []}` to `'PRICE_RETURN_MISMATCH'`, and similar `DIMENSION_FAILURE`, `MTF_PARTIAL_SURVIVAL`, `CIRCUIT_BREAKER:VOL_SHOCK` cases.
- Failures explained: multiple `test_advanced_regime_engine_verified_fixes.py` and `test_advanced_regime_engine_hardening.py` failures.
- Recommendation: decide whether the current dict schema is canonical; update tests/contracts later if yes.

## 5. Phase-C liquidity-sweep tests reference removed/renamed `_bar_count`

- Classification: **STALE TEST** or compatibility shim decision.
- Evidence: `LiquiditySweepAlpha` initializes `_bar_idx`, not `_bar_count`; tests directly set `model._pool_set_bar[...] = model._bar_count`, causing AttributeError.
- Failures explained: seven Phase-C tests fail immediately on `_bar_count`.
- Recommendation: later update tests to `_bar_idx` or provide compatibility alias if external callers still use `_bar_count`.

## 6. Phase-C regime vocabulary disagreement: tests expect `TRENDING_UP/DOWN`, implementation emits/whitelists `UPTREND/DOWNTREND`

- Classification: **STALE TEST** unless external API requires old names.
- Evidence: tests assert `_detect_regime(101,100) == TRENDING_UP` and assert `UPTREND not in _VALID_REGIMES`; implementation explicitly returns `UPTREND/DOWNTREND` and whitelists them.
- Failures explained: `test_detect_regime_returns_valid_vocab`, `test_volatile_in_valid_regimes`, `test_regime_output_field_is_valid_vocab`.
- Recommendation: decide canonical LSA vocabulary before changing code.

## 7. Main wiring audit expects symbols/pipeline objects that are absent under current import/runtime policy

- Classification: **STALE TEST** or **ARCHITECTURE DECISION**.
- Evidence: failures say `main missing detect_entry_trigger`, `_signal_pipeline_engine should be constructed`, and `SniperExecutionEngine requires BTCBOT_LIVE_MODE=1`.
- Failures explained: three `tests/test_regime_wiring_audit.py` failures.
- Recommendation: validate whether these are required production APIs or intentionally removed/deferred live-mode guards.

## 8. Safety-path tests reveal unresolved edge cases in validation/fallback semantics

- Classification: **REAL BUG** candidates.
- Evidence: failures include `ValueError: price must be finite, got nan`, `ValueError: price must be positive, got -150.0`, `ValueError: price must be positive, got 0.0`, and fallback-output lambdas receiving unexpected `engine_id` keyword.
- Failures explained: several hardening tests around NaN/extreme returns, negative/zero prices, and fallback schema failure.
- Recommendation: after artifact validation, triage these as isolated safety bugs without changing classifier thresholds/confidence/alpha logic.

## 9. Circuit-breaker priority/cooldown/self-heal tests disagree with current behavior

- Classification: **REAL BUG** candidates or **ARCHITECTURE DECISION**.
- Evidence: failures include breaker reason overwritten from `MAX_DRAWDOWN` to `VOL_SHOCK`, healing branch output `UNCALIBRATED` instead of `HALTED`, cooldown initialization mismatches, and full self-heal not resetting `_last_price`.
- Failures explained: multiple advanced hardening/live-risk tests.
- Recommendation: defer implementation until governance-vs-regime-output ordering is intentionally specified.

## 10. Observability/state-load/snapshot tests disagree with current logging/replay behavior

- Classification: mixed **STALE TEST** and **REAL BUG** candidates.
- Evidence: failures expect structured snapshot-load log text, `STATE_LOAD_DEGRADE` field logs, replay payload count, schema-failure metrics label behavior, and warning-worker weakref cleanup.
- Failures explained: snapshot/logging/metrics/worker hardening failures.
- Recommendation: separate contract drift from memory/resource leak risk; the warning-worker strong-reference failure deserves production attention.

## Overall recommendation

The strongest current blocker is not classifier math; it is artifact/governance state. Do not modify `compute_hmm_regime()`, `RegimeMarkovSmoother`, thresholds, confidence formulas, alpha logic, or execution logic until a real BTC production-valid artifact is available and the same validation suite is rerun. Then classify residual failures again: many current failures are explained by the synthetic artifact forcing `UNCALIBRATED` output or by stale tests expecting older contracts.

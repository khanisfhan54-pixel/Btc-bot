# Advanced Regime Engine Architecture Map

## Scope
Forensic map of the BTC-Bot regime subsystem as inspected on 2026-06-11. This is diagnosis-only; no runtime code was changed.

## Core files and classes

| Area | File | Class/function | Role |
|---|---|---|---|
| Advanced regime orchestration | `advanced_regime_engine.py` | `AdvancedRegimeEngine.__init__`, `AdvancedRegimeEngine.update`, `_load_model_weights`, `_trigger_circuit_breaker`, `_self_heal` | Owns state, weight loading, live update loop, output schema construction, risk halt/self-heal behavior. |
| Output schema | `advanced_regime_engine.py` | `_build_output`, `_validate_output_schema`, `_normalize_prob_vector` | Central output constructor and schema guard. Converts string feed statuses into structured `risk_metrics.feed_status={primary, flags}`. |
| HMM score mapping | `advanced_regime_engine.py` | `compute_hmm_regime` | Converts 3-state SJM/HMM probabilities (`bull`, `bear`, `crisis`) into heuristic `TREND/RANGE/BEAR/TOXIC` scores. Emits `score_map`, but does not normalize it to a probability distribution. |
| Markov smoothing | `advanced_regime_engine.py` | `RegimeMarkovSmoother`, `_scores_to_evidence`, `update` | Converts score evidence into 4-state smoothed regime probabilities; includes hysteresis against weak switches. |
| NHHMM layer | `advanced_regime_engine.py` | `NHHMM_Engine`, `load_weights`, `_compute_transition_matrix`, `forward_pass_step` | 3-state predictive macro model using feature-conditioned transition matrix plus return emissions. |
| Sparse Jump Model | `advanced_regime_engine.py` | `SparseJumpModel`, `load_weights`, `filter` | 3-state centroid model over normalized features; produces alpha-like regime probabilities. |
| Weight I/O | `model_weights.py` | `ModelWeightManager.save_weights`, `load_weights` | Serializes `.npz` arrays and optional scalar JSON; used by `_load_model_weights`. |
| Calibration pipeline | `calibrate_regime.py` | module pipeline, `triple_barrier_labels`, `add_toxic_label`, `calibrate` | Generates `weights/advanced_regime_weights.npz` and `weights/calibration_provenance.json`; default source is synthetic unless `REGIME_DATA_SOURCE=real`. |
| Backtest integration | `backtest_engine.py`, `run_backtest.py`, `main.py` | `BacktestEngine`, `write_summary_json`, `run_backtest.main`, `run_analysis_cycle` | Consumes regime output, emits `backtest_summary.json`, and gates production/backtest behavior. |
| Alpha orchestration integration | `main.py`, `engine.py`, `alpha_liquidity_sweep_predictor.py` | signal pipeline construction, `SniperExecutionEngine`, `LiquiditySweepAlpha.predict` | Regime payload feeds higher-level signal/orchestration paths. Some tests expect symbols or signal-only behavior that current import/live-mode gates do not provide. |
| SHPE / report calibration utilities | `stop_hunt_engine/model/calibrator.py`, `stop_hunt_engine/training/walk_forward.py`, `stop_hunt_engine/training/report.py` | `ProbabilityCalibrator`, `run_walk_forward`, `write_reports` | Separate stop-hunt probability engine calibration/report stack; not the ARE weight artifact generator. |

## Dependency graph

```text
calibrate_regime.py
  ├─ generates weights/advanced_regime_weights.npz
  └─ writes weights/calibration_provenance.json

model_weights.py::ModelWeightManager
  └─ loaded by advanced_regime_engine.py::AdvancedRegimeEngine._load_model_weights
       ├─ NHHMM_Engine.load_weights(beta, mu, sigma)
       ├─ SparseJumpModel.load_weights(centroids, feature_weights)
       └─ feature_mean/feature_std consumed by _normalize_features

AdvancedRegimeEngine.update(market_data)
  ├─ validates price/return/features/MTF payload
  ├─ resolves canonical return and MTF fusion
  ├─ NHHMM_Engine.forward_pass_step
  ├─ SparseJumpModel.filter
  ├─ compute_hmm_regime
  ├─ RegimeMarkovSmoother.update
  ├─ risk gates: price-return mismatch, drawdown, loss streak, vol shock, confidence collapse
  │    └─ _trigger_circuit_breaker
  ├─ recovery paths
  │    └─ _self_heal
  └─ _build_output
       └─ _validate_output_schema

Backtest/main/alpha orchestration
  ├─ consume regime output fields: regime_label, confidence, risk_metrics, signal_valid, execution_mode
  └─ write reports: backtest_summary.json / backtest_result.json / SHPE report.json
```

## Important architectural observations

1. The live ARE is a 3-state loaded artifact feeding a 4-label heuristic regime layer. This is the root of many TREND/RANGE expectation failures: the artifact has no trained 4-state range state; `RANGE` is derived heuristically from 3 probabilities.
2. `score_map` is intentionally bounded heuristic evidence, not a normalized distribution. Current warnings show sums outside the expected band, but the code only logs and continues.
3. Output schema has evolved from scalar feed statuses to structured nested feed status objects. Many tests still assert old string values directly under `risk_metrics.feed_status`.
4. `signal_valid` is wired primarily to `self._weights_loaded` in normal output, then forced false under DEGRADED/circuit/failsafe paths. It does not prove real-data provenance or predictive calibration.
5. Circuit breaker reason behavior is “last reason wins in `_circuit_breaker_reason` even when breaker is already active,” while some tests expect first reason preservation.
6. `_self_heal` assumes ownership of `self._lock` and manually releases it for side effects. Direct calls outside the synchronized update path can release an un-acquired `RLock`, causing concurrency failures.

# Regime Output Contract Audit

## Functions audited

- `_build_output(...)`
- `compute_hmm_regime(...)`
- `AdvancedRegimeEngine.update(...)`

## Actual semantics

| Field | Actual source | Actual meaning |
|---|---|---|
| `signal_valid` | `_build_output` argument; normal path passes `bool(self._weights_loaded)`; forced false for DEGRADED/schema/circuit/failsafe paths | Operational validity flag. It mostly means the engine has shape-valid loaded weights and the current path was not explicitly degraded. It does **not** prove real-data provenance or predictive validity. |
| `confidence` | `compute_hmm_regime`: `max(bull,bear,crisis)` from normalized SJM/HMM probability vector | Max posterior/probability mass from the model output. It is not calibrated probability of correctness. |
| `conviction` | `compute_hmm_regime`: `1 - normalized entropy(alpha_safe)` | Entropy-derived certainty. It is not an externally calibrated confidence interval. |
| `engine_status` | In normal output currently passed as determinism status (`OK`, `RNG_RESTORE_FAILED`, `OK_WITH_HISTORY`) rather than `_engine_status`; in some fail-safe paths specific statuses are passed | Mixed operational/determinism status. This is architecture-mismatched: `_engine_status` and risk metrics status are not consistently the same semantic channel. |
| `feed_status` | `_build_output` nests it under `risk_metrics.feed_status={primary, flags}` | Structured feed condition. Old tests expect a string; actual schema is dict. |
| `execution_mode` | derived by regime/risk path (`trend_follow`, `range_mean_revert`, `fail_safe`, `circuit_breaker`, etc.) | Strategy/risk mode, not a feed health indicator. |
| `score_map` | `compute_hmm_regime`, included inside regime-score dict but not final `_build_output` | Heuristic 4-label evidence vector. It is bounded but not normalized. |

## Direct answers

### Does `signal_valid` mean weights loaded?
Partly yes. On the normal path, `signal_valid=bool(self._weights_loaded)`.

### Does `signal_valid` mean calibrated?
Only in the code's loose internal sense that shape-valid arrays loaded and `_calibration_status` was set to `calibrated`. It does not verify provenance or real-data calibration.

### Does `signal_valid` mean predictive validity?
No. No holdout accuracy, calibration, or production provenance gate is checked at output time.

### Does `signal_valid` mean all of the above?
No.

### Does `confidence` mean calibrated probability?
No.

### Does `confidence` mean heuristic confidence?
Partly. It is a model probability mass that is used heuristically as confidence.

### Does `confidence` mean posterior probability?
It equals the maximum component of the normalized 3-state probability vector (`bull`, `bear`, `crisis`) produced downstream of the SJM/HMM path.

### Does `confidence` mean score magnitude?
No. It is not the max of `score_map`.

### Is `score_map` a probability distribution?
No. The code computes a score sum, logs if it is outside a tolerance, but does not normalize it.

### Is `score_map` a heuristic score vector?
Yes.

## Contract defects

1. `signal_valid` and `_calibration_status` conflate load success with production calibration validity.
2. `engine_status` mixes determinism status and operational status.
3. `feed_status` schema changed to structured dict without all tests/integrations being updated.
4. `score_map` naming invites probability-distribution expectations, but actual values are heuristic and may sum far from 1.
5. The final `_build_output` omits `score_map`, so downstream consumers cannot audit score evidence unless they inspect intermediate regime-score dicts.

# Regime Confirmation Audit

## Root Cause
Instrumentation on `tests/test_advanced_regime_engine.py::test_bull_bias` collected 264 raw directional suppression events. The analysis identified `switch:conviction_lt_065` as the primary blocker, tied with `switch:strength_lt_gate`; the raw TREND/BEAR scores were repeatedly confirmed back to RANGE because switch confirmation could not satisfy the conviction and strength gates. The prescribed Fix B was applied because `switch:conviction_lt_065` was the first primary blocker reported by `analyze_regime_suppression.py`.

## Gate Failure Counts
| Gate | Count |
|------|-------|
| early_override:edge_below_thresh | 0 |
| early_override:conviction_below_055 | 0 |
| early_override:margin_below_thresh | 0 |
| early_override:no_ema_hint | 0 |
| switch:cooldown_blocked | 0 |
| switch:conviction_lt_065 | 126 |
| switch:persistence_lt_min | 0 |
| switch:strength_lt_gate | 126 |

## Transition Matrix (raw → confirmed)
| From | To | Count |
|------|----|-------|
| TREND | TREND | 0 |
| TREND | RANGE | 177 |
| BEAR | BEAR | 0 |
| BEAR | RANGE | 87 |

## Primary Blocker
The analysis output reported `switch:conviction_lt_065` as the primary blocker with 126 occurrences, tied in count with `switch:strength_lt_gate`.

## Proposed Fix
Fix B was applied: the switch filter now uses an adaptive conviction gate based on `regime_scores["uncertainty"]`, with the required hard floor at 0.52. Fix A was not applied because early override edge suppression was not observed as the primary blocker. Fix C remains a red herring for this test set because `_smoothed_regime` and `regime_state_probs` are not referenced by the targeted tests.

## Expected Impact
The adaptive conviction gate should reduce false switch rejections when moderate uncertainty depresses conviction near the previous absolute 0.65 gate. In this local degraded/no-weights fixture, validation still fails `tests/test_advanced_regime_engine.py::test_bull_bias` because observed convictions remain far below the 0.52 hard floor required by Fix B, so the switch still cannot approve TREND.

## Production Risk
- Fix A (cold-start edge EMA): zero risk — only affects first 3 ticks after init/reset
- Fix B (adaptive conviction gate): low risk — threshold lowered by max 25% only at
  high uncertainty; hard floor at 0.52 prevents trivial bypass; execution guard at
  0.64 edge_score is unchanged and acts as independent filter
- No changes to position sizing, compute_hmm_regime, or execution logic

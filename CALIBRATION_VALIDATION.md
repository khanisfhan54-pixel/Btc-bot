# Phase 5 — Probability Calibration Validation

## Audited items
- `_shrink_prob`: searched repository; no active function with this exact name was found. Probability shrinkage/calibration in the SHPE path is handled by `ProbabilityCalibrator` and training holdout / walk-forward utilities.
- Calibration logic: SHPE training uses holdout calibration and walk-forward validation uses out-of-fold predictions.
- Conviction mapping: backtest alpha conviction mapping clamps confidence to an open interval and avoids hard 0/1 likelihoods.

## Leakage control
Metrics must be reported from out-of-fold predictions only. Full-sample predictions are not valid evidence.

## Required metrics
- Brier Score: available in SHPE calibration metrics utilities.
- Reliability curve: available via reliability bins.
- Confidence histogram: to be computed from out-of-fold probabilities.
- ECE: available in calibration metrics utilities.
- MCE: maximum absolute bin gap from the same reliability bins.

## Validation result
Existing walk-forward/calibration utilities already produce out-of-fold prediction payloads. This task did not add strategy tuning or threshold optimization.

## Risk
If a caller computes metrics on full-sample predictions, the report is invalid. The validation standard remains out-of-fold only.

## Expected outcome
Probability calibration evidence is accepted only when backed by out-of-fold predictions.

## Validation procedure
Run calibration tests and SHPE walk-forward tests.

# SHPE Walk-Forward Leakage Audit

## 1. Root cause

`stop_hunt_engine/training/walk_forward.py` used an expanding-window split that trained each fold on `samples[:start]` and tested on `samples[start:end]`. `stop_hunt_engine/training/label_generator.py` creates each usable SHPE label from future bars `i + 1` through `i + horizon_bars` after features are fixed.

Because the train slice ended immediately before the test slice, the final `horizon_bars` training labels in each fold could be computed from bars that were inside the upcoming test fold.

## 2. Exact leakage mechanism

Default SHPE `horizon_bars` is `3`. For a fold with first test original `row_index = T`, any training sample with:

```text
train_row_index + horizon_bars >= T
```

has a label horizon that overlaps the test fold. Before the fix, the training slice included these rows because it used `samples[:start]` with no purge/embargo.

On the 80-row smoke dataset with `min_train=12` and `test_size=4`, the validated pre-fix overlap was exactly three rows per fold:

| Fold | Test row-index range | Leaking training row indices | Overlap count |
| --- | --- | --- | --- |
| 0 | 31-34 | 28, 29, 30 | 3 |
| 1 | 35-38 | 32, 33, 34 | 3 |
| 2 | 39-42 | 36, 37, 38 | 3 |
| 3 | 43-46 | 40, 41, 42 | 3 |
| 4 | 47-50 | 44, 45, 46 | 3 |
| 5 | 51-54 | 48, 49, 50 | 3 |
| 6 | 55-58 | 52, 53, 54 | 3 |
| 7 | 59-62 | 56, 57, 58 | 3 |
| 8 | 63-66 | 60, 61, 62 | 3 |
| 9 | 67-70 | 64, 65, 66 | 3 |
| 10 | 71-74 | 68, 69, 70 | 3 |
| 11 | 75-76 | 72, 73, 74 | 3 |

## 3. Files changed

- `stop_hunt_engine/training/walk_forward.py`
  - Added fold-local purge calculation.
  - Added fail-closed validation that raises if any retained training label horizon reaches the first test row index.
  - Kept model architecture, classifier, calibration call, feature encoding, prediction, signal generation, risk, and execution paths unchanged.
- `stop_hunt_engine/tests/test_shpe_training_workflow.py`
  - Added anti-leakage assertions for purged folds.
  - Added fail-closed test for an intentionally unpurged boundary.
- `audit/post_fix_walkforward_audit.md`
  - Documents root cause, proof, tests, and metrics.

## 4. Tests added

- `test_walk_forward_purges_train_label_horizons_from_test_folds`
  - Verifies every retained training row satisfies `row_index + horizon_bars < first_test_row_index`.
  - Verifies chronological ordering remains intact.
  - Verifies each executed fold purges at least `horizon_bars` rows.
- `test_walk_forward_fail_closed_on_unpurged_label_horizon_overlap`
  - Calls the fold validation guard on the old unpurged boundary and expects a `RuntimeError`.
  - Confirms the purged boundary passes the same guard.

## 5. Proof leakage removed

The implemented walk-forward mode is now `expanding_window_purged`. For each fold:

1. Start from the original expanding-window candidate train/test boundary.
2. Remove at least `horizon_bars` samples from the end of the training slice.
3. Continue trimming if any retained training sample still has `row_index + horizon_bars >= first_test_row_index`.
4. Fail closed before training if any retained training label horizon overlaps the test fold.

Post-fix smoke walk-forward proof:

| Fold | First test row index | Train rows retained | Purged rows | Last retained train label horizon end |
| --- | ---: | ---: | ---: | ---: |
| 0 | 31 | 9 | 3 | 30 |
| 1 | 35 | 13 | 3 | 34 |
| 2 | 39 | 17 | 3 | 38 |
| 3 | 43 | 21 | 3 | 42 |
| 4 | 47 | 25 | 3 | 46 |
| 5 | 51 | 29 | 3 | 50 |
| 6 | 55 | 33 | 3 | 54 |
| 7 | 59 | 37 | 3 | 58 |
| 8 | 63 | 41 | 3 | 62 |
| 9 | 67 | 45 | 3 | 66 |
| 10 | 71 | 49 | 3 | 70 |
| 11 | 75 | 53 | 3 | 74 |

Every retained training label horizon ends strictly before the first test row index.

## 6. Metrics before/after

Metrics were regenerated on the same deterministic 80-row SHPE smoke dataset with `min_train=12` and `test_size=4`.

| Metric | Old leaky value | New purged value | Difference |
| --- | ---: | ---: | ---: |
| Sharpe ratio | -0.6859395994416145 | -1.335089551413575 | -0.6491499519719604 |
| Max drawdown | -1.5834830972691054 | -2.0736729506300824 | -0.49018985336097703 |
| Win rate | 0.4444444444444444 | 0.38095238095238093 | -0.06349206349206349 |
| Number of trades | 18 | 21 | +3 |
| Brier score | 0.37787511083397285 | 0.3356453539061554 | -0.04222975692781743 |
| Expected calibration error | 0.39919754242462757 | 0.2998515987375267 | -0.09934594368710084 |
| Predictions | 46 | 46 | 0 |
| Fold count | 12 | 12 | 0 |
| Tested date range | 1704076500000-1704090000000 | 1704076500000-1704090000000 | unchanged |

Calibration method remained `platt`; small early folds may still fall back to uncalibrated probabilities when their calibration holdout lacks both classes, which is existing behavior.

## 7. Remaining risks

- This fix protects SHPE walk-forward validation from label-horizon overlap with test folds. It does not alter full offline training in `train_and_save`, because that path is not a validation split and was outside the requested scope.
- The purge is index-based because SHPE labels are generated by original sample row index and bar horizon. If future datasets introduce non-contiguous labelled rows, the guard continues trimming beyond the minimum purge until the retained horizon end is strictly before the first test row index.
- Runtime inference, signal generation, risk controls, execution behavior, feature definitions, target definitions, model architecture, classifier, and calibration logic were intentionally left unchanged.

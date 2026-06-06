# Purged Walk-Forward Validation Audit

## Configuration

- Splitter: `purged_walk_forward_splits`
- Sample audit configuration: `n_samples=270`, `train_size=100`, `test_size=20`, `step=1`, `purge_size=10`, `embargo_size=10`
- Training pipeline default: `validation_mode="purged"`
- Training pipeline purge setting: target `horizon_bars`
- Training pipeline embargo setting: configurable `embargo_size` with default `0`

## Folds Generated

| Fold | Train indices | Purge indices | Test indices | Embargo indices |
| --- | --- | --- | --- | --- |
| 0 | 0-99 | 100-109 | 110-129 | 130-139 |
| 1 | 140-239 | 240-249 | 250-269 | none after terminal test |

## Leakage Checks Passed

- No train/test overlap: passed in `test_purged_walk_forward_excludes_purge_and_embargo_regions` and `test_purged_walk_forward_no_train_inside_forbidden_interval`.
- Purge region excluded from training: passed in `test_purged_walk_forward_excludes_purge_and_embargo_regions`.
- Embargo region excluded before the next fold begins: passed in `test_purged_walk_forward_multiple_folds_begin_after_embargo`.
- Train strictly before test: passed in `test_purged_walk_forward_excludes_purge_and_embargo_regions` and `test_purged_walk_forward_no_train_inside_forbidden_interval`.
- Future label leakage guard: passed in `test_purged_split_prevents_future_leakage` with `max(train_indices) < min(test_indices) - purge_size`.

## Test Results

- `pytest stop_hunt_engine/tests/test_purged_walk_forward.py stop_hunt_engine/tests/test_walk_forward.py stop_hunt_engine/tests/test_walk_forward_rolling.py stop_hunt_engine/tests/test_no_leakage.py -v`: passed, 12 tests.
- `python3 -m py_compile stop_hunt_engine/validation/purged_walk_forward.py stop_hunt_engine/tests/test_purged_walk_forward.py stop_hunt_engine/training/walk_forward.py stop_hunt_engine/training/__main__.py`: passed.
- `pytest stop_hunt_engine/tests -v`: failed during collection because this environment does not have the existing project dependency `numpy` installed. Attempting `python3 -m pip install 'numpy>=1.26,<2.0.0'` failed because package index access returned `403 Forbidden`.

## Failing Tests

No code-level failures were observed in the purged walk-forward validation tests. The full stop_hunt_engine suite was blocked by the environment-level missing dependency `numpy` before the affected training workflow tests could execute.

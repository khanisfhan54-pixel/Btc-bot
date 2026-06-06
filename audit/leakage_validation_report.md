# Leakage Validation Report

## A. Files audited

- `stop_hunt_engine/validation/walk_forward.py`
- `stop_hunt_engine/validation/purged_walk_forward.py`
- `stop_hunt_engine/training/walk_forward.py`
- `stop_hunt_engine/training/dataset_builder.py`
- `stop_hunt_engine/training/label_generator.py`
- `stop_hunt_engine/training/trainer.py`
- `stop_hunt_engine/validation/timestamp_alignment_audit.py`
- `stop_hunt_engine/integrations/feature_pipeline.py`
- `stop_hunt_engine/tests/test_walk_forward.py`
- `stop_hunt_engine/tests/test_purged_walk_forward.py`
- `stop_hunt_engine/tests/test_timestamp_alignment.py`

## B. Leakage checks implemented

Implemented in `stop_hunt_engine/validation/leakage.py` and covered by `stop_hunt_engine/tests/test_temporal_ordering.py`:

1. **Train/Test overlap**
   - Purged boundary validation fails if train and test index sets intersect.
2. **Timestamp overlap**
   - `assert_temporal_ordering()` verifies `max(train_timestamp) < min(test_timestamp)`.
3. **Feature availability leakage**
   - `assert_feature_availability_alignment()` verifies `feature_available_ts_ms <= prediction_timestamp_ms`.
4. **Label horizon leakage**
   - `assert_label_horizon_overlap()` verifies every training label horizon ends strictly before the first test timestamp.
5. **External feature timestamp leakage**
   - `assert_external_feature_alignment()` delegates to the existing timestamp alignment audit for funding, OI, liquidations, LOB/book, and regime timestamps.
6. **Purged walk-forward boundary violations**
   - `assert_purged_walk_forward_boundary()` validates purge size, train/test disjointness, chronological ordering, and no training sample inside `[test_start - purge_size, test_end + embargo_size]`.

## C. Synthetic leakage tests

Synthetic cases added:

- Case A: future `funding_timestamp_ms` fails.
- Case B: future `oi_timestamp_ms` fails.
- Case C: future `regime_timestamp_ms` fails.
- Case D: future label horizon fails.
- Case E: properly aligned timestamps pass.

Additional synthetic validations cover feature availability leaks, train/test timestamp collisions, future LOB/book timestamps, and purged-boundary violations.

## D. Pass/Fail status

- PASS: `pytest -q stop_hunt_engine/tests/test_temporal_ordering.py stop_hunt_engine/tests/test_purged_walk_forward.py`
- WARNING: `pytest -q stop_hunt_engine/tests/test_temporal_ordering.py stop_hunt_engine/tests/test_purged_walk_forward.py stop_hunt_engine/tests/test_timestamp_alignment.py` could not collect `test_timestamp_alignment.py` because the current environment is missing `numpy`, which is declared in `requirements.txt`.

## E. Exact failure examples

The new synthetic tests assert these fail-closed examples:

- `feature availability leakage detected: row=1 feature_available_ts_ms=... prediction_timestamp_ms=...`
- `train/test timestamp overlap detected: max_train_timestamp_ms=... min_test_timestamp_ms=...`
- `Timestamp leakage detected` for future funding, OI, regime, and LOB/book timestamps.
- `label horizon leakage detected: first_test_timestamp_ms=... leaking_train_horizons=[...]`
- `purged walk-forward boundary violation: forbidden_interval=[...] leaking_train_indices=[...]`

## F. Remaining leakage risks

- Source data producers must continue preserving raw external event timestamps. If upstream pipelines drop timestamps, validation fails closed when external features are present.
- Timestamp units are assumed to be milliseconds. Mixed second/millisecond feeds should be normalized before dataset creation.
- The purged-boundary helper validates index-based purge and embargo sizes, matching the existing split generator. Timestamp-duration purge/embargo rules would require an additional duration-aware helper if introduced in the future.
- Full repository test execution depends on optional runtime dependencies such as `numpy`, `scipy`, `sklearn`, `pandas`, and `pyarrow` being installed.

## G. Production readiness score

**8.5 / 10**

The validation layer now fails closed for the requested temporal leakage classes without changing strategy, model, signal, or execution logic. The remaining readiness gap is environmental/dependency completeness for full-suite CI and continued upstream preservation of source timestamps.

# Leakage Discovery Audit

## Scope

Files audited for the leakage validation upgrade:

- `stop_hunt_engine/validation/walk_forward.py`
- `stop_hunt_engine/validation/purged_walk_forward.py`
- `stop_hunt_engine/training/walk_forward.py`
- `stop_hunt_engine/training/dataset_builder.py`
- `stop_hunt_engine/training/label_generator.py`
- `stop_hunt_engine/training/trainer.py`
- `stop_hunt_engine/validation/timestamp_alignment_audit.py`
- `stop_hunt_engine/integrations/feature_pipeline.py`
- Existing leakage-adjacent tests under `stop_hunt_engine/tests/`

## Current leakage tests

Before this change, the direct walk-forward leakage tests primarily validated disjoint and ordered train/test indices:

- `stop_hunt_engine/tests/test_walk_forward.py` checked `max(train) < min(test)` for standard walk-forward splits.
- `stop_hunt_engine/tests/test_purged_walk_forward.py` checked train/test disjointness, purge gaps, and embargo-driven fold advancement for purged splits.
- `stop_hunt_engine/tests/test_timestamp_alignment.py` checked that a future funding timestamp is reported by the timestamp alignment audit, but it did not provide complete synthetic coverage for OI, regime, LOB/book timestamps, label horizon overlap, or feature availability versus prediction timestamp.

Gap found: the basic split checks did not form a single fail-closed validation framework covering train/test timestamp overlap, row-level feature availability, all external feature timestamps, label-horizon overlap, and purged boundary enforcement.

## Current walk-forward implementation

- `stop_hunt_engine/validation/walk_forward.py` provides expanding and rolling index split generators. They are chronological by construction and yield train ranges before test ranges.
- `stop_hunt_engine/validation/purged_walk_forward.py` provides fixed-window purged splits with layout `train | purge | test | embargo`.
- `stop_hunt_engine/training/walk_forward.py` selects expanding, rolling, or purged validation mode. Purged mode uses `target.horizon_bars` as the purge size and accepts an `embargo_size` argument.
- Existing training walk-forward code already contains defensive checks to prevent non-chronological fold boundaries and train label horizons that reach the first test row index.

Gap found: tests did not independently validate strict timestamp ordering (`max(train_timestamp) < min(test_timestamp)`) for every fold or explicitly validate the full forbidden purged interval as a reusable validation primitive.

## Current timestamp validation

- `stop_hunt_engine/validation/timestamp_alignment_audit.py` audits external feature sources by comparing source timestamps against a row prediction timestamp.
- Audited external sources include funding, open interest, liquidations, LOB/book, and regime timestamps.
- `stop_hunt_engine/training/dataset_builder.py` also validates source timestamps when feature rows are converted into SHPE samples.
- `stop_hunt_engine/training/trainer.py` invokes timestamp leakage validation before sample/label alignment.

Gap found: feature availability alignment (`feature_available_ts_ms <= prediction_timestamp_ms`) was not exposed as a separate fail-closed validation primitive and was not covered by a dedicated test.

## Current label generation logic

- `stop_hunt_engine/training/label_generator.py` generates labels from future bars up to `target.horizon_bars`.
- Positive labels carry `event_timestamp_ms`; labels with insufficient future bars are marked with `label=None`.
- Existing walk-forward training logic prevents train label horizons from reaching test samples by comparing row-index horizons against the first test row.

Gap found: there was no standalone timestamp-based label-horizon test asserting `label_horizon_end < first_test_timestamp`.

## Discovery conclusion

The repository already had partial safeguards, but they were distributed and mostly index-oriented. This upgrade adds a reusable temporal leakage validation module and focused tests for:

1. Train/test index overlap and timestamp overlap.
2. Feature availability versus prediction timestamp.
3. External feature timestamp alignment.
4. Label horizon timestamp overlap.
5. Purged walk-forward forbidden-boundary enforcement.
6. Synthetic leakage injections that must fail closed.

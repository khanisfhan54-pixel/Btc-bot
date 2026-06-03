# SHPE Training Dataset Validation

## Leakage status: PASS

- Rows audited: 97
- Label rows audited: 97
- Label horizon: 3 bars
- Violations: 0

## Proof obligations

- `feature_available_ts_ms < label horizon`: PASS
- Positive labels have `feature_available_ts_ms < event_timestamp_ms`: PASS
- No feature uses future bars: PASS by construction of `derive_features`, which only reads rows `idx - lookback + 1` through `idx` and external source timestamps are validated not later than `feature_available_ts_ms`.

## Notes

For OHLCV bars, `timestamp_ms` is the bar start and `feature_available_ts_ms` is the bar end. Therefore features become available after the source event bar starts but before any future label horizon bars are evaluated.

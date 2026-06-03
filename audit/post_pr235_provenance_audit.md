# Post-PR235 SHPE Dataset Provenance Audit

## 1. Files changed

- `stop_hunt_engine/training/dataset_builder.py`
  - Removed the warning/continue path for external feature groups that lack source timestamps.
  - Added fail-closed source timestamp parsing and validation for missing, null, non-numeric, negative, and future timestamps.
- `stop_hunt_engine/training/__main__.py`
  - Added `regime_timestamp_ms` to deterministic SHPE smoke rows so smoke data satisfies the stricter provenance contract.
- `stop_hunt_engine/tests/test_shpe_training_workflow.py`
  - Expanded provenance tests for all external source groups.
  - Replaced the previous missing-timestamp warning test with fail-closed missing/null/non-numeric/negative/future timestamp tests.
  - Added a dataset integrity test proving no artifact is created when all external feature groups lack timestamps.
- `audit/post_pr235_provenance_audit.md`
  - Documents the final provenance hardening, verified fail cases, smoke results, remaining risks, and merge recommendation.

No signal-generation, execution, risk, model, target, calibration, label, walk-forward, or inference logic was changed.

## 2. Validation logic added

The dataset builder now enforces this invariant for every external feature group that contributes values to a row:

```text
source_timestamp_ms is present, numeric, non-negative, and <= feature_available_ts_ms
```

If a feature group contributes values but none of its accepted timestamp fields are present, dataset building raises `ValueError` and aborts before artifact creation.

Validated source groups and accepted timestamp fields:

| Source | Feature presence fields | Required timestamp aliases |
| --- | --- | --- |
| Funding | `funding_rate_8h`, `funding_z30d`, `funding_oi_sign_divergence` | `funding_timestamp_ms`, `funding_ts_ms` |
| Open Interest | `delta_oi_velocity`, `oi_pct_change_1h`, `oi_buildup_flag`, `oi_price_divergence_sign` | `oi_timestamp_ms`, `open_interest_timestamp_ms`, `open_interest_ts_ms` |
| Liquidations | `nearest_long_cluster_dist_pct`, `nearest_short_cluster_dist_pct`, `cascade_amplification_flag` | `liquidation_timestamp_ms`, `liq_timestamp_ms`, `liquidation_ts_ms` |
| LOB / Order Book | `ofi_zscore`, `l1_order_flow_proxy_z`, `book_imbalance`, `imbalance`, `depth_replenishment_ratio` | `last_book_event_ts_ms` |
| Regime | `regime`, `regime_label`, `regime_confidence`, `regime_conviction`, `regime_edge_score`, `regime_signal_valid`, `regime_expected_volatility` | `regime_timestamp_ms`, `regime_ts_ms` |

The removed warning path was:

```text
Feature Present + No Timestamp -> LOGGER.warning("external feature timestamp NOT VERIFIED") -> Continue
```

The replacement behavior is:

```text
Feature Present + No Timestamp -> ValueError -> Abort Dataset Build
```

## 3. Tests added or updated

The SHPE workflow test file now covers the full provenance matrix for every external source group:

- PASS: timestamp present and `<= feature_available_ts_ms`.
- FAIL: timestamp missing.
- FAIL: timestamp null.
- FAIL: timestamp non-numeric.
- FAIL: timestamp negative.
- FAIL: timestamp after `feature_available_ts_ms`.

The dataset integrity regression test creates rows containing funding, OI, liquidation, LOB, and regime values without timestamps and verifies:

- `build_dataset()` raises `ValueError`.
- The dataset output directory is not created.

Existing walk-forward purge tests and label timestamp semantics tests remain in place and pass.

## 4. Fail cases verified

Verified fail-closed behavior for all requested source groups:

| Source | Missing | Null | Non-numeric | Negative | Future timestamp |
| --- | --- | --- | --- | --- | --- |
| Funding | PASS | PASS | PASS | PASS | PASS |
| Open Interest | PASS | PASS | PASS | PASS | PASS |
| Liquidations | PASS | PASS | PASS | PASS | PASS |
| LOB / Order Book | PASS | PASS | PASS | PASS | PASS |
| Regime | PASS | PASS | PASS | PASS | PASS |

`PASS` in this table means the expected failure was observed and dataset building aborted.

## 5. Smoke-test results

Required smoke command:

```bash
python -m stop_hunt_engine.training --smoke-test --run-version provenance_validation
```

Result: completed successfully.

Generated walk-forward artifact:

```text
artifacts/shpe/reports/provenance_validation/walk_forward.json
```

Fresh smoke metrics:

| Metric | Value |
| --- | ---: |
| Sharpe ratio | -1.3351063145911515 |
| Max drawdown | -2.0736627709052806 |
| Win rate | 0.38095238095238093 |
| Number of trades | 21 |
| Brier score | 0.3356448346869497 |
| Expected calibration error | 0.29985214617878575 |

The deterministic smoke rows now include `regime_timestamp_ms`, while existing LOB provenance continues to use `last_book_event_ts_ms`.

## 6. Remaining risks

- The dataset builder validates only source timestamp fields that are explicitly listed as accepted aliases. If upstream producers use a new timestamp field name, ingestion will fail closed until that alias is reviewed and added.
- Rows with no external feature values for a source group do not require that source's timestamp, because the source did not contribute values to that row.
- Runtime feature construction and inference behavior were not changed; this hardening applies to offline SHPE dataset integrity.

## 7. Final merge recommendation

Merge is recommended.

The final NOT VERIFIED provenance state has been eliminated for SHPE training dataset rows. External feature groups can no longer enter training without valid source timestamps, and invalid/future timestamps fail closed before dataset artifacts are written.

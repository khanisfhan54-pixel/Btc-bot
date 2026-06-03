# PR #234 Remaining SHPE Validation Audit

## 1. Discovery summary

### Label event timestamp semantics

Current behavior before this patch:

- `label_generator.py` detected positive events by scanning future bars `i+1..i+horizon_bars`.
- When a future bar triggered the stop-hunt sweep/rejection condition, `event_timestamp_ms` was set from the future sample's `timestamp_ms`.
- In SHPE datasets, `timestamp_ms` is the bar start and `feature_available_ts_ms` is the bar end.

Risk:

- For an immediate-next-bar event, the future bar start can equal the current row's `feature_available_ts_ms`.
- That means `event_timestamp_ms == feature_available_ts_ms`, which does not represent an event that became known after feature availability.

Fix:

- Preserve the existing event/target logic, but record positive event timestamps from the completed future bar's `feature_available_ts_ms`.
- Fail closed when any positive label has `event_timestamp_ms <= feature_available_ts_ms`.

### External feature as-of validation

Current behavior before this patch:

- `dataset_builder.py` validated core OHLC chronology and feature availability.
- It validated trade/book timestamps only through a generic lookahead check.
- Funding, open-interest, liquidation, LOB, and regime feature values could be copied into SHPE training samples without source-specific as-of validation when timestamp fields were present.

Risk:

- If a flat feature row contains an external feature value whose source timestamp is after `feature_available_ts_ms`, that future source information could enter offline training.

Fix:

- Add source-specific as-of validation for timestamp fields when present.
- Fail closed with source, field, source timestamp, and `feature_available_ts_ms` when `source_timestamp > feature_available_ts_ms`.
- If an external feature group has values but no recognized timestamp field, preserve behavior and emit a warning marking the source as `NOT VERIFIED`.

## 2. External source timestamp coverage

| Source | Feature presence fields checked | Timestamp fields verified if present | Missing timestamp behavior |
| --- | --- | --- | --- |
| Funding | `funding_rate_8h`, `funding_z30d`, `funding_oi_sign_divergence` | `funding_timestamp_ms`, `funding_ts_ms` | Warn once, preserve behavior |
| Open Interest | `delta_oi_velocity`, `oi_pct_change_1h`, `oi_buildup_flag`, `oi_price_divergence_sign` | `oi_timestamp_ms`, `open_interest_timestamp_ms`, `open_interest_ts_ms` | Warn once, preserve behavior |
| Liquidations | `nearest_long_cluster_dist_pct`, `nearest_short_cluster_dist_pct`, `cascade_amplification_flag` | `liquidation_timestamp_ms`, `liq_timestamp_ms`, `liquidation_ts_ms` | Warn once, preserve behavior |
| LOB / order book | `ofi_zscore`, `l1_order_flow_proxy_z`, `book_imbalance`, `imbalance`, `depth_replenishment_ratio` | `last_book_event_ts_ms` | Warn once, preserve behavior |
| Regime | `regime`, `regime_label`, `regime_confidence`, `regime_conviction`, `regime_edge_score`, `regime_signal_valid`, `regime_expected_volatility` | `regime_timestamp_ms`, `regime_ts_ms` | Warn once, preserve behavior |

No new feature definitions were added. These checks only validate timestamp fields when they are already present in incoming flat feature rows.

## 3. Files changed

- `stop_hunt_engine/training/label_generator.py`
  - Uses completed future event timestamps for positive labels.
  - Adds fail-closed ordering validation for positive label event timestamps.
- `stop_hunt_engine/training/dataset_builder.py`
  - Adds source-specific external feature timestamp validation.
  - Adds warnings for external feature groups with values but no timestamp field.
- `stop_hunt_engine/tests/test_shpe_training_workflow.py`
  - Adds pass/fail as-of tests for funding, OI, liquidations, LOB, and regime timestamps.
  - Adds tests for immediate-next-bar and multi-bar positive label event timestamp semantics.
  - Adds fail-closed label timestamp ordering test.
- `audit/pr234_remaining_findings_audit.md`
  - Documents this follow-up audit and fresh metrics.

## 4. Proof event timestamp semantics are fixed

Positive labels now record the completed future event timestamp. Tests cover:

- Immediate-next-bar positive event: `event_timestamp_ms == future_bar.feature_available_ts_ms` and is strictly greater than the current sample's `feature_available_ts_ms`.
- Multi-bar positive event: same strict ordering across a later horizon bar.
- Mutated invalid payload: if the completed future event timestamp is not strictly after the current sample's `feature_available_ts_ms`, label generation raises `ValueError`.

## 5. Proof external as-of validation is fixed

Tests cover both pass and fail-closed cases for all requested external source groups:

- Funding timestamp before/equal feature availability passes; after feature availability fails closed.
- OI timestamp before/equal feature availability passes; after feature availability fails closed.
- Liquidation timestamp before/equal feature availability passes; after feature availability fails closed.
- LOB timestamp before/equal feature availability passes; after feature availability fails closed.
- Regime timestamp before/equal feature availability passes; after feature availability fails closed.

The failure message includes `source`, timestamp field, offending timestamp, and `feature_available_ts_ms`.

## 6. Fresh post-purge validation metrics

Fresh command:

```bash
python -m stop_hunt_engine.training --smoke-test --run-version post_purge_validation
```

Generated walk-forward artifact:

```text
artifacts/shpe/reports/post_purge_validation/walk_forward.json
```

Fresh post-purge metrics:

| Metric | Old leaky value | Fresh new purged value | Difference |
| --- | ---: | ---: | ---: |
| Sharpe ratio | -0.6859395994416145 | -1.335089551413575 | -0.6491499519719604 |
| Max drawdown | -1.5834830972691054 | -2.0736729506300824 | -0.49018985336097703 |
| Win rate | 0.4444444444444444 | 0.38095238095238093 | -0.06349206349206349 |
| Number of trades | 18 | 21 | +3 |
| Brier score | 0.37787511083397285 | 0.3356453539061554 | -0.04222975692781743 |
| Expected calibration error | 0.39919754242462757 | 0.2998515987375267 | -0.09934594368710084 |
| Fold count | 12 | 12 | 0 |
| Prediction count | 46 | 46 | 0 |
| Tested date range | 1704076500000-1704090000000 | 1704076500000-1704090000000 | unchanged |

The fresh metrics match the prior post-purge metrics because these follow-up fixes change validation/timestamp semantics only; they do not change features, target conditions, model architecture, calibration method, classifier, or prediction logic.

## 7. Remaining open risks

- Generated smoke rows contain a regime label but no regime source timestamp, so the new validation correctly emits `external feature timestamp NOT VERIFIED: source=regime`. This preserves current behavior while documenting that persisted smoke-row regime as-of timing is not independently verifiable.
- Funding/OI/liquidation training rows in this repository do not appear to define persisted source timestamp fields by default. When those timestamp fields are absent, the source is warned as not verified rather than blocked, per requirement.
- This patch validates flat offline training input rows. Runtime feature construction already performs nearest-past selection for runtime sources and was not changed.

## 8. Merge recommendation

Merge is recommended if reviewers accept warning-only behavior for external sources that do not provide any timestamp field. All requested SHPE tests and smoke validation pass, and no strategy, target, feature definition, model architecture, calibration method, risk, execution, or runtime prediction logic was changed.

# Report Schema Diff

## Files audited

- `backtest_summary.json`
- `backtest_result.json`
- `run_backtest.py`
- `backtest_engine.py`
- `stop_hunt_engine/training/report.py`
- Report-related tests in `tests/test_5m_l2_strict_and_report.py`

## Current `backtest_summary.json` top-level schema

Actual top-level keys:

- `run_timestamp`
- `run_status`
- `data_provenance`
- `cost_assumptions`
- `calibration_status`
- `ohlcv_metrics`
- `l2_metrics`
- `comparison`
- `prior_run_comparison`
- `issues`
- `warnings`
- `blockers`
- `unavailable_metrics`
- `verdict`
- `production_readiness`

## Expected schema observed from report tests

`tests/test_5m_l2_strict_and_report.py` expects at least:

- `prior_run_comparison`
- `run_status` as one of `BLOCKED`, `OK`, `PARTIAL`
- `calibration`
- `blockers` list
- `unavailable_metrics` list containing named unavailable metrics

## Mismatches

| Field | Expected | Actual | Classification |
|---|---|---|---|
| `run_status` | Scalar status string in `{BLOCKED, OK, PARTIAL}` | Object with subkeys `ohlcv`, `l2` | Schema mismatch |
| `calibration` | Present object, with at least `status` in tests | Missing; actual field is `calibration_status` | Schema mismatch |
| `calibration_status` | Not the tested key | Present with `prob_calibration`, `hawkes_params`, `vol_ratio_threshold` | Naming mismatch |
| `blockers` | Present list | Present list | OK |
| `unavailable_metrics` | Present list | Present list | OK |
| `prior_run_comparison` | Present | Present | OK |
| `production_readiness` | Often expected as decision object in production audits | Actual scalar/string-like verdict field | Potential contract mismatch |
| metric availability | Tests require unavailable metrics to expose metric names/reasons | Actual list exists, but content varies by generated report | Content-level mismatch risk |

## `backtest_result.json` schema

Actual top-level keys include:

- `symbol`, `timeframe`, `initial_capital`, `final_equity`
- `total_return_pct`, `max_drawdown_pct`, `sharpe_ratio`, `win_rate_pct`
- `total_trades`, `long_signals`, `short_signals`, `hold_signals`
- `signal_only_mode`, `signal_quality_valid`, `signal_quality_reason`
- `regime_state`, `trades`

This is a distinct execution/backtest result schema, not the `backtest_summary.json` research validation schema.

## SHPE report schema

`stop_hunt_engine/training/report.py` writes `report.json` with:

- target definition
- dataset size/date range/class balance/per-regime counts
- walk-forward configuration
- calibration metrics: Brier and ECE
- artifact paths
- fallback comparison

This is separate from ARE's `backtest_summary.json` and should not be treated as the same report contract.

## Verdict

The report failures are mostly schema drift/architecture mismatch, not numerical engine defects. `backtest_summary.json` has both old and new concepts, but tests expect `run_status` and `calibration` names that no longer match the actual emitted report.

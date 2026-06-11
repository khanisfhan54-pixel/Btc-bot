# Change Plan

## Current behavior
Required production-readiness docs/tests are absent or incomplete, and code paths need targeted verification for parity, calibration, real-data enforcement, probability validation, microstructure validation, purged walk-forward reporting, and fail-closed trading behavior.

## Root cause
The unresolved phases span multiple modules and prior reports but do not provide the exact requested evidence files or regression tests.

## Risk
Large strategy changes could alter trading behavior. To avoid this, changes will be surgical, limited to audit-proven parity/fail-closed/data-validation/calibration plumbing and tests.

## Expected outcome
1. Produce the requested audit/report markdown files.
2. Add/adjust minimal code needed for calibration E2E, production-valid real-data enforcement, purged walk-forward verification, and fail-closed no-trade checks.
3. Add exactly `tests/test_calibration_pipeline_e2e.py` and `tests/test_are_fail_closed.py`.
4. Commit changes and create a PR record.

## Validation procedure
Use static inspection, targeted tests, full pytest where feasible, compileall, calibration tests, regime tests, and backtest tests/commands. Failures due to existing environment constraints will be documented explicitly.

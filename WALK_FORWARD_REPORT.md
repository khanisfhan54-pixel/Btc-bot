# Phase 7 — Purged Walk-Forward Report

## Implementation status
The repository contains purged walk-forward utilities with purge/embargo support. Backtest walk-forward reporting now includes the requested per-fold metric keys where available:
- Sharpe
- Sortino
- Max drawdown
- Profit factor
- Win rate
- Trade count

## Leakage control
Purged splits must prevent train label horizons from overlapping test folds and may apply embargo after test windows. Existing SHPE tests assert fail-closed behavior for overlap.

## Per-fold report schema
Each fold record in `BacktestEngine.run_walk_forward_validation()` includes:
`fold`, `train_start`, `train_end`, `test_start`, `test_end`, `sharpe`, `sortino`, `max_drawdown`, `profit_factor`, `win_rate`, `trade_count`.

## Risk
If input data is insufficient, walk-forward returns `WALK_FORWARD_INSUFFICIENT_DATA` rather than fabricating folds.

## Expected outcome
Walk-forward evidence can be reviewed per fold without overlap leakage.

## Validation procedure
Run `stop_hunt_engine/tests/test_shpe_training_workflow.py` walk-forward tests and backtest walk-forward smoke checks.

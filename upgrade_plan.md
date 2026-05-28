# UPGRADE ROADMAP

## PHASE 1: Replay Integrity
1. Re-engineer `L2CSVReplayLoader.load()` to parse cumulative relative percentages into absolute L2 limits.
2. Formally eliminate `generate_l2_book` in backtest logic.
*Validation:* Execute `--l2-csv data/bookDepth.csv` and assert no BLOCKED status and no synthetic generation.

## PHASE 2: Walk-Forward Consistency
1. Implement a complete Purged Time-Series Split function in `run_backtest.py`.
2. Ensure `walk_forward` metrics correctly generate hold-out performance scores instead of reporting unavailable.
*Validation:* Walk-forward Sharpe Ratio executes successfully and returns < 3.0.

## PHASE 3: Calibration Tuning
1. Refactor `LiquiditySweepAlpha._ml_sweep_probability` to load `.pkl` generated calibrators instead of raw scaling.
2. Validate confidence entropy > 0.5.
*Validation:* Run backtest and assess confidence distribution.

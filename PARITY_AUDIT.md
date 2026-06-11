# Phase 1 — Live vs Backtest Parity Audit

## Scope
Traced decision flow across `main.py`, `engine.py`, `backtest_engine.py`, `AlphaOrchestrator`, `AdvancedRegimeEngine`, and `LiquiditySweepAlpha` for inputs, features, regime labels, confidence, signal sources, execution gating, risk gating, and position sizing.

## Findings
1. Backtest correctly builds a canonical ARE payload (`return`, `features`, `price`, `timestamp`) but previously only blocked `UNCALIBRATED` feed status text. Other invalid statuses could remain actionable.
2. Backtest contained candle-derived snapshot/trade fallback helpers. These are useful for diagnostic/legacy runs but cannot be production-valid substitutes for `aggTrades`, `bookDepth`, real timestamps, OFI, or liquidity events.
3. Orchestration is already the single production signal path; no threshold or strategy logic change was required.
4. Position sizing remains owned by existing ARE/execution logic; no sizing optimization was performed.

## Parity-critical change implemented
Backtest execution now blocks entries when ARE output has:
- `signal_valid == False`
- `execution_mode` in `halt`, `circuit_breaker`, `fail_safe`, `halt_igarch`
- `engine_status == DEGRADED`
- `risk_metrics.feed_status.primary` outside `OK` and `MTF_PARTIAL_SURVIVAL`

## Risk
The change may reduce historical trade count where previous backtests traded through invalid feed states. This is intended fail-closed behavior.

## Expected outcome
Backtest no longer claims executable parity when the live ARE would be invalid/degraded/halted.

## Validation procedure
Run `tests/test_are_fail_closed.py`, `tests/test_are_gating_parity.py`, regime tests, and backtest tests.

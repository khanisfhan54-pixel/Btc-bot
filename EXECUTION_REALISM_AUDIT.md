# Execution Realism Audit (Phase 9)

| Assumption | Current Logic | Risk | Production Impact | Fix | Validation |
| --- | --- | --- | --- | --- | --- |
| Entry fills | Backtest previously opened full size immediately at slippage-adjusted close. | Overstates fill certainty. | Inflated trade count and optimistic PnL. | Entry now passes through deterministic queue-aware fill simulation using fill probability, confidence, side depth, and remaining size. | `tests/test_backtest_accounting_phase8_14.py` |
| Exit fills | Exit still closes filled position at current close with configured exit slippage. | Does not model exit queue priority. | Exit liquidity can remain optimistic under stress. | Documented as remaining risk; no strategy behavior changed. | Audit-only |
| Slippage | Static bps on entry and exit settlement. | Cannot reflect volatility/liquidity shocks. | Cost tails understated. | Existing static slippage preserved; queue model now controls size realism separately. | Existing backtest paths |
| Fees | Percent fee applied on entry/exit notional in settlement helper. | Prior formula mixed return pct and balance multiplier. | Accounting did not match qty-sized positions. | New PnL helper applies fees on actual notional. | `test_trade_pnl_uses_position_quantity_not_balance_multiplier` |
| Funding | Previously absent from backtest equity. | Perpetual swap carry ignored. | Directional bias when funding is large. | Funding cashflow layer credits/debits open positions by side and interval. | Positive/negative/flat funding tests |
| Latency | Existing feature and snapshot age checks remain in `ExecutionLogic`; backtest does not add artificial latency. | Fast fills may be optimistic. | Live/replay divergence during rapid markets. | Documented; no signal/threshold changes. | Existing execution tests |
| Market impact | No explicit nonlinear market-impact curve in backtest settlement. | Large orders may be too cheap. | Capacity overstated. | Queue/depth cap constrains realized size; impact curve remains future work. | Queue fill tests |
| Queue | Features were enriched but not consumed by fills. | Perfect-fill assumption. | Backtest/live execution mismatch. | Queue-aware partial fills and pending order timeout added. | 100/50/0 fill tests |

# PnL Accounting Forensics Audit (Phase 13)

## Investigated Formula
Previous settlement used:

```python
pnl = balance * net_pnl_pct * 0.25
```

## Provenance
`git blame` attributes the `0.25` multiplier to commit `4250d733` on 2026-04-04. No adjacent code or docs explained the mathematical basis. The current execution decision returns `position_size` as quantity, capped by account equity and risk, so applying a fixed 25% balance multiplier ignored the actual filled quantity.

## Determination
- **Why factor exists:** likely a legacy exposure haircut, not documented in code or audit artifacts.
- **Whether required:** not required after `ExecutionLogic` returns quantity-sized orders.
- **Double counting:** yes, it could double count or undercount exposure because risk sizing was already reflected in `position_size`.

## Fix
- Added `calculate_trade_pnl()` that computes PnL from filled quantity, entry/exit prices, fees on entry+exit notional, exit slippage, and optional funding.
- Backtest settlement now uses the helper and no fixed balance multiplier.
- Trade return is net PnL divided by entry notional.

## Validation
- `test_trade_pnl_uses_position_quantity_not_balance_multiplier` proves 2 contracts from 100 to 110 realizes 20 units before costs, not a balance-scaled proxy.

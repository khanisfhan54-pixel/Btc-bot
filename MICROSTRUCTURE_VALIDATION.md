# Phase 6 — Microstructure Validation

| Feature | Source | Formula / Semantics | Warmup | Failure conditions | Data requirements | Lookahead risk |
|---|---|---|---|---|---|---|
| OFI | Real book snapshots/book features | Order-flow imbalance from current/past book deltas or supplied `ofi_z` | Rolling z-score history where applicable | Missing/non-finite OFI, missing book | Real bookDepth/book snapshot timestamps aligned to bars | Must use current/past snapshots only |
| CVD | aggTrades | Buy-aggressor volume minus sell-aggressor volume accumulated causally | Requires trade history | Missing trade direction/quantity | Real aggTrades with `is_buyer_maker`/side and quantity | Future trades cannot enter current bar |
| Sweep detection | LiquiditySweepAlpha market data | Detects breach/sweep behavior from current market state and prior pools | Pool seeding/history window | Missing order book/trades, unseeded pools, non-finite values | Real order book and trades for production | Future high/low/trades must not be used |
| Hawkes intensity | Trade-burst features | Event intensity based on prior/current event arrivals | Event history | Missing timestamps/events | Real trade timestamps | Future events would leak intensity |
| Liquidity grab | Liquidity/pool context | Interaction of price breach, book/trade response, and pool memory | Pool history | Missing pools/book/trade context | Real liquidity levels and microstructure | Must avoid future reversal confirmation in entry bar |

## Proof of current/past-only usage
The modified calibration real loader joins real rows by minute and computes each return from current close versus prior close. Backtest production-valid mode requires aligned real book features and aggTrades counts rather than synthetic future-derived substitutes.

## Risk
Diagnostic candle simulations are not production microstructure. Reports must label them non-production-valid.

## Expected outcome
OFI/CVD/sweep/Hawkes/liquidity-grab evidence is valid only with real, aligned, causal inputs.

## Validation procedure
Run microstructure-related tests and inspect backtest labels.

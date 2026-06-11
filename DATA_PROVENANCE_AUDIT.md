# Phase 3 — Real BTC Data Integration / Provenance Audit

## Synthetic generators and fallback paths identified
- `backtest_engine._simulate_snapshot_from_candle`: constructs top-of-book from OHLCV candle range/volume.
- `backtest_engine._simulate_trades_from_candle`: constructs one trade from candle close/volume and candle direction.
- LSA market-data path can fall back to `len(trades)` when aggTrades counts are unavailable.
- L2/book feature paths fall back to candle-derived snapshots unless explicit production-valid mode is requested.
- `generate_l2_book` name was searched; no active function by that exact name was found in the modified production path.

## Production-valid enforcement
A new explicit `production_valid` backtest mode fails closed if real book features or aggTrades counts are missing. Returned label format:

`NON_PRODUCTION_VALID:<exact_reason>`

Reasons currently emitted by the new fail-closed boundary include:
- `bookDepth_missing`
- `bookDepth_missing_for_some_bars`
- `aggTrades_missing`

## Data requirements for production-valid backtests
- Real `aggTrades` counts aligned to replay minutes.
- Real `bookDepth`/book-derived snapshots aligned one-to-one to bars.
- Real timestamps; no generated timestamp replacement.
- Real OFI/book imbalance from the supplied book feature object.
- Real liquidity events; candle-only liquidity inference remains diagnostic.

## Risk
Production-valid mode will reject historical runs that previously completed using candle-simulated microstructure.

## Expected outcome
Synthetic fallbacks remain available only for diagnostic/legacy runs and cannot silently pass as production-valid.

## Validation procedure
Run fail-closed tests and backtest tests; inspect `backtest_label`/`non_production_reason` in production-valid runs.

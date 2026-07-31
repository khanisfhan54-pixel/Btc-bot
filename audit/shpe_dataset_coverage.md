# SHPE Dataset Coverage

## Dataset quality status: PASS

| Source | Rows | Start | End | Duplicate timestamps | Timestamp disorder | Future timestamps | Gap count |
| --- | --- | --- | --- | --- | --- | --- | --- |
| data/aggTrades.csv transact_time | 2386 | 2026-03-27T00:00:03.838000+00:00 | 2026-03-27T08:01:58.026000+00:00 | 692 | 0 | 0 | 0 |
| data/bookDepth.csv timestamp | 10428 | 2026-03-27T00:00:08+00:00 | 2026-03-27T08:01:30+00:00 | 9559 | 0 | 0 | 0 |
| derived 5m OHLCV bars | 97 | 2026-03-27T00:00:00+00:00 | 2026-03-27T08:00:00+00:00 | 0 | 0 | 0 | 0 |

## Source coverage

- OHLCV: derived from `data/aggTrades.csv` into contiguous BTCUSDT 5m bars.
- L2/orderbook depth: available in `data/bookDepth.csv` and mapped to 5m book imbalance/depth proxy features.
- Trades: available in `data/aggTrades.csv`.
- Funding: not available in repository historical data; SHPE funding fields are zero-filled by the existing dataset builder defaults.
- Open interest: not available in repository historical data; SHPE OI fields are zero-filled by the existing dataset builder defaults.
- Liquidations: not available in repository historical data; SHPE liquidation fields are zero-filled by the existing dataset builder defaults.
- Regime history: no standalone regime history file found; research-only regimes are derived from historical close/volatility and supplied as SHPE regime context.

## Missing data checks

- Gaps fail condition: not triggered.
- Timestamp disorder fail condition: not triggered.
- Duplicate timestamp fail condition: not triggered for derived OHLCV.
- Future timestamp fail condition: not triggered.

## Caveat

The repository contains a short intraday historical sample only. This is actual BTCUSDT historical repository data, not smoke data, but it is not enough to support production-readiness claims.

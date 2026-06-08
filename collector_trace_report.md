# Collector Trace Report

## Scope

This report traces the collector ingestion path requested for the Binance USDⓈ-M futures streams:

```text
Binance Combined Stream
  -> WebSocketClient
  -> run_collector.py
  -> handle_message()
  -> feature_computer.py
  -> validator.py
  -> gap_detector.py
  -> parquet_writer.py
  -> health_monitor.py
```

## Verified Binance stream and endpoint requirements

Primary source checked: Binance USDⓈ-M Futures WebSocket Market Streams documentation.

* Combined stream frames are wrapped as `{"stream":"<streamName>","data":<rawPayload>}`.
* Symbols in stream names are lowercase.
* Partial book depth belongs to the `/public` endpoint; aggregate trades and mark price belong to the `/market` endpoint.
* Legacy unrouted URLs (`wss://fstream.binance.com/stream` and `/ws`) only receive public data after the migration, so depth can continue to work while `aggTrade` and `markPrice` stop pushing.

## Root execution trace by stream

### `btcusdt@depth10@100ms` / orderbook

| Stage | Trace result |
| --- | --- |
| Actual websocket payload | Binance partial book depth payload includes event `e=depthUpdate`, event time `E`, symbol `s`, bid levels `b`, and ask levels `a`. |
| Actual stream name | `btcusdt@depth10@100ms`. |
| Endpoint | `/public/stream?streams=btcusdt@depth10@100ms`. |
| `WebSocketClient` | Receives combined JSON, decodes with `json.loads`, calls `CollectorApp.handle_message(data)`. |
| `handle_message()` branch | Routed by `_route_stream()` when normalized stream starts with `btcusdt@` and contains `@depth`. |
| `compute_orderbook_features()` | Requires exactly 10 bid and 10 ask levels; converts prices/quantities; computes best bid/ask, mid, microprice, spread, depth totals, and OBI levels. |
| Validation result | `validate_orderbook()` checks timestamp, 10 levels, positive uncrossed best bid/ask, spread `<100bps`, positive aggregate quantities, and OBI bounds. |
| Gap detector result | `check_gap("orderbook", timestamp)` updates last-seen timestamp and alerts on threshold breach. |
| Writer result | `ParquetWriter("orderbook", ORDERBOOK_SCHEMA).write(features)` appends to buffer and flushes on rotation or buffer threshold. |
| Health monitor result | `record_message("orderbook", timestamp)` increments `messages_per_minute["orderbook"]` and updates `last_book_ts`. |

### `btcusdt@aggTrade` / trades

| Stage | Trace result |
| --- | --- |
| Actual websocket payload | Binance aggregate trade payload includes event `e=aggTrade`, event time `E`, symbol `s`, aggregate trade ID `a`, price `p`, quantity `q`, trade time `T`, and buyer-maker flag `m`. |
| Actual stream name | `btcusdt@aggTrade` (also defensively routes case-normalized `btcusdt@aggtrade`). |
| Endpoint | `/market/stream?streams=btcusdt@aggTrade/btcusdt@markPrice@1s`. |
| `WebSocketClient` | Receives combined JSON, decodes with `json.loads`, calls `CollectorApp.handle_message(data)`. |
| `handle_message()` branch | Routed by `_route_stream()` when normalized stream starts with `btcusdt@` and contains `@aggtrade`. |
| `compute_trades_features()` | Converts aggregate trade ID, price, quantity, and buyer-maker flag; derives `side_sign` and `signed_qty`; uses event time `E` as exchange timestamp. |
| Validation result | `validate_trade()` checks timestamp freshness, positive price/quantity, optional 5% mid-price sanity check, and monotonic aggregate trade ID. |
| Gap detector result | `check_gap("trades", timestamp)` updates last-seen timestamp and alerts on threshold breach. |
| Writer result | `ParquetWriter("trades", TRADES_SCHEMA).write(features)` appends to buffer and flushes on rotation or buffer threshold. |
| Health monitor result | `record_message("trades", timestamp)` increments `messages_per_minute["trades"]` and updates `last_trade_ts`. |

### `btcusdt@markPrice@1s` / markprice

| Stage | Trace result |
| --- | --- |
| Actual websocket payload | Binance mark-price payload includes event `e=markPriceUpdate`, event time `E`, symbol `s`, mark price `p`, funding rate `r`, and next funding time `T`. |
| Actual stream name | `btcusdt@markPrice@1s` (also defensively routes case-normalized `btcusdt@markprice@1s`). |
| Endpoint | `/market/stream?streams=btcusdt@aggTrade/btcusdt@markPrice@1s`. |
| `WebSocketClient` | Receives combined JSON, decodes with `json.loads`, calls `CollectorApp.handle_message(data)`. |
| `handle_message()` branch | Routed by `_route_stream()` when normalized stream starts with `btcusdt@` and contains `@markprice`. |
| `compute_markprice_features()` | Converts mark price, funding rate, and next funding time; computes funding bps and hours to funding. |
| Validation result | `validate_markprice()` checks timestamp freshness, positive mark price, funding rate within +/-1%, and next funding after exchange timestamp. |
| Gap detector result | `check_gap("markprice", timestamp)` updates last-seen timestamp and alerts on threshold breach. |
| Writer result | `ParquetWriter("markprice", MARKPRICE_SCHEMA).write(features)` appends to buffer and flushes on rotation or buffer threshold. |
| Health monitor result | `record_message("markprice", timestamp)` increments `messages_per_minute["markprice"]` and updates `last_mark_ts`. |

## Routing findings

Before this fix, `CollectorApp.handle_message()` used exact stream-string comparisons:

```python
if stream == "btcusdt@depth10@100ms":
elif stream == "btcusdt@aggTrade":
elif stream == "btcusdt@markPrice@1s":
```

The route matcher is now symbol-scoped and case-insensitive for the required stream families only:

```python
if "@depth" in normalized_stream: return "orderbook"
if "@aggtrade" in normalized_stream: return "trades"
if "@markprice" in normalized_stream: return "markprice"
```

This preserves existing BTCUSDT stream behavior while removing brittle exact-case and suffix matching for the three required collector stream families.

## Subscription findings

Before this fix, the exact URL was:

```text
wss://fstream.binance.com/stream?streams=btcusdt@depth10@100ms/btcusdt@aggTrade/btcusdt@markPrice@1s
```

That URL is now unsafe because it is an unrouted legacy endpoint. Binance documentation says unrouted futures WebSocket connections only receive public data after the migration; `@depth` continues to work, while `/market` streams such as `@aggTrade` and `@markPrice` stop pushing.

After this fix, the exact URLs are:

```text
wss://fstream.binance.com/public/stream?streams=btcusdt@depth10@100ms
wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade/btcusdt@markPrice@1s
```

## Diagnostics added

For the first 20 combined messages, `handle_message()` now emits:

```python
logger.info(f"RAW_STREAM={stream}")
logger.info(f"RAW_MESSAGE={msg}")
```

Per-stream counters now track:

```text
received
computed
empty_features
validated
rejected
written
```

These counters are emitted on startup verification failure and shutdown.

## Local validation evidence

* Unit tests confirmed case-insensitive routing for the required stream names, including lowercase received variants.
* Unit tests confirmed lowercase `aggtrade` and `markprice` messages reach `HealthMonitor.record_message()` and increment validated counters.
* Full collector test suite passed: `22 passed, 3 warnings`.

## Live operation evidence

A 60-second startup run was attempted in this container. The environment HTTP(S) proxy rejected Binance WebSocket connections with HTTP 403, so no Binance live messages could be received here. The new startup safety check correctly failed closed after 60 seconds with:

```text
RuntimeError: Startup stream inactivity after 60s: orderbook, trades, markprice
```

This validates the production safety behavior in the constrained environment, but it does not constitute an exchange-live success run. The collector must be run in the deployment environment with direct Binance WebSocket access to collect the requested 10-minute evidence showing non-zero orderbook/trades/markprice counters.

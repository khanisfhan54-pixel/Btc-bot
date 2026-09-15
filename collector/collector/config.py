import pyarrow as pa

# Constants
SYMBOL = "BTCUSDT"
BINANCE_PUBLIC_WS_URL = "wss://fstream.binance.com/public/stream?streams=btcusdt@depth@100ms"
BINANCE_MARKET_WS_URL = "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade/btcusdt@markPrice@1s/btcusdt@forceOrder"
# Intervals and Thresholds
ORDERBOOK_STALE_MS = 500
TRADES_STALE_MS = 5000   # was 30000
MARKPRICE_STALE_MS = 5000
OI_STALE_MS = 30000  # OI updates ~every 3s via REST or ~5s via stream
LIQUIDATION_STALE_MS = 1800000  # Liquidations are event-sparse; alert after 30 minutes silent.

# File Paths
DATA_DIR = "data"
LOGS_DIR = "logs"

# Schemas
# All timestamp columns: Unix epoch, milliseconds, UTC
ORDERBOOK_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ms", tz="UTC")),
    ("exchange_timestamp", pa.timestamp("ms", tz="UTC")),
    ("local_timestamp", pa.timestamp("ms", tz="UTC")),
    ("bids_price", pa.list_(pa.float64())),
    ("bids_qty", pa.list_(pa.float64())),
    ("asks_price", pa.list_(pa.float64())),
    ("asks_qty", pa.list_(pa.float64())),
    ("best_bid", pa.float64()),
    ("best_ask", pa.float64()),
    ("mid_price", pa.float64()),
    ("micro_price", pa.float64()),
    ("spread", pa.float64()),
    ("spread_bps", pa.float64()),
    ("total_bid_qty", pa.float64()),
    ("total_ask_qty", pa.float64()),
    ("obi", pa.float64()),
    ("obi_level_1", pa.float64()),
    ("obi_level_3", pa.float64()),
    ("obi_level_5", pa.float64()),
], metadata={"schema_version": "1.0", "stream_name": "orderbook", "symbol": SYMBOL})

TRADES_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ms", tz="UTC")),
    ("exchange_timestamp", pa.timestamp("ms", tz="UTC")),
    ("local_timestamp", pa.timestamp("ms", tz="UTC")),
    ("trade_id", pa.int64()),
    ("price", pa.float64()),
    ("quantity", pa.float64()),
    ("is_buyer_maker", pa.bool_()),
    ("side_sign", pa.int8()),
    ("signed_qty", pa.float64()),
], metadata={"schema_version": "1.0", "stream_name": "trades", "symbol": SYMBOL})

BINANCE_TRADES_RAW_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ms", tz="UTC")), ("local_receive_ts", pa.timestamp("ms", tz="UTC")),
    ("exchange_timestamp", pa.timestamp("ms", tz="UTC")), ("trade_id", pa.int64()),
    ("native_trade_id", pa.string()), ("price", pa.float64()), ("quantity", pa.float64()),
], metadata={"schema_version": "2.0", "migration": "v2: native_trade_id is authoritative; legacy trade_id is nullable", "stream_name": "binance_trades_raw", "symbol": SYMBOL})

MARKPRICE_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ms", tz="UTC")),
    ("exchange_timestamp", pa.timestamp("ms", tz="UTC")),
    ("local_timestamp", pa.timestamp("ms", tz="UTC")),
    ("mark_price", pa.float64()),
    ("funding_rate", pa.float64()),
    ("next_funding_time", pa.int64()),
    ("funding_rate_bps", pa.float64()),
    ("hours_to_funding", pa.float64()),
], metadata={"schema_version": "1.0", "stream_name": "markprice", "symbol": SYMBOL})

OPENINTEREST_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ms", tz="UTC")),
    ("exchange_timestamp", pa.timestamp("ms", tz="UTC")),
    ("local_timestamp", pa.timestamp("ms", tz="UTC")),
    ("open_interest", pa.float64()),
], metadata={"schema_version": "1.0", "stream_name": "openinterest", "symbol": SYMBOL})

LIQUIDATION_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ms", tz="UTC")),
    ("exchange_timestamp", pa.timestamp("ms", tz="UTC")),
    ("local_timestamp", pa.timestamp("ms", tz="UTC")),
    ("side", pa.int8()),
    ("price", pa.float64()),
    ("quantity", pa.float64()),
    ("signed_qty", pa.float64()),
    ("order_status", pa.string()),
    ("time_in_force", pa.string()),
], metadata={"schema_version": "1.0", "stream_name": "liquidation", "symbol": SYMBOL})

BINANCE_ORDERBOOK_RAW_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ms", tz="UTC")),  # Canonical local processing timestamp.
    ("exchange_timestamp", pa.timestamp("ms", tz="UTC")),
    ("local_receive_ts", pa.timestamp("ms", tz="UTC")), ("local_process_ts", pa.timestamp("ms", tz="UTC")),
    ("bids", pa.list_(pa.list_(pa.string()))), ("asks", pa.list_(pa.list_(pa.string()))),
    ("update_id", pa.int64()), ("first_update_id", pa.int64()), ("previous_update_id", pa.int64()),
    ("book_source", pa.string()), ("event_kind", pa.string()), ("recovery_generation", pa.int64()), ("quality_state", pa.string()),
], metadata={"schema_version": "2.0", "migration": "v2: book levels are canonical decimal strings; timestamp is canonical local event processing timestamp", "stream_name": "binance_orderbook_raw", "symbol": SYMBOL})

QUALITY_EVENTS_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ms", tz="UTC")),
    ("exchange", pa.string()),
    ("stream", pa.string()),
    ("event_type", pa.string()),
    ("reason", pa.string()),
    ("gap_size_ms", pa.int64()),
    ("rows_lost", pa.string()),
    ("quality_state", pa.string()),
    ("connection_id", pa.string()),
    ("previous_state", pa.string()), ("new_state", pa.string()),
    ("expected_previous_update_id", pa.int64()), ("actual_previous_update_id", pa.int64()),
    ("update_id", pa.int64()), ("first_update_id", pa.int64()), ("previous_update_id", pa.int64()), ("local_receive_ts", pa.timestamp("ms", tz="UTC")),
    ("local_process_ts", pa.timestamp("ms", tz="UTC")),
], metadata={"schema_version": "1.1", "stream_name": "quality_events", "symbol": SYMBOL})

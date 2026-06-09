import pyarrow as pa

# Constants
SYMBOL = "BTCUSDT"
BINANCE_PUBLIC_WS_URL = "wss://fstream.binance.com/public/stream?streams=btcusdt@depth10@100ms"
BINANCE_MARKET_WS_URL = "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade/btcusdt@markPrice@1s"
BINANCE_WS_URL = BINANCE_PUBLIC_WS_URL

# Intervals and Thresholds
ORDERBOOK_STALE_MS = 500
TRADES_STALE_MS = 5000   # was 30000
MARKPRICE_STALE_MS = 5000

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

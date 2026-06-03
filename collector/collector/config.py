import os
import pyarrow as pa

SYMBOL = "BTCUSDT"

# Connection strings
WS_URL = f"wss://fstream.binance.com/stream?streams=btcusdt@depth10@100ms/btcusdt@aggTrade/btcusdt@markPrice@1s"

# Data Directories
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
ALIGNED_DIR = os.path.join(DATA_DIR, "aligned")
STATS_DIR = os.path.join(DATA_DIR, "stats")
SPLITS_DIR = os.path.join(DATA_DIR, "splits")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

ORDERBOOK_DIR = os.path.join(RAW_DIR, "orderbook")
TRADES_DIR = os.path.join(RAW_DIR, "trades")
MARKPRICE_DIR = os.path.join(RAW_DIR, "markprice")

# Telemetry limits
DISK_WARNING_GB = 7.0
DISK_CRITICAL_GB = 4.0
DISK_EMERGENCY_GB = 2.0

MAX_SPREAD_BPS = 100.0
MAX_FUNDING_RATE_ABS = 0.01

MAX_VALIDATION_FAILURE_RATE = 0.001 # 0.1%

GAP_THRESHOLDS_MS = {
    "orderbook": 500,
    "trades": 30000,
    "markprice": 5000,
}

# Schemas
SCHEMA_VERSION = "1.0"

ORDERBOOK_SCHEMA = pa.schema([
    ("exchange_timestamp", pa.int64()),
    ("local_timestamp", pa.int64()),
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
])

TRADES_SCHEMA = pa.schema([
    ("exchange_timestamp", pa.int64()),
    ("local_timestamp", pa.int64()),
    ("trade_id", pa.int64()),
    ("price", pa.float64()),
    ("quantity", pa.float64()),
    ("is_buyer_maker", pa.bool_()),
    ("side_sign", pa.int8()),
    ("signed_qty", pa.float64()),
])

MARKPRICE_SCHEMA = pa.schema([
    ("exchange_timestamp", pa.int64()),
    ("local_timestamp", pa.int64()),
    ("mark_price", pa.float64()),
    ("funding_rate", pa.float64()),
    ("next_funding_time", pa.int64()),
    ("funding_rate_bps", pa.float64()),
    ("hours_to_funding", pa.float64()),
])

ALIGNED_SCHEMA = pa.schema([
    ("timestamp", pa.int64()),
    ("best_bid", pa.float64()),
    ("best_ask", pa.float64()),
    ("mid_price", pa.float64()),
    ("micro_price", pa.float64()),
    ("spread_bps", pa.float64()),
    ("obi", pa.float64()),
    ("obi_level_1", pa.float64()),
    ("obi_level_3", pa.float64()),
    ("obi_level_5", pa.float64()),
    ("total_bid_qty", pa.float64()),
    ("total_ask_qty", pa.float64()),
    ("trade_count", pa.int32()),
    ("buy_volume", pa.float64()),
    ("sell_volume", pa.float64()),
    ("net_volume", pa.float64()),
    ("trade_flow_imbalance", pa.float64()),
    ("vwap", pa.float64()),
    ("last_price", pa.float64()),
    ("mark_price", pa.float64()),
    ("funding_rate_bps", pa.float64()),
    ("hours_to_funding", pa.float64()),
    ("orderbook_gap", pa.bool_()),
    ("markprice_gap", pa.bool_()),
])

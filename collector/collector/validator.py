import time
from typing import Dict, Any, Tuple
from collector import config
from collector.utils import get_logger

logger = get_logger("validator")

class RecordValidator:
    def __init__(self):
        self.last_timestamps = {"orderbook": 0, "trades": 0, "markprice": 0}
        self.last_trade_id = 0
        self.last_mid_price = None

    def validate_orderbook(self, record: Dict[str, Any]) -> Tuple[bool, str]:
        ts = record.get("exchange_timestamp")
        if not ts or ts <= 0:
            return False, "Invalid timestamp"

        # Check staleness/future
        now_ms = time.time() * 1000
        if abs(now_ms - ts) > 5000:
            return False, f"Timestamp out of sync: {ts} vs local {now_ms}"

        if ts <= self.last_timestamps["orderbook"]:
            return False, f"Non-increasing timestamp: {ts} <= {self.last_timestamps['orderbook']}"

        bids_price = record.get("bids_price", [])
        asks_price = record.get("asks_price", [])
        bids_qty = record.get("bids_qty", [])
        asks_qty = record.get("asks_qty", [])

        if len(bids_price) != 10 or len(asks_price) != 10:
            return False, "Book depth must be 10"

        best_bid = record.get("best_bid", 0)
        best_ask = record.get("best_ask", 0)

        if best_bid <= 0 or best_ask <= 0:
            return False, "Best bid/ask must be > 0"

        if best_ask <= best_bid:
            return False, "Crossed book"

        spread_bps = record.get("spread_bps", 0)
        if spread_bps > config.MAX_SPREAD_BPS:
            return False, f"Spread exceeds max bps: {spread_bps}"

        total_bid_qty = record.get("total_bid_qty", 0)
        total_ask_qty = record.get("total_ask_qty", 0)

        if total_bid_qty <= 0 or total_ask_qty <= 0:
            return False, "Total bid/ask qty must be > 0"

        obi = record.get("obi", 0)
        if not -1.0 <= obi <= 1.0:
            return False, f"OBI out of bounds: {obi}"

        self.last_timestamps["orderbook"] = ts
        self.last_mid_price = record.get("mid_price")
        return True, ""

    def validate_trade(self, record: Dict[str, Any]) -> Tuple[bool, str]:
        ts = record.get("exchange_timestamp")
        if not ts or ts <= 0:
            return False, "Invalid timestamp"

        now_ms = time.time() * 1000
        if abs(now_ms - ts) > 5000:
            return False, f"Timestamp out of sync: {ts} vs local {now_ms}"

        if ts < self.last_timestamps["trades"]: # allow equal for trades in same ms
            return False, f"Decreasing timestamp: {ts} < {self.last_timestamps['trades']}"

        price = record.get("price", 0)
        qty = record.get("quantity", 0)

        if price <= 0 or qty <= 0:
            return False, "Price and qty must be > 0"

        trade_id = record.get("trade_id", 0)
        if trade_id <= self.last_trade_id:
            return False, f"Duplicate or decreasing trade_id: {trade_id} <= {self.last_trade_id}"

        if self.last_mid_price:
            if abs(price - self.last_mid_price) / self.last_mid_price > 0.05:
                return False, f"Trade price {price} deviates >5% from mid_price {self.last_mid_price}"

        self.last_timestamps["trades"] = ts
        self.last_trade_id = trade_id
        return True, ""

    def validate_markprice(self, record: Dict[str, Any]) -> Tuple[bool, str]:
        ts = record.get("exchange_timestamp")
        if not ts or ts <= 0:
            return False, "Invalid timestamp"

        now_ms = time.time() * 1000
        if abs(now_ms - ts) > 5000:
            return False, f"Timestamp out of sync: {ts} vs local {now_ms}"

        if ts <= self.last_timestamps["markprice"]:
            return False, f"Non-increasing timestamp: {ts} <= {self.last_timestamps['markprice']}"

        mark_price = record.get("mark_price", 0)
        if mark_price <= 0:
            return False, "Mark price must be > 0"

        funding_rate = record.get("funding_rate", 0)
        if abs(funding_rate) > config.MAX_FUNDING_RATE_ABS:
            return False, f"Funding rate {funding_rate} out of bounds"

        next_funding_time = record.get("next_funding_time", 0)
        if next_funding_time <= ts:
            return False, "Next funding time must be > exchange timestamp"

        self.last_timestamps["markprice"] = ts
        return True, ""

    def reset(self):
        """Reset validation state on reconnect."""
        self.last_timestamps = {"orderbook": 0, "trades": 0, "markprice": 0}
        self.last_trade_id = 0
        self.last_mid_price = None

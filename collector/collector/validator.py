import time
from typing import Dict, Any, Tuple, Optional
from collector.utils import logger

class Validator:
    def __init__(self):
        self.last_timestamps: Dict[str, int] = {
            "orderbook": 0,
            "trades": 0,
            "markprice": 0
        }
        self.last_trade_id = -1
        self.last_mid_price: Optional[float] = None
        self.failures_in_window = 0
        self.total_in_window = 0
        self.window_start = time.time()

    def check_failure_rate(self) -> bool:
        now = time.time()
        if now - self.window_start > 60:
            rate = self.failures_in_window / self.total_in_window if self.total_in_window > 0 else 0
            self.failures_in_window = 0
            self.total_in_window = 0
            self.window_start = now
            if rate > 0.001:
                return True
        return False

    def validate_timestamp(self, stream_name: str, record: Dict[str, Any]) -> Tuple[bool, str]:
        ts = record.get("timestamp")
        if ts is None or ts <= 0:
            return False, "Invalid timestamp"

        sys_time = int(time.time() * 1000)
        if abs(ts - sys_time) > 5000:
            return False, "Timestamp out of 5s tolerance"

        if ts <= self.last_timestamps[stream_name]:
            return False, "Timestamp regression"

        return True, ""

    def validate_orderbook(self, record: Dict[str, Any]) -> Tuple[bool, str]:
        self.total_in_window += 1

        valid_ts, reason = self.validate_timestamp("orderbook", record)
        if not valid_ts:
            self._handle_failure("orderbook", reason, record)
            return False, reason

        if len(record.get("bids_price", [])) != 10 or len(record.get("asks_price", [])) != 10:
            reason = "Invalid level count"
            self._handle_failure("orderbook", reason, record)
            return False, reason

        best_bid = record.get("best_bid", 0)
        best_ask = record.get("best_ask", 0)
        if best_bid <= 0 or best_ask <= 0:
            reason = "Invalid best bid/ask"
            self._handle_failure("orderbook", reason, record)
            return False, reason

        if best_ask <= best_bid:
            reason = "Crossed book"
            self._handle_failure("orderbook", reason, record)
            return False, reason

        if record.get("spread_bps", 0) >= 100:
            reason = "Spread > 100bps"
            self._handle_failure("orderbook", reason, record)
            return False, reason

        if record.get("total_bid_qty", 0) <= 0 or record.get("total_ask_qty", 0) <= 0:
            reason = "Invalid total qty"
            self._handle_failure("orderbook", reason, record)
            return False, reason

        obi = record.get("obi", -2)
        if obi < -1.0 or obi > 1.0:
            reason = "OBI out of bounds"
            self._handle_failure("orderbook", reason, record)
            return False, reason

        self.last_timestamps["orderbook"] = record["timestamp"]
        self.last_mid_price = record.get("mid_price")
        return True, ""

    def validate_trade(self, record: Dict[str, Any]) -> Tuple[bool, str]:
        self.total_in_window += 1

        valid_ts, reason = self.validate_timestamp("trades", record)
        if not valid_ts:
            self._handle_failure("trades", reason, record)
            return False, reason

        price = record.get("price", 0)
        if price <= 0:
            reason = "Invalid price"
            self._handle_failure("trades", reason, record)
            return False, reason

        if record.get("quantity", 0) <= 0:
            reason = "Invalid quantity"
            self._handle_failure("trades", reason, record)
            return False, reason

        if self.last_mid_price is not None:
            if abs(price - self.last_mid_price) / self.last_mid_price > 0.05:
                reason = "Price > 5% from mid_price"
                self._handle_failure("trades", reason, record)
                return False, reason

        trade_id = record.get("trade_id", -1)
        if self.last_trade_id != -1 and trade_id <= self.last_trade_id:
            reason = "Duplicate/Regressive trade_id"
            self._handle_failure("trades", reason, record)
            return False, reason

        self.last_timestamps["trades"] = record["timestamp"]
        self.last_trade_id = trade_id
        return True, ""

    def validate_markprice(self, record: Dict[str, Any]) -> Tuple[bool, str]:
        self.total_in_window += 1

        valid_ts, reason = self.validate_timestamp("markprice", record)
        if not valid_ts:
            self._handle_failure("markprice", reason, record)
            return False, reason

        if record.get("mark_price", 0) <= 0:
            reason = "Invalid mark price"
            self._handle_failure("markprice", reason, record)
            return False, reason

        funding_rate = record.get("funding_rate", -2)
        if funding_rate < -0.01 or funding_rate > 0.01:
            reason = "Funding rate out of bounds"
            self._handle_failure("markprice", reason, record)
            return False, reason

        if record.get("next_funding_time", 0) <= record.get("exchange_timestamp", 0):
            reason = "Invalid next funding time"
            self._handle_failure("markprice", reason, record)
            return False, reason

        self.last_timestamps["markprice"] = record["timestamp"]
        return True, ""

    def _handle_failure(self, stream_name: str, reason: str, record: Dict[str, Any]):
        self.failures_in_window += 1
        logger.error("Validation failed", stream=stream_name, reason=reason, timestamp=record.get("timestamp"), record=record)

    def reset(self):
        self.last_timestamps = {"orderbook": 0, "trades": 0, "markprice": 0}
        self.last_trade_id = -1
        self.last_mid_price = None
        self.failures_in_window = 0
        self.total_in_window = 0
        self.window_start = time.time()

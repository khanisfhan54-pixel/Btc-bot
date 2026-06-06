import time
import asyncio
from typing import Dict
from .utils import logger, send_telegram_alert
from .disk_monitor import DiskMonitor

class HealthMonitor:
    def __init__(self, disk_monitor: DiskMonitor, validator_ref: any, ws_client_ref: any):
        self.disk_monitor = disk_monitor
        self.validator = validator_ref
        self.ws_client = ws_client_ref
        self.last_trade_ts = 0
        self.last_book_ts = 0
        self.last_mark_ts = 0
        self.messages_per_minute: Dict[str, int] = {"orderbook": 0, "trades": 0, "markprice": 0}
        self.running = False

    def record_message(self, stream_name: str, ts: int):
        self.messages_per_minute[stream_name] += 1
        if stream_name == "orderbook":
            self.last_book_ts = max(self.last_book_ts, ts)
        elif stream_name == "trades":
            self.last_trade_ts = max(self.last_trade_ts, ts)
        elif stream_name == "markprice":
            self.last_mark_ts = max(self.last_mark_ts, ts)

    async def start(self):
        self.running = True
        logger.info("Health monitor started")
        while self.running:
            await asyncio.sleep(60)
            self._check_health()

    def stop(self):
        self.running = False

    def _check_health(self):
        now = int(time.time() * 1000)

        # Check staleness
        book_stale = (now - self.last_book_ts) > 60000 if self.last_book_ts > 0 else True
        trade_stale = (now - self.last_trade_ts) > 60000 if self.last_trade_ts > 0 else True
        mark_stale = (now - self.last_mark_ts) > 60000 if self.last_mark_ts > 0 else True

        if book_stale or trade_stale or mark_stale:
            stale_streams = []
            if book_stale: stale_streams.append("orderbook")
            if trade_stale: stale_streams.append("trades")
            if mark_stale: stale_streams.append("markprice")
            msg = f"Stream silent > 60s: {', '.join(stale_streams)}"
            logger.error(msg)
            send_telegram_alert(msg)

        disk_free = self.disk_monitor.get_free_gb()

        logger.info("Health check summary",
                    last_trade_ts=self.last_trade_ts,
                    last_book_ts=self.last_book_ts,
                    last_mark_ts=self.last_mark_ts,
                    messages_per_minute=self.messages_per_minute.copy(),
                    validation_failures_in_window=self.validator.failures_in_window,
                    disk_free_gb=disk_free,
                    ws_connected=self.ws_client.connected)

        # Reset minute counters
        self.messages_per_minute = {"orderbook": 0, "trades": 0, "markprice": 0}

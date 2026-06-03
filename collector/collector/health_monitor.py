import time
import threading
from collector.utils import get_logger, send_telegram_alert
from collector.disk_monitor import check_disk_space

logger = get_logger("health_monitor")

class HealthMonitor:
    def __init__(self, check_interval_sec=60):
        self.check_interval_sec = check_interval_sec
        self.last_trade_ts = 0
        self.last_book_ts = 0
        self.last_mark_ts = 0
        self.running = False
        self._thread = None
        self.emergency_shutdown = False

    def update_timestamp(self, stream_name: str, ts: int):
        if stream_name == "orderbook":
            self.last_book_ts = ts
        elif stream_name == "trades":
            self.last_trade_ts = ts
        elif stream_name == "markprice":
            self.last_mark_ts = ts

    def _monitor_loop(self):
        while self.running:
            time.sleep(self.check_interval_sec)

            now = time.time() * 1000

            alerts = []
            if now - self.last_book_ts > 60000 and self.last_book_ts > 0:
                alerts.append("Orderbook stream silent for > 60s")
            if now - self.last_trade_ts > 60000 and self.last_trade_ts > 0:
                alerts.append("Trades stream silent for > 60s")
            if now - self.last_mark_ts > 60000 and self.last_mark_ts > 0:
                alerts.append("MarkPrice stream silent for > 60s")

            if alerts:
                msg = "\n".join(alerts)
                logger.error("stream_silence", alerts=alerts)
                send_telegram_alert(f"Connection Lost/Silent Stream:\n{msg}")

            # Check disk space periodically
            free_gb, should_shutdown = check_disk_space()

            if should_shutdown:
                self.emergency_shutdown = True
                self.running = False
                break

            logger.info("health_check",
                        last_book_age_ms=now - self.last_book_ts if self.last_book_ts else -1,
                        last_trade_age_ms=now - self.last_trade_ts if self.last_trade_ts else -1,
                        last_mark_age_ms=now - self.last_mark_ts if self.last_mark_ts else -1,
                        disk_free_gb=free_gb)


    def start(self):
        self.running = True
        self.emergency_shutdown = False
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=2)

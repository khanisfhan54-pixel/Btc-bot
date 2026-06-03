import sys
import time
import asyncio
import signal
import os
from typing import Dict, Any

from collector import config
from collector.utils import setup_logging, get_logger, send_telegram_alert
from collector.websocket_client import BinanceWebsocketClient
from collector.validator import RecordValidator
from collector.gap_detector import GapDetector
from collector.parquet_writer import ParquetManager
from collector.health_monitor import HealthMonitor
from collector.feature_computer import (
    compute_orderbook_features,
    compute_trade_features,
    compute_markprice_features
)

# Set up logging
os.makedirs(config.LOGS_DIR, exist_ok=True)
setup_logging(os.path.join(config.LOGS_DIR, "collector.log"))
logger = get_logger("main")

class CollectorApp:
    def __init__(self):
        self.validator = RecordValidator()
        self.gap_detector = GapDetector()
        self.parquet_manager = ParquetManager()
        self.health_monitor = HealthMonitor()
        self.ws_client = BinanceWebsocketClient(
            message_handler=self.handle_message,
            reconnect_handler=self.handle_reconnect
        )
        self.validation_failures = 0
        self.total_messages = 0
        self.failure_window_start = time.time()

    def handle_reconnect(self):
        logger.info("system_reset_on_reconnect")
        self.validator.reset()
        self.gap_detector.reset()

    def handle_message(self, stream_name: str, raw_msg: Dict[str, Any]):
        self.total_messages += 1

        # Rate limit checks for validation failures
        now = time.time()
        if now - self.failure_window_start > 60:
            if self.total_messages > 0:
                failure_rate = self.validation_failures / self.total_messages
                if failure_rate > config.MAX_VALIDATION_FAILURE_RATE:
                    logger.error("validation_spike", rate=failure_rate)
                    send_telegram_alert(f"Validation failure spike: {failure_rate*100:.2f}%")
            self.validation_failures = 0
            self.total_messages = 0
            self.failure_window_start = now

        try:
            local_ts = int(time.time() * 1000)

            # Base record
            record = {
                "local_timestamp": local_ts,
                "exchange_timestamp": int(raw_msg.get("E", 0)) if stream_name != "trades" else int(raw_msg.get("E", 0))
            }

            if stream_name == "orderbook":
                features = compute_orderbook_features(raw_msg)
                record.update(features)
                is_valid, reason = self.validator.validate_orderbook(record)

            elif stream_name == "trades":
                record["trade_id"] = int(raw_msg.get("a", 0))
                record["price"] = float(raw_msg.get("p", 0))
                record["quantity"] = float(raw_msg.get("q", 0))
                record["is_buyer_maker"] = raw_msg.get("m", False)
                features = compute_trade_features(raw_msg)
                record.update(features)
                is_valid, reason = self.validator.validate_trade(record)

            elif stream_name == "markprice":
                record["mark_price"] = float(raw_msg.get("p", 0))
                record["funding_rate"] = float(raw_msg.get("r", 0))
                record["next_funding_time"] = int(raw_msg.get("T", 0))
                features = compute_markprice_features(raw_msg)
                record.update(features)
                is_valid, reason = self.validator.validate_markprice(record)

            else:
                return

            if not is_valid:
                self.validation_failures += 1
                logger.warning("record_rejected", stream=stream_name, reason=reason, raw=raw_msg)
                return

            self.gap_detector.check_gap(stream_name, record["exchange_timestamp"])
            self.parquet_manager.write(stream_name, record)
            self.health_monitor.update_timestamp(stream_name, local_ts)

        except Exception as e:
            logger.error("message_handling_error", stream=stream_name, error=str(e))

    async def run(self):
        logger.info("collector_starting", schema_version=config.SCHEMA_VERSION)
        send_telegram_alert("Collector Started")

        self.health_monitor.start()

        try:
            # Run the websocket client and a background task to check for emergency shutdowns
            async def watch_health():
                while self.ws_client.running:
                    if self.health_monitor.emergency_shutdown:
                        logger.critical("emergency_shutdown_triggered_by_health_monitor")
                        self.shutdown(signum=15)
                        break
                    await asyncio.sleep(1)

            await asyncio.gather(
                self.ws_client.connect_and_run(),
                watch_health()
            )
        finally:
            self.shutdown()

    def shutdown(self, signum=None, frame=None):
        logger.info("collector_shutting_down", reason="signal" if signum else "exception")
        send_telegram_alert("Collector Shutting Down")

        self.ws_client.stop()
        self.health_monitor.stop()
        self.parquet_manager.close_all()
        sys.exit(0)

if __name__ == "__main__":
    app = CollectorApp()

    # Setup signal handlers
    signal.signal(signal.SIGINT, app.shutdown)
    signal.signal(signal.SIGTERM, app.shutdown)

    try:
        asyncio.run(app.run())
    except Exception as e:
        logger.critical("fatal_error", error=str(e))
        send_telegram_alert(f"Fatal Error: {str(e)}")
        app.shutdown()

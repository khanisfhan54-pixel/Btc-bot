import os
import sys
import time
import asyncio
import signal
from collector.utils import logger, send_telegram_alert
from collector.config import BINANCE_WS_URL, ORDERBOOK_SCHEMA, TRADES_SCHEMA, MARKPRICE_SCHEMA
from collector.feature_computer import compute_orderbook_features, compute_trades_features, compute_markprice_features
from collector.validator import Validator
from collector.gap_detector import GapDetector
from collector.disk_monitor import DiskMonitor
from collector.health_monitor import HealthMonitor
from collector.parquet_writer import ParquetWriter
from collector.websocket_client import WebSocketClient

class CollectorApp:
    def __init__(self):
        self.running = False

        self.disk_monitor = DiskMonitor(shutdown_callback=self.shutdown)
        self.disk_monitor.check_disk_space()

        self.validator = Validator()
        self.gap_detector = GapDetector()

        self.ob_writer = ParquetWriter("orderbook", ORDERBOOK_SCHEMA)
        self.trades_writer = ParquetWriter("trades", TRADES_SCHEMA)
        self.mark_writer = ParquetWriter("markprice", MARKPRICE_SCHEMA)

        self.ws_client = WebSocketClient(
            url=BINANCE_WS_URL,
            on_message=self.handle_message,
            on_reconnect=self.handle_reconnect
        )

        self.health_monitor = HealthMonitor(self.disk_monitor, self.validator, self.ws_client)

        self.tasks = []

    async def handle_message(self, msg: dict):
        if "stream" not in msg or "data" not in msg:
            return

        stream = msg["stream"]
        data = msg["data"]

        if stream == "btcusdt@depth10@100ms":
            features = compute_orderbook_features(data)
            if features:
                valid, _ = self.validator.validate_orderbook(features)
                if valid:
                    self.gap_detector.check_gap("orderbook", features["timestamp"])
                    self.ob_writer.write(features)
                    self.health_monitor.record_message("orderbook", features["timestamp"])

        elif stream == "btcusdt@aggTrade":
            features = compute_trades_features(data)
            if features:
                valid, _ = self.validator.validate_trade(features)
                if valid:
                    self.gap_detector.check_gap("trades", features["timestamp"])
                    self.trades_writer.write(features)
                    self.health_monitor.record_message("trades", features["timestamp"])

        elif stream == "btcusdt@markPrice@1s":
            features = compute_markprice_features(data)
            if features:
                valid, _ = self.validator.validate_markprice(features)
                if valid:
                    self.gap_detector.check_gap("markprice", features["timestamp"])
                    self.mark_writer.write(features)
                    self.health_monitor.record_message("markprice", features["timestamp"])

        if self.validator.check_failure_rate():
            logger.error("Validation spike detected: >0.1% failures in 60s window")
            send_telegram_alert("Validation spike detected: >0.1% failures in 60s window")

    def handle_reconnect(self):
        logger.info("Resetting validation and gap tracking on reconnect")
        self.validator.reset()
        self.gap_detector.reset()

    async def start(self):
        logger.info("Starting Collector Application")
        send_telegram_alert("Collector Application Started")

        self.running = True

        # Start health monitor
        self.tasks.append(asyncio.create_task(self.health_monitor.start()))

        # Start websocket client
        self.tasks.append(asyncio.create_task(self.ws_client.start()))

        try:
            await asyncio.gather(*self.tasks)
        except asyncio.CancelledError:
            pass
        finally:
            self.shutdown()

    def shutdown(self):
        if not self.running:
            return

        logger.info("Shutting down Collector Application...")
        self.running = False

        self.ws_client.stop()
        self.health_monitor.stop()

        for task in self.tasks:
            task.cancel()

        self.ob_writer.close()
        self.trades_writer.close()
        self.mark_writer.close()

        msg = "Collector Application Shutdown"
        logger.info(msg)
        send_telegram_alert(msg)

def handle_sigint(signum, frame):
    logger.info(f"Received signal {signum}, initiating shutdown...")
    if app:
        app.shutdown()
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    app = CollectorApp()

    try:
        asyncio.run(app.start())
    except KeyboardInterrupt:
        pass

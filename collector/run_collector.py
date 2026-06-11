import asyncio
import signal
from urllib.parse import parse_qs, urlparse
from collector.collector.utils import logger, send_telegram_alert, validate_telegram_startup
from collector.collector.config import (
    BINANCE_MARKET_WS_URL,
    BINANCE_PUBLIC_WS_URL,
    ORDERBOOK_SCHEMA,
    TRADES_SCHEMA,
    MARKPRICE_SCHEMA,
    OPENINTEREST_SCHEMA,
    LIQUIDATION_SCHEMA,
    SYMBOL,
)
from collector.collector.feature_computer import (
    compute_liquidation_features,
    compute_markprice_features,
    compute_openinterest_features,
    compute_orderbook_features,
    compute_trades_features,
)
from collector.collector.validator import Validator
from collector.collector.gap_detector import GapDetector
from collector.collector.disk_monitor import DiskMonitor
from collector.collector.health_monitor import HealthMonitor
from collector.collector.parquet_writer import ParquetWriter
from collector.collector.websocket_client import WebSocketClient

STREAM_INACTIVE_STARTUP_SECONDS = 60
RAW_LOG_LIMIT = 20
OI_POLL_INTERVAL_S = 3.0
OI_URL = "https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT"

class CollectorApp:
    def __init__(self):
        self.running = False
        self.raw_messages_logged = 0
        self.stream_counters = {
            "orderbook": {"received": 0, "computed": 0, "empty_features": 0, "validated": 0, "rejected": 0, "written": 0},
            "trades": {"received": 0, "computed": 0, "empty_features": 0, "validated": 0, "rejected": 0, "written": 0},
            "markprice": {"received": 0, "computed": 0, "empty_features": 0, "validated": 0, "rejected": 0, "written": 0},
            "openinterest": {"received": 0, "computed": 0, "empty_features": 0, "validated": 0, "rejected": 0, "written": 0},
            "liquidation": {"received": 0, "computed": 0, "empty_features": 0, "validated": 0, "rejected": 0, "written": 0},
            "unrouted": {"received": 0},
        }
        self.validation_fail_reasons = {"orderbook": {}, "trades": {}, "markprice": {}, "openinterest": {}, "liquidation": {}}

        self.disk_monitor = DiskMonitor(shutdown_callback=self.shutdown)
        self.disk_monitor.check_disk_space()

        self.validator = Validator()
        self.gap_detector = GapDetector()

        self.ob_writer = ParquetWriter("orderbook", ORDERBOOK_SCHEMA)
        self.trades_writer = ParquetWriter("trades", TRADES_SCHEMA)
        self.mark_writer = ParquetWriter("markprice", MARKPRICE_SCHEMA)
        self.oi_writer = ParquetWriter("openinterest", OPENINTEREST_SCHEMA)
        self.liq_writer = ParquetWriter("liquidation", LIQUIDATION_SCHEMA)

        self.ws_clients = [
            WebSocketClient(
                url=BINANCE_PUBLIC_WS_URL,
                on_message=self.handle_message,
                on_reconnect=self._make_reconnect_handler(BINANCE_PUBLIC_WS_URL)
            ),
            WebSocketClient(
                url=BINANCE_MARKET_WS_URL,
                on_message=self.handle_message,
                on_reconnect=self._make_reconnect_handler(BINANCE_MARKET_WS_URL)
            ),
        ]

        self.health_monitor = HealthMonitor(self.disk_monitor, self.validator, self)

        self.tasks = []

    @property
    def connected(self):
        return all(client.connected for client in self.ws_clients)

    def _requested_streams(self, url: str):
        parsed = urlparse(url)
        streams = parse_qs(parsed.query).get("streams", [""])[0]
        return [stream for stream in streams.split("/") if stream]

    def _route_stream(self, stream: str):
        normalized_stream = stream.lower()
        symbol_prefix = f"{SYMBOL.lower()}@"

        if not normalized_stream.startswith(symbol_prefix):
            return None
        if "@depth" in normalized_stream:
            return "orderbook"
        if "@aggtrade" in normalized_stream:
            return "trades"
        if "@markprice" in normalized_stream:
            return "markprice"
        if "@forceorder" in normalized_stream:
            return "liquidation"
        return None

    def _log_raw_sample(self, stream: str, msg: dict):
        if self.raw_messages_logged >= RAW_LOG_LIMIT:
            return
        logger.info(f"RAW_STREAM={stream}")
        logger.info(f"RAW_MESSAGE={msg}")
        self.raw_messages_logged += 1

    def _record_validation_rejection(self, stream_name: str, reason: str):
        reasons = self.validation_fail_reasons.setdefault(stream_name, {})
        reasons[reason] = reasons.get(reason, 0) + 1

    async def handle_message(self, msg: dict):
        if "stream" not in msg or "data" not in msg:
            return

        stream = msg["stream"]
        data = msg["data"]
        self._log_raw_sample(stream, msg)

        route = self._route_stream(stream)
        if route is None:
            self.stream_counters["unrouted"]["received"] += 1
            logger.warning("Unrouted stream message", stream=stream)
        elif route == "orderbook":
            self._handle_orderbook(data, stream)
        elif route == "trades":
            self._handle_trades(data, stream)
        elif route == "markprice":
            self._handle_markprice(data, stream)
        elif route == "liquidation":
            self._handle_liquidation(data, stream)

        if self.validator.check_failure_rate():
            logger.error("Validation spike detected: >0.1% failures in 60s window")
            send_telegram_alert("Validation spike detected: >0.1% failures in 60s window")

    def _handle_orderbook(self, data: dict, stream: str):
        self.stream_counters["orderbook"]["received"] += 1
        features = compute_orderbook_features(data)
        if not features:
            self.stream_counters["orderbook"]["empty_features"] += 1
            logger.warning("Feature extraction returned empty", stream="orderbook", raw_stream=stream, keys=sorted(data.keys()))
            return

        self.stream_counters["orderbook"]["computed"] += 1
        valid, reason = self.validator.validate_orderbook(features)
        if not valid:
            self.stream_counters["orderbook"]["rejected"] += 1
            self._record_validation_rejection("orderbook", reason)
            logger.info("Validation result", stream="orderbook", validation_pass=False, validation_fail_reason=reason)
            return

        self.stream_counters["orderbook"]["validated"] += 1
        logger.info("Validation result", stream="orderbook", validation_pass=True, validation_fail_reason="")
        self.gap_detector.check_gap("orderbook", features["exchange_timestamp"])
        self.ob_writer.write(features)
        self.stream_counters["orderbook"]["written"] += 1
        self.health_monitor.record_message("orderbook", features["timestamp"])

    def _handle_trades(self, data: dict, stream: str):
        self.stream_counters["trades"]["received"] += 1
        features = compute_trades_features(data)
        if not features:
            self.stream_counters["trades"]["empty_features"] += 1
            logger.warning("Feature extraction returned empty", stream="trades", raw_stream=stream, keys=sorted(data.keys()))
            return

        self.stream_counters["trades"]["computed"] += 1
        valid, reason = self.validator.validate_trade(features)
        if not valid:
            self.stream_counters["trades"]["rejected"] += 1
            self._record_validation_rejection("trades", reason)
            logger.info("Validation result", stream="trades", validation_pass=False, validation_fail_reason=reason)
            return

        self.stream_counters["trades"]["validated"] += 1
        logger.info("Validation result", stream="trades", validation_pass=True, validation_fail_reason="")
        self.gap_detector.check_gap("trades", features["exchange_timestamp"])
        self.trades_writer.write(features)
        self.stream_counters["trades"]["written"] += 1
        self.health_monitor.record_message("trades", features["timestamp"])

    def _handle_liquidation(self, data: dict, stream: str):
        self.stream_counters["liquidation"]["received"] += 1
        try:
            features = compute_liquidation_features(data)
            if not features:
                self.stream_counters["liquidation"]["empty_features"] += 1
                logger.warning("Feature extraction returned empty", stream="liquidation", raw_stream=stream, keys=sorted(data.keys()))
                return

            self.stream_counters["liquidation"]["computed"] += 1
            self.stream_counters["liquidation"]["validated"] += 1
            self.liq_writer.write(features)
            self.stream_counters["liquidation"]["written"] += 1
            self.health_monitor.record_message("liquidation", features["timestamp"])
        except Exception as exc:
            self.stream_counters["liquidation"]["rejected"] += 1
            self._record_validation_rejection("liquidation", type(exc).__name__)
            logger.error("Liquidation handling failed", stream="liquidation", raw_stream=stream, error=str(exc))

    def _handle_markprice(self, data: dict, stream: str):
        self.stream_counters["markprice"]["received"] += 1
        features = compute_markprice_features(data)
        if not features:
            self.stream_counters["markprice"]["empty_features"] += 1
            logger.warning("Feature extraction returned empty", stream="markprice", raw_stream=stream, keys=sorted(data.keys()))
            return

        self.stream_counters["markprice"]["computed"] += 1
        valid, reason = self.validator.validate_markprice(features)
        if not valid:
            self.stream_counters["markprice"]["rejected"] += 1
            self._record_validation_rejection("markprice", reason)
            logger.info("Validation result", stream="markprice", validation_pass=False, validation_fail_reason=reason)
            return

        self.stream_counters["markprice"]["validated"] += 1
        logger.info("Validation result", stream="markprice", validation_pass=True, validation_fail_reason="")
        self.gap_detector.check_gap("markprice", features["exchange_timestamp"])
        self.mark_writer.write(features)
        self.stream_counters["markprice"]["written"] += 1
        self.health_monitor.record_message("markprice", features["timestamp"])

    def _make_reconnect_handler(self, url: str):
        streams_for_url = self._requested_streams(url)

        def handler():
            logger.info("Resetting validation and gap tracking on reconnect", url=url, streams=streams_for_url)
            for stream_name_fragment in streams_for_url:
                route = self._route_stream(stream_name_fragment)
                if route:
                    preserved_last_mid_price = self.validator.last_mid_price if route == "orderbook" else None
                    self.validator.reset_stream(route)
                    if route == "orderbook":
                        self.validator.last_mid_price = preserved_last_mid_price
                    self.gap_detector.reset_stream(route)

        return handler

    async def start(self):
        logger.info("Starting Collector Application")
        logger.info("Collector WebSocket subscription", url=BINANCE_PUBLIC_WS_URL, requested_streams=self._requested_streams(BINANCE_PUBLIC_WS_URL))
        logger.info("Collector WebSocket subscription", url=BINANCE_MARKET_WS_URL, requested_streams=self._requested_streams(BINANCE_MARKET_WS_URL))
        send_telegram_alert("Collector Application Started")

        self.running = True

        # BUG 4 FIX: Register signals inside the running event loop for safe async shutdown.
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self._async_shutdown(s))
            )

        for ws_client in self.ws_clients:
            self.tasks.append(asyncio.create_task(ws_client.start()))

        # Wait for both WebSocket connections before starting OI polling and health monitor.
        for ws_client in self.ws_clients:
            connected = await ws_client.wait_connected(timeout_seconds=30.0)
            if not connected:
                logger.warning("ws_client_pre_connect_timeout", url=ws_client.url)

        self.tasks.append(asyncio.create_task(self.health_monitor.start()))
        self.tasks.append(asyncio.create_task(self._poll_openinterest()))
        self.tasks.append(asyncio.create_task(self._verify_startup_streams()))

        try:
            await asyncio.gather(*self.tasks)
        except asyncio.CancelledError:
            pass
        finally:
            self.shutdown()

    async def _poll_openinterest(self):
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=5)
        while self.running:
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(OI_URL) as resp:
                        data = await resp.json()
                self.stream_counters["openinterest"]["received"] += 1
                features = compute_openinterest_features(data)
                if features:
                    self.stream_counters["openinterest"]["computed"] += 1
                    self.stream_counters["openinterest"]["validated"] += 1
                    self.oi_writer.write(features)
                    self.stream_counters["openinterest"]["written"] += 1
                    self.health_monitor.record_message("openinterest", features["timestamp"])
                else:
                    self.stream_counters["openinterest"]["empty_features"] += 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("OI poll failed", error=str(e))
            await asyncio.sleep(OI_POLL_INTERVAL_S)

    async def _verify_startup_streams(self):
        await asyncio.sleep(STREAM_INACTIVE_STARTUP_SECONDS)
        inactive_streams = [
            stream_name
            for stream_name in ("orderbook", "trades", "markprice")
            if self.stream_counters[stream_name]["received"] == 0
        ]
        if inactive_streams:
            msg = f"Startup stream inactivity after {STREAM_INACTIVE_STARTUP_SECONDS}s: {', '.join(inactive_streams)}"
            logger.error(msg, stream_counters=self.stream_counters, validation_fail_reasons=self.validation_fail_reasons)
            send_telegram_alert(msg)
            raise RuntimeError(msg)
        logger.info("Startup stream verification passed", stream_counters=self.stream_counters, validation_fail_reasons=self.validation_fail_reasons)

    async def _async_shutdown(self, signum: int):
        logger.info("Received signal, initiating async shutdown", signum=signum)
        self.shutdown()
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()

    def shutdown(self):
        if not self.running:
            return

        logger.info("Shutting down Collector Application...", stream_counters=self.stream_counters, validation_fail_reasons=self.validation_fail_reasons)
        self.running = False

        for ws_client in self.ws_clients:
            ws_client.stop()
        self.health_monitor.stop()

        for task in self.tasks:
            task.cancel()

        self.ob_writer.close()
        self.trades_writer.close()
        self.mark_writer.close()
        self.oi_writer.close()
        self.liq_writer.close()

        msg = "Collector Application Shutdown"
        logger.info(msg)
        send_telegram_alert(msg)

if __name__ == "__main__":
    validate_telegram_startup()
    app = CollectorApp()
    try:
        asyncio.run(app.start())
    except KeyboardInterrupt:
        pass

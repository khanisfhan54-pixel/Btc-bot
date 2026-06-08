# Collector Fix Report

## Root cause

The collector subscribed to an unrouted legacy Binance USDⓈ-M futures combined-stream URL:

```text
wss://fstream.binance.com/stream?streams=btcusdt@depth10@100ms/btcusdt@aggTrade/btcusdt@markPrice@1s
```

Binance migrated futures WebSocket market data to routed endpoints. On unrouted legacy connections, public streams can continue to push, but market streams stop pushing. This exactly matches the reported symptom:

```json
{
  "orderbook": ">0",
  "trades": 0,
  "markprice": 0
}
```

because `btcusdt@depth10@100ms` is a public depth stream, while `btcusdt@aggTrade` and `btcusdt@markPrice@1s` are market streams.

A secondary fragility was exact stream-name routing in `handle_message()`. Exact comparisons could fail if Binance or a client library emitted a case-normalized stream name such as `btcusdt@aggtrade` or `btcusdt@markprice@1s`.

## Files changed

1. `collector/collector/config.py`
   * Added routed Binance public URL for orderbook depth.
   * Added routed Binance market URL for aggregate trades and mark price.
   * Kept `BINANCE_WS_URL` as a backward-compatible alias to the public URL.

2. `collector/run_collector.py`
   * Replaced one legacy `WebSocketClient` with two routed clients: one public and one market.
   * Added exact startup subscription logging for each URL.
   * Added first-20-message raw stream/message logging.
   * Added symbol-scoped, case-insensitive stream-family routing for `@depth`, `@aggtrade`, and `@markprice`.
   * Added per-stream received/computed/empty/validated/rejected/written counters.
   * Added a 60-second startup inactivity fail-closed check.

3. `collector/tests/test_run_collector_routing.py`
   * Added routing regression tests for the required stream names and lowercase variants.
   * Added an async ingestion test proving lowercase trade and mark-price messages reach `HealthMonitor.record_message()`.

## Exact lines changed

* `collector/collector/config.py`: lines 5-7 define the routed public and market WebSocket URLs and the backward-compatible alias.
* `collector/run_collector.py`: lines 21-22 define startup/raw-log limits; lines 27-33 define stream counters; lines 45-58 create the public and market WebSocket clients; lines 62-69 expose combined connection status and requested-stream parsing; lines 71-90 implement stream routing and first-20 raw logging; lines 92-182 implement routed ingestion, validation logging, writer calls, and health recording; lines 189-208 log subscriptions and start all ingestion tasks; lines 217-226 enforce the 60-second fail-closed startup check; lines 239-243 stop all WebSocket clients on shutdown.
* `collector/tests/test_run_collector_routing.py`: lines 73-81 cover stream-route matching; lines 84-97 verify lower-case trade and markprice messages reach health recording.

## Before behavior

```text
URL: wss://fstream.binance.com/stream?streams=btcusdt@depth10@100ms/btcusdt@aggTrade/btcusdt@markPrice@1s
```

* The connection used an unrouted endpoint.
* Public depth could push.
* Market streams (`aggTrade`, `markPrice`) could remain silent.
* Exact stream comparisons made routing brittle.
* Collector could keep running with only orderbook data.

## After behavior

```text
Public URL: wss://fstream.binance.com/public/stream?streams=btcusdt@depth10@100ms
Market URL: wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade/btcusdt@markPrice@1s
```

* Orderbook depth is subscribed through the `/public` endpoint.
* Aggregate trades and mark price are subscribed through the `/market` endpoint.
* Routing is symbol-scoped and recognizes only the required stream families.
* First 20 messages log `RAW_STREAM=...` and `RAW_MESSAGE=...`.
* Per-stream counters show received, computed, validation, rejection, and writer progress.
* Startup fails closed if orderbook, trades, or markprice remains inactive after 60 seconds.

## Validation evidence

### Unit test suite

```text
$ cd collector && python -m pytest -q
......................                                                   [100%]
22 passed, 3 warnings in 2.75s
```

### Live startup safety check in this container

```text
$ mkdir -p data && timeout 75s python run_collector.py
...
RuntimeError: Startup stream inactivity after 60s: orderbook, trades, markprice
```

The run could not reach Binance because this container's proxy rejected WebSocket connections:

```text
WebSocket error: proxy rejected connection: HTTP 403
```

The safety check correctly prevented the collector from continuing silently with inactive streams.

## Remaining risks

* A deployment-environment 10-minute live validation is still required because this container cannot establish Binance WebSocket connections through its proxy.
* The collector now uses two WebSocket sessions because Binance split public and market traffic by routed endpoint. This is the smallest safe ingestion fix consistent with Binance's endpoint mapping, but operators should monitor both sessions.
* `ParquetWriter.write()` buffers records and flushes on rotation/threshold/close, so immediate disk row visibility may lag in normal operation.

## Unified diff

```diff
diff --git a/collector/collector/config.py b/collector/collector/config.py
index 388431c..2917f65 100644
--- a/collector/collector/config.py
+++ b/collector/collector/config.py
@@ -2,7 +2,9 @@ import pyarrow as pa
 
 # Constants
 SYMBOL = "BTCUSDT"
-BINANCE_WS_URL = "wss://fstream.binance.com/stream?streams=btcusdt@depth10@100ms/btcusdt@aggTrade/btcusdt@markPrice@1s"
+BINANCE_PUBLIC_WS_URL = "wss://fstream.binance.com/public/stream?streams=btcusdt@depth10@100ms"
+BINANCE_MARKET_WS_URL = "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade/btcusdt@markPrice@1s"
+BINANCE_WS_URL = BINANCE_PUBLIC_WS_URL
 
 # Intervals and Thresholds
 ORDERBOOK_STALE_MS = 500
diff --git a/collector/run_collector.py b/collector/run_collector.py
index 0d6b473..3af4515 100644
--- a/collector/run_collector.py
+++ b/collector/run_collector.py
@@ -1,10 +1,15 @@
-import os
-import sys
-import time
 import asyncio
 import signal
+from urllib.parse import parse_qs, urlparse
 from collector.utils import logger, send_telegram_alert
-from collector.config import BINANCE_WS_URL, ORDERBOOK_SCHEMA, TRADES_SCHEMA, MARKPRICE_SCHEMA
+from collector.config import (
+    BINANCE_MARKET_WS_URL,
+    BINANCE_PUBLIC_WS_URL,
+    ORDERBOOK_SCHEMA,
+    TRADES_SCHEMA,
+    MARKPRICE_SCHEMA,
+    SYMBOL,
+)
 from collector.feature_computer import compute_orderbook_features, compute_trades_features, compute_markprice_features
 from collector.validator import Validator
 from collector.gap_detector import GapDetector
@@ -13,9 +18,19 @@ from collector.health_monitor import HealthMonitor
 from collector.parquet_writer import ParquetWriter
 from collector.websocket_client import WebSocketClient
 
+STREAM_INACTIVE_STARTUP_SECONDS = 60
+RAW_LOG_LIMIT = 20
+
 class CollectorApp:
     def __init__(self):
         self.running = False
+        self.raw_messages_logged = 0
+        self.stream_counters = {
+            "orderbook": {"received": 0, "computed": 0, "empty_features": 0, "validated": 0, "rejected": 0, "written": 0},
+            "trades": {"received": 0, "computed": 0, "empty_features": 0, "validated": 0, "rejected": 0, "written": 0},
+            "markprice": {"received": 0, "computed": 0, "empty_features": 0, "validated": 0, "rejected": 0, "written": 0},
+            "unrouted": {"received": 0},
+        }
 
         self.disk_monitor = DiskMonitor(shutdown_callback=self.shutdown)
         self.disk_monitor.check_disk_space()
@@ -27,54 +42,142 @@ class CollectorApp:
         self.trades_writer = ParquetWriter("trades", TRADES_SCHEMA)
         self.mark_writer = ParquetWriter("markprice", MARKPRICE_SCHEMA)
 
-        self.ws_client = WebSocketClient(
-            url=BINANCE_WS_URL,
-            on_message=self.handle_message,
-            on_reconnect=self.handle_reconnect
-        )
+        self.ws_clients = [
+            WebSocketClient(
+                url=BINANCE_PUBLIC_WS_URL,
+                on_message=self.handle_message,
+                on_reconnect=self.handle_reconnect
+            ),
+            WebSocketClient(
+                url=BINANCE_MARKET_WS_URL,
+                on_message=self.handle_message,
+                on_reconnect=self.handle_reconnect
+            ),
+        ]
 
-        self.health_monitor = HealthMonitor(self.disk_monitor, self.validator, self.ws_client)
+        self.health_monitor = HealthMonitor(self.disk_monitor, self.validator, self)
 
         self.tasks = []
 
+    @property
+    def connected(self):
+        return all(client.connected for client in self.ws_clients)
+
+    def _requested_streams(self, url: str):
+        parsed = urlparse(url)
+        streams = parse_qs(parsed.query).get("streams", [""])[0]
+        return [stream for stream in streams.split("/") if stream]
+
+    def _route_stream(self, stream: str):
+        normalized_stream = stream.lower()
+        symbol_prefix = f"{SYMBOL.lower()}@"
+
+        if not normalized_stream.startswith(symbol_prefix):
+            return None
+        if "@depth" in normalized_stream:
+            return "orderbook"
+        if "@aggtrade" in normalized_stream:
+            return "trades"
+        if "@markprice" in normalized_stream:
+            return "markprice"
+        return None
+
+    def _log_raw_sample(self, stream: str, msg: dict):
+        if self.raw_messages_logged >= RAW_LOG_LIMIT:
+            return
+        logger.info(f"RAW_STREAM={stream}")
+        logger.info(f"RAW_MESSAGE={msg}")
+        self.raw_messages_logged += 1
+
     async def handle_message(self, msg: dict):
         if "stream" not in msg or "data" not in msg:
             return
 
         stream = msg["stream"]
         data = msg["data"]
+        self._log_raw_sample(stream, msg)
 
-        if stream == "btcusdt@depth10@100ms":
-            features = compute_orderbook_features(data)
-            if features:
-                valid, _ = self.validator.validate_orderbook(features)
-                if valid:
-                    self.gap_detector.check_gap("orderbook", features["timestamp"])
-                    self.ob_writer.write(features)
-                    self.health_monitor.record_message("orderbook", features["timestamp"])
-
-        elif stream == "btcusdt@aggTrade":
-            features = compute_trades_features(data)
-            if features:
-                valid, _ = self.validator.validate_trade(features)
-                if valid:
-                    self.gap_detector.check_gap("trades", features["timestamp"])
-                    self.trades_writer.write(features)
-                    self.health_monitor.record_message("trades", features["timestamp"])
-
-        elif stream == "btcusdt@markPrice@1s":
-            features = compute_markprice_features(data)
-            if features:
-                valid, _ = self.validator.validate_markprice(features)
-                if valid:
-                    self.gap_detector.check_gap("markprice", features["timestamp"])
-                    self.mark_writer.write(features)
-                    self.health_monitor.record_message("markprice", features["timestamp"])
+        route = self._route_stream(stream)
+        if route is None:
+            self.stream_counters["unrouted"]["received"] += 1
+            logger.warning("Unrouted stream message", stream=stream)
+        elif route == "orderbook":
+            self._handle_orderbook(data, stream)
+        elif route == "trades":
+            self._handle_trades(data, stream)
+        elif route == "markprice":
+            self._handle_markprice(data, stream)
 
         if self.validator.check_failure_rate():
             logger.error("Validation spike detected: >0.1% failures in 60s window")
             send_telegram_alert("Validation spike detected: >0.1% failures in 60s window")
 
+    def _handle_orderbook(self, data: dict, stream: str):
+        self.stream_counters["orderbook"]["received"] += 1
+        features = compute_orderbook_features(data)
+        if not features:
+            self.stream_counters["orderbook"]["empty_features"] += 1
+            logger.warning("Feature extraction returned empty", stream="orderbook", raw_stream=stream, keys=sorted(data.keys()))
+            return
+
+        self.stream_counters["orderbook"]["computed"] += 1
+        valid, reason = self.validator.validate_orderbook(features)
+        if not valid:
+            self.stream_counters["orderbook"]["rejected"] += 1
+            logger.info("Validation result", stream="orderbook", validation_pass=False, validation_fail_reason=reason)
+            return
+
+        self.stream_counters["orderbook"]["validated"] += 1
+        logger.info("Validation result", stream="orderbook", validation_pass=True, validation_fail_reason="")
+        self.gap_detector.check_gap("orderbook", features["timestamp"])
+        self.ob_writer.write(features)
+        self.stream_counters["orderbook"]["written"] += 1
+        self.health_monitor.record_message("orderbook", features["timestamp"])
+
+    def _handle_trades(self, data: dict, stream: str):
+        self.stream_counters["trades"]["received"] += 1
+        features = compute_trades_features(data)
+        if not features:
+            self.stream_counters["trades"]["empty_features"] += 1
+            logger.warning("Feature extraction returned empty", stream="trades", raw_stream=stream, keys=sorted(data.keys()))
+            return
+
+        self.stream_counters["trades"]["computed"] += 1
+        valid, reason = self.validator.validate_trade(features)
+        if not valid:
+            self.stream_counters["trades"]["rejected"] += 1
+            logger.info("Validation result", stream="trades", validation_pass=False, validation_fail_reason=reason)
+            return
+
+        self.stream_counters["trades"]["validated"] += 1
+        logger.info("Validation result", stream="trades", validation_pass=True, validation_fail_reason="")
+        self.gap_detector.check_gap("trades", features["timestamp"])
+        self.trades_writer.write(features)
+        self.stream_counters["trades"]["written"] += 1
+        self.health_monitor.record_message("trades", features["timestamp"])
+
+    def _handle_markprice(self, data: dict, stream: str):
+        self.stream_counters["markprice"]["received"] += 1
+        features = compute_markprice_features(data)
+        if not features:
+            self.stream_counters["markprice"]["empty_features"] += 1
+            logger.warning("Feature extraction returned empty", stream="markprice", raw_stream=stream, keys=sorted(data.keys()))
+            return
+
+        self.stream_counters["markprice"]["computed"] += 1
+        valid, reason = self.validator.validate_markprice(features)
+        if not valid:
+            self.stream_counters["markprice"]["rejected"] += 1
+            logger.info("Validation result", stream="markprice", validation_pass=False, validation_fail_reason=reason)
+            return
+
+        self.stream_counters["markprice"]["validated"] += 1
+        logger.info("Validation result", stream="markprice", validation_pass=True, validation_fail_reason="")
+        self.gap_detector.check_gap("markprice", features["timestamp"])
+        self.mark_writer.write(features)
+        self.stream_counters["markprice"]["written"] += 1
+        self.health_monitor.record_message("markprice", features["timestamp"])
+
     def handle_reconnect(self):
         logger.info("Resetting validation and gap tracking on reconnect")
         self.validator.reset()
@@ -82,6 +185,8 @@ class CollectorApp:
 
     async def start(self):
         logger.info("Starting Collector Application")
+        logger.info("Collector WebSocket subscription", url=BINANCE_PUBLIC_WS_URL, requested_streams=self._requested_streams(BINANCE_PUBLIC_WS_URL))
+        logger.info("Collector WebSocket subscription", url=BINANCE_MARKET_WS_URL, requested_streams=self._requested_streams(BINANCE_MARKET_WS_URL))
         send_telegram_alert("Collector Application Started")
 
         self.running = True
@@ -95,7 +200,9 @@ class CollectorApp:
             )
 
         self.tasks.append(asyncio.create_task(self.health_monitor.start()))
-        self.tasks.append(asyncio.create_task(self.ws_client.start()))
+        for ws_client in self.ws_clients:
+            self.tasks.append(asyncio.create_task(ws_client.start()))
+        self.tasks.append(asyncio.create_task(self._verify_startup_streams()))
 
         try:
             await asyncio.gather(*self.tasks)
@@ -104,6 +211,20 @@ class CollectorApp:
         finally:
             self.shutdown()
 
+    async def _verify_startup_streams(self):
+        await asyncio.sleep(STREAM_INACTIVE_STARTUP_SECONDS)
+        inactive_streams = [
+            stream_name
+            for stream_name in ("orderbook", "trades", "markprice")
+            if self.health_monitor.messages_per_minute.get(stream_name, 0) == 0
+        ]
+        if inactive_streams:
+            msg = f"Startup stream inactivity after {STREAM_INACTIVE_STARTUP_SECONDS}s: {', '.join(inactive_streams)}"
+            logger.error(msg, stream_counters=self.stream_counters)
+            send_telegram_alert(msg)
+            raise RuntimeError(msg)
+        logger.info("Startup stream verification passed", stream_counters=self.stream_counters)
+
     async def _async_shutdown(self, signum: int):
         logger.info("Received signal, initiating async shutdown", signum=signum)
         self.shutdown()
@@ -115,10 +236,11 @@ class CollectorApp:
         if not self.running:
             return
 
-        logger.info("Shutting down Collector Application...")
+        logger.info("Shutting down Collector Application...", stream_counters=self.stream_counters)
         self.running = False
 
-        self.ws_client.stop()
+        for ws_client in self.ws_clients:
+            ws_client.stop()
         self.health_monitor.stop()
 
         for task in self.tasks:
```

### New routing test file

```diff
diff --git a/collector/tests/test_run_collector_routing.py b/collector/tests/test_run_collector_routing.py
new file mode 100644
index 0000000..b5fb0f1
--- /dev/null
+++ b/collector/tests/test_run_collector_routing.py
@@ -0,0 +1,97 @@
+import importlib.util
+import sys
+import time
+from pathlib import Path
+from unittest.mock import ANY, MagicMock
+
+import pytest
+
+_outer_collector = sys.modules.get("collector")
+_collector_dir = Path(__file__).resolve().parents[1]
+sys.path.insert(0, str(_collector_dir))
+sys.modules.pop("collector", None)
+_spec = importlib.util.spec_from_file_location("_run_collector_under_test", _collector_dir / "run_collector.py")
+_run_collector = importlib.util.module_from_spec(_spec)
+_spec.loader.exec_module(_run_collector)
+CollectorApp = _run_collector.CollectorApp
+if _outer_collector is not None:
+    sys.modules["collector"] = _outer_collector
+
+
+def _valid_depth_msg():
+    return {
+        "E": int(time.time() * 1000),
+        "b": [[str(100.0 - i * 0.1), "1.0"] for i in range(10)],
+        "a": [[str(101.0 + i * 0.1), "1.0"] for i in range(10)],
+    }
+
+
+def _valid_trade_msg(trade_id=123):
+    return {
+        "E": int(time.time() * 1000),
+        "a": trade_id,
+        "p": "100.5",
+        "q": "1.0",
+        "m": False,
+    }
+
+
+def _valid_mark_msg():
+    now = int(time.time() * 1000)
+    return {
+        "E": now,
+        "p": "100.5",
+        "r": "0.0001",
+        "T": now + 3600000,
+    }
+
+
+def _app_without_init():
+    app = CollectorApp.__new__(CollectorApp)
+    app.raw_messages_logged = 20
+    app.stream_counters = {
+        "orderbook": {"received": 0, "computed": 0, "empty_features": 0, "validated": 0, "rejected": 0, "written": 0},
+        "trades": {"received": 0, "computed": 0, "empty_features": 0, "validated": 0, "rejected": 0, "written": 0},
+        "markprice": {"received": 0, "computed": 0, "empty_features": 0, "validated": 0, "rejected": 0, "written": 0},
+        "unrouted": {"received": 0},
+    }
+    app.validator = MagicMock()
+    app.validator.validate_orderbook.return_value = (True, "")
+    app.validator.validate_trade.return_value = (True, "")
+    app.validator.validate_markprice.return_value = (True, "")
+    app.validator.check_failure_rate.return_value = False
+    app.validator.failures_in_window = 0
+    app.gap_detector = MagicMock()
+    app.ob_writer = MagicMock()
+    app.trades_writer = MagicMock()
+    app.mark_writer = MagicMock()
+    app.health_monitor = MagicMock()
+    app.health_monitor.messages_per_minute = {"orderbook": 0, "trades": 0, "markprice": 0}
+    return app
+
+
+def test_route_stream_matches_case_insensitive_required_streams():
+    app = CollectorApp.__new__(CollectorApp)
+
+    assert app._route_stream("btcusdt@depth10@100ms") == "orderbook"
+    assert app._route_stream("btcusdt@aggTrade") == "trades"
+    assert app._route_stream("btcusdt@aggtrade") == "trades"
+    assert app._route_stream("btcusdt@markPrice@1s") == "markprice"
+    assert app._route_stream("btcusdt@markprice@1s") == "markprice"
+    assert app._route_stream("ethusdt@aggtrade") is None
+
+
+@pytest.mark.asyncio
+async def test_handle_message_routes_lowercase_trade_and_markprice_to_health_monitor():
+    app = _app_without_init()
+
+    await app.handle_message({"stream": "btcusdt@depth10@100ms", "data": _valid_depth_msg()})
+    await app.handle_message({"stream": "btcusdt@aggtrade", "data": _valid_trade_msg()})
+    await app.handle_message({"stream": "btcusdt@markprice@1s", "data": _valid_mark_msg()})
+
+    app.health_monitor.record_message.assert_any_call("orderbook", ANY)
+    app.health_monitor.record_message.assert_any_call("trades", ANY)
+    app.health_monitor.record_message.assert_any_call("markprice", ANY)
+    assert app.stream_counters["trades"]["validated"] == 1
+    assert app.stream_counters["markprice"]["validated"] == 1
+    assert app.stream_counters["unrouted"]["received"] == 0
```

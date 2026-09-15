import asyncio
import signal
import time
import json
import os
from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from dataclasses import replace
from collector.collector.utils import logger, send_telegram_alert, validate_telegram_startup
from collector.collector.config import (
    BINANCE_MARKET_WS_URL,
    BINANCE_PUBLIC_WS_URL,
    ORDERBOOK_SCHEMA,
    TRADES_SCHEMA,
    MARKPRICE_SCHEMA,
    OPENINTEREST_SCHEMA,
    LIQUIDATION_SCHEMA,
    QUALITY_EVENTS_SCHEMA,
    BINANCE_ORDERBOOK_RAW_SCHEMA,
    BINANCE_TRADES_RAW_SCHEMA,
    SYMBOL,
)
from collector.collector.adapters.binance import BinanceAdapter
from collector.collector.book_engine import LocalBook
from collector.collector.canonical import CanonicalOrderBookEvent
from collector.collector.quality_events import BookQuality, QualityEvent, QualityEventType
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
BINANCE_DEPTH_SNAPSHOT_URL = "https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=1000"

class CollectorApp:
    def __init__(self):
        self.running = False
        self._closed = False
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
        self.binance_adapter = BinanceAdapter()
        self.binance_book = LocalBook("BINANCE")
        self._book_snapshot_lock = asyncio.Lock()
        self._recovery_task = None
        self._quality_queue = asyncio.Queue(maxsize=1024)
        self._quality_task = None
        self._quality_overflow = 0

        self.quality_writer = ParquetWriter("quality_events", QUALITY_EVENTS_SCHEMA, segment_rows=1, segment_seconds=1)
        self._quality_journal_path = self.quality_writer.stream_dir / "quality_queue.pending.json"
        # A SIGKILL cannot drain RAM.  A durable pending marker makes that
        # uncertainty visible on the next process rather than claiming zero loss.
        if self._quality_journal_path.exists():
            self._persist_quality_event({"stream":"quality_events", "event_type":QualityEventType.DATA_DROP,
                "reason":"quality_queue_unfinished_on_previous_process", "rows_lost":None})
            self._quality_journal_path.unlink(missing_ok=True)
        self.ob_writer = ParquetWriter("orderbook", ORDERBOOK_SCHEMA, quality_event_sink=self._persist_quality_event)
        # Raw reconstructed state is independently durable; legacy feature stream remains compatible.
        self.raw_book_writer = ParquetWriter("binance_orderbook_raw", BINANCE_ORDERBOOK_RAW_SCHEMA, quality_event_sink=self._persist_quality_event)
        self.trades_writer = ParquetWriter("trades", TRADES_SCHEMA)
        self.raw_trades_writer = ParquetWriter("binance_trades_raw", BINANCE_TRADES_RAW_SCHEMA)
        self.mark_writer = ParquetWriter("markprice", MARKPRICE_SCHEMA)
        self.oi_writer = ParquetWriter("openinterest", OPENINTEREST_SCHEMA)
        self.liq_writer = ParquetWriter("liquidation", LIQUIDATION_SCHEMA)

        self.ws_clients = [
            WebSocketClient(
                url=BINANCE_PUBLIC_WS_URL,
                on_message=self.handle_message,
                on_reconnect=self._make_reconnect_handler(BINANCE_PUBLIC_WS_URL),
                on_quality_event=self._websocket_quality_event, stream_group="public"
            ),
            WebSocketClient(
                url=BINANCE_MARKET_WS_URL,
                on_message=self.handle_message,
                on_reconnect=self._make_reconnect_handler(BINANCE_MARKET_WS_URL),
                on_quality_event=self._websocket_quality_event, stream_group="market"
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

    async def handle_message(self, msg: dict, local_receive_ts: int | None = None):
        # Tests and offline callers may supply decoded events directly.  The
        # WebSocket client supplies the authoritative pre-decode timestamp.
        if local_receive_ts is None:
            local_receive_ts = int(time.time() * 1000)
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
            await self._handle_binance_orderbook(msg, local_receive_ts)
        elif route == "trades":
            self._handle_binance_trade(msg, stream, local_receive_ts)
        elif route == "markprice":
            self._handle_markprice(data, stream)
        elif route == "liquidation":
            self._handle_liquidation(data, stream)

        if self.validator.check_failure_rate():
            logger.error("Validation spike detected: >0.1% failures in 60s window")
            send_telegram_alert("Validation spike detected: >0.1% failures in 60s window")

    def _websocket_quality_event(self, event_type, reason, connection_id=None, stream_group="websocket"):
        """Websocket hot path: bounded non-blocking enqueue only, never parquet I/O."""
        event={"exchange":"BINANCE", "stream":stream_group, "event_type":event_type, "reason":reason,
               "connection_id":connection_id, "local_ts":int(time.time()*1000)}
        if not hasattr(self, "_quality_queue"):
            # Legacy/offline callers have no websocket worker; this branch is not used by WebSocketClient.
            self._persist_quality_event(event)
            return
        try:
            self._quality_queue.put_nowait(event)
            self._write_quality_pending_marker()
        except asyncio.QueueFull:
            # The drop is observable in logs/counters and a later persistence task writes one aggregate marker.
            self._quality_overflow += 1
            logger.error("quality_event_queue_overflow", dropped=self._quality_overflow)

    def _write_quality_pending_marker(self):
        path = self._quality_journal_path
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump({"pending": self._quality_queue.qsize(), "updated_ms": int(time.time() * 1000)}, handle)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)

    async def _quality_persistence_loop(self):
        while self.running or not self._quality_queue.empty():
            try:
                event=await asyncio.wait_for(self._quality_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            self._persist_quality_event(event)
            self._quality_queue.task_done()
            if self._quality_queue.empty():
                self._quality_journal_path.unlink(missing_ok=True)
        if self._quality_overflow:
            self._persist_quality_event({"stream":"quality_events", "event_type":QualityEventType.DATA_DROP,
                "reason":"quality_queue_overflow", "rows_lost":self._quality_overflow})

    def _persist_quality_event(self, event: dict):
        """Persist versioned lineage. Missing values remain null rather than fabricated."""
        rows_lost = event.get("rows_lost"); event_type = event.get("event_type", QualityEventType.ERROR.value)
        if isinstance(event_type, QualityEventType): event_type = event_type.value
        local_ts = event.get("local_ts", int(time.time() * 1000))
        self.quality_writer.write({"timestamp": local_ts, "exchange": event.get("exchange", "BINANCE"),
            "stream": event.get("stream", "orderbook"), "event_type": event_type, "reason": event.get("reason", ""),
            "gap_size_ms": event.get("gap_size_ms"), "rows_lost": None if rows_lost is None else str(rows_lost),
            "quality_state": event.get("new_state", event.get("quality_state", self.binance_book.state.state.value)),
            "connection_id": event.get("connection_id"), "previous_state": event.get("previous_state"),
            "new_state": event.get("new_state"), "expected_previous_update_id": event.get("expected_previous_update_id"),
            "actual_previous_update_id": event.get("actual_previous_update_id"), "update_id": event.get("update_id"), "first_update_id": event.get("first_update_id"),
            "previous_update_id": event.get("previous_update_id"),
            "local_receive_ts": event.get("local_receive_ts"), "local_process_ts": event.get("local_process_ts", local_ts)})

    def _record_book_quality(self, kind, reason, transition=None, event=None):
        transition=transition or self.binance_book.last_transition
        event=event or getattr(transition, "event", None)
        self._persist_quality_event({"exchange":"BINANCE", "stream":"orderbook", "event_type":kind, "reason":reason,
            "local_ts":int(time.time()*1000), "previous_state":getattr(getattr(transition,"previous_state",None),"value",None),
            "new_state":getattr(getattr(transition,"new_state",None),"value",None),
            "expected_previous_update_id":getattr(transition,"expected_previous_update_id",None),
            "actual_previous_update_id":getattr(event,"previous_update_id",None), "update_id":getattr(event,"update_id",None),
            "first_update_id":getattr(event,"first_update_id",None), "previous_update_id":getattr(event,"previous_update_id",None),
            "local_receive_ts":getattr(event,"local_receive_ts",None), "local_process_ts":getattr(event,"local_process_ts",None)})

    async def _recover_binance_book(self, reason: str):
        """Fetch outside the state lock; live diffs buffer while REST is outstanding."""
        async with self._book_snapshot_lock:
            old=self.binance_book.state.state; self.binance_book.state.resync()
            self._record_book_quality(QualityEventType.RESYNC, reason)
        try:
            import aiohttp
            request_ts=int(time.time()*1000)
            timeout=aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(BINANCE_DEPTH_SNAPSHOT_URL) as response:
                    response.raise_for_status(); snapshot=await response.json()
                    receive_ts=int(time.time()*1000)
            process_ts=int(time.time()*1000)
            if not isinstance(snapshot, dict) or "lastUpdateId" not in snapshot: raise ValueError("missing_last_update_id")
            if not snapshot.get("bids") or not snapshot.get("asks"): raise ValueError("empty_snapshot")
            snapshot_event=CanonicalOrderBookEvent("BINANCE","orderbook",None,None,receive_ts, local_process_ts=process_ts,
                bids=tuple((Decimal(p),Decimal(q)) for p,q in snapshot["bids"]), asks=tuple((Decimal(p),Decimal(q)) for p,q in snapshot["asks"]),
                update_id=int(snapshot["lastUpdateId"]),is_snapshot=True,book_source="DIFF_DEPTH_RECONSTRUCTED")
            async with self._book_snapshot_lock:
                old=self.binance_book.state.state
                if not self.binance_book.binance_snapshot(snapshot_event.update_id,snapshot_event):
                    self._record_book_quality(QualityEventType.ERROR, self.binance_book.last_reason)
                    return False
                self._persist_reconstructed_books(self.binance_book.committed_recovery_events)
                self.binance_book.committed_recovery_events=[]
                self._record_book_quality(QualityEventType.RECOVERY,"snapshot_bridge_completed")
                return True
        except asyncio.TimeoutError:
            why="snapshot_timeout"
        except ValueError as exc:
            why=str(exc)
        except Exception as exc:
            why="snapshot_http_error"
            logger.error("binance_book_snapshot_failed", error=str(exc))
        async with self._book_snapshot_lock:
            old=self.binance_book.state.state; self.binance_book.state.gap()
            self._record_book_quality(QualityEventType.ERROR,why)
        return False

    async def _handle_binance_orderbook(self, raw: dict, local_receive_ts: int):
        self.stream_counters["orderbook"]["received"] += 1
        if getattr(self, "_book_snapshot_lock", None) is None: self._book_snapshot_lock = asyncio.Lock()
        if not hasattr(self, "_recovery_task"): self._recovery_task = None
        for event in self.binance_adapter.normalize(raw, local_receive_ts=local_receive_ts):
            if event.book_source != "DIFF_DEPTH_RECONSTRUCTED":
                self._record_book_quality(QualityEventType.BOOK_INVALID,"partial_depth_not_authoritative"); return
            event=replace(event,local_process_ts=int(time.time()*1000))
            async with self._book_snapshot_lock:
                before=self.binance_book.state.state; applied=self.binance_book.apply(event); after=self.binance_book.state.state
                if after == BookQuality.SEQUENCE_GAP and before != after:
                    self._record_book_quality(QualityEventType.SEQUENCE_GAP,self.binance_book.last_reason,event=event)
                needs_recovery=applied is None and after in (BookQuality.SEQUENCE_GAP, BookQuality.RECOVERING)
                if self.binance_book.duplicate_count:
                    # bounded: one durable counter event per observed duplicate; reset after recording.
                    self._record_book_quality(QualityEventType.DUPLICATE,"binance_duplicate_update",event=event); self.binance_book.duplicate_count=0
            if needs_recovery:
                if self._recovery_task is None or self._recovery_task.done(): self._recovery_task=asyncio.create_task(self._recover_binance_book("sequence_gap_or_initial_snapshot"))
                return
            if applied is None: return
            self._persist_reconstructed_books([(applied, "NORMAL_INCREMENTAL", self.binance_book.recovery_generation)])
            data={"E":applied.exchange_event_ts,"b":[[str(p),str(q)] for p,q in applied.bids],"a":[[str(p),str(q)] for p,q in applied.asks]}
            features=compute_orderbook_features(data)
            if not features: self.stream_counters["orderbook"]["empty_features"] += 1; return
            features["timestamp"]=applied.local_process_ts; features["local_timestamp"]=applied.local_receive_ts; features["exchange_timestamp"]=applied.exchange_event_ts
            self._write_orderbook_features(features)

    def _persist_reconstructed_books(self, rows):
        """Raw reconstructed book is exact and independent of lossy features."""
        raw_writer=getattr(self, "raw_book_writer", None)
        if raw_writer is None:
            return
        for applied, event_kind, generation in rows:
            raw_writer.write({"timestamp":applied.local_process_ts,"exchange_timestamp":applied.exchange_event_ts,
                "local_receive_ts":applied.local_receive_ts,"local_process_ts":applied.local_process_ts,
                "bids":[[str(p),str(q)] for p,q in applied.bids],"asks":[[str(p),str(q)] for p,q in applied.asks],"update_id":applied.update_id,
                "first_update_id":applied.first_update_id,"previous_update_id":applied.previous_update_id,
                "book_source":applied.book_source,"event_kind":event_kind,"recovery_generation":generation,"quality_state":applied.quality_state})

    def _write_orderbook_features(self, features: dict):
        self.stream_counters["orderbook"]["computed"] += 1
        valid, reason = self.validator.validate_orderbook(features)
        if not valid:
            self.stream_counters["orderbook"]["rejected"] += 1
            self._record_validation_rejection("orderbook", reason)
            return
        self.stream_counters["orderbook"]["validated"] += 1
        self.gap_detector.check_gap("orderbook", features["exchange_timestamp"])
        self.ob_writer.write(features)
        self.stream_counters["orderbook"]["written"] += 1
        self.health_monitor.record_message("orderbook", features["local_timestamp"])

    @staticmethod
    def _lossless_legacy_trade_id(value):
        """Adapt a native ID only when the legacy int64 column can represent it exactly."""
        if value is None:
            raise ValueError("missing_native_trade_id")
        if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
            raise ValueError("non_numeric_native_trade_id")
        numeric = int(value)
        if numeric > 2**63 - 1 or str(numeric) != value:
            raise ValueError("non_lossless_native_trade_id")
        return numeric

    def _handle_binance_trade(self, raw: dict, stream: str, local_receive_ts: int):
        self.stream_counters["trades"]["received"] += 1
        events = self.binance_adapter.normalize(raw, local_receive_ts=local_receive_ts)
        for event in events:
            # Legacy parquet schema is int64. Preserve canonical IDs only when
            # conversion is exact; otherwise reject visibly instead of truncating.
            try: trade_id = self._lossless_legacy_trade_id(event.trade_id)
            except (TypeError, ValueError): trade_id = None
            process_ts = int(time.time() * 1000)
            raw_trade_writer=getattr(self, "raw_trades_writer", None)
            if raw_trade_writer is not None:
                raw_trade_writer.write({"timestamp":process_ts, "local_receive_ts":event.local_receive_ts,
                    "exchange_timestamp":event.exchange_transaction_ts or event.exchange_event_ts, "trade_id":trade_id,
                    "native_trade_id":event.trade_id, "price":event.price, "quantity":event.quantity})
            if trade_id is None:
                # Raw native record is durable first; old int64 feature stream
                # cannot faithfully represent this identifier.
                self.stream_counters["trades"]["rejected"] += 1
                self._record_validation_rejection("trades", "legacy_trade_id_not_lossless")
                self._persist_quality_event({"stream":"trades", "event_type":QualityEventType.ERROR, "reason":"legacy_trade_id_not_lossless"})
                continue
            features = {
                "timestamp": process_ts, "local_timestamp": event.local_receive_ts,
                "exchange_timestamp": event.exchange_transaction_ts or event.exchange_event_ts,
                "trade_id": trade_id, "price": event.price, "quantity": event.quantity,
                "is_buyer_maker": event.side == "SELL",
                "side_sign": -1 if event.side == "SELL" else 1,
                "signed_qty": -event.quantity if event.side == "SELL" else event.quantity,
            }
            self._handle_trade_features(features)

    def _handle_trade_features(self, features: dict):
        self.stream_counters["trades"]["computed"] += 1
        valid, reason = self.validator.validate_trade(features)
        if not valid:
            self.stream_counters["trades"]["rejected"] += 1
            self._record_validation_rejection("trades", reason)
            return
        self.stream_counters["trades"]["validated"] += 1
        self.gap_detector.check_gap("trades", features["exchange_timestamp"])
        self.trades_writer.write(features)
        self.stream_counters["trades"]["written"] += 1
        self.health_monitor.record_message("trades", features["local_timestamp"])

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
            valid, reason = self.validator.validate_liquidation(features)
            if not valid:
                self.stream_counters["liquidation"]["rejected"] += 1
                self._record_validation_rejection("liquidation", reason)
                logger.info("Validation result", stream="liquidation", validation_pass=False, validation_fail_reason=reason)
                return

            self.stream_counters["liquidation"]["validated"] += 1
            logger.info("Validation result", stream="liquidation", validation_pass=True, validation_fail_reason="")
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
                    if route == "orderbook":
                        if getattr(self, "binance_book", None) is None:
                            self.binance_book = LocalBook("BINANCE")
                        self.binance_book.invalidate("websocket_reconnect")

        return handler

    async def start(self):
        logger.info("Starting Collector Application")
        logger.info("Collector WebSocket subscription", url=BINANCE_PUBLIC_WS_URL, requested_streams=self._requested_streams(BINANCE_PUBLIC_WS_URL))
        logger.info("Collector WebSocket subscription", url=BINANCE_MARKET_WS_URL, requested_streams=self._requested_streams(BINANCE_MARKET_WS_URL))
        send_telegram_alert("Collector Application Started")

        self.running = True
        self._quality_task = asyncio.create_task(self._quality_persistence_loop())

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

        await self._recover_binance_book("startup")
        self.tasks.append(asyncio.create_task(self.health_monitor.start()))
        self.tasks.append(asyncio.create_task(self._poll_openinterest()))
        self.tasks.append(asyncio.create_task(self._verify_startup_streams()))

        try:
            await asyncio.gather(*self.tasks)
        except asyncio.CancelledError:
            pass
        finally:
            if self._recovery_task is not None and not self._recovery_task.done():
                self._recovery_task.cancel()
                await asyncio.gather(self._recovery_task, return_exceptions=True)
            await self._quality_queue.join()
            self.running = False
            if self._quality_task is not None:
                await self._quality_task
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
                    self.gap_detector.check_gap("openinterest", features["exchange_timestamp"])
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
        self.running = False
        if self._recovery_task is not None and not self._recovery_task.done():
            self._recovery_task.cancel()
            await asyncio.gather(self._recovery_task, return_exceptions=True)
        if self._quality_task is not None:
            await self._quality_task
        self.shutdown()
        for task in self.tasks:
            task.cancel()

    def shutdown(self):
        if self._closed:
            return
        self._closed = True

        logger.info("Shutting down Collector Application...", stream_counters=self.stream_counters, validation_fail_reasons=self.validation_fail_reasons)
        self.running = False

        for ws_client in self.ws_clients:
            ws_client.stop()
        self.health_monitor.stop()

        for task in self.tasks:
            task.cancel()
        if self._recovery_task is not None and not self._recovery_task.done():
            self._recovery_task.cancel()

        self.ob_writer.close()
        self.raw_book_writer.close()
        self.trades_writer.close()
        self.raw_trades_writer.close()
        self.mark_writer.close()
        self.oi_writer.close()
        self.liq_writer.close()
        self.quality_writer.close()

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

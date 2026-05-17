# l2_pipeline.py

import json
import asyncio
import websockets
import time
import os
import logging as _logging
from typing import Any, Dict, List

DATA_FILE = "l2_replay_data.json"

orderbook: Dict[str, List[List[float]]] = {"bids": [], "asks": []}
trades_buffer: List[Dict[str, Any]] = []

# One lock for consistent snapshots
lock = asyncio.Lock()

# Ensure file exists
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        pass

# ============================================================
# AUDIT FIX ISSUE-F: stale-feed detection and reconnect telemetry
# ============================================================
_l2_logger = _logging.getLogger("l2_pipeline")
_last_depth_msg_ts: float = 0.0
_last_trade_msg_ts: float = 0.0
_STALE_FEED_THRESHOLD_SECONDS: float = 30.0


# ================================
# ORDER BOOK STREAM
# ================================
async def handle_depth():
    global _last_depth_msg_ts
    url = "wss://stream.binance.com:9443/ws/btcusdt@depth20@100ms"
    reconnect_count = 0
    backoff = 1.0  # seconds

    while True:
        try:
            async with websockets.connect(url) as ws:
                _l2_logger.info(
                    "[L2_PIPELINE] depth stream connected (reconnect_count=%d)", reconnect_count
                )
                backoff = 1.0  # reset on successful connect
                reconnect_count = 0

                async for msg in ws:
                    data = json.loads(msg)
                    bids = [[float(p), float(q)] for p, q in data.get("bids", [])]
                    asks = [[float(p), float(q)] for p, q in data.get("asks", [])]

                    async with lock:
                        orderbook["bids"] = bids
                        orderbook["asks"] = asks
                        _last_depth_msg_ts = time.time()  # AUDIT FIX ISSUE-F

        except Exception as e:
            reconnect_count += 1
            _l2_logger.error(
                "[L2_PIPELINE] depth stream error (attempt=%d backoff=%.1fs): %s",
                reconnect_count, backoff, e,
            )
            # Stale-feed detection: emit metric on reconnect
            stale_duration = time.time() - _last_depth_msg_ts
            if _last_depth_msg_ts > 0 and stale_duration > _STALE_FEED_THRESHOLD_SECONDS:
                _l2_logger.warning(
                    "[L2_PIPELINE][STALE_FEED] depth feed silent for %.1f seconds. "
                    "OFI features may be stale. Reconnecting.",
                    stale_duration,
                )
            await asyncio.sleep(min(backoff, 60.0))
            backoff = min(backoff * 2.0, 60.0)  # exponential back-off, capped at 60s


# ================================
# TRADE STREAM
# ================================
async def handle_trades():
    global _last_trade_msg_ts
    url = "wss://stream.binance.com:9443/ws/btcusdt@trade"
    reconnect_count = 0
    backoff = 1.0  # seconds

    while True:
        try:
            async with websockets.connect(url) as ws:
                _l2_logger.info(
                    "[L2_PIPELINE] trade stream connected (reconnect_count=%d)", reconnect_count
                )
                backoff = 1.0  # reset on successful connect
                reconnect_count = 0

                async for msg in ws:
                    data = json.loads(msg)
                    trade = {
                        "price": float(data["p"]),
                        "size": float(data["q"]),
                        "side": "buy" if not data["m"] else "sell",
                        "timestamp": data["T"],
                    }

                    async with lock:
                        trades_buffer.append(trade)
                        if len(trades_buffer) > 200:
                            trades_buffer.pop(0)
                        _last_trade_msg_ts = time.time()  # AUDIT FIX ISSUE-F

        except Exception as e:
            reconnect_count += 1
            _l2_logger.error(
                "[L2_PIPELINE] trade stream error (attempt=%d backoff=%.1fs): %s",
                reconnect_count, backoff, e,
            )
            stale_duration = time.time() - _last_trade_msg_ts
            if _last_trade_msg_ts > 0 and stale_duration > _STALE_FEED_THRESHOLD_SECONDS:
                _l2_logger.warning(
                    "[L2_PIPELINE][STALE_FEED] trade feed silent for %.1f seconds. "
                    "OFI features may be stale. Reconnecting.",
                    stale_duration,
                )
            await asyncio.sleep(min(backoff, 60.0))
            backoff = min(backoff * 2.0, 60.0)  # exponential back-off, capped at 60s


# ================================
# STALE-FEED WATCHDOG — AUDIT FIX ISSUE-F
# ================================
async def stale_feed_watchdog():
    """Emit periodic stale-feed alerts when streams are silent."""
    while True:
        await asyncio.sleep(10.0)
        now = time.time()
        for name, last_ts in [("depth", _last_depth_msg_ts), ("trades", _last_trade_msg_ts)]:
            if last_ts > 0:
                silence = now - last_ts
                if silence > _STALE_FEED_THRESHOLD_SECONDS:
                    _l2_logger.warning(
                        "[L2_PIPELINE][STALE_FEED] %s feed silent for %.1fs — "
                        "OFI features will be stale until reconnect.",
                        name, silence,
                    )


# ================================
# WRITER (FIXED)
# ================================
async def writer():
    while True:
        try:
            async with lock:
                has_bids = bool(orderbook["bids"])
                has_asks = bool(orderbook["asks"])
                has_trades = bool(trades_buffer)

            # Skip empty startup snapshots until all streams are populated
            if has_bids and has_asks and has_trades:
                async with lock:
                    snapshot = {
                        "local_timestamp": time.time(),
                        "price": trades_buffer[-1]["price"],
                        "orderbook": {
                            "bids": orderbook["bids"][:],
                            "asks": orderbook["asks"][:]
                        },
                        "trades": trades_buffer[-50:]
                    }

                with open(DATA_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(snapshot) + "\n")

                _l2_logger.debug("[L2_PIPELINE] Snapshot written")

            await asyncio.sleep(1)

        except Exception as e:
            _l2_logger.error("[L2_PIPELINE] Writer error: %s", e)
            await asyncio.sleep(2)


# ================================
# MAIN
# ================================
async def main():
    await asyncio.gather(
        handle_depth(),
        handle_trades(),
        writer(),
        stale_feed_watchdog(),  # AUDIT FIX ISSUE-F
    )


if __name__ == "__main__":
    _l2_logger.info("[L2_PIPELINE] Starting L2 data pipeline...")
    asyncio.run(main())

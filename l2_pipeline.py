# l2_pipeline.py

import json
import asyncio
try:
    import websockets
except Exception:  # pragma: no cover - helper import path for tooling/tests
    websockets = None
import time
import os
from typing import Any, Dict, List


def align_book_to_bars(bars, book) -> List[Dict[str, Any]]:
    bar_rows = [b for b in (bars or []) if isinstance(b, (list, tuple)) and len(b) >= 1]
    book_rows = [s for s in (book or []) if isinstance(s, dict) and "timestamp" in s]
    if not bar_rows:
        return []
    if not book_rows:
        raise ValueError("book is empty; cannot align to bars")
    aligned: List[Dict[str, Any]] = []
    j = 0
    last = None
    for bar in bar_rows:
        bar_ts = int(bar[0])
        while j < len(book_rows) and int(book_rows[j].get("timestamp", -1)) <= bar_ts:
            last = book_rows[j]
            j += 1
        if last is None:
            raise ValueError(f"no book snapshot available at or before bar timestamp {bar_ts}")
        aligned.append(last)
    return aligned

DATA_FILE = "l2_replay_data.json"

orderbook: Dict[str, List[List[float]]] = {"bids": [], "asks": []}
trades_buffer: List[Dict[str, Any]] = []

# One lock for consistent snapshots
lock = asyncio.Lock()

# Ensure file exists
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        pass


# ================================
# ORDER BOOK STREAM
# ================================
async def handle_depth():
    url = "wss://stream.binance.com:9443/ws/btcusdt@depth20@100ms"

    while True:
        try:
            async with websockets.connect(url) as ws:
                print("✅ Connected to depth stream")

                async for msg in ws:
                    data = json.loads(msg)

                    bids = [[float(p), float(q)] for p, q in data.get("bids", [])]
                    asks = [[float(p), float(q)] for p, q in data.get("asks", [])]

                    async with lock:
                        orderbook["bids"] = bids
                        orderbook["asks"] = asks

        except Exception as e:
            print(f"❌ Depth stream error: {e}")
            await asyncio.sleep(2)


# ================================
# TRADE STREAM
# ================================
async def handle_trades():
    url = "wss://stream.binance.com:9443/ws/btcusdt@trade"

    while True:
        try:
            async with websockets.connect(url) as ws:
                print("✅ Connected to trade stream")

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

        except Exception as e:
            print(f"❌ Trade stream error: {e}")
            await asyncio.sleep(2)


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

                print("📦 Snapshot written")

            await asyncio.sleep(1)

        except Exception as e:
            print(f"❌ Writer error: {e}")
            await asyncio.sleep(2)


# ================================
# MAIN
# ================================
async def main():
    await asyncio.gather(
        handle_depth(),
        handle_trades(),
        writer()
    )


if __name__ == "__main__":
    print("🚀 Starting L2 data pipeline...")
    asyncio.run(main())

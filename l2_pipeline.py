# l2_pipeline.py

import json
import asyncio
import websockets
import time
import os

DATA_FILE = "l2_replay_data.json"

orderbook = {"bids": [], "asks": []}
trades_buffer = []

# Ensure file exists
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w") as f:
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

                    orderbook["bids"] = [
                        [float(p), float(q)] for p, q in data.get("bids", [])
                    ]
                    orderbook["asks"] = [
                        [float(p), float(q)] for p, q in data.get("asks", [])
                    ]

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

                    trades_buffer.append({
                        "price": float(data["p"]),
                        "size": float(data["q"]),
                        "side": "buy" if not data["m"] else "sell",
                        "timestamp": data["T"]
                    })

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
            snapshot = {
                "local_timestamp": time.time(),
                "price": trades_buffer[-1]["price"] if trades_buffer else None,
                "orderbook": {
                    "bids": orderbook["bids"][:],
                    "asks": orderbook["asks"][:]
                },
                "trades": trades_buffer[-50:]
            }

            with open(DATA_FILE, "a") as f:
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

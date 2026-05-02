import csv
import json
from collections import defaultdict

TRADE_FILE = "aggTrades.csv"
DEPTH_FILE = "bookDepth.csv"
OUTPUT_FILE = "l2_backtest_ready.json"
TIME_BUCKET_MS = 1000

trades_by_bucket = defaultdict(list)

with open(TRADE_FILE, "r") as f:
    reader = csv.DictReader(f)
    for row in reader:
        ts = int(row["T"])
        bucket = ts // TIME_BUCKET_MS

        trades_by_bucket[bucket].append({
            "price": float(row["p"]),
            "qty": float(row["q"]),
            "timestamp": ts
        })

snapshots = {}

with open(DEPTH_FILE, "r") as f:
    reader = csv.DictReader(f)
    for row in reader:
        ts = int(row["timestamp"])
        bucket = ts // TIME_BUCKET_MS

        bid_price = float(row["bidPrice"])
        bid_qty = float(row["bidQty"])
        ask_price = float(row["askPrice"])
        ask_qty = float(row["askQty"])

        snapshots[bucket] = {
            "timestamp": ts,
            "bids": [[bid_price, bid_qty]],
            "asks": [[ask_price, ask_qty]],
        }

output = []

for bucket in sorted(snapshots.keys()):
    snapshot = snapshots[bucket]
    trades = trades_by_bucket.get(bucket, [])

    output.append({
        "snapshot": snapshot,
        "trades": trades
    })

with open(OUTPUT_FILE, "w") as f:
    json.dump(output, f)

print(f"Saved {len(output)} rows to {OUTPUT_FILE}")

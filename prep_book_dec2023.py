#!/usr/bin/env python3
"""Pre-aggregate bookTicker_dec2023.csv to one snapshot per 30s bucket."""
import time, pandas as pd, numpy as np

SRC = "data/bookTicker_dec2023.csv"
DST = "data/bookTicker_dec2023_30s.csv"
BUCKET_MS = 30_000

t0 = time.time()
parts = []
total_raw = 0
for chunk in pd.read_csv(SRC, usecols=["transaction_time","best_bid_price","best_bid_qty","best_ask_price","best_ask_qty"], chunksize=2_000_000):
    total_raw += len(chunk)
    chunk["bucket"] = (chunk["transaction_time"] // BUCKET_MS).astype(np.int64)
    idx = chunk.groupby("bucket")["transaction_time"].idxmax()
    parts.append(chunk.loc[idx])
    print(f"  chunk done: total_raw={total_raw:,}  elapsed={time.time()-t0:.1f}s")

big = pd.concat(parts, ignore_index=True)
big["bucket"] = (big["transaction_time"] // BUCKET_MS).astype(np.int64)
final_idx = big.groupby("bucket")["transaction_time"].idxmax()
final = big.loc[final_idx].sort_values("transaction_time").drop(columns=["bucket"])
final.to_csv(DST, index=False)
print(f"DONE  raw_rows={total_raw:,}  downsampled={len(final):,}  total_elapsed={time.time()-t0:.1f}s")
print(f"  wrote {DST}")

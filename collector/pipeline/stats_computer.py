import os
import json
import glob
import pandas as pd
import numpy as np

def compute_stats(data_dir: str = "data"):
    aligned_files = glob.glob(os.path.join(data_dir, "aligned", "*.parquet"))

    if not aligned_files:
        print("No aligned data found.")
        return

    print(f"Computing stats from {len(aligned_files)} aligned files...")

    # We load them all to compute global stats (can be memory intensive, for real production might need online stats)
    dfs = []
    for f in aligned_files:
        dfs.append(pd.read_parquet(f))

    df = pd.concat(dfs, ignore_index=True)

    exclude_cols = ["timestamp", "orderbook_gap", "markprice_gap"]
    cols_to_stat = [c for c in df.columns if c not in exclude_cols]

    stats = {}
    for col in cols_to_stat:
        s = df[col].dropna()
        stats[col] = {
            "mean": float(s.mean()),
            "std": float(s.std()),
            "min": float(s.min()),
            "max": float(s.max()),
            "p01": float(s.quantile(0.01)),
            "p99": float(s.quantile(0.99)),
            "count": int(s.count()),
            "null_count": int(df[col].isna().sum())
        }

    out_dir = os.path.join(data_dir, "stats")
    os.makedirs(out_dir, exist_ok=True)

    out_file = os.path.join(out_dir, "aligned_stats.json")
    with open(out_file, "w") as f:
        json.dump(stats, f, indent=2)

    print(f"Saved stats to {out_file}")

if __name__ == "__main__":
    compute_stats()

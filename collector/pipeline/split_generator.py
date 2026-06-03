import os
import json
import glob
import pandas as pd
from typing import List

def generate_splits(data_dir: str = "data"):
    labeled_dir = os.path.join(data_dir, "aligned", "labeled")
    files = sorted(glob.glob(os.path.join(labeled_dir, "*.parquet")))

    if not files:
        print("No labeled files found.")
        return

    dates = [os.path.basename(f).replace(".parquet", "") for f in files]
    print(f"Found {len(dates)} dates to split.")

    n = len(dates)
    train_end = int(n * 0.7)
    val_end = int(n * 0.85)

    train_dates = dates[:train_end]
    val_dates = dates[train_end:val_end]
    test_dates = dates[val_end:]

    manifest = {
        "train": train_dates,
        "val": val_dates,
        "test": test_dates
    }

    out_dir = os.path.join(data_dir, "splits")
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(out_dir, "split_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print("Saved split_manifest.json")

    # Combine datasets
    for split_name, split_dates in manifest.items():
        if not split_dates:
            continue

        print(f"Building {split_name}.parquet...")
        dfs = []
        for d in split_dates:
            dfs.append(pd.read_parquet(os.path.join(labeled_dir, f"{d}.parquet")))

        df_split = pd.concat(dfs, ignore_index=True)
        df_split.to_parquet(os.path.join(out_dir, f"{split_name}.parquet"), compression="snappy")
        print(f"Saved {split_name}.parquet")

if __name__ == "__main__":
    generate_splits()

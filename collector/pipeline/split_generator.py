import os
import json
import glob
import pandas as pd
from typing import List

def generate_splits(data_dir: str = "data", embargo_days: int = 1):
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

    if embargo_days < 0:
        raise ValueError("embargo_days must be non-negative")

    if embargo_days == 0:
        train_dates = dates[:train_end]
        val_dates = dates[train_end:val_end]
        test_dates = dates[val_end:]
    else:
        # Embargo: remove last embargo_days from train, first embargo_days from val.
        train_dates = dates[:train_end - embargo_days] if train_end > embargo_days else dates[:train_end]
        val_dates_raw = dates[train_end:val_end]
        val_dates = val_dates_raw[embargo_days:] if len(val_dates_raw) > embargo_days else val_dates_raw

        # Same for val/test boundary: remove last embargo_days from val and first embargo_days from test.
        val_dates = val_dates[:-embargo_days] if len(val_dates) > embargo_days else val_dates
        test_dates_raw = dates[val_end:]
        test_dates = test_dates_raw[embargo_days:] if len(test_dates_raw) > embargo_days else test_dates_raw

    manifest = {
        "train": train_dates,
        "val": val_dates,
        "test": test_dates,
        "embargo_days": embargo_days,
    }

    out_dir = os.path.join(data_dir, "splits")
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(out_dir, "split_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print("Saved split_manifest.json")
    print(f"Embargo: {embargo_days} day(s) between splits")
    print(f"Train: {len(train_dates)} days, Val: {len(val_dates)} days, Test: {len(test_dates)} days")

    # Combine datasets
    for split_name in ("train", "val", "test"):
        split_dates = manifest[split_name]
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

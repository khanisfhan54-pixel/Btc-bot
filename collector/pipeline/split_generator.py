import os
import glob
import json
import pandas as pd
import pyarrow.parquet as pq
from collector import config
from collector.utils import get_logger

logger = get_logger("split_generator")

def generate_splits():
    labeled_dir = os.path.join(config.ALIGNED_DIR, "labeled")
    files = sorted(glob.glob(os.path.join(labeled_dir, "*.parquet")))

    if not files:
        logger.error("no_labeled_files_found")
        return

    dates = [os.path.basename(f).replace(".parquet", "") for f in files]

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

    os.makedirs(config.SPLITS_DIR, exist_ok=True)

    # Save manifest
    with open(os.path.join(config.SPLITS_DIR, "split_manifest.json"), 'w') as f:
        json.dump(manifest, f, indent=2)

    logger.info("manifest_saved", train=len(train_dates), val=len(val_dates), test=len(test_dates))

    # Concatenate files per split
    for split_name, split_dates in manifest.items():
        if not split_dates:
            continue

        logger.info("creating_split_file", split=split_name)
        split_files = [os.path.join(labeled_dir, f"{d}.parquet") for d in split_dates]

        df = pd.concat([pq.read_table(f).to_pandas() for f in split_files])
        out_path = os.path.join(config.SPLITS_DIR, f"{split_name}.parquet")
        df.to_parquet(out_path, compression='snappy')

        logger.info("split_file_saved", split=split_name, path=out_path, rows=len(df))

if __name__ == "__main__":
    generate_splits()

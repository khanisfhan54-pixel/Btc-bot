import os
import glob
import pandas as pd
import numpy as np
from collector import config
from collector.utils import get_logger

logger = get_logger("label_generator")

def generate_labels(date_str: str, grid_ms: int = 100, threshold_bps: float = 0.5):
    in_path = os.path.join(config.ALIGNED_DIR, f"{date_str}.parquet")
    if not os.path.exists(in_path):
        logger.error("file_not_found", path=in_path)
        return

    df = pd.read_parquet(in_path)

    # Calculate steps based on grid
    steps_1s = 1000 // grid_ms
    steps_5s = 5000 // grid_ms
    steps_15s = 15000 // grid_ms
    steps_60s = 60000 // grid_ms
    steps_300s = 300000 // grid_ms

    threshold_ratio = threshold_bps / 10000.0

    horizons = {
        "1s": steps_1s,
        "5s": steps_5s,
        "15s": steps_15s,
        "60s": steps_60s,
        "300s": steps_300s
    }

    for name, shift in horizons.items():
        # Future mid price
        future_mid = df['mid_price'].shift(-shift)

        # Calculate return
        ret_col = f"return_{name}"
        df[ret_col] = (future_mid - df['mid_price']) / df['mid_price']

        # Direction classes
        dir_col = f"direction_{name}"
        df[dir_col] = 0
        df.loc[df[ret_col] > threshold_ratio, dir_col] = 1
        df.loc[df[ret_col] < -threshold_ratio, dir_col] = -1
        df[dir_col] = df[dir_col].astype(np.int8)

    # Save labeled dataset
    out_dir = os.path.join(config.ALIGNED_DIR, "labeled")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{date_str}.parquet")

    df.to_parquet(out_path, compression='snappy')
    logger.info("labels_generated", date=date_str, path=out_path)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        generate_labels(sys.argv[1])
    else:
        # Run for all
        files = glob.glob(os.path.join(config.ALIGNED_DIR, "*.parquet"))
        for f in files:
            date_str = os.path.basename(f).replace(".parquet", "")
            generate_labels(date_str)

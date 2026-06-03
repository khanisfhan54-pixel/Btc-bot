import os
import glob
import pandas as pd
import numpy as np

def generate_labels(date_str: str, grid_ms: int = 100, threshold: float = 0.00005, data_dir: str = "data"):
    print(f"Generating labels for {date_str}...")

    in_file = os.path.join(data_dir, "aligned", f"{date_str}.parquet")
    if not os.path.exists(in_file):
        print(f"Input file not found: {in_file}")
        return

    df = pd.read_parquet(in_file)

    # Calculate shifts for different horizons
    horizons_s = [1, 5, 15, 60, 300]

    for h in horizons_s:
        shift_rows = int((h * 1000) / grid_ms)

        # Future mid price
        future_mid = df["mid_price"].shift(-shift_rows)

        # Return
        ret_col = f"return_{h}s"
        df[ret_col] = (future_mid - df["mid_price"]) / df["mid_price"]

        # Direction
        dir_col = f"direction_{h}s"
        df[dir_col] = 0
        df.loc[df[ret_col] > threshold, dir_col] = 1
        df.loc[df[ret_col] < -threshold, dir_col] = -1
        df[dir_col] = df[dir_col].astype(np.int8)

    out_dir = os.path.join(data_dir, "aligned", "labeled")
    os.makedirs(out_dir, exist_ok=True)

    out_file = os.path.join(out_dir, f"{date_str}.parquet")
    df.to_parquet(out_file, compression="snappy")
    print(f"Saved labeled data to {out_file}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        generate_labels(sys.argv[1])
    else:
        print("Usage: python label_generator.py YYYY-MM-DD")

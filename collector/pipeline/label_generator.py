import os
import glob
import pandas as pd
import numpy as np
from typing import List, Tuple

def _generate_label_columns(df: pd.DataFrame, grid_ms: int, threshold: float, horizons_s: List[int]) -> Tuple[pd.DataFrame, int]:
    # Calculate shifts for different horizons
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

    label_cols = [f"return_{h}s" for h in horizons_s] + [f"direction_{h}s" for h in horizons_s]
    rows_before = len(df)
    df = df.dropna(subset=label_cols).reset_index(drop=True)
    rows_dropped = rows_before - len(df)
    drop_pct = rows_dropped / rows_before * 100 if rows_before else 0
    print(f"Dropped {rows_dropped} rows with NaN labels ({drop_pct:.2f}%)")

    return df, rows_dropped


def generate_labels(date_str: str, grid_ms: int = 100, threshold: float = 0.00005, data_dir: str = "data"):
    print(f"Generating labels for {date_str}...")

    in_file = os.path.join(data_dir, "aligned", f"{date_str}.parquet")
    if not os.path.exists(in_file):
        print(f"Input file not found: {in_file}")
        return

    df = pd.read_parquet(in_file)

    horizons_s = [1, 5, 15, 60, 300]
    df, _ = _generate_label_columns(df, grid_ms, threshold, horizons_s)

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

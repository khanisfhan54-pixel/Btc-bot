import os
import glob
import pandas as pd
import pyarrow.parquet as pq
from datetime import datetime, timedelta
from collector import config

def verify_dataset(date_start: str, date_end: str):
    start = datetime.strptime(date_start, "%Y-%m-%d")
    end = datetime.strptime(date_end, "%Y-%m-%d")

    current = start
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        print(f"\nVerifying {date_str}...")

        for stream in ["orderbook", "trades", "markprice"]:
            files = sorted(glob.glob(os.path.join(config.RAW_DIR, stream, f"{date_str}-*.parquet")))

            if len(files) != 24:
                print(f"  [WARN] {stream}: Expected 24 files, found {len(files)}")

            for f in files:
                try:
                    table = pq.read_table(f)
                    df = table.to_pandas()

                    # Schema metadata
                    meta = table.schema.metadata
                    if not meta or b"schema_version" not in meta:
                        print(f"  [FAIL] {os.path.basename(f)}: Missing schema version metadata")

                    # Monotonicity
                    if not df['exchange_timestamp'].is_monotonic_increasing:
                        print(f"  [FAIL] {os.path.basename(f)}: Timestamps are not monotonically increasing")

                    # NaN check (basic)
                    if stream == "orderbook":
                        if df[['best_bid', 'best_ask', 'mid_price']].isna().any().any():
                            print(f"  [FAIL] {os.path.basename(f)}: NaNs found in critical columns")

                except Exception as e:
                    print(f"  [ERROR] Failed to read {os.path.basename(f)}: {str(e)}")

        current += timedelta(days=1)
    print("Verification complete.")

if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3:
        verify_dataset(sys.argv[1], sys.argv[2])
    else:
        print("Usage: python verify_dataset.py YYYY-MM-DD YYYY-MM-DD")

import os
import glob
import pandas as pd
from collector import config

def replay_day(date_str: str):
    path = os.path.join(config.ALIGNED_DIR, "labeled", f"{date_str}.parquet")
    if not os.path.exists(path):
        path = os.path.join(config.ALIGNED_DIR, f"{date_str}.parquet")

    if not os.path.exists(path):
        print(f"Error: No aligned or labeled data found for {date_str}")
        return

    print(f"Replaying {path}")
    df = pd.read_parquet(path)

    # Validation checks

    # 1. Nulls
    core_cols = ['best_bid', 'best_ask', 'mid_price', 'mark_price']
    for col in core_cols:
        null_count = df[col].isna().sum()
        if null_count > 0:
            print(f"  [FAIL] {col} contains {null_count} NaNs")

    # 2. Values
    if not df['obi'].between(-1, 1).all():
        print("  [FAIL] OBI is outside [-1, 1]")

    if (df['spread_bps'] <= 0).any():
        print(f"  [FAIL] Found {sum(df['spread_bps'] <= 0)} rows with spread <= 0")

    # 3. Jumps
    mid_jumps = df['mid_price'].pct_change().abs()
    large_jumps = mid_jumps > 0.02
    if large_jumps.any():
        print(f"  [FAIL] Found {large_jumps.sum()} mid_price jumps > 2%")

    print("\nSummary Statistics (First 5 rows):")
    print(df[['timestamp', 'mid_price', 'spread_bps', 'obi', 'trade_count']].head())

if __name__ == "__main__":
    import sys
    if len(sys.argv) == 2:
        replay_day(sys.argv[1])
    else:
        print("Usage: python replay_test.py YYYY-MM-DD")

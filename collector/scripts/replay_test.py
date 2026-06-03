import os
import sys
import pandas as pd

def replay_test(date_str: str, data_dir: str = "data"):
    print(f"Running replay test for {date_str}...")

    aligned_file = os.path.join(data_dir, "aligned", f"{date_str}.parquet")
    labeled_file = os.path.join(data_dir, "aligned", "labeled", f"{date_str}.parquet")

    file_to_load = labeled_file if os.path.exists(labeled_file) else aligned_file

    if not os.path.exists(file_to_load):
        print(f"File not found: {file_to_load}")
        return

    print(f"Loading {file_to_load}")
    df = pd.read_parquet(file_to_load)

    print(f"Rows: {len(df)}")
    print(f"Columns: {len(df.columns)}")

    passed = True

    # Check NaNs
    if df[["best_bid", "best_ask", "mark_price"]].isna().any().any():
        print("[FAIL] NaNs found in orderbook/markprice columns")
        passed = False
    else:
        print("[PASS] No NaNs in orderbook/markprice columns")

    # Check OBI bounds
    if (df["obi"] < -1.0).any() or (df["obi"] > 1.0).any():
        print("[FAIL] OBI out of bounds [-1, 1]")
        passed = False
    else:
        print("[PASS] OBI in bounds")

    # Check spread
    if (df["spread_bps"] <= 0).any():
        print("[FAIL] spread_bps <= 0 found")
        passed = False
    else:
        print("[PASS] spread_bps > 0")

    # Check mid price stability
    pct_change = df["mid_price"].pct_change().abs()
    if (pct_change > 0.02).any():
        print("[FAIL] mid_price jumped > 2%")
        passed = False
    else:
        print("[PASS] mid_price is stable (no jumps > 2%)")

    if os.path.exists(labeled_file):
        if "return_1s" in df.columns and "direction_1s" in df.columns:
            print("[PASS] Label columns present")
        else:
            print("[FAIL] Label columns missing")
            passed = False

    print("\nFeature Summary:")
    print(df[["obi", "spread_bps", "trade_flow_imbalance", "funding_rate_bps", "mid_price"]].describe())

    if passed:
        print("\nReplay test passed.")
    else:
        print("\nReplay test failed.")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) == 2:
        replay_test(sys.argv[1])
    else:
        print("Usage: python replay_test.py YYYY-MM-DD")

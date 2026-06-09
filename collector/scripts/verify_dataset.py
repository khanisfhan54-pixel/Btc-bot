import os
import sys
import glob
import pandas as pd

def verify_date(date_str: str, data_dir: str = "data"):
    print(f"Verifying {date_str}...")

    streams = ["orderbook", "trades", "markprice"]

    all_passed = True

    for stream in streams:
        stream_dir = os.path.join(data_dir, "raw", stream)

        # Check files
        for hour in range(24):
            file_name = f"{date_str}-{hour:02d}.parquet"
            file_path = os.path.join(stream_dir, file_name)

            if not os.path.exists(file_path):
                print(f"  [FAIL] Missing {stream} {hour:02d}: {file_path}")
                all_passed = False
                continue

            try:
                df = pd.read_parquet(file_path)
            except Exception as e:
                print(f"  [FAIL] Cannot open {file_path}: {e}")
                all_passed = False
                continue

            if "timestamp" not in df.columns:
                print(f"  [FAIL] Missing timestamp column in {file_path}")
                all_passed = False
                continue

            if stream == "trades":
                if "trade_id" in df.columns and df.duplicated(subset=["timestamp", "trade_id"]).any():
                    print(f"  [FAIL] Duplicate (timestamp, trade_id) pairs in {file_path}")
                    all_passed = False
            else:
                if df["timestamp"].duplicated().any():
                    print(f"  [FAIL] Duplicate timestamps in {file_path}")
                    all_passed = False

            if stream == "trades":
                timestamp_diffs = df["timestamp"].diff().dropna()
                zero_delta = pd.Timedelta(0) if pd.api.types.is_timedelta64_dtype(timestamp_diffs) else 0
                if not (timestamp_diffs >= zero_delta).all():
                    print(f"  [FAIL] Timestamps not monotonically non-decreasing in {file_path}")
                    all_passed = False
            else:
                if not df["timestamp"].is_monotonic_increasing:
                    print(f"  [FAIL] Timestamps not monotonically increasing in {file_path}")
                    all_passed = False

            if df.isna().any().any():
                print(f"  [FAIL] NaNs found in {file_path}")
                all_passed = False

    if all_passed:
        print(f"  [PASS] {date_str} verified successfully.")
        return True
    return False

def verify_dataset(start_date: str, end_date: str, data_dir: str = "data"):
    dates = pd.date_range(start_date, end_date).strftime("%Y-%m-%d").tolist()

    all_passed = True
    for d in dates:
        if not verify_date(d, data_dir):
            all_passed = False

    if all_passed:
        print("\nAll dates passed verification.")
    else:
        print("\nSome dates failed verification.")

if __name__ == "__main__":
    if len(sys.argv) == 3:
        verify_dataset(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 2:
        verify_dataset(sys.argv[1], sys.argv[1])
    else:
        print("Usage: python verify_dataset.py START_DATE [END_DATE]")
        print("Example: python verify_dataset.py 2026-06-01 2026-06-30")

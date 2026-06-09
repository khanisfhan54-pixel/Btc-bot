import os
import sys
import glob
import pandas as pd
from datetime import datetime, timedelta

def generate_gap_report(start_date: str, end_date: str, data_dir: str = "data"):
    dates = pd.date_range(start_date, end_date).strftime("%Y-%m-%d").tolist()

    streams = ["orderbook", "trades", "markprice"]
    thresholds = {"orderbook": 500, "trades": 5000, "markprice": 5000}

    print(f"{'Stream':<15} {'Date':<15} {'Gaps':<10} {'Total Gap Time':<15} {'Longest Gap':<15} {'Coverage %':<10}")
    print("-" * 80)

    for d in dates:
        for stream in streams:
            stream_dir = os.path.join(data_dir, "raw", stream)
            files = sorted(glob.glob(os.path.join(stream_dir, f"{d}-*.parquet")))

            if not files:
                continue

            dfs = []
            for f in files:
                try:
                    dfs.append(pd.read_parquet(f, columns=["timestamp"]))
                except:
                    pass

            if not dfs:
                continue

            df = pd.concat(dfs).sort_values("timestamp")
            if pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
                df["timestamp"] = (
                    pd.to_datetime(df["timestamp"], utc=True)
                    .astype("datetime64[ns, UTC]")
                    .astype("int64")
                    // 1_000_000
                )

            diffs = df["timestamp"].diff().dropna()

            gaps = diffs[diffs > thresholds[stream]]

            num_gaps = len(gaps)
            total_gap_time = gaps.sum() if num_gaps > 0 else 0
            longest_gap = gaps.max() if num_gaps > 0 else 0

            # Coverage
            total_ms = 24 * 60 * 60 * 1000
            coverage = ((total_ms - total_gap_time) / total_ms) * 100
            coverage = max(0, min(100, coverage))

            print(f"{stream:<15} {d:<15} {num_gaps:<10} {int(total_gap_time):<13}ms {int(longest_gap):<13}ms {coverage:.2f}%")

if __name__ == "__main__":
    if len(sys.argv) == 3:
        generate_gap_report(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 2:
        generate_gap_report(sys.argv[1], sys.argv[1])
    else:
        print("Usage: python gap_report.py START_DATE [END_DATE]")

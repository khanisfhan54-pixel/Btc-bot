import os
import glob
import pandas as pd
import pyarrow.parquet as pq
from datetime import datetime, timedelta
from collector import config

def analyze_gaps(date_start: str, date_end: str):
    start = datetime.strptime(date_start, "%Y-%m-%d")
    end = datetime.strptime(date_end, "%Y-%m-%d")

    current = start
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        print(f"\nGap Report for {date_str}")
        print(f"{'Stream':<15} {'Date':<15} {'Gaps':<10} {'Total Gap Time':<20} {'Longest Gap':<15} {'Coverage %'}")
        print("-" * 85)

        for stream in ["orderbook", "trades", "markprice"]:
            files = glob.glob(os.path.join(config.RAW_DIR, stream, f"{date_str}-*.parquet"))
            if not files:
                print(f"{stream:<15} {date_str:<15} {'N/A':<10} {'N/A':<20} {'N/A':<15} {'0.00%'}")
                continue

            df = pd.concat([pq.read_table(f, columns=['exchange_timestamp']).to_pandas() for f in files])
            df = df.sort_values('exchange_timestamp')

            diffs = df['exchange_timestamp'].diff()
            threshold = config.GAP_THRESHOLDS_MS.get(stream, 0)

            gaps = diffs[diffs > threshold]

            total_time = 24 * 60 * 60 * 1000 # ms in a day

            num_gaps = len(gaps)
            total_gap_time = gaps.sum() if num_gaps > 0 else 0
            longest_gap = gaps.max() if num_gaps > 0 else 0

            # Approximate coverage
            coverage = 100 * (1 - (total_gap_time / total_time))

            print(f"{stream:<15} {date_str:<15} {num_gaps:<10} {str(int(total_gap_time))+'ms':<20} {str(int(longest_gap))+'ms':<15} {coverage:.2f}%")

        current += timedelta(days=1)

if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3:
        analyze_gaps(sys.argv[1], sys.argv[2])
    else:
        print("Usage: python gap_report.py YYYY-MM-DD YYYY-MM-DD")

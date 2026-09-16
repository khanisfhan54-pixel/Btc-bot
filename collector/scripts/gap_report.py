import sys
import pandas as pd
from collector.collector.storage_layout import iter_segments

def generate_gap_report(start_date: str, end_date: str, data_dir: str = "data"):
    dates = pd.date_range(start_date, end_date).strftime("%Y-%m-%d").tolist()

    streams = ["orderbook", "trades", "markprice"]
    thresholds = {"orderbook": 500, "trades": 5000, "markprice": 5000}

    print(f"{'Stream':<15} {'Date':<15} {'Gaps':<10} {'Total Gap Time':<15} {'Longest Gap':<15} {'Coverage %':<10}")
    print("-" * 80)

    all_sources_found = True
    for d in dates:
        for stream in streams:
            files = list(iter_segments(data_dir, stream, date=d))

            if not files:
                print(f"[FAIL] No published source segments for {stream} {d}")
                all_sources_found = False
                continue

            dfs = []
            for f in files:
                try:
                    dfs.append(pd.read_parquet(f, columns=["timestamp"]))
                except (OSError, ValueError) as exc:
                    print(f"[FAIL] Cannot read {f}: {exc}")
                    all_sources_found = False

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
    return all_sources_found

if __name__ == "__main__":
    if len(sys.argv) == 3:
        ok = generate_gap_report(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 2:
        ok = generate_gap_report(sys.argv[1], sys.argv[1])
    else:
        print("Usage: python gap_report.py START_DATE [END_DATE]")
        ok = False
    raise SystemExit(0 if ok else 1)

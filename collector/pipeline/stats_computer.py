import os
import glob
import json
import pandas as pd
from typing import List
from collector import config
from collector.utils import get_logger

logger = get_logger("stats_computer")

def compute_stats(parquet_files: List[str], output_path: str):
    if not parquet_files:
        logger.warning("no_files_for_stats")
        return

    logger.info("computing_stats", num_files=len(parquet_files))

    # We load columns chunk by chunk to save memory
    columns_to_skip = ["timestamp", "exchange_timestamp", "local_timestamp",
                       "bids_price", "bids_qty", "asks_price", "asks_qty",
                       "is_buyer_maker", "trade_id", "orderbook_gap", "markprice_gap"]

    # Get schema from first file
    sample_df = pd.read_parquet(parquet_files[0], engine='pyarrow')
    cols = [c for c in sample_df.columns if c not in columns_to_skip]

    stats = {}

    for col in cols:
        try:
            # Load only this column across all files
            series_list = []
            for f in parquet_files:
                df = pd.read_parquet(f, columns=[col], engine='pyarrow')
                series_list.append(df[col])

            full_series = pd.concat(series_list)

            # Compute stats
            stats[col] = {
                "mean": float(full_series.mean()),
                "std": float(full_series.std()),
                "min": float(full_series.min()),
                "max": float(full_series.max()),
                "p01": float(full_series.quantile(0.01)),
                "p99": float(full_series.quantile(0.99)),
                "count": int(full_series.count()),
                "null_count": int(full_series.isna().sum())
            }
            logger.info("computed_stat_for_col", col=col)

        except Exception as e:
            logger.error("stats_computation_failed", col=col, error=str(e))

    os.makedirs(config.STATS_DIR, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(stats, f, indent=2)

    logger.info("stats_saved", path=output_path)

if __name__ == "__main__":
    aligned_files = glob.glob(os.path.join(config.ALIGNED_DIR, "*.parquet"))
    compute_stats(aligned_files, os.path.join(config.STATS_DIR, "aligned_stats.json"))

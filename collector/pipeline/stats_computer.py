import os
import json
import glob
from typing import Dict, Any, Iterable, List
import pandas as pd
import numpy as np


def _load_parquet_files(files: Iterable[str]) -> pd.DataFrame:
    dfs = [pd.read_parquet(f) for f in files]
    return pd.concat(dfs, ignore_index=True)


def _is_list_typed(series: pd.Series) -> bool:
    non_null = series.dropna()
    if non_null.empty:
        return False
    return non_null.map(lambda value: isinstance(value, (list, tuple, np.ndarray))).any()


def _compute_column_stats(df: pd.DataFrame, exclude_cols: List[str] = None) -> Dict[str, Dict[str, Any]]:
    exclude_cols = exclude_cols or []
    stats = {}

    for col in df.columns:
        if col in exclude_cols or _is_list_typed(df[col]):
            continue
        if not (pd.api.types.is_numeric_dtype(df[col]) or pd.api.types.is_bool_dtype(df[col])):
            continue

        s = df[col].dropna()
        if s.empty:
            stats[col] = {
                "mean": None,
                "std": None,
                "min": None,
                "max": None,
                "p01": None,
                "p99": None,
                "count": 0,
                "null_count": int(df[col].isna().sum())
            }
            continue

        stats[col] = {
            "mean": float(s.mean()),
            "std": float(s.std()),
            "min": float(s.min()),
            "max": float(s.max()),
            "p01": float(s.quantile(0.01)),
            "p99": float(s.quantile(0.99)),
            "count": int(s.count()),
            "null_count": int(df[col].isna().sum())
        }

    return stats


def _write_stats(stats: Dict[str, Dict[str, Any]], data_dir: str, filename: str):
    out_dir = os.path.join(data_dir, "stats")
    os.makedirs(out_dir, exist_ok=True)

    out_file = os.path.join(out_dir, filename)
    with open(out_file, "w") as f:
        json.dump(stats, f, indent=2)

    print(f"Saved stats to {out_file}")


def compute_raw_stream_stats(data_dir: str, stream_name: str, output_filename: str):
    raw_files = glob.glob(os.path.join(data_dir, "raw", stream_name, "*.parquet"))

    if not raw_files:
        print(f"No raw {stream_name} data found.")
        return

    print(f"Computing stats from {len(raw_files)} raw {stream_name} files...")
    df = _load_parquet_files(raw_files)
    stats = _compute_column_stats(df)
    _write_stats(stats, data_dir, output_filename)


def compute_orderbook_stats(data_dir: str = "data"):
    compute_raw_stream_stats(data_dir, "orderbook", "orderbook_stats.json")


def compute_trades_stats(data_dir: str = "data"):
    compute_raw_stream_stats(data_dir, "trades", "trades_stats.json")


def compute_markprice_stats(data_dir: str = "data"):
    compute_raw_stream_stats(data_dir, "markprice", "markprice_stats.json")


def compute_aligned_stats(data_dir: str = "data"):
    aligned_files = glob.glob(os.path.join(data_dir, "aligned", "*.parquet"))

    if not aligned_files:
        print("No aligned data found.")
        return

    print(f"Computing stats from {len(aligned_files)} aligned files...")
    df = _load_parquet_files(aligned_files)

    exclude_cols = ["timestamp", "orderbook_gap", "markprice_gap"]
    stats = _compute_column_stats(df, exclude_cols=exclude_cols)
    _write_stats(stats, data_dir, "aligned_stats.json")


def compute_stats(data_dir: str = "data"):
    compute_orderbook_stats(data_dir)
    compute_trades_stats(data_dir)
    compute_markprice_stats(data_dir)
    compute_aligned_stats(data_dir)


if __name__ == "__main__":
    compute_stats()

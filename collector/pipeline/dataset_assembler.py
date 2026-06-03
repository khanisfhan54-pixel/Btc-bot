import calendar
import os
import pandas as pd
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pa_parquet
from datetime import datetime, timedelta

def assemble_dataset(date_str: str, grid_ms: int = 100, data_dir: str = "data"):
    print(f"Assembling dataset for {date_str} with grid {grid_ms}ms")

    # Load all files for the given date
    ob_dir = os.path.join(data_dir, "raw", "orderbook")
    trades_dir = os.path.join(data_dir, "raw", "trades")
    mark_dir = os.path.join(data_dir, "raw", "markprice")

    ob_dfs = []
    trades_dfs = []
    mark_dfs = []

    # Try to load all 24 hours
    for hour in range(24):
        file_prefix = f"{date_str}-{hour:02d}"

        ob_file = os.path.join(ob_dir, f"{file_prefix}.parquet")
        if os.path.exists(ob_file):
            ob_dfs.append(pd.read_parquet(ob_file))

        trades_file = os.path.join(trades_dir, f"{file_prefix}.parquet")
        if os.path.exists(trades_file):
            trades_dfs.append(pd.read_parquet(trades_file))

        mark_file = os.path.join(mark_dir, f"{file_prefix}.parquet")
        if os.path.exists(mark_file):
            mark_dfs.append(pd.read_parquet(mark_file))

    if not ob_dfs or not mark_dfs:
        print(f"Insufficient data for {date_str}")
        return

    df_ob = pd.concat(ob_dfs).sort_values("timestamp").reset_index(drop=True)
    df_trades = pd.concat(trades_dfs).sort_values("timestamp").reset_index(drop=True) if trades_dfs else pd.DataFrame()
    df_mark = pd.concat(mark_dfs).sort_values("timestamp").reset_index(drop=True)

    # Create common time grid
    start_dt = datetime.strptime(date_str, "%Y-%m-%d")
    end_dt = start_dt + timedelta(days=1)

    start_ts = calendar.timegm(start_dt.timetuple()) * 1000
    end_ts = calendar.timegm(end_dt.timetuple()) * 1000

    grid_ts = np.arange(start_ts, end_ts, grid_ms)
    df_grid = pd.DataFrame({"timestamp": grid_ts})

    # Forward fill orderbook and mark price using merge_asof
    df_ob = df_ob.drop(columns=["exchange_timestamp", "local_timestamp", "bids_price", "bids_qty", "asks_price", "asks_qty", "spread"], errors="ignore")
    df_mark = df_mark.drop(columns=["exchange_timestamp", "local_timestamp", "next_funding_time", "funding_rate"], errors="ignore")

    # Track staleness for gaps
    df_ob["ob_ts"] = df_ob["timestamp"]
    df_mark["mark_ts"] = df_mark["timestamp"]

    df_aligned = pd.merge_asof(df_grid, df_ob, on="timestamp", direction="backward")
    df_aligned = pd.merge_asof(df_aligned, df_mark, on="timestamp", direction="backward")

    df_aligned["orderbook_gap"] = (df_aligned["timestamp"] - df_aligned["ob_ts"]) > 500
    df_aligned["markprice_gap"] = (df_aligned["timestamp"] - df_aligned["mark_ts"]) > 5000

    df_aligned = df_aligned.drop(columns=["ob_ts", "mark_ts"])

    # Aggregate trades
    if not df_trades.empty:
        # Bin trades by grid timestamp. A trade at t falls into the bin (t_grid-grid_ms, t_grid]
        # We can achieve this by ceiling the trade timestamp to the nearest grid point
        df_trades["grid_ts"] = np.ceil((df_trades["timestamp"] - start_ts) / grid_ms) * grid_ms + start_ts
        df_trades["grid_ts"] = df_trades["grid_ts"].astype(np.int64)

        df_trades["is_buyer"] = ~df_trades["is_buyer_maker"]
        df_trades["buy_vol"] = np.where(df_trades["is_buyer"], df_trades["quantity"], 0)
        df_trades["sell_vol"] = np.where(~df_trades["is_buyer"], df_trades["quantity"], 0)
        df_trades["vol_x_price"] = df_trades["quantity"] * df_trades["price"]

        trade_aggs = df_trades.groupby("grid_ts").agg(
            trade_count=("trade_id", "count"),
            buy_volume=("buy_vol", "sum"),
            sell_volume=("sell_vol", "sum"),
            net_volume=("signed_qty", "sum"),
            vol_x_price_sum=("vol_x_price", "sum"),
            last_price=("price", "last")
        ).reset_index()

        trade_aggs["trade_flow_imbalance"] = np.where(
            (trade_aggs["buy_volume"] + trade_aggs["sell_volume"]) > 0,
            trade_aggs["net_volume"] / (trade_aggs["buy_volume"] + trade_aggs["sell_volume"]),
            0
        )
        trade_aggs["vwap"] = trade_aggs["vol_x_price_sum"] / (trade_aggs["buy_volume"] + trade_aggs["sell_volume"])
        trade_aggs = trade_aggs.drop(columns=["vol_x_price_sum"])

        df_aligned = pd.merge(df_aligned, trade_aggs, left_on="timestamp", right_on="grid_ts", how="left")
        df_aligned = df_aligned.drop(columns=["grid_ts"])
    else:
        df_aligned["trade_count"] = 0
        df_aligned["buy_volume"] = 0.0
        df_aligned["sell_volume"] = 0.0
        df_aligned["net_volume"] = 0.0
        df_aligned["trade_flow_imbalance"] = 0.0
        df_aligned["vwap"] = np.nan
        df_aligned["last_price"] = np.nan

    # Fill NaNs for trades where appropriate
    df_aligned["trade_count"] = df_aligned["trade_count"].fillna(0).astype(np.int32)
    df_aligned["buy_volume"] = df_aligned["buy_volume"].fillna(0.0)
    df_aligned["sell_volume"] = df_aligned["sell_volume"].fillna(0.0)
    df_aligned["net_volume"] = df_aligned["net_volume"].fillna(0.0)
    df_aligned["trade_flow_imbalance"] = df_aligned["trade_flow_imbalance"].fillna(0.0)

    # Save aligned dataset
    out_dir = os.path.join(data_dir, "aligned")
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, f"{date_str}.parquet")

    df_aligned.to_parquet(out_file, compression="snappy")
    print(f"Saved aligned dataset to {out_file}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        assemble_dataset(sys.argv[1])
    else:
        print("Usage: python dataset_assembler.py YYYY-MM-DD")

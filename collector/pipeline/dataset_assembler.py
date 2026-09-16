import calendar
import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from collector.collector.storage_layout import iter_segments


def _read_stream(data_dir: str, stream: str, date_str: str) -> list[pd.DataFrame]:
    """Read all published raw segments for one stream/date (legacy included)."""
    frames = []
    for path in iter_segments(data_dir, stream, date=date_str):
        frame = pd.read_parquet(path)
        if "timestamp" in frame and pd.api.types.is_datetime64_any_dtype(frame["timestamp"]):
            # Arrow/Pandas may preserve a millisecond physical timestamp; force
            # nanosecond resolution before converting to epoch milliseconds.
            frame["timestamp"] = (
                pd.to_datetime(frame["timestamp"], utc=True)
                .astype("datetime64[ns, UTC]")
                .astype("int64") // 1_000_000
            )
        frames.append(frame)
    return frames

def assemble_dataset(date_str: str, grid_ms: int = 100, data_dir: str = "data"):
    print(f"Assembling dataset for {date_str} with grid {grid_ms}ms")

    ob_dfs = _read_stream(data_dir, "orderbook", date_str)
    trades_dfs = _read_stream(data_dir, "trades", date_str)
    mark_dfs = _read_stream(data_dir, "markprice", date_str)

    if not ob_dfs or not mark_dfs:
        print(f"Insufficient data for {date_str}")
        return

    df_ob = pd.concat(ob_dfs).sort_values("timestamp").reset_index(drop=True)
    df_trades = pd.concat(trades_dfs).sort_values("timestamp").reset_index(drop=True) if trades_dfs else pd.DataFrame()
    df_mark = pd.concat(mark_dfs).sort_values("timestamp").reset_index(drop=True)

    for df_ref in [df_ob, df_trades, df_mark]:
        if not df_ref.empty and pd.api.types.is_datetime64_any_dtype(df_ref["timestamp"]):
            df_ref["timestamp"] = (
                pd.to_datetime(df_ref["timestamp"], utc=True)
                .astype("datetime64[ns, UTC]")
                .astype("int64")
                // 1_000_000
            )

    # Create common time grid
    start_dt = datetime.strptime(date_str, "%Y-%m-%d")
    end_dt = start_dt + timedelta(days=1)

    start_ts = calendar.timegm(start_dt.timetuple()) * 1000
    end_ts = calendar.timegm(end_dt.timetuple()) * 1000

    grid_ts = np.arange(start_ts, end_ts, grid_ms)
    df_grid = pd.DataFrame({"timestamp": grid_ts})

    # Causal joins have explicit freshness limits.  Never present old market
    # state as current during an outage.
    df_ob = df_ob.drop(columns=["bids_price", "bids_qty", "asks_price", "asks_qty"], errors="ignore")

    # Track staleness for gaps
    df_ob["ob_ts"] = df_ob["timestamp"]
    df_mark["mark_ts"] = df_mark["timestamp"]

    df_aligned = pd.merge_asof(df_grid, df_ob, on="timestamp", direction="backward", tolerance=500)
    df_aligned = pd.merge_asof(df_aligned, df_mark, on="timestamp", direction="backward", tolerance=5000)

    df_aligned["orderbook_gap"] = df_aligned["ob_ts"].isna() | ((df_aligned["timestamp"] - df_aligned["ob_ts"]) > 500)
    df_aligned["markprice_gap"] = df_aligned["mark_ts"].isna() | ((df_aligned["timestamp"] - df_aligned["mark_ts"]) > 5000)

    df_aligned = df_aligned.drop(columns=["ob_ts", "mark_ts"])

    # Preserve stress observations.  This flag is descriptive only; no spread
    # value is masked or forward-filled.
    SPREAD_SPIKE_THRESHOLD = 1.0
    if "spread" in df_aligned.columns:
        df_aligned["spread_spike_flag"] = df_aligned["spread"] > SPREAD_SPIKE_THRESHOLD
    else:
        df_aligned["spread_spike_flag"] = False
    df_aligned["spread_spike_flag"] = df_aligned["spread_spike_flag"].astype(bool)

    # De-saturate orderbook imbalance features for downstream linear models while
    # preserving the raw OBI columns.
    OBI_COLS = ["obi", "obi_level_1", "obi_level_3", "obi_level_5"]
    CLIP = 0.9999
    for col in OBI_COLS:
        if col in df_aligned.columns:
            clipped = df_aligned[col].clip(-CLIP, CLIP)
            df_aligned[f"{col}_fisher"] = np.arctanh(clipped).astype("float64")

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

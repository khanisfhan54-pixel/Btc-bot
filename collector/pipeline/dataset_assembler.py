import os
import glob
import pyarrow.parquet as pq
import pandas as pd
import numpy as np
from collector import config
from collector.utils import get_logger

logger = get_logger("dataset_assembler")

def assemble_day(date_str: str, grid_ms: int = 100):
    logger.info("assembling_day", date=date_str)

    # Load all hourly files for the day
    ob_files = sorted(glob.glob(os.path.join(config.ORDERBOOK_DIR, f"{date_str}-*.parquet")))
    tr_files = sorted(glob.glob(os.path.join(config.TRADES_DIR, f"{date_str}-*.parquet")))
    mp_files = sorted(glob.glob(os.path.join(config.MARKPRICE_DIR, f"{date_str}-*.parquet")))

    if not ob_files:
        logger.warning("no_data_for_day", date=date_str)
        return

    df_ob = pd.concat([pq.read_table(f).to_pandas() for f in ob_files])
    df_ob = df_ob.sort_values("exchange_timestamp").reset_index(drop=True)

    df_mp = pd.DataFrame()
    if mp_files:
        df_mp = pd.concat([pq.read_table(f).to_pandas() for f in mp_files])
        df_mp = df_mp.sort_values("exchange_timestamp").reset_index(drop=True)

    df_tr = pd.DataFrame()
    if tr_files:
        df_tr = pd.concat([pq.read_table(f).to_pandas() for f in tr_files])
        df_tr = df_tr.sort_values("exchange_timestamp").reset_index(drop=True)

    # Create common time grid
    start_ts = df_ob['exchange_timestamp'].min()
    end_ts = df_ob['exchange_timestamp'].max()

    # Align to grid
    start_ts = (start_ts // grid_ms) * grid_ms
    end_ts = (end_ts // grid_ms) * grid_ms

    grid = pd.DataFrame({'timestamp': np.arange(start_ts, end_ts + grid_ms, grid_ms)})

    # --- Orderbook Forward Fill ---
    df_ob_merge = pd.merge_asof(
        grid, df_ob,
        left_on='timestamp', right_on='exchange_timestamp',
        direction='backward'
    )

    # Mark gaps
    df_ob_merge['orderbook_gap'] = df_ob_merge['exchange_timestamp'].isna() | ((df_ob_merge['timestamp'] - df_ob_merge['exchange_timestamp']) > 500)

    # --- Markprice Forward Fill ---
    if not df_mp.empty:
        df_mp_merge = pd.merge_asof(
            grid, df_mp,
            left_on='timestamp', right_on='exchange_timestamp',
            direction='backward'
        )
        df_ob_merge['mark_price'] = df_mp_merge['mark_price']
        df_ob_merge['funding_rate_bps'] = df_mp_merge['funding_rate_bps']
        df_ob_merge['hours_to_funding'] = df_mp_merge['hours_to_funding']
        df_ob_merge['markprice_gap'] = df_mp_merge['exchange_timestamp'].isna() | ((df_ob_merge['timestamp'] - df_mp_merge['exchange_timestamp']) > 5000)
    else:
        df_ob_merge['mark_price'] = np.nan
        df_ob_merge['funding_rate_bps'] = np.nan
        df_ob_merge['hours_to_funding'] = np.nan
        df_ob_merge['markprice_gap'] = True

    # --- Trades Aggregation ---
    if not df_tr.empty:
        # Group trades by grid intervals
        df_tr['grid_ts'] = ((df_tr['exchange_timestamp'] - 1) // grid_ms + 1) * grid_ms

        # Calculate aggregates
        tr_agg = df_tr.groupby('grid_ts').agg(
            trade_count=('trade_id', 'count'),
            buy_volume=('quantity', lambda x: x[~df_tr.loc[x.index, 'is_buyer_maker']].sum()),
            sell_volume=('quantity', lambda x: x[df_tr.loc[x.index, 'is_buyer_maker']].sum()),
            vwap=('price', lambda x: np.average(x, weights=df_tr.loc[x.index, 'quantity']) if len(x)>0 else np.nan),
            last_price=('price', 'last')
        ).reset_index()

        tr_agg['net_volume'] = tr_agg['buy_volume'] - tr_agg['sell_volume']
        total_vol = tr_agg['buy_volume'] + tr_agg['sell_volume']
        tr_agg['trade_flow_imbalance'] = np.where(total_vol > 0, tr_agg['net_volume'] / total_vol, 0)

        # Merge
        df_final = pd.merge(df_ob_merge, tr_agg, left_on='timestamp', right_on='grid_ts', how='left')

        # Fill missing trade slots with 0s / NaNs
        df_final['trade_count'] = df_final['trade_count'].fillna(0).astype(np.int32)
        df_final['buy_volume'] = df_final['buy_volume'].fillna(0)
        df_final['sell_volume'] = df_final['sell_volume'].fillna(0)
        df_final['net_volume'] = df_final['net_volume'].fillna(0)
        df_final['trade_flow_imbalance'] = df_final['trade_flow_imbalance'].fillna(0)
    else:
        df_final = df_ob_merge.copy()
        df_final['trade_count'] = 0
        df_final['buy_volume'] = 0.0
        df_final['sell_volume'] = 0.0
        df_final['net_volume'] = 0.0
        df_final['trade_flow_imbalance'] = 0.0
        df_final['vwap'] = np.nan
        df_final['last_price'] = np.nan

    # Select columns as per schema
    cols = [
        "timestamp", "best_bid", "best_ask", "mid_price", "micro_price",
        "spread_bps", "obi", "obi_level_1", "obi_level_3", "obi_level_5",
        "total_bid_qty", "total_ask_qty", "trade_count", "buy_volume",
        "sell_volume", "net_volume", "trade_flow_imbalance", "vwap",
        "last_price", "mark_price", "funding_rate_bps", "hours_to_funding",
        "orderbook_gap", "markprice_gap"
    ]

    df_out = df_final[cols]

    # Save
    os.makedirs(config.ALIGNED_DIR, exist_ok=True)
    out_path = os.path.join(config.ALIGNED_DIR, f"{date_str}.parquet")
    df_out.to_parquet(out_path, compression='snappy')
    logger.info("day_assembled", date=date_str, rows=len(df_out), path=out_path)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        assemble_day(sys.argv[1])
    else:
        print("Usage: python dataset_assembler.py YYYY-MM-DD")

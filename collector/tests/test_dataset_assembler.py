import os

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from collector.pipeline.dataset_assembler import assemble_dataset


def _write_timestamp_ms_parquet(df, path):
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    schema = pa.schema(
        [
            pa.field(field.name, pa.timestamp("ms", tz="UTC"))
            if field.name == "timestamp"
            else field
            for field in table.schema
        ]
    )
    pq.write_table(table.cast(schema), path)


@pytest.fixture
def test_data(tmp_path):
    data_dir = str(tmp_path)
    date_str = "2026-06-03"

    # Create dirs
    os.makedirs(os.path.join(data_dir, "raw", "orderbook"))
    os.makedirs(os.path.join(data_dir, "raw", "trades"))
    os.makedirs(os.path.join(data_dir, "raw", "markprice"))

    start_ts = int(pd.Timestamp(f"{date_str} 00:00:00", tz="UTC").timestamp() * 1000)

    # Orderbook data
    ob_df = pd.DataFrame({
        "timestamp": [start_ts, start_ts + 200, start_ts + 400],
        "best_bid": [100.0, 100.5, 101.0],
        "best_ask": [101.0, 101.5, 102.0],
        "mid_price": [100.5, 101.0, 101.5],
        "spread_bps": [10.0, 10.0, 10.0],
        "obi": [0.0, 0.0, 0.0]
    })
    _write_timestamp_ms_parquet(ob_df, os.path.join(data_dir, "raw", "orderbook", f"{date_str}-00.parquet"))

    # Trades data
    trades_df = pd.DataFrame({
        "timestamp": [start_ts + 50, start_ts + 150, start_ts + 250],
        "trade_id": [1, 2, 3],
        "price": [100.5, 100.5, 101.0],
        "quantity": [1.0, 2.0, 3.0],
        "is_buyer_maker": [False, True, False],
        "signed_qty": [1.0, -2.0, 3.0]
    })
    _write_timestamp_ms_parquet(trades_df, os.path.join(data_dir, "raw", "trades", f"{date_str}-00.parquet"))

    # Markprice data
    mark_df = pd.DataFrame({
        "timestamp": [start_ts, start_ts + 1000],
        "mark_price": [100.5, 101.5],
        "funding_rate_bps": [1.0, 1.0],
        "hours_to_funding": [8.0, 7.99]
    })
    _write_timestamp_ms_parquet(mark_df, os.path.join(data_dir, "raw", "markprice", f"{date_str}-00.parquet"))

    return data_dir, date_str, start_ts


def test_dataset_assembler(test_data):
    data_dir, date_str, start_ts = test_data

    assemble_dataset(date_str, grid_ms=100, data_dir=data_dir)

    aligned_file = os.path.join(data_dir, "aligned", f"{date_str}.parquet")
    assert os.path.exists(aligned_file)

    df = pd.read_parquet(aligned_file)

    # 24 hours * 60 min * 60 sec * 10 (for 100ms) = 864000 rows
    assert len(df) == 864000
    assert pd.api.types.is_integer_dtype(df["timestamp"])

    # Check first few rows
    # t=0
    assert df.loc[0, "timestamp"] == start_ts
    assert df.loc[0, "mid_price"] == 100.5
    assert df.loc[0, "trade_count"] == 0
    assert pd.isna(df.loc[0, "vwap"])

    # t=100 (trade 1 falls here)
    assert df.loc[1, "timestamp"] == start_ts + 100
    assert df.loc[1, "mid_price"] == 100.5
    assert df.loc[1, "trade_count"] == 1
    assert df.loc[1, "buy_volume"] == 1.0
    assert df.loc[1, "sell_volume"] == 0.0
    assert df.loc[1, "net_volume"] == 1.0
    assert df.loc[1, "vwap"] == 100.5

    # t=200 (trade 2 falls here) and t=300 (trade 3 falls here), proving grid_ts binning
    assert df.loc[2, "trade_count"] == 1
    assert df.loc[2, "buy_volume"] == 0.0
    assert df.loc[2, "sell_volume"] == 2.0
    assert df.loc[2, "net_volume"] == -2.0
    assert df.loc[3, "trade_count"] == 1
    assert df.loc[3, "buy_volume"] == 3.0
    assert df.loc[3, "sell_volume"] == 0.0
    assert df.loc[3, "net_volume"] == 3.0

    # Check gaps
    assert df.loc[0, "orderbook_gap"] == False
    # at t=100, last ob was at t=0, so gap is 100 (not > 500)
    assert df.loc[1, "orderbook_gap"] == False

    # way out in the future should be a gap
    # last ob is t=400, so by t=1000 it is > 500
    assert df.loc[10, "orderbook_gap"] == True

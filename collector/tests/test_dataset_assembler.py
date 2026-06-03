import pytest
import pandas as pd
import numpy as np

def test_assembler_logic():
    # Simple logic test matching assembler

    # OB Data
    df_ob = pd.DataFrame({
        "exchange_timestamp": [1000, 1100, 1200],
        "mid_price": [100.0, 101.0, 102.0]
    })

    # Trade Data
    df_tr = pd.DataFrame({
        "exchange_timestamp": [1050, 1150, 1160],
        "trade_id": [1, 2, 3],
        "quantity": [1.0, 2.0, 3.0],
        "price": [100.5, 101.5, 101.5],
        "is_buyer_maker": [False, True, False]
    })

    grid = pd.DataFrame({'timestamp': [1000, 1100, 1200]})

    df_ob_merge = pd.merge_asof(
        grid, df_ob,
        left_on='timestamp', right_on='exchange_timestamp',
        direction='backward'
    )

    assert df_ob_merge['mid_price'].tolist() == [100.0, 101.0, 102.0]

    # Aggregation
    df_tr['grid_ts'] = ((df_tr['exchange_timestamp'] - 1) // 100 + 1) * 100

    tr_agg = df_tr.groupby('grid_ts').agg(
        trade_count=('trade_id', 'count'),
        buy_volume=('quantity', lambda x: x[~df_tr.loc[x.index, 'is_buyer_maker']].sum())
    ).reset_index()

    assert tr_agg[tr_agg['grid_ts'] == 1100]['trade_count'].iloc[0] == 1
    assert tr_agg[tr_agg['grid_ts'] == 1200]['trade_count'].iloc[0] == 2

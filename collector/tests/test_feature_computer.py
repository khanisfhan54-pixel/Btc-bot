import pytest
from collector.feature_computer import compute_orderbook_features, compute_trade_features, compute_markprice_features

def test_compute_orderbook_features():
    msg = {
        "b": [["50000.0", "1.0"], ["49990.0", "2.0"]],
        "a": [["50010.0", "1.0"], ["50020.0", "2.0"]]
    }

    feats = compute_orderbook_features(msg)

    assert feats["best_bid"] == 50000.0
    assert feats["best_ask"] == 50010.0
    assert feats["mid_price"] == 50005.0
    assert feats["spread"] == 10.0
    assert feats["spread_bps"] == (10.0 / 50005.0) * 10000

    # micro price: (50000*1.0 + 50010*1.0) / (1.0+1.0) = 50005
    assert feats["micro_price"] == 50005.0

    assert feats["total_bid_qty"] == 3.0
    assert feats["total_ask_qty"] == 3.0

    # obi: (3-3)/(3+3) = 0
    assert feats["obi"] == 0.0

def test_compute_trade_features():
    msg1 = {"q": "1.5", "m": False} # taker buy
    feats1 = compute_trade_features(msg1)
    assert feats1["side_sign"] == 1
    assert feats1["signed_qty"] == 1.5

    msg2 = {"q": "2.0", "m": True} # taker sell
    feats2 = compute_trade_features(msg2)
    assert feats2["side_sign"] == -1
    assert feats2["signed_qty"] == -2.0

def test_compute_markprice_features():
    msg = {"r": "0.0001", "E": 1000, "T": 3601000}
    feats = compute_markprice_features(msg)

    assert feats["funding_rate_bps"] == 1.0
    assert feats["hours_to_funding"] == 1.0 # 3600000 ms = 1 hr

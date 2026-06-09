import pytest
from collector.collector.feature_computer import compute_orderbook_features, compute_trades_features, compute_markprice_features, compute_openinterest_features, compute_liquidation_features

def test_compute_orderbook_features():
    msg = {
        "E": 1234567890,
        "b": [[str(100.0 - i), str(1.0)] for i in range(10)],
        "a": [[str(101.0 + i), str(2.0)] for i in range(10)]
    }

    features = compute_orderbook_features(msg)

    assert features["exchange_timestamp"] == 1234567890
    assert features["best_bid"] == 100.0
    assert features["best_ask"] == 101.0
    assert features["mid_price"] == 100.5
    assert features["spread"] == 1.0
    assert abs(features["spread_bps"] - (1.0 / 100.5 * 10000)) < 1e-5
    assert features["total_bid_qty"] == 10.0
    assert features["total_ask_qty"] == 20.0
    assert abs(features["obi"] - (-10.0 / 30.0)) < 1e-5

def test_compute_orderbook_features_invalid():
    msg = {"b": [], "a": [["101.0", "2.0"]]}
    features = compute_orderbook_features(msg)
    assert not features


def test_compute_orderbook_features_pads_five_bid_levels():
    msg = {
        "E": 1234567890,
        "b": [[str(100.0 - i), str(1.0 + i)] for i in range(5)],
        "a": [[str(101.0 + i), str(2.0)] for i in range(10)],
    }

    features = compute_orderbook_features(msg)

    assert features
    assert len(features["bids_price"]) == 10
    assert len(features["bids_qty"]) == 10
    assert features["bids_price"][5:] == [96.0] * 5
    assert features["bids_qty"][5:] == [0.0] * 5


def test_compute_orderbook_features_accepts_single_level_and_computes_obi():
    msg = {
        "E": 1234567890,
        "b": [["100.0", "3.0"]],
        "a": [["101.0", "1.0"]],
    }

    features = compute_orderbook_features(msg)

    assert features
    assert len(features["bids_price"]) == 10
    assert len(features["asks_price"]) == 10
    assert features["bids_qty"] == [3.0] + [0.0] * 9
    assert features["asks_qty"] == [1.0] + [0.0] * 9
    assert features["obi"] == 0.5
    assert features["obi_level_1"] == 0.5


def test_compute_orderbook_features_rejects_zero_bids():
    msg = {"b": [], "a": [["101.0", "2.0"] for _ in range(10)]}
    features = compute_orderbook_features(msg)
    assert features == {}

def test_compute_trades_features():
    msg = {
        "E": 1234567890,
        "a": 123,
        "p": "100.0",
        "q": "1.5",
        "m": True # buyer is maker -> seller is taker -> side_sign = -1
    }

    features = compute_trades_features(msg)

    assert features["trade_id"] == 123
    assert features["price"] == 100.0
    assert features["quantity"] == 1.5
    assert features["side_sign"] == -1
    assert features["signed_qty"] == -1.5


def test_compute_trades_features_prefers_trade_time_over_event_time():
    msg = {
        "T": 1234567000,
        "E": 1234567890,
        "a": 123,
        "p": "100.0",
        "q": "1.5",
        "m": True
    }

    features = compute_trades_features(msg)

    assert features["exchange_timestamp"] == 1234567000


def test_compute_trades_features_falls_back_to_event_time_without_trade_time():
    msg = {
        "E": 1234567890,
        "a": 123,
        "p": "100.0",
        "q": "1.5",
        "m": True
    }

    features = compute_trades_features(msg)

    assert features["exchange_timestamp"] == 1234567890


def test_compute_trades_features_falls_back_to_local_timestamp_without_exchange_times(monkeypatch):
    monkeypatch.setattr("collector.collector.feature_computer.time.time", lambda: 1234567.89)
    msg = {
        "a": 123,
        "p": "100.0",
        "q": "1.5",
        "m": True
    }

    features = compute_trades_features(msg)

    assert features["timestamp"] == 1234567890
    assert features["local_timestamp"] == 1234567890
    assert features["exchange_timestamp"] == 1234567890


def test_compute_trades_features_trade_time_latency_includes_dispatch_lag(monkeypatch):
    monkeypatch.setattr("collector.collector.feature_computer.time.time", lambda: 1234568.0)
    trade_time = 1234567000
    event_time = 1234567890
    msg = {
        "T": trade_time,
        "E": event_time,
        "a": 123,
        "p": "100.0",
        "q": "1.5",
        "m": True
    }

    features = compute_trades_features(msg)
    trade_latency = features["local_timestamp"] - features["exchange_timestamp"]
    previous_event_latency = features["local_timestamp"] - event_time

    assert trade_latency > 0
    assert trade_latency == previous_event_latency + (event_time - trade_time)

def test_compute_markprice_features():
    msg = {
        "E": 1234567890,
        "p": "100.0",
        "r": "0.0001",
        "T": 1234567890 + 3600000
    }

    features = compute_markprice_features(msg)

    assert features["mark_price"] == 100.0
    assert features["funding_rate"] == 0.0001
    assert features["funding_rate_bps"] == 1.0
    assert features["hours_to_funding"] == 1.0


def test_compute_openinterest_features_valid_response(monkeypatch):
    monkeypatch.setattr("collector.collector.feature_computer.time.time", lambda: 1234568.0)
    msg = {"openInterest": "123.45", "time": "1234567000", "price": "100.0"}

    features = compute_openinterest_features(msg)

    assert features == {
        "timestamp": 1234568000,
        "exchange_timestamp": 1234567000,
        "local_timestamp": 1234568000,
        "open_interest": 123.45,
    }


def test_compute_openinterest_features_missing_openinterest_returns_empty():
    assert compute_openinterest_features({"time": "1234567000"}) == {}


def test_compute_liquidation_features_buy_force_order(monkeypatch):
    monkeypatch.setattr("collector.collector.feature_computer.time.time", lambda: 1234568.0)
    msg = {
        "o": {
            "S": "BUY",
            "p": "100.0",
            "q": "1.5",
            "T": "1234567000",
            "X": "FILLED",
            "f": "IOC",
        }
    }

    features = compute_liquidation_features(msg)

    assert features == {
        "timestamp": 1234568000,
        "exchange_timestamp": 1234567000,
        "local_timestamp": 1234568000,
        "side": 1,
        "price": 100.0,
        "quantity": 1.5,
        "signed_qty": 1.5,
        "order_status": "FILLED",
        "time_in_force": "IOC",
    }


def test_compute_liquidation_features_sell_force_order(monkeypatch):
    monkeypatch.setattr("collector.collector.feature_computer.time.time", lambda: 1234568.0)
    msg = {
        "o": {
            "S": "SELL",
            "p": "100.0",
            "q": "2.0",
            "T": "1234567000",
            "X": "FILLED",
            "f": "IOC",
        }
    }

    features = compute_liquidation_features(msg)

    assert features["side"] == -1
    assert features["signed_qty"] == -2.0
    assert features["price"] == 100.0
    assert features["quantity"] == 2.0

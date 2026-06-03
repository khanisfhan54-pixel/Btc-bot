import pytest
import time
from collector.collector.validator import Validator

@pytest.fixture
def validator():
    return Validator()

def test_validate_orderbook(validator):
    ts = int(time.time() * 1000)
    record = {
        "timestamp": ts,
        "bids_price": [100.0] * 10,
        "asks_price": [101.0] * 10,
        "best_bid": 100.0,
        "best_ask": 101.0,
        "spread_bps": 10.0,
        "total_bid_qty": 10.0,
        "total_ask_qty": 10.0,
        "obi": 0.0
    }

    valid, reason = validator.validate_orderbook(record)
    assert valid
    assert reason == ""

def test_validate_orderbook_crossed(validator):
    ts = int(time.time() * 1000)
    record = {
        "timestamp": ts,
        "bids_price": [101.0] * 10,
        "asks_price": [100.0] * 10,
        "best_bid": 101.0,
        "best_ask": 100.0,
        "spread_bps": -10.0,
        "total_bid_qty": 10.0,
        "total_ask_qty": 10.0,
        "obi": 0.0
    }

    valid, reason = validator.validate_orderbook(record)
    assert not valid
    assert reason == "Crossed book"

def test_validate_trade_duplicate(validator):
    ts = int(time.time() * 1000)
    record = {
        "timestamp": ts,
        "trade_id": 100,
        "price": 100.0,
        "quantity": 1.0
    }

    valid, _ = validator.validate_trade(record)
    assert valid

    # Duplicate ID
    record["timestamp"] += 1
    valid, reason = validator.validate_trade(record)
    assert not valid
    assert "Duplicate/Regressive" in reason

def test_validate_markprice_extreme_funding(validator):
    ts = int(time.time() * 1000)
    record = {
        "timestamp": ts,
        "mark_price": 100.0,
        "funding_rate": 0.05, # > 1%
        "exchange_timestamp": ts,
        "next_funding_time": ts + 1000
    }

    valid, reason = validator.validate_markprice(record)
    assert not valid
    assert "Funding rate out of bounds" in reason

def test_timestamp_regression(validator):
    ts = int(time.time() * 1000)
    record = {
        "timestamp": ts,
        "bids_price": [100.0] * 10,
        "asks_price": [101.0] * 10,
        "best_bid": 100.0,
        "best_ask": 101.0,
        "spread_bps": 10.0,
        "total_bid_qty": 10.0,
        "total_ask_qty": 10.0,
        "obi": 0.0
    }

    valid, _ = validator.validate_orderbook(record)
    assert valid

    # Same timestamp
    valid, reason = validator.validate_orderbook(record)
    assert not valid
    assert "Timestamp regression" in reason

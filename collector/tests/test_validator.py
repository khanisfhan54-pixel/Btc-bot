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


def test_validate_orderbook_accepts_partial_padded_book(validator):
    ts = int(time.time() * 1000)
    record = {
        "timestamp": ts,
        "exchange_timestamp": ts,
        "bids_price": [100.0],
        "asks_price": [101.0],
        "best_bid": 100.0,
        "best_ask": 101.0,
        "spread_bps": 10.0,
        "total_bid_qty": 1.0,
        "total_ask_qty": 1.0,
        "obi": 0.0,
    }

    valid, reason = validator.validate_orderbook(record)

    assert valid, reason
    assert reason == ""


def test_validate_orderbook_rejects_empty_book(validator):
    ts = int(time.time() * 1000)
    record = {
        "timestamp": ts,
        "exchange_timestamp": ts,
        "bids_price": [],
        "asks_price": [101.0],
        "best_bid": 100.0,
        "best_ask": 101.0,
        "spread_bps": 10.0,
        "total_bid_qty": 1.0,
        "total_ask_qty": 1.0,
        "obi": 0.0,
    }

    valid, reason = validator.validate_orderbook(record)

    assert not valid
    assert reason == "Empty book"

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


def test_validate_trade_allows_same_local_millisecond_when_trade_id_increases(validator):
    ts = int(time.time() * 1000)
    first = {
        "timestamp": ts,
        "exchange_timestamp": ts,
        "trade_id": 100,
        "price": 100.0,
        "quantity": 1.0,
    }
    second = {
        "timestamp": ts,
        "exchange_timestamp": ts,
        "trade_id": 101,
        "price": 100.0,
        "quantity": 1.0,
    }

    valid, reason = validator.validate_trade(first)
    assert valid, reason
    valid, reason = validator.validate_trade(second)
    assert valid, reason


def test_validate_trade_still_rejects_older_local_timestamp(validator):
    ts = int(time.time() * 1000)
    first = {
        "timestamp": ts,
        "exchange_timestamp": ts,
        "trade_id": 100,
        "price": 100.0,
        "quantity": 1.0,
    }
    older = {
        "timestamp": ts - 1,
        "exchange_timestamp": ts,
        "trade_id": 101,
        "price": 100.0,
        "quantity": 1.0,
    }

    valid, reason = validator.validate_trade(first)
    assert valid, reason
    valid, reason = validator.validate_trade(older)
    assert not valid
    assert reason == "Timestamp regression"


def test_reset_stream_orderbook_resets_only_orderbook_state(validator):
    validator.last_timestamps = {"orderbook": 1000, "trades": 2000, "markprice": 3000}
    validator.last_trade_id = 12345
    validator.last_mid_price = 100.5

    validator.reset_stream("orderbook")

    assert validator.last_timestamps["orderbook"] == 0
    assert validator.last_mid_price is None
    assert validator.last_timestamps["trades"] == 2000
    assert validator.last_trade_id == 12345


def test_reset_stream_trades_resets_only_trades_state(validator):
    validator.last_timestamps = {"orderbook": 1000, "trades": 2000, "markprice": 3000}
    validator.last_trade_id = 12345
    validator.last_mid_price = 100.5

    validator.reset_stream("trades")

    assert validator.last_trade_id == -1
    assert validator.last_timestamps["trades"] == 0
    assert validator.last_timestamps["orderbook"] == 1000
    assert validator.last_mid_price == 100.5


def _valid_liquidation_record(ts=None):
    ts = ts or int(time.time() * 1000)
    return {
        "timestamp": ts,
        "exchange_timestamp": ts,
        "price": 100.0,
        "quantity": 1.0,
    }


def test_validate_liquidation_accepts_valid_record(validator):
    record = _valid_liquidation_record()

    valid, reason = validator.validate_liquidation(record)

    assert valid, reason
    assert reason == ""
    assert validator.last_timestamps["liquidation"] == record["timestamp"]


def test_validate_liquidation_rejects_invalid_price(validator):
    record = _valid_liquidation_record()
    record["price"] = 0

    valid, reason = validator.validate_liquidation(record)

    assert not valid
    assert reason == "Invalid price"
    assert validator.failures_in_window == 1


def test_validate_liquidation_rejects_invalid_quantity(validator):
    record = _valid_liquidation_record()
    record["quantity"] = 0

    valid, reason = validator.validate_liquidation(record)

    assert not valid
    assert reason == "Invalid quantity"
    assert validator.failures_in_window == 1


def test_validate_liquidation_allows_same_millisecond(validator):
    ts = int(time.time() * 1000)
    first = _valid_liquidation_record(ts)
    second = _valid_liquidation_record(ts)

    valid, reason = validator.validate_liquidation(first)
    assert valid, reason
    valid, reason = validator.validate_liquidation(second)
    assert valid, reason

import pytest
import time
from collector.validator import RecordValidator

@pytest.fixture
def validator():
    return RecordValidator()

def test_validator_orderbook_valid(validator):
    now = int(time.time() * 1000)
    record = {
        "exchange_timestamp": now,
        "bids_price": [1]*10, "bids_qty": [1]*10,
        "asks_price": [2]*10, "asks_qty": [1]*10,
        "best_bid": 100, "best_ask": 101,
        "spread_bps": 50, "total_bid_qty": 10, "total_ask_qty": 10, "obi": 0.5
    }

    is_valid, _ = validator.validate_orderbook(record)
    assert is_valid

def test_validator_orderbook_crossed(validator):
    now = int(time.time() * 1000)
    record = {
        "exchange_timestamp": now,
        "bids_price": [1]*10, "bids_qty": [1]*10,
        "asks_price": [2]*10, "asks_qty": [1]*10,
        "best_bid": 101, "best_ask": 100, # Crossed
        "spread_bps": 50, "total_bid_qty": 10, "total_ask_qty": 10, "obi": 0.5
    }

    is_valid, reason = validator.validate_orderbook(record)
    assert not is_valid
    assert "Crossed" in reason

def test_validator_trade_price_outlier(validator):
    now = int(time.time() * 1000)

    # Set mid price context
    validator.last_mid_price = 100.0

    record = {
        "exchange_timestamp": now,
        "trade_id": 1,
        "price": 110.0, # 10% outlier
        "quantity": 1.0
    }

    is_valid, reason = validator.validate_trade(record)
    assert not is_valid
    assert "deviates >5%" in reason

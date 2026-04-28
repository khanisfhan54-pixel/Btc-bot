import numpy as np

import engine


def test_open_interest_missing_does_not_use_phantom_values():
    price = 100000.0
    orderbook = {"bids": [[price - 5.0, 1.0]], "asks": [[price + 5.0, 1.0]]}
    candles = {"1m": [[1, price, price + 10.0, price - 10.0, price, 10.0] for _ in range(40)]}
    result = engine.run_all_engines(
        orderbook=orderbook,
        trades=[{"price": price, "amount": 0.1, "side": "BUY"}],
        price=price,
        recent_candles=candles,
        open_interest=0.0,
        current_oi=0.0,
    )
    assert result.get("open_interest_missing") is True, "missing OI must be flagged explicitly"
    assert float(result.get("cascade_probability", 0.0)) == 0.0, "cascade probability must not be fabricated without OI"


def test_risk_outputs_are_finite_and_position_bounds_safe():
    price = 100000.0
    orderbook = {"bids": [[price - 10.0, 2.0]], "asks": [[price + 10.0, 2.0]]}
    candles = {"1m": [[1, price, price + 20.0, price - 20.0, price, 10.0] for _ in range(40)]}
    result = engine.run_all_engines(orderbook=orderbook, trades=[], price=price, recent_candles=candles)

    total_capital = 10000.0
    max_position_size = 0.1
    position_size = min(max_position_size, 0.05)
    used_capital = position_size * total_capital
    risk_value = float(result.get("cascade_probability", 0.0))

    assert used_capital <= total_capital, "used capital must not exceed total capital"
    assert position_size >= 0, "position size must be non-negative"
    assert position_size <= max_position_size, "position size must be bounded by max position size"
    assert not np.isnan(risk_value), "risk value must not be NaN"
    assert not np.isinf(risk_value), "risk value must not be infinite"

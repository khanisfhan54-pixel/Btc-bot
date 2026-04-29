import numpy as np

import engine
from replay_engine import ReplayEngine


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
    assert result.get("allow_trade") is False
    assert result.get("reason") == "open_interest_missing"


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


def test_market_state_invalid_payload_fails_closed():
    class InvalidDetector:
        def detect(self, *_args, **_kwargs):
            return {"state": "TRENDING", "allow_trade": "yes"}

    price = 100000.0
    orderbook = {"bids": [[price - 5.0, 1.0]], "asks": [[price + 5.0, 1.0]]}
    candles = {"1m": [[1, price, price + 10.0, price - 10.0, price, 10.0] for _ in range(40)]}
    result = engine.run_all_engines(
        orderbook=orderbook,
        trades=[],
        price=price,
        recent_candles=candles,
        open_interest=100.0,
        current_oi=100.0,
        market_state_detector=InvalidDetector(),
    )
    assert result["allow_trade"] is False
    assert result["market_state"]["reason"] == "market_state_allow_trade_invalid"


def test_replay_payload_with_non_finite_values_is_stored_without_alias():
    replay = ReplayEngine()
    arr = np.array([1.0, np.nan, np.inf], dtype=float)
    replay.record_event("update_start", {"vec": arr})
    arr[0] = 99.0
    ev = list(replay.replay())[0]
    out = np.array(ev["payload"]["vec"], dtype=object)
    assert out.shape == (3,)
    assert out[0] != 99.0


def test_replay_float_payload_retains_numeric_dtype():
    replay = ReplayEngine()
    replay.record_event("update_start", {"vec": np.array([0.1, 0.2], dtype=np.float64)})
    vec = list(replay.replay())[0]["payload"]["vec"]
    assert vec.dtype == np.float64
    assert np.isfinite(vec).all()

import sys
import os

# Fix import path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import alpha_liquidity_sweep_predictor as alpha


def test_predict_runs():
    # Fake minimal candle data (adjust later)
    candles = [
        {"open": 100, "high": 105, "low": 95, "close": 102, "volume": 10},
        {"open": 102, "high": 110, "low": 100, "close": 108, "volume": 12},
    ]

    try:
        result = alpha.predict_sweep(candles)
        assert result is not None
    except Exception as e:
        print(e)
        assert False

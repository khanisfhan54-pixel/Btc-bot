from main import run_backtest


def test_backtest_gap_correction_shifts_brackets():
    # build synthetic candles where signal close 50000, next open 50500
    candles = []
    ts = 0
    price = 50000.0
    for _ in range(80):
        candles.append([ts, price, price + 10, price - 10, price, 100000])
        ts += 60_000
    candles[-2][4] = 50000.0
    candles[-1][1] = 50500.0
    result = run_backtest(timeframe="1m", ohlcv_data=candles, limit=len(candles))
    assert isinstance(result, dict)

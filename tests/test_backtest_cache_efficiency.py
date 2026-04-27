from engine import run_all_engines


def test_run_all_engines_cache_hit_for_identical_inputs():
    recent = [[1, 10, 11, 9, 10, 100], [2, 10, 11, 9, 10, 100]]
    ob = {"bids": [[10, 1.0]], "asks": [[11, 1.0]]}
    trades = [{"price": 10, "amount": 1, "side": "buy"}]
    run_all_engines(orderbook=ob, trades=trades, price=10, recent_candles=recent)
    before = int(getattr(run_all_engines, "_cache_hits", 0))
    run_all_engines(orderbook=ob, trades=trades, price=10, recent_candles=recent)
    after = int(getattr(run_all_engines, "_cache_hits", 0))
    assert after >= before + 1

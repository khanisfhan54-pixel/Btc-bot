import threading
import numpy as np

import engine


def _base_inputs(price=100000.0):
    orderbook = {
        "bids": [[price - 5.0, 2.0], [price - 10.0, 1.5]],
        "asks": [[price + 5.0, 2.2], [price + 10.0, 1.2]],
    }
    candles = {"1m": [[1, price, price + 10.0, price - 10.0, price, 10.0] for _ in range(40)]}
    trades = [{"price": price, "amount": 0.1, "side": "BUY", "ts": 1}]
    return orderbook, trades, candles


def test_run_all_engines_valid_inputs_returns_dict_with_finite_values():
    orderbook, trades, candles = _base_inputs()
    result = engine.run_all_engines(orderbook=orderbook, trades=trades, price=100000.0, recent_candles=candles)
    assert result is not None, "run_all_engines should return a result dict for valid inputs"
    assert isinstance(result, dict), "run_all_engines must return a dict"
    assert result["price"] > 0, "price must remain positive in valid engine output"
    assert not np.isnan(float(result.get("confidence", 0.0))), "confidence must be finite"


def test_run_all_engines_rejects_invalid_price_fail_closed():
    orderbook, trades, candles = _base_inputs(price=0.0)
    result = engine.run_all_engines(orderbook=orderbook, trades=trades, price=0.0, recent_candles=candles)
    assert result is not None, "invalid price path must still return structured output"
    assert isinstance(result, dict), "invalid price path must return dict"
    assert result.get("allow_trade") is False, "invalid price must fail closed"
    assert result.get("reason") == "invalid_price", "invalid price reason should be explicit"


def test_evaluate_meta_filter_fail_closed_when_unavailable(monkeypatch):
    monkeypatch.setattr(engine, "_get_meta_filter", lambda: None)
    res = engine.evaluate_meta_filter(features={}, signal={"signal": "LONG"})
    assert res["allow_trade"] is False, "meta filter unavailability must fail closed"


def test_run_all_engines_deterministic_and_thread_safe_cache_reads():
    orderbook, trades, candles = _base_inputs()
    kwargs = dict(orderbook=orderbook, trades=trades, price=100000.0, recent_candles=candles, symbol="BTC/USDT")
    result1 = engine.run_all_engines(**kwargs)
    result2 = engine.run_all_engines(**kwargs)
    assert result1 == result2, "identical inputs should return deterministic identical outputs"

    outputs = []
    lock = threading.Lock()

    def _worker():
        out = engine.run_all_engines(**kwargs)
        with lock:
            outputs.append(out)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(outputs) == 8, "all concurrent run_all_engines calls should complete"
    assert all(o == result1 for o in outputs), "concurrent identical requests should remain deterministic"


def test_shared_alpha_predictor_is_singleton_thread_safe():
    instances = []
    lock = threading.Lock()

    def _worker():
        inst = engine.get_shared_alpha_predictor()
        with lock:
            instances.append(inst)

    threads = [threading.Thread(target=_worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(instances) == 16
    assert len({id(i) for i in instances}) == 1


def test_build_trade_plan_uses_scaled_thresholds():
    price = 100000.0
    candles = [[i, price, price + 150.0, price - 120.0, price + 20.0, 10.0] for i in range(1, 50)]
    liquidity_map = {"liquidity_map": [{"price": 99850.0}, {"price": 100150.0}]}
    plan = engine.build_trade_plan(price, "LONG", liquidity_map, recent_candles=candles)
    assert isinstance(plan, dict)
    assert plan["entry"] > 0
    assert plan["sl"] < plan["entry"]
    assert len(plan["tp"]) == 3

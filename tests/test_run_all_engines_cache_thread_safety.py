import copy
import threading

from engine import run_all_engines, _build_run_all_engines_cache_key


def _inputs():
    recent = [[1, 50000.0, 50010.0, 49990.0, 50000.0, 100.0], [2, 50000.0, 50010.0, 49990.0, 50000.0, 100.0]]
    orderbook = {"bids": [[50000.0, 2.0]], "asks": [[50001.0, 2.0]]}
    trades = [{"price": 50000.0, "amount": 0.2, "side": "buy"}]
    kwargs = {
        "orderbook": orderbook,
        "trades": trades,
        "price": 50000.0,
        "symbol": "BTC/USDT",
        "recent_candles": recent,
        "open_interest": 1_000_000.0,
        "funding_rate": 0.0001,
        "orderbook_snapshots": [orderbook],
        "liquidation_events": [],
        "performance": {},
        "volume_intelligence": {"volume_strength": 0.5},
        "ohlcv": recent,
        "oi_history": [990000.0, 1_000_000.0],
        "current_oi": 1_000_000.0,
    }
    return recent, orderbook, trades, kwargs


def test_run_all_engines_cache_is_thread_safe_and_copy_safe():
    recent, orderbook, trades, kwargs = _inputs()
    run_all_engines._backtest_cache = {}
    run_all_engines._cache_hits = 0
    run_all_engines._cache_misses = 0

    first = run_all_engines(**kwargs)
    assert isinstance(first, dict)

    results = []
    errs = []
    thread_count = 10

    def worker():
        try:
            results.append(run_all_engines(**kwargs))
        except Exception as exc:  # pragma: no cover
            errs.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errs
    assert len(results) == thread_count
    assert all(r == results[0] for r in results)
    assert all(r is not results[0] for r in results[1:])

    results[0]["liquidity_map"]["liquidity_zones"].append({"price": 1, "size": 1})
    fresh = run_all_engines(**kwargs)
    assert {"price": 1, "size": 1} not in fresh["liquidity_map"].get("liquidity_zones", [])
    assert getattr(run_all_engines, "_cache_hits", 0) >= 1
    assert getattr(run_all_engines, "_cache_misses", 0) >= 1
    assert isinstance(fresh, dict)

    key = _build_run_all_engines_cache_key(**kwargs)
    run_all_engines._backtest_cache[key] = "malformed"
    out = run_all_engines(**kwargs)
    assert isinstance(out, dict)


def test_run_all_engines_cache_misses_when_semantically_relevant_inputs_change():
    _, _, _, base_kwargs = _inputs()
    run_all_engines._backtest_cache = {}
    run_all_engines._cache_hits = 0
    run_all_engines._cache_misses = 0

    run_all_engines(**base_kwargs)
    assert getattr(run_all_engines, "_cache_misses", 0) >= 1

    hits_before_same = getattr(run_all_engines, "_cache_hits", 0)
    run_all_engines(**copy.deepcopy(base_kwargs))
    assert getattr(run_all_engines, "_cache_hits", 0) == hits_before_same + 1

    def assert_changed_input_miss(changed_kwargs: dict) -> None:
        hits_before = getattr(run_all_engines, "_cache_hits", 0)
        misses_before = getattr(run_all_engines, "_cache_misses", 0)
        out = run_all_engines(**changed_kwargs)
        assert isinstance(out, dict)
        assert getattr(run_all_engines, "_cache_hits", 0) == hits_before
        assert getattr(run_all_engines, "_cache_misses", 0) == misses_before + 1

    k_funding = copy.deepcopy(base_kwargs)
    k_funding["funding_rate"] = 0.0002
    assert_changed_input_miss(k_funding)

    k_oi = copy.deepcopy(base_kwargs)
    k_oi["open_interest"] = 1_100_000.0
    assert_changed_input_miss(k_oi)

    k_liq = copy.deepcopy(base_kwargs)
    k_liq["liquidation_events"] = [{"side": "SELL", "size": 10.0, "price": 49900.0}]
    assert_changed_input_miss(k_liq)

    k_vi = copy.deepcopy(base_kwargs)
    k_vi["volume_intelligence"] = {"volume_strength": 0.8}
    assert_changed_input_miss(k_vi)

    k_trades = copy.deepcopy(base_kwargs)
    k_trades["trades"] = [{"price": 50005.0, "amount": 0.2, "side": "buy"}]  # same length, changed content
    assert_changed_input_miss(k_trades)

    cached = run_all_engines(**base_kwargs)
    cached["liquidity_map"]["liquidity_zones"].append({"price": 1, "size": 1})
    fresh = run_all_engines(**base_kwargs)
    assert {"price": 1, "size": 1} not in fresh["liquidity_map"].get("liquidity_zones", [])

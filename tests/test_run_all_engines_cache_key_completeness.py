import copy

from engine import _build_run_all_engines_cache_key


def _base_kwargs():
    recent = [[1, 50000.0, 50010.0, 49990.0, 50000.0, 100.0], [2, 50000.0, 50010.0, 49990.0, 50000.0, 100.0]]
    orderbook = {"bids": [[50000.0, 2.0]], "asks": [[50001.0, 2.0]]}
    return {
        "orderbook": orderbook,
        "trades": [{"price": 50000.0, "amount": 0.2, "side": "buy"}],
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
        "market_state_detector": None,
    }


def test_run_all_engines_cache_key_captures_semantic_inputs():
    base_kwargs = _base_kwargs()
    identical_kwargs_copy = copy.deepcopy(base_kwargs)

    kwargs_with_changed_funding_rate = copy.deepcopy(base_kwargs)
    kwargs_with_changed_funding_rate["funding_rate"] = 0.0002

    kwargs_with_changed_open_interest = copy.deepcopy(base_kwargs)
    kwargs_with_changed_open_interest["open_interest"] = 1_100_000.0

    kwargs_with_changed_liquidation_events = copy.deepcopy(base_kwargs)
    kwargs_with_changed_liquidation_events["liquidation_events"] = [{"side": "SELL", "size": 10.0, "price": 49900.0}]

    kwargs_with_changed_volume_intelligence = copy.deepcopy(base_kwargs)
    kwargs_with_changed_volume_intelligence["volume_intelligence"] = {"volume_strength": 0.7}

    kwargs_with_changed_trades = copy.deepcopy(base_kwargs)
    kwargs_with_changed_trades["trades"] = [{"price": 50005.0, "amount": 0.2, "side": "buy"}]

    assert _build_run_all_engines_cache_key(**base_kwargs) != _build_run_all_engines_cache_key(**kwargs_with_changed_funding_rate)
    assert _build_run_all_engines_cache_key(**base_kwargs) != _build_run_all_engines_cache_key(**kwargs_with_changed_open_interest)
    assert _build_run_all_engines_cache_key(**base_kwargs) != _build_run_all_engines_cache_key(**kwargs_with_changed_liquidation_events)
    assert _build_run_all_engines_cache_key(**base_kwargs) != _build_run_all_engines_cache_key(**kwargs_with_changed_volume_intelligence)
    assert _build_run_all_engines_cache_key(**base_kwargs) != _build_run_all_engines_cache_key(**kwargs_with_changed_trades)
    assert _build_run_all_engines_cache_key(**base_kwargs) == _build_run_all_engines_cache_key(**identical_kwargs_copy)

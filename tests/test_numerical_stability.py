import numpy as np
import threading

import engine
import main
from alpha_liquidity_sweep_predictor import predict_sweep
from replay_engine import ReplayEngine


def test_run_all_engines_nan_inf_and_empty_inputs_safe():
    out = engine.run_all_engines(orderbook={}, trades=[], price=float("nan"), recent_candles=[])
    assert isinstance(out, dict), "run_all_engines should return dict for NaN input"
    assert out.get("allow_trade") is False, "NaN price must fail closed"


def test_predict_sweep_extreme_values_stays_finite():
    output = predict_sweep(
        liquidity={
            "nearest_above": {"distance_points": 1e12, "price": 1e12},
            "nearest_below": {"distance_points": 1e-12, "price": 1e-12},
        },
        market_state={"state": "COMPRESSION", "compression": float("inf"), "volatility": float("nan"), "bias": 1e9},
        volume_intel={"volume_spike": True, "volume_strength": float("inf")},
    )
    output_arr = np.array([
        float(output.get("confidence", 0.0)),
        float(output.get("prob_above", 0.0)),
        float(output.get("prob_below", 0.0)),
    ])
    assert not np.isnan(output_arr).any(), "output must not contain NaN"
    assert not np.isinf(output_arr).any(), "output must not contain Inf"
    assert np.isfinite(output_arr).all(), "output must be finite"


def test_predict_sweep_empty_inputs_returns_safe_fallback():
    output = predict_sweep(liquidity={}, market_state={}, volume_intel={})
    output_arr = np.array([
        float(output.get("confidence", 0.0)),
        float(output.get("prob_above", 0.0)),
        float(output.get("prob_below", 0.0)),
    ])
    assert np.isfinite(output_arr).all(), "empty-input fallback should be finite"


def test_invalid_inputs_raise_or_safe_fallback():
    try:
        output = predict_sweep(liquidity=None, market_state=None, volume_intel=None)
        output_arr = np.array([
            float(output.get("confidence", 0.0)),
            float(output.get("prob_above", 0.0)),
            float(output.get("prob_below", 0.0)),
        ])
        assert np.isfinite(output_arr).all(), "invalid inputs should return safe finite fallback"
    except ValueError:
        assert True, "raising controlled ValueError is acceptable behavior"


def test_detect_liquidity_sweep_invalid_price_fail_closed():
    out = engine.detect_liquidity_sweep(trades=[], price=float("nan"))
    values = np.array([float(out.get("size_usd", 0.0))])
    assert out["sweep"] is False
    assert out["reason"] == "invalid_price"
    assert not np.isnan(values).any()
    assert not np.isinf(values).any()


def test_alpha_output_bounded_finite_and_deterministic():
    price = 100000.0
    orderbook = {"bids": [[price - 5.0, 2.0]], "asks": [[price + 5.0, 1.0]]}
    candles = {"1m": [[1, price, price + 20.0, price - 20.0, price, 10.0] for _ in range(60)]}
    kwargs = dict(
        orderbook=orderbook,
        trades=[{"price": price, "amount": 0.2, "side": "BUY"}],
        price=price,
        recent_candles=candles,
        symbol="BTC/USDT",
        open_interest=100.0,
        current_oi=100.0,
        orderbook_snapshots=[orderbook, orderbook, orderbook],
    )
    out1 = engine.run_all_engines(**kwargs)
    out2 = engine.run_all_engines(**kwargs)
    alpha = out1.get("alpha", {})
    arr = np.array([alpha.get("confidence", 0.0), alpha.get("prob_above", 0.0), alpha.get("prob_below", 0.0)], dtype=float)
    assert np.isfinite(arr).all()
    assert not np.isnan(arr).any()
    assert (arr >= 0.0).all() and (arr <= 1.0).all()
    assert out1 == out2


def test_alpha_output_deterministic_under_concurrency_and_shared_getter():
    price = 100000.0
    orderbook = {"bids": [[price - 5.0, 2.0]], "asks": [[price + 5.0, 1.0]]}
    candles = {"1m": [[1, price, price + 20.0, price - 20.0, price, 10.0] for _ in range(60)]}
    kwargs = dict(
        orderbook=orderbook,
        trades=[{"price": price, "amount": 0.2, "side": "BUY"}],
        price=price,
        recent_candles=candles,
        symbol="BTC/USDT",
        open_interest=100.0,
        current_oi=100.0,
        orderbook_snapshots=[orderbook, orderbook, orderbook],
    )
    baseline = engine.run_all_engines(**kwargs)
    outputs = []
    lock = threading.Lock()

    def _worker():
        out = engine.run_all_engines(**kwargs)
        arr = np.array([
            float(out.get("alpha", {}).get("confidence", 0.0)),
            float(out.get("alpha", {}).get("prob_above", 0.0)),
            float(out.get("alpha", {}).get("prob_below", 0.0)),
        ])
        assert not np.isnan(arr).any()
        with lock:
            outputs.append(out)

    threads = [threading.Thread(target=_worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(o == baseline for o in outputs)
    assert main.get_shared_alpha_predictor() is engine.get_shared_alpha_predictor()


def test_replay_state_hash_fallback_not_constant_for_distinct_bad_payloads():
    replay = ReplayEngine()
    a = []
    a.append(a)
    b = []
    b.append({"x": b})
    h1 = replay._state_hash({"payload": a, "schema_version": "2.3"})
    h2 = replay._state_hash({"payload": b, "schema_version": "2.3"})
    assert isinstance(h1, str) and isinstance(h2, str)
    assert h1 != h2


def test_replay_float_vector_is_numeric_and_finite_when_finite_input():
    replay = ReplayEngine()
    replay.record_event("update_start", {"vec": np.array([0.01, 0.02, 0.03], dtype=np.float64)})
    out = list(replay.replay())[0]["payload"]["vec"]
    assert out.dtype == np.float64
    assert np.isfinite(out).all()
    assert not np.isnan(out).any()

import random
import threading

from feature_engine import FeatureEngine
from alpha_liquidity_sweep_predictor import LiquiditySweepAlpha
from thread_safe_wrappers import ThreadSafeFeatureEngine, ThreadSafeAlphaPredictor


def test_thread_safe_feature_engine_update_concurrent():
    wrapped = ThreadSafeFeatureEngine(FeatureEngine())
    errs = []

    def worker(seed: int):
        rng = random.Random(seed)
        for _ in range(1000):
            px = 50000 + rng.uniform(-100, 100)
            snapshot = {
                "bids": [[px - 1, 2.0], [px - 2, 3.0]],
                "asks": [[px + 1, 2.0], [px + 2, 3.0]],
                "timestamp": 1.0,
            }
            trades = [{"price": px, "amount": 0.1, "side": "buy"}]
            try:
                wrapped.update(snapshot, trades)
            except Exception as exc:  # noqa: BLE001
                errs.append(exc)

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert not errs


def test_thread_safe_alpha_predictor_predict_concurrent():
    wrapped = ThreadSafeAlphaPredictor(LiquiditySweepAlpha())
    errs = []

    payload = {
        "price": 50000.0,
        "close_price": 50000.0,
        "curr_book": {"bids": [{"price": 49999.0, "size": 1.0}] * 10, "asks": [{"price": 50001.0, "size": 1.0}] * 10},
        "prev_book": {"bids": [{"price": 49999.0, "size": 1.0}] * 10, "asks": [{"price": 50001.0, "size": 1.0}] * 10},
        "timestamp": 1.0,
        "trades_count": 1,
        "atr": 10.0,
        "ema_fast": 50001.0,
        "ema_slow": 50000.0,
        "pre_sweep_depth": 1.0,
        "curr_depth": 1.0,
        "sweep_time_elapsed": 1.0,
    }

    def worker():
        for _ in range(1000):
            try:
                wrapped.predict(payload)
            except Exception as exc:  # noqa: BLE001
                errs.append(exc)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert not errs

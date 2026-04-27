import random
import numpy as np

from alpha_liquidity_sweep_predictor import LiquiditySweepAlpha


def _book(mid: float, ofi_bias: float):
    bids = []
    asks = []
    for i in range(10):
        bids.append({"price": mid - i - 1, "size": 10.0 + ofi_bias + i})
        asks.append({"price": mid + i + 1, "size": 10.0 - ofi_bias + i})
    return {"bids": bids, "asks": asks}


def test_welford_matches_numpy_variance():
    rng = random.Random(7)
    alpha = LiquiditySweepAlpha(history_window=200)
    values = []
    prev = _book(50000, 0.0)
    for _ in range(10_000):
        bias = rng.uniform(-5.0, 5.0)
        curr = _book(50000, bias)
        alpha.calculate_ofi_zscore(prev, curr)
        prev = curr
        values.append(alpha.ofi_history[-1])
        if len(values) > alpha.history_window:
            values.pop(0)
    np_var = float(np.var(np.asarray(values, dtype=float), ddof=1))
    impl_var = max(alpha._ofi_M2 / max(alpha._ofi_count - 1, 1), 1e-8)
    assert abs(np_var - impl_var) < 1e-6


def test_welford_large_constant_stays_finite():
    alpha = LiquiditySweepAlpha(history_window=300)
    prev = _book(50000, 1e6)
    for _ in range(50_000):
        curr = _book(50000, 1e6)
        alpha.calculate_ofi_zscore(prev, curr)
        prev = curr
    var = max(alpha._ofi_M2 / max(alpha._ofi_count - 1, 1), 1e-8)
    assert np.isfinite(var)
    assert var >= 0.0

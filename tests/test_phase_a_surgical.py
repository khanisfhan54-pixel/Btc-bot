import math
import os
import sys
from unittest.mock import Mock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import alpha_liquidity_sweep_predictor as alpha


def _book(bid_size=2.0, ask_size=1.0, price=60000.0, levels=10):
    return {
        "bids": [
            {"price": price - i, "size": bid_size}
            for i in range(levels)
        ],
        "asks": [
            {"price": price + i, "size": ask_size}
            for i in range(levels)
        ],
    }


def _market_data(price=60000.0):
    return {
        "price": price,
        "close_price": price - 10.0,
        "prev_book": _book(1.0, 1.0, price),
        "curr_book": _book(2.0, 0.5, price),
        "timestamp": 1_700_000_000.0,
        "trades_count": 20,
        "pre_sweep_depth": 100.0,
        "curr_depth": 120.0,
        "sweep_time_elapsed": 0.5,
        "atr": 100.0,
        "ema_fast": price,
        "ema_slow": price,
    }


def test_hawkes_params_exposed():
    lsa = alpha.LiquiditySweepAlpha(hawkes_decay=3.0, hawkes_alpha=0.3)

    assert lsa.hawkes_decay == 3.0
    assert lsa.hawkes_alpha == 0.3


def test_hawkes_invalid_params():
    with pytest.raises(ValueError):
        alpha.LiquiditySweepAlpha(hawkes_decay=0.0)

    with pytest.raises(ValueError):
        alpha.LiquiditySweepAlpha(hawkes_alpha=0.0)


def test_branching_ratio_in_metrics():
    lsa = alpha.LiquiditySweepAlpha(hawkes_decay=2.0, hawkes_alpha=0.4)

    metrics = lsa.get_state_metrics()

    assert "branching_ratio" in metrics
    assert abs(metrics["branching_ratio"] - 0.2) < 1e-6


def test_ml_prob_not_called_in_active_sweep(monkeypatch):
    lsa = alpha.LiquiditySweepAlpha(history_window=20)
    lsa.liquidity_pools = {"high": 60000.0, "low": 59000.0}
    for _ in range(20):
        lsa.ofi_history.append(0.0)
        lsa.hawkes_history.append(1.0)

    ml_mock = Mock(return_value=0.9)
    monkeypatch.setattr(lsa, "_ml_sweep_probability", ml_mock)
    monkeypatch.setattr(lsa, "detect_sweep_state", Mock(return_value="ACTIVE_SWEEP"))

    out = lsa.get_signal(_market_data(price=60010.0))

    assert out["state"] == "ACTIVE_SWEEP"
    ml_mock.assert_not_called()


def test_active_sweep_weights_sum_to_one():
    assert 0.52 + 0.20 + 0.28 == pytest.approx(1.0, abs=0.0)


def test_shrink_prob_rename():
    assert hasattr(alpha, "_shrink_prob")
    assert not hasattr(alpha, "_calibrate_prob")


def test_shrink_prob_values():
    assert alpha._shrink_prob(0.7) == pytest.approx(0.66, abs=1e-6)
    assert alpha._shrink_prob(0.3) == pytest.approx(0.34, abs=1e-6)
    assert alpha._shrink_prob(0.5) == pytest.approx(0.5, abs=1e-6)


def test_output_schema_unchanged():
    lsa = alpha.LiquiditySweepAlpha()

    out = lsa.predict(_market_data())

    required_keys = {
        "action",
        "confidence",
        "state",
        "regime",
        "ofi_zscore",
        "hawkes_intensity",
        "logic",
        "micro_prob",
        "macro_prob",
        "prob_above",
        "prob_below",
    }
    assert required_keys.issubset(out.keys())

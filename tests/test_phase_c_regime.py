import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import alpha_liquidity_sweep_predictor as alpha
from alpha_liquidity_sweep_predictor import _VALID_REGIMES


def _book(price=100.0, bid_size=10.0, ask_size=10.0, levels=10):
    return {
        "bids": [{"price": price - i, "size": bid_size} for i in range(levels)],
        "asks": [{"price": price + i, "size": ask_size} for i in range(levels)],
    }


def _market_data(price=100.0, timestamp=1_700_000_000.0, atr=1.0, ema_fast=None, ema_slow=None):
    return {
        "price": price,
        "close_price": price,
        "prev_book": _book(price=price),
        "curr_book": _book(price=price),
        "timestamp": timestamp,
        "trades_count": 20,
        "pre_sweep_depth": 100.0,
        "curr_depth": 250.0,
        "sweep_time_elapsed": 1.0,
        "atr": atr,
        "ema_fast": price if ema_fast is None else ema_fast,
        "ema_slow": price if ema_slow is None else ema_slow,
        "bid_depth": 100.0,
        "ask_depth": 100.0,
    }


def _warm_model(model):
    prev = _book()
    curr = _book()
    for _ in range(25):
        model.calculate_ofi_zscore(prev, curr)
    for i in range(6):
        model._update_hawkes(1_700_000_000.0 + i, 20)


def _seed_pools(model, high=100.0, low=90.0):
    model.liquidity_pools["high"] = high
    model.liquidity_pools["low"] = low
    model._pool_set_bar["high"] = model._bar_count
    model._pool_set_bar["low"] = model._bar_count


def _patch_active_edge(monkeypatch, model, side="high"):
    monkeypatch.setattr(model, "detect_sweep_state", lambda price, atr, hawkes: "ACTIVE_SWEEP")
    monkeypatch.setattr(model, "_detect_fake_breakout", lambda sweep_side, close_price, ofi_z: (True, 1.0))
    monkeypatch.setattr(model, "check_resiliency", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(model, "_liquidity_forecast", lambda: 1.0)
    if side == "high":
        pred = {"prob_up": 0.01, "prob_down": 0.99}
        macro = {"prob_above": 0.01, "prob_below": 0.99}
    else:
        pred = {"prob_up": 0.99, "prob_down": 0.01}
        macro = {"prob_above": 0.99, "prob_below": 0.01}
    monkeypatch.setattr(model, "_predict_next_sweep", lambda *args, **kwargs: pred)
    monkeypatch.setattr(alpha, "predict_sweep", lambda *args, **kwargs: macro)


def test_detect_regime_returns_valid_vocab():
    model = alpha.LiquiditySweepAlpha()

    assert model._detect_regime(101.0, 100.0) == "TRENDING_UP"
    assert model._detect_regime(99.0, 100.0) == "TRENDING_DOWN"
    assert model._detect_regime(100.0, 100.0) == "RANGING"


def test_detect_regime_volatile_state():
    model = alpha.LiquiditySweepAlpha()

    assert model._detect_regime(101.0, 100.0, vol_ratio=0.02) == "VOLATILE"
    assert model._detect_regime(99.0, 100.0, vol_ratio=0.02) == "VOLATILE"


def test_volatile_in_valid_regimes():
    assert "VOLATILE" in _VALID_REGIMES
    assert "TRENDING_UP" in _VALID_REGIMES
    assert "TRENDING_DOWN" in _VALID_REGIMES
    assert "UPTREND" not in _VALID_REGIMES
    assert "DOWNTREND" not in _VALID_REGIMES


def test_active_sweep_hold_in_trending_up_high_sweep(monkeypatch):
    model = alpha.LiquiditySweepAlpha()
    _warm_model(model)
    _seed_pools(model, high=100.0, low=90.0)
    _patch_active_edge(monkeypatch, model, side="high")

    out = model.get_signal(_market_data(price=100.5, ema_fast=103.0, ema_slow=100.0))

    assert out["action"] == "HOLD"
    assert "suppressed" in out["logic"]
    assert "trend_aligned=True" in out["logic"]


def test_active_sweep_hold_in_trending_down_low_sweep(monkeypatch):
    model = alpha.LiquiditySweepAlpha()
    _warm_model(model)
    _seed_pools(model, high=110.0, low=100.0)
    _patch_active_edge(monkeypatch, model, side="low")

    out = model.get_signal(_market_data(price=99.5, ema_fast=97.0, ema_slow=100.0))

    assert out["action"] == "HOLD"
    assert "suppressed" in out["logic"]
    assert "trend_aligned=True" in out["logic"]


def test_active_sweep_not_blocked_in_ranging(monkeypatch):
    model = alpha.LiquiditySweepAlpha()
    _warm_model(model)
    _seed_pools(model, high=100.0, low=90.0)
    _patch_active_edge(monkeypatch, model, side="high")

    out = model.get_signal(_market_data(price=100.5, ema_fast=100.0, ema_slow=100.0))

    assert out["action"] in {"SELL", "BUY"}
    assert "trend_aligned=False" in out["logic"]


def test_volatile_gate_suppresses_pre_sweep(monkeypatch):
    model = alpha.LiquiditySweepAlpha()
    _warm_model(model)
    _seed_pools(model)
    monkeypatch.setattr(model, "detect_sweep_state", lambda price, atr, hawkes: "PRE_SWEEP_BUILDUP")

    out = model.get_signal(_market_data(price=99.5, atr=2.0, ema_fast=100.0, ema_slow=100.0))

    assert out["action"] == "HOLD"
    assert out["regime"] == "VOLATILE"
    assert "VOLATILE regime gate" in out["logic"]


def test_volatile_gate_suppresses_active_sweep(monkeypatch):
    model = alpha.LiquiditySweepAlpha()
    _warm_model(model)
    _seed_pools(model)
    monkeypatch.setattr(model, "detect_sweep_state", lambda price, atr, hawkes: "ACTIVE_SWEEP")

    out = model.get_signal(_market_data(price=100.5, atr=2.0, ema_fast=103.0, ema_slow=100.0))

    assert out["action"] == "HOLD"
    assert out["regime"] == "VOLATILE"
    assert "VOLATILE regime gate" in out["logic"]


def test_volatile_gate_count_increments(monkeypatch):
    model = alpha.LiquiditySweepAlpha()
    _warm_model(model)
    _seed_pools(model)
    monkeypatch.setattr(model, "detect_sweep_state", lambda price, atr, hawkes: "PRE_SWEEP_BUILDUP")

    for i in range(3):
        model.get_signal(_market_data(price=99.5, timestamp=1_700_000_100.0 + i, atr=2.0))

    assert model.get_state_metrics()["volatile_gate_count"] == 3


def test_volatile_gate_count_in_metrics():
    metrics = alpha.LiquiditySweepAlpha().get_state_metrics()

    assert "volatile_gate_count" in metrics
    assert metrics["volatile_gate_count"] == 0


def test_regime_output_field_is_valid_vocab():
    model = alpha.LiquiditySweepAlpha()
    out = model.get_signal(_market_data(price=100.0, ema_fast=100.0, ema_slow=100.0))
    assert out["regime"] in _VALID_REGIMES

    out = model.get_signal(_market_data(price=100.0, timestamp=1_700_000_001.0, ema_fast=103.0, ema_slow=100.0))
    assert out["regime"] in _VALID_REGIMES
    assert out["regime"] == "TRENDING_UP"


def test_confidence_scaled_in_trending_up_no_external_context(monkeypatch):
    model = alpha.LiquiditySweepAlpha(direction_mode="fade")
    _warm_model(model)
    _seed_pools(model, high=100.0, low=90.0)
    monkeypatch.setattr(model, "detect_sweep_state", lambda price, atr, hawkes: "PRE_SWEEP_BUILDUP")
    monkeypatch.setattr(model, "_predict_next_sweep", lambda *args, **kwargs: {"prob_up": 0.99, "prob_down": 0.01})
    monkeypatch.setattr(alpha, "predict_sweep", lambda *args, **kwargs: {"prob_above": 0.99, "prob_below": 0.01})

    out = model.get_signal(_market_data(price=99.5, ema_fast=103.0, ema_slow=100.0), regime_context=None)
    combined_prob = float(out["logic"].split("Prob: ", 1)[1])

    assert out["state"] == "PRE_SWEEP_BUILDUP"
    assert out["regime"] == "TRENDING_UP"
    assert out["action"] == "SELL"
    assert out["confidence"] < combined_prob


def test_confidence_scaled_in_trending_down_no_external_context(monkeypatch):
    model = alpha.LiquiditySweepAlpha(direction_mode="fade")
    _warm_model(model)
    _seed_pools(model, high=110.0, low=100.0)
    monkeypatch.setattr(model, "detect_sweep_state", lambda price, atr, hawkes: "PRE_SWEEP_BUILDUP")
    monkeypatch.setattr(model, "_predict_next_sweep", lambda *args, **kwargs: {"prob_up": 0.01, "prob_down": 0.99})
    monkeypatch.setattr(alpha, "predict_sweep", lambda *args, **kwargs: {"prob_above": 0.01, "prob_below": 0.99})

    out = model.get_signal(_market_data(price=100.5, ema_fast=97.0, ema_slow=100.0), regime_context=None)
    combined_prob = float(out["logic"].split("Prob: ", 1)[1])

    assert out["state"] == "PRE_SWEEP_BUILDUP"
    assert out["regime"] == "TRENDING_DOWN"
    assert out["action"] == "BUY"
    assert out["confidence"] < combined_prob


def test_phase_b_items_still_present():
    metrics = alpha.LiquiditySweepAlpha().get_state_metrics()

    assert "ofi_level_weighting" in metrics
    assert "pool_age_high_bars" in metrics
    assert "branching_ratio" in metrics

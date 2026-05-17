import math
import time

import numpy as np

import advanced_regime_engine as are
import alpha_liquidity_sweep_predictor as lsp
import main
from trade_lifecycle_manager import TradeLifecycleManager


def _book(px: float, size: float = 1.0):
    bids = [{"price": px - i, "size": size + i * 0.1} for i in range(10)]
    asks = [{"price": px + i, "size": size + i * 0.1} for i in range(10)]
    return {"bids": bids, "asks": asks}


def test_format_execution_message_reconciliation_block_returns_string():
    main._reconciliation_blocks[main.SYMBOL] = time.time() + 10
    try:
        msg = main._format_execution_message("title", "LONG", 100.0, 0.9, correlation_id="abc")
        assert isinstance(msg, str)
        assert "reconciliation_block_active" in msg
    finally:
        main._reconciliation_blocks[main.SYMBOL] = 0.0


def test_liquidity_predictor_timestamp_normalization_consistent_units():
    model = lsp.LiquiditySweepAlpha()
    sec = 1_700_000_000.0
    ms = sec * 1000.0
    ns = sec * 1_000_000_000.0
    m1 = model._normalize_timestamp(sec)
    m2 = model._normalize_timestamp(ms)
    m3 = model._normalize_timestamp(ns)
    assert abs(m1 - m2) < 1e-6
    assert abs(m1 - m3) < 1e-6


def test_ofi_rolling_accumulators_match_history_stats():
    model = lsp.LiquiditySweepAlpha(history_window=30)
    prev = _book(100.0)
    for i in range(60):
        curr = _book(100.0 + (i % 3) * 0.1, size=1.0 + (i % 5) * 0.2)
        model.calculate_ofi_zscore(prev, curr)
        prev = curr
    expected_sum = float(sum(model.ofi_history))
    expected_sq = float(sum(v * v for v in model.ofi_history))
    assert math.isclose(model.ofi_sum, expected_sum, rel_tol=1e-12, abs_tol=1e-12)
    assert math.isclose(model.ofi_sq_sum, expected_sq, rel_tol=1e-12, abs_tol=1e-12)


def test_trade_lifecycle_correlation_id_not_stale():
    tl = TradeLifecycleManager()
    first = tl.get_correlation_id()
    assert first == ""
    tl.on_entry(symbol="BTC/USDT")
    active = tl.get_correlation_id()
    tl.on_exit(symbol="BTC/USDT")
    second = tl.get_correlation_id()
    assert active
    assert first != active
    assert second == ""
    assert second != active


def test_sjm_weight_shape_validation_blocks_malformed(monkeypatch):
    bad = {"sjm_centroids": np.zeros((3, 4), dtype=float)}

    def _load(*args, **kwargs):
        return bad

    monkeypatch.setattr(are.ModelWeightManager, "load_weights", _load)
    eng = are.AdvancedRegimeEngine(n_features=3, enable_background_workers=False)
    assert eng._weights_loaded is False


def test_set_price_anchor_recovers_mode_when_timestamp_resumes():
    eng = are.AdvancedRegimeEngine(enable_background_workers=False)
    ok, _ = eng._set_price_anchor(100.0, None, 1)
    assert ok
    assert eng._pnl_mode == "TICK"
    ok2, reason = eng._set_price_anchor(101.0, 1_700_000_000.0, 2)
    assert ok2, reason
    assert eng._pnl_mode == "TIMESTAMP"


def test_hold_preserved_under_l1_book():
    lsa = lsp.LiquiditySweepAlpha(enable_sweep_directional_fallback=False)
    l1_book = {
        "bids": [{"price": 60000.0, "size": 1.0}],
        "asks": [{"price": 60010.0, "size": 1.0}],
    }
    out = lsa.get_signal({
        "price": 60005.0,
        "prev_book": l1_book,
        "curr_book": l1_book,
        "timestamp": 1700000000.0,
        "trades_count": 50,
        "atr": 150.0,
        "ema_fast": 60010.0,
        "ema_slow": 60005.0,
    })
    assert out["action"] == "HOLD"
    assert out["ofi_zscore"] == 0.0 or abs(out["ofi_zscore"]) < 0.01

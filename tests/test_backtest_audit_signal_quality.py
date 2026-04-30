from backtest_engine import BacktestEngine, BacktestConfig


def _candles(n=120):
    return [[i * 60_000, 50000.0, 50020.0, 49980.0, 50000.0 + (i % 3), 100000.0] for i in range(n)]


def test_backtest_signal_only_isolation_and_alpha_present():
    bt = BacktestEngine(config=BacktestConfig(), signal_only=True)
    out = bt.run_backtest(_candles(), signal_quality_required=False, allow_ohlcv_synthetic=True)
    assert out["signal_only_mode"] is True
    assert bt.execution_logic is None
    assert out["alpha_non_empty_count"] > 0
    assert out["signal_quality_valid"] is False
    assert "ohlcv_synthetic_microstructure" in out["signal_quality_reason"]


def test_backtest_output_contains_signal_quality_schema():
    bt = BacktestEngine(config=BacktestConfig(), signal_only=True)
    out = bt.run_backtest(_candles(), signal_quality_required=False, allow_ohlcv_synthetic=True)
    for key in ("signal_coverage", "signal_quality_valid", "signal_quality_reason", "signal_only_mode"):
        assert key in out


def test_backtest_determinism_schema_stable():
    bt = BacktestEngine(config=BacktestConfig(), signal_only=True)
    out1 = bt.run_backtest(_candles(), signal_quality_required=False, allow_ohlcv_synthetic=True)
    out2 = bt.run_backtest(_candles(), signal_quality_required=False, allow_ohlcv_synthetic=True)
    for key in ("signal_coverage", "long_signals", "short_signals", "hold_signals", "alpha_non_empty_count", "signal_quality_reason"):
        assert out1[key] == out2[key]


def test_signal_quality_mode_rejects_ohlcv_only_inputs():
    bt = BacktestEngine(config=BacktestConfig(), signal_only=True)
    out = bt.run_backtest(_candles(), signal_quality_required=True, allow_ohlcv_synthetic=False)
    assert out["signal_quality_valid"] is False
    assert out["signal_quality_reason"] == "signal_quality_requires_microstructure_replay_data"
    assert out["total_trades"] == 0

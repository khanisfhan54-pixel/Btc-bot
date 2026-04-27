from backtest_engine import BacktestEngine


def test_backtest_early_return_counters_initialized():
    bt = BacktestEngine()
    out = bt.run_backtest([[0, 1, 1, 1, 1, 1]] * 10)
    assert out["total_trades"] == 0

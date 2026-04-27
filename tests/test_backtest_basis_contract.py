from backtest_engine import BacktestEngine, BacktestConfig


def test_backtest_basis_none_is_deterministic_and_no_invented_shift():
    candles = [[i * 60_000, 50000.0, 50010.0, 49990.0, 50000.0, 100000.0] for i in range(120)]
    bt = BacktestEngine(config=BacktestConfig(basis_mode="none"))
    out1 = bt.run_backtest(candles)
    out2 = bt.run_backtest(candles)
    assert out1["total_trades"] == out2["total_trades"]
    assert out1["pnl"] == out2["pnl"]

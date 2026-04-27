from unittest.mock import patch

import main


def test_main_backtest_delegates_to_backtest_engine_contract():
    candles = [[i * 60000, 50000.0, 50010.0, 49990.0, 50000.0, 100000.0] for i in range(80)]
    payload = {
        "total_trades": 1,
        "pnl": 100.0,
        "win_rate": 1.0,
        "max_drawdown": 0.0,
        "sharpe": 1.0,
        "trade_log": [],
    }
    with patch("main.BacktestEngine.run_backtest", return_value=payload) as run_bt, patch.object(main, "regime_engine", None):
        out = main.run_backtest(ohlcv_data=candles, limit=len(candles))

    assert run_bt.call_count == 1
    assert out["total_trades"] == 1
    assert out["final_equity"] == 10100.0
    assert out["total_return_pct"] == 1.0
    assert out["win_rate_pct"] == 100.0
    assert out["trades"] == []
    assert out["timeframe"] is not None
    assert isinstance(out, dict)

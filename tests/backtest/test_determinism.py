from main import run_backtest

def test_determinism():
    data=[[i,100,100,100,100,1] for i in range(200)]
    a=run_backtest(ohlcv_data=data)
    b=run_backtest(ohlcv_data=data)
    assert a.get("final_equity")==b.get("final_equity")

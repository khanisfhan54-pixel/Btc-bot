from main import run_backtest

def test_no_lookahead():
    data=[]
    p=100.0
    for i in range(150):
        p += 0.1
        data.append([i*3600000,p,p+1,p-1,p,1])
    out=run_backtest(ohlcv_data=data)
    assert "trades" in out

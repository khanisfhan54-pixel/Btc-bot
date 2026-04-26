import threading
from engine import run_all_engines, _ALPHA_STATE

def test_alpha_state_concurrency():
    errs=[]
    def worker():
        try:
            run_all_engines(orderbook={"bids":[[1,1]],"asks":[[1.1,1]]}, trades=[{"price":1,"amount":1,"side":"buy"}], price=1.0, symbol="BTC/USDT", recent_candles=[[0,1,1,1,1,1]])
        except Exception as e:
            errs.append(e)
    ts=[threading.Thread(target=worker) for _ in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    d=_ALPHA_STATE.get("BTCUSDT",{}).get("direction","NEUTRAL")
    assert d in ("LONG","SHORT","NEUTRAL")

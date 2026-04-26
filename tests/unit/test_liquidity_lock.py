import threading
from engine import _LIQUIDITY_SWEEP_ALPHA, _LIQUIDITY_UPDATE_LOCK

def test_liquidity_lock_no_torn_state():
    errs=[]
    def worker():
        try:
            for _ in range(100):
                with _LIQUIDITY_UPDATE_LOCK:
                    _LIQUIDITY_SWEEP_ALPHA.update_liquidity_pools([1,2],[0.5,0.7])
                    out=_LIQUIDITY_SWEEP_ALPHA.get_signal({"price":1,"close_price":1,"prev_book":{},"curr_book":{},"timestamp":0,"trades_count":0,"atr":0.1,"ema_fast":1,"ema_slow":1})
                    assert isinstance(out,dict)
        except Exception as e:
            errs.append(e)
    t1=threading.Thread(target=worker); t2=threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert not errs

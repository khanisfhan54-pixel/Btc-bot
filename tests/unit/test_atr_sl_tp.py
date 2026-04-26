from main import _compute_atr_sl_tp

def test_atr_sl_tp_high_vol():
    candles=[]
    p=100.0
    for i in range(20):
        candles.append([i,p,p*1.05,p*0.95,p,1])
    sl,tp=_compute_atr_sl_tp(candles,100.0)
    assert sl>0.005
    assert tp>sl

def test_atr_sl_tp_short():
    assert _compute_atr_sl_tp([[0,1,1,1,1,1]]*10,100.0)==(None,None)

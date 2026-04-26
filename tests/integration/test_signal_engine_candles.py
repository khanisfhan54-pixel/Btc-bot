from signal_engine import SignalEngine

def test_signal_engine_candles_non_hold():
    se=SignalEngine()
    candles=[[i,100+i,101+i,99+i,100+i,1] for i in range(60)]
    out=se.generate({"candles":candles,"imbalance":0.2})
    assert out.get("signal") != "HOLD"

import main

def test_position_size_bounds(monkeypatch):
    monkeypatch.setattr(main, "EXCHANGE_MIN_NOTIONAL_USD", 10000.0)
    monkeypatch.setattr(main, "calculate_position_size", lambda balance, risk_percent, stop_loss_distance: 0.1)
    monkeypatch.setattr(main.engine, "get_balance", lambda : 100.0)
    out=main._execute_liquidity_trade("LONG",100.0,0.8,{"1h":[[0,100,101,99,100,1]]*20},{"liquidity_sweep":{"side":"BUY","sweep":True}},sl_price=99,tp_price=101)
    assert out["executed"] is False
    assert out["reason"] == "position_size_out_of_bounds"

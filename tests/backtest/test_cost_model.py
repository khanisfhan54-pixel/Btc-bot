from main import BacktestCostModel

def test_cost_model_round_trip():
    c=BacktestCostModel()
    assert c.round_trip_cost_pct > 0

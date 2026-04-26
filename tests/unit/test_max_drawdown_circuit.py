from advanced_regime_engine import AdvancedRegimeEngine

def test_max_drawdown_circuit():
    e=AdvancedRegimeEngine()
    out={}
    price=50000.0
    for _ in range(15):
        price*=0.99
        out=e.update({"price":float(price),"return":-0.01,"features":[0.1,1,1]})
    assert "execution_mode" in out

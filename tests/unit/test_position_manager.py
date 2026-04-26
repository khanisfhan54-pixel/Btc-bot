import pytest
from position_manager import PositionManager

def test_position_manager_lifecycle(tmp_path):
    pm = PositionManager(path=str(tmp_path/'positions.json'))
    pm.on_entry("BTC/USDT","LONG",1.0,100.0,"o1")
    assert pm.has_position("BTC/USDT")
    with pytest.raises(RuntimeError):
        pm.on_entry("BTC/USDT","LONG",1.0,100.0,"o2")
    out = pm.on_exit("BTC/USDT",110.0,"now")
    assert out["realized_pnl"] > 0
    assert not pm.has_position("BTC/USDT")

def test_position_manager_persist(tmp_path):
    p = tmp_path/'positions.json'
    pm = PositionManager(path=str(p))
    pm.on_entry("BTC/USDT","LONG",1.0,100.0,"o1")
    pm2 = PositionManager(path=str(p))
    assert pm2.has_position("BTC/USDT")

import pytest

execution_mod = pytest.importorskip("execution")
ExecutionEngine = execution_mod.ExecutionEngine



class DummyExchange:
    def create_order(self, **kwargs):
        if kwargs.get("type") == "STOP_MARKET":
            raise RuntimeError("sl failed")
        return {"id": "x1", "price": 100.0}

    def create_market_order(self, symbol, side, amount):
        return {"id": "m1"}

    def cancel_order(self, order_id, symbol=None):
        raise RuntimeError("cancel failed")


class DummyLE:
    pass


def test_bracket_failure_compensation_calls_alert(monkeypatch):
    engine = ExecutionEngine(exchange=DummyExchange(), learning_engine=DummyLE())
    called = {"n": 0}

    def fake_alert(msg: str):
        called["n"] += 1

    monkeypatch.setattr(engine, "_send_emergency_alert", fake_alert)
    out = engine.place_order_with_sl_tp("BTC/USDT", "buy", 1.0, 90.0, 110.0)
    assert out["partial_failure"] is True
    assert called["n"] == 1
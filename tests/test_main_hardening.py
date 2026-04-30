import time
import threading
import pytest
import main


def test_h2_reconciliation_prune():
    now = time.time()
    main._reconciliation_blocks.clear()
    main._reconciliation_blocks.update({
        "a": now - 10,
        "b": now - 1,
        "c": now - 5,
        "d": now + 10,
        "e": now + 20,
    })
    main._prune_reconciliation_blocks()
    assert len(main._reconciliation_blocks) == 2
    assert all(v > time.time() for v in main._reconciliation_blocks.values())


def test_c2_fallback_engine_trade_abort(monkeypatch):
    monkeypatch.setattr(main, "ENGINE_IS_FALLBACK", True)
    class E:
        def get_balance(self):
            return 0.0
    monkeypatch.setattr(main, "engine", E())
    out = main._execute_liquidity_trade("LONG", 100.0, 0.8, {"1h": [[0,0,0,0,100,0]]*20}, {}, sl_price=99.0, tp_price=101.0, position_size=None)
    assert out["executed"] is False
    assert out["reason"] == "fallback_engine_zero_balance"


def test_h1_fetch_timeout_partial_failure(monkeypatch):
    main.FETCH_TIMEOUT_SECONDS = 0.1
    def fake_fetch(exchange, symbol, timeframe="1m", limit=240):
        if timeframe == "1m":
            time.sleep(0.3)
        return [[1,2,3,4,5,6]]
    monkeypatch.setattr(main, "fetch_ohlcv", fake_fetch)
    start = time.time()
    out = main._fetch_multi_tf(object(), "BTC/USDT")
    elapsed = time.time() - start
    assert out["1m"] == []
    assert out["5m"] and out["15m"] and out["1h"]
    assert elapsed < 5


def test_m2_cold_start_log_distinction(monkeypatch, caplog):
    main._cold_start_complete = False
    main._last_valid_features = None
    main._last_valid_features_ts = 0.0

    class FX:
        id = "x"
        def load_markets(self):
            return {"BTC/USDT": {}}
    ex = FX()
    monkeypatch.setattr(main, "_fetch_multi_tf", lambda *_: {"1m": [[0,0,0,0,100,1]], "15m": [[0,0,0,0,100,1]], "5m": [], "1h": [[0,0,0,0,100,1]]})
    monkeypatch.setattr(main, "fetch_orderbook", lambda *_: {"bids": [[100,1]], "asks": [[101,1]]})
    monkeypatch.setattr(main, "fetch_recent_trades", lambda *_: [])
    monkeypatch.setattr(main, "run_all_engines", lambda **_: {})
    monkeypatch.setattr(main, "analyze_volume_intelligence", lambda **_: {})
    monkeypatch.setattr(main, "_fetch_open_interest", lambda *_: 0.0)
    monkeypatch.setattr(main, "_fetch_funding_rate", lambda *_: 0.0)
    monkeypatch.setattr(main, "feature_engine", type("F", (), {"update": lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))})())

    main.run_analysis_cycle(ex)
    assert "Cold start feature engine failure" in caplog.text

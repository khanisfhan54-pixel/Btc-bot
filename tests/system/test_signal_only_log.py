import logging
import main

class DummyEx:
    id="x"
    def load_markets(self):
        return {"BTC/USDT":{}}

def test_signal_only_log(caplog, monkeypatch):
    monkeypatch.setattr(main, "_fetch_multi_tf", lambda e,s:{"1m":[[0,1,1,1,1,1]],"1h":[[0,1,1,1,1,1]]})
    monkeypatch.setattr(main, "fetch_orderbook", lambda e,s:{"bids":[[1,1]],"asks":[[1,1]]})
    monkeypatch.setattr(main, "fetch_recent_trades", lambda e,s:[])
    monkeypatch.setattr(main, "analyze_volume_intelligence", lambda **k:{})
    monkeypatch.setattr(main, "run_all_engines", lambda **k:{})
    caplog.set_level(logging.WARNING)
    main.SIGNAL_PIPELINE_CONFIG["signal_only_mode"]=True
    main.run_analysis_cycle(DummyEx(), None, DummyEx(), "BTC/USDT")
    assert "signal_only_mode=True" in caplog.text

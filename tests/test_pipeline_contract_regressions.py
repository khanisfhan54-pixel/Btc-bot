import logging

import alpha_orchestrator as ao
import engine as engine_mod
import main


class DummyEx:
    id = "x"

    def load_markets(self):
        return {"BTC/USDT": {}}


def _base_monkeypatch(monkeypatch):
    monkeypatch.setattr(main, "_fetch_multi_tf", lambda e, s: {"1m": [[1700000000, 100, 101, 99, 100, 1]], "1h": [[1700000000, 100, 101, 99, 100, 1]]})
    monkeypatch.setattr(main, "fetch_orderbook", lambda e, s: {"bids": [[100.0, 1.0]], "asks": [[100.1, 1.0]]})
    monkeypatch.setattr(main, "fetch_recent_trades", lambda e, s: [{"price": 100.0, "amount": 0.1, "side": "buy"}])
    monkeypatch.setattr(main, "analyze_volume_intelligence", lambda **k: {})
    monkeypatch.setattr(main, "run_all_engines", lambda **k: {"alpha": {}, "liquidity_map": {}})
    monkeypatch.setattr(main.signal_engine, "generate", lambda f: {"signal": "LONG", "confidence": 0.8})
    monkeypatch.setattr(main.order_router, "route", lambda *a, **k: {"execute": True, "reason": "test", "order_type": "market"})
    monkeypatch.setattr(main, "evaluate_meta_filter", lambda **k: {"allow_trade": True, "risk_scale": 1.0, "meta_state": {}})
    monkeypatch.setattr(main.execution_engine, "decide", lambda **k: {"execute": True, "position_size": 0.01, "sl": 99.0, "tp": 101.0, "reason": "ok"})
    monkeypatch.setattr(main.capital_allocator, "allocate", lambda **k: {"capital_scale": 1.0, "allow_trading": True, "max_exposure": 1000.0, "reason": "ok"})
    monkeypatch.setattr(main.engine, "get_balance", lambda: 1000.0)


def test_alpha_signal_rejects_negative_expected_edge():
    try:
        ao.AlphaSignal(
            source_id="src",
            direction=-1,
            conviction=0.5,
            expected_edge_bps=-1.0,
            timestamp=1700000000.0,
        )
        assert False, "expected ValueError for negative expected_edge_bps"
    except ValueError:
        pass


def test_main_orchestrator_failure_is_fail_closed(monkeypatch, caplog):
    _base_monkeypatch(monkeypatch)
    main.SIGNAL_PIPELINE_CONFIG["signal_only_mode"] = False
    monkeypatch.setattr(main.alpha_orchestrator, "orchestrate", lambda *a, **k: (_ for _ in ()).throw(TypeError("bad orchestrator inputs")))
    caplog.set_level(logging.ERROR)
    result = main.run_analysis_cycle(DummyEx(), None, DummyEx(), "BTC/USDT")
    assert result["execution"]["executed"] is False
    assert "orchestrate failed; fail-closed HOLD" in caplog.text


def test_engine_smc_cache_key_distinguishes_same_second_market_changes(monkeypatch):
    calls = {"n": 0}

    def fake_smc(**kwargs):
        calls["n"] += 1
        return {"signal": "LONG" if kwargs["price"] > 100 else "SHORT", "confidence": 8}

    monkeypatch.setattr(engine_mod, "evaluate_smc_sniper", fake_smc)

    base_kwargs = {
        "orderbook": {"bids": [[100.0, 1.0]], "asks": [[100.1, 1.0]]},
        "trades": [{"price": 100.0, "amount": 0.1}],
        "exchange": None,
        "symbol": "BTC/USDT",
        "cascade_prob": 0.0,
        "recent_candles": {"1m": [[1700000000, 100, 101, 99, 100, 1.0]]},
        "open_interest": 1_000_000.0,
        "funding_rate": 0.0,
        "liquidation_events": [],
        "performance": {},
        "volume_intelligence": {},
        "orderbook_snapshots": [{"bids": [[100.0, 1.0]], "asks": [[100.1, 1.0]]}],
    }

    engine_mod.run_all_engines(price=100.0, **base_kwargs)
    changed = dict(base_kwargs)
    changed["orderbook"] = {"bids": [[101.0, 2.0]], "asks": [[101.1, 1.0]]}
    changed["orderbook_snapshots"] = [changed["orderbook"]]
    engine_mod.run_all_engines(price=101.0, **changed)
    assert calls["n"] == 2


def test_live_allocator_contract_uses_canonical_signature(monkeypatch):
    _base_monkeypatch(monkeypatch)
    main.SIGNAL_PIPELINE_CONFIG["signal_only_mode"] = False
    monkeypatch.setattr(
        main.alpha_orchestrator,
        "orchestrate",
        lambda *a, **k: ao.OrchestratedAction(
            action=ao.Action.HOLD,
            net_conviction=0.0,
            expected_edge_bps=0.0,
            urgency=0.0,
            meta_info={},
        ),
    )
    captured = {}

    def capture_allocate(**kwargs):
        captured.update(kwargs)
        return {"capital_scale": 1.0, "allow_trading": True, "max_exposure": 1000.0, "reason": "ok"}

    monkeypatch.setattr(main.capital_allocator, "allocate", capture_allocate)
    main.run_analysis_cycle(DummyEx(), None, DummyEx(), "BTC/USDT")
    if captured:
        assert set(["signal_confidence", "regime_context", "current_equity"]).issubset(captured.keys())

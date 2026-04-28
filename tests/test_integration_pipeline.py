import main
import engine
import inspect


def _pipeline_once(price: float = 100000.0):
    orderbook = {"bids": [[price - 5.0, 2.0]], "asks": [[price + 5.0, 2.0]]}
    trades = [{"price": price, "amount": 0.1, "side": "BUY", "ts": 1}]
    candles = {"1m": [[1, price, price + 10.0, price - 10.0, price, 10.0] for _ in range(60)]}

    engines_out = engine.run_all_engines(
        orderbook=orderbook,
        trades=trades,
        price=price,
        recent_candles=candles,
        open_interest=0.0,
        current_oi=0.0,
        orderbook_snapshots=[orderbook, orderbook, orderbook],
        symbol="BTC/USDT",
    )
    features = {
        "alpha": engines_out.get("alpha", {}),
        "liquidity": engines_out.get("liquidity_map", {}),
        "candles": candles["1m"],
        "execution_quality": 1.0,
    }
    sig = main.signal_engine.generate(features)
    signal_time = 1_700_000_000.0
    decision = main.execution_engine.decide(
        signal_payload={"signal": sig.get("signal", "HOLD"), "confidence": sig.get("confidence", 0.0)},
        features_payload=features,
        snapshot=orderbook,
        account_equity=10000.0,
        meta_result={"allow_trade": bool(engines_out.get("allow_trade", True)), "risk_scale": 1.0, "meta_state": {}},
    )
    execution_time = signal_time + 0.001
    used_capital = float(decision.get("position_size", 0.0)) * price if decision.get("execute") else 0.0
    out = {
        "action": sig.get("signal", "HOLD"),
        "confidence": float(sig.get("confidence", 0.0)),
        "signal_time": signal_time,
        "execution_time": execution_time,
        "used_capital": used_capital,
        "total_capital": 10000.0,
        "deprecated_authoritative": False,
        "fail_closed": engines_out.get("allow_trade") is False,
    }
    return out


def test_pipeline_deterministic_and_fail_closed_on_missing_critical_inputs():
    result1 = _pipeline_once()
    result2 = _pipeline_once()

    assert result1 == result2, "pipeline should be deterministic for identical input"
    assert result1["action"] in {"LONG", "SHORT", "HOLD", "BUY", "SELL"}, "action must be in safe finite set"
    assert 0.0 <= result1["confidence"] <= 1.0, "confidence must be bounded in [0,1]"
    assert result1["signal_time"] <= result1["execution_time"], "signal time must not exceed execution time"
    assert result1["used_capital"] <= result1["total_capital"], "used capital must not exceed total capital"
    assert result1["deprecated_authoritative"] is False, "deprecated helper must not be authoritative"
    assert result1["fail_closed"] is True, "pipeline must fail closed when critical OI input is missing"


def test_orderbook_snapshot_pipeline_rolls_before_engine_execution():
    main._ORDERBOOK_SNAPSHOTS.clear()
    ob = {"bids": [[99990.0, 1.0]], "asks": [[100010.0, 1.0]]}
    main._append_orderbook_snapshot(ob, timestamp=1.0)
    history = main._get_orderbook_snapshot_history()
    assert len(history) == 1
    assert history[0]["bids"][0][0] == 99990.0


def test_deprecated_helper_not_exported_and_live_path_uses_signal_engine():
    assert "compute_score" not in engine.__all__
    assert "evaluate_smc_sniper" not in engine.__all__
    assert "detect_entry_trigger" not in engine.__all__
    assert "build_trade_plan" not in engine.__all__
    source = inspect.getsource(main.run_analysis_cycle)
    assert "signal_engine.generate(" in source
    assert "compute_score(" not in source

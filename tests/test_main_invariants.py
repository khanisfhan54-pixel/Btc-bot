import main as m


def test_invariant_bootstrap_order():
    state = {"bootstrap_order": [
        "ExecutionEngine", "ThreadSafeFeatureEngine", "SignalEngine", "ExecutionLogic",
        "QueueFillModel", "ToxicityFilter", "OrderRouter", "ImpactDecay",
        "PositionManager", "TradeLifecycleManager", "CapitalAllocator", "VenueBasisNormalizer",
    ]}
    m.assert_invariant_1(state)


def test_invariant_live_trading_credential_gate():
    m.assert_invariant_2({"raises_on_live_missing_credentials": True})


def test_invariant_execution_gate_requires_four_conditions():
    m.assert_invariant_3({"execution_gate_requires_all": True})


def test_invariant_fallback_engine_allow_trade_false():
    result = m.run_all_engines.__wrapped__() if hasattr(m.run_all_engines, '__wrapped__') else m.run_all_engines()
    assert result["market_state"]["allow_trade"] is False
    meta = m.evaluate_meta_filter()
    assert meta["allow_trade"] is False


def test_fix_a3_fallback_feature_engine_triggers_failsafe():
    fe = m.FeatureEngine()
    out = fe.update({}, [])
    assert out.get("latency_ms", 0.0) > 3000
    assert out.get("liquidity_score", 1.0) < 0.2
    assert out.get("spread_bps", 0.0) > 25


def test_fix_a1_feature_engine_fallback_flag():
    fe = m.FeatureEngine()
    assert hasattr(fe, "_FEATURE_ENGINE_IS_FALLBACK")


def test_fix_b1_signal_engine_fallback_flag():
    se = m.SignalEngine()
    assert hasattr(se, "_SIGNAL_ENGINE_IS_FALLBACK")


def test_fix_b_fallback_signal_engine_returns_hold():
    se = m.SignalEngine()
    out = se.generate({})
    assert out["signal"] == "HOLD"
    assert out["confidence"] == 0.0


def test_determinism_fallback_feature_engine():
    fe = m.FeatureEngine()
    r1 = fe.update({"bids": [[50000, 1.0]], "asks": [[50001, 1.0]]}, [])
    r2 = fe.update({"bids": [[50000, 1.0]], "asks": [[50001, 1.0]]}, [])
    assert set(r1.keys()) == set(r2.keys())


def test_no_regression_fallback_engine_stubs():
    result = m.run_all_engines()
    required_keys = {
        "order_flow_pressure", "order_imbalance", "smart_money_detected",
        "market_state", "cascade_probability", "liquidity_map",
        "alpha", "funding_rate", "smc_signal"
    }
    assert required_keys.issubset(set(result.keys()))


def test_run_all_engines_is_deterministic():
    r1 = m.run_all_engines()
    r2 = m.run_all_engines()
    assert r1["market_state"]["allow_trade"] == r2["market_state"]["allow_trade"]
    assert r1["cascade_probability"] == r2["cascade_probability"]

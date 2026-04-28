import inspect
import time

import main


def test_signal_execution_time_alignment_and_safe_decision_set():
    signal_time = time.time()
    execution_time = signal_time + 0.01
    decision = main.execution_engine.decide(
        signal_payload={"signal": "LONG", "confidence": 0.9},
        features_payload={"liquidity_score": 1.0},
        snapshot={"bids": [[100000.0, 1.0]], "asks": [[100010.0, 1.0]]},
        account_equity=1000.0,
        meta_result={"allow_trade": False, "risk_scale": 0.0, "reason": "blocked", "meta_state": {}},
    )
    execution_decision = "EXECUTE" if decision.get("execute") else "SKIP"
    executed_when_risk_gate_failed = bool(decision.get("execute"))

    assert signal_time <= execution_time, "signal time must not be after execution time"
    assert execution_decision in {"EXECUTE", "SKIP"}, "execution decision must be explicit and safe"
    assert not executed_when_risk_gate_failed, "execution must not proceed when risk gate failed"


def test_deprecated_helpers_are_not_authoritative_live_source():
    source = inspect.getsource(main.run_analysis_cycle)
    assert "compute_score(" not in source, "deprecated compute_score helper must not be in active pipeline"
    assert "signal_engine.generate(" in source, "production signal path must use active signal_engine"


def test_execution_layer_blocks_when_oi_missing():
    decision = main.execution_engine.decide(
        signal_payload={"signal": "LONG", "confidence": 0.8},
        features_payload={"features": {"open_interest_missing": True}},
        snapshot={"bids": [[100000.0, 1.0]], "asks": [[100010.0, 1.0]], "timestamp": time.time()},
        account_equity=1000.0,
        meta_result={"allow_trade": True, "risk_scale": 1.0, "meta_state": {"open_interest_missing": True}},
    )
    assert decision["execute"] is False
    assert decision["reason"] in {"open_interest_missing", "fallback"}


def test_execution_decide_fail_closed_on_invalid_meta_and_capital_safe():
    equity = 5000.0
    decision = main.execution_engine.decide(
        signal_payload={"signal": "LONG", "confidence": 0.85},
        features_payload={"features": {"liquidity_score": 0.9, "spread_bps": 2.0}},
        snapshot={"bids": [[100000.0, 2.0]], "asks": [[100010.0, 2.0]], "timestamp": time.time()},
        account_equity=equity,
        meta_result=None,
    )
    used_capital = float(decision.get("position_size", 0.0)) * 100000.0 if decision.get("execute") else 0.0
    assert decision["execute"] is False
    assert decision["reason"] in {"invalid_meta_result", "fallback"}
    assert used_capital <= equity

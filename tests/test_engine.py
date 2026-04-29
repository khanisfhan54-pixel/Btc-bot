import threading
import warnings
import numpy as np

import engine
import main
from trading_utils import safe_float
from replay_engine import ReplayEngine


def _base_inputs(price=100000.0):
    orderbook = {
        "bids": [[price - 5.0, 2.0], [price - 10.0, 1.5]],
        "asks": [[price + 5.0, 2.2], [price + 10.0, 1.2]],
    }
    candles = {"1m": [[1, price, price + 10.0, price - 10.0, price, 10.0] for _ in range(40)]}
    trades = [{"price": price, "amount": 0.1, "side": "BUY", "ts": 1}]
    return orderbook, trades, candles


def test_run_all_engines_valid_inputs_returns_dict_with_finite_values():
    orderbook, trades, candles = _base_inputs()
    result = engine.run_all_engines(orderbook=orderbook, trades=trades, price=100000.0, recent_candles=candles)
    assert result is not None, "run_all_engines should return a result dict for valid inputs"
    assert isinstance(result, dict), "run_all_engines must return a dict"
    assert result["price"] > 0, "price must remain positive in valid engine output"
    assert not np.isnan(float(result.get("confidence", 0.0))), "confidence must be finite"


def test_run_all_engines_rejects_invalid_price_fail_closed():
    orderbook, trades, candles = _base_inputs(price=0.0)
    result = engine.run_all_engines(orderbook=orderbook, trades=trades, price=0.0, recent_candles=candles)
    assert result is not None, "invalid price path must still return structured output"
    assert isinstance(result, dict), "invalid price path must return dict"
    assert result.get("allow_trade") is False, "invalid price must fail closed"
    assert result.get("reason") == "invalid_price", "invalid price reason should be explicit"


def test_evaluate_meta_filter_fail_closed_when_unavailable(monkeypatch):
    monkeypatch.setattr(engine, "_get_meta_filter", lambda: None)
    res = engine.evaluate_meta_filter(features={}, signal={"signal": "LONG"})
    assert res["allow_trade"] is False, "meta filter unavailability must fail closed"


def test_run_all_engines_deterministic_and_thread_safe_cache_reads():
    orderbook, trades, candles = _base_inputs()
    kwargs = dict(orderbook=orderbook, trades=trades, price=100000.0, recent_candles=candles, symbol="BTC/USDT")
    result1 = engine.run_all_engines(**kwargs)
    result2 = engine.run_all_engines(**kwargs)
    assert result1 == result2, "identical inputs should return deterministic identical outputs"

    outputs = []
    lock = threading.Lock()

    def _worker():
        out = engine.run_all_engines(**kwargs)
        with lock:
            outputs.append(out)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(outputs) == 8, "all concurrent run_all_engines calls should complete"
    assert all(o == result1 for o in outputs), "concurrent identical requests should remain deterministic"


def test_shared_alpha_predictor_is_singleton_thread_safe():
    instances = []
    lock = threading.Lock()

    def _worker():
        inst = engine.get_shared_alpha_predictor()
        with lock:
            instances.append(inst)

    threads = [threading.Thread(target=_worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(instances) == 16
    assert len({id(i) for i in instances}) == 1


def test_build_trade_plan_uses_scaled_thresholds():
    price = 100000.0
    candles = [[i, price, price + 150.0, price - 120.0, price + 20.0, 10.0] for i in range(1, 50)]
    liquidity_map = {"liquidity_map": [{"price": 99850.0}, {"price": 100150.0}]}
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        plan = engine.build_trade_plan(price, "LONG", liquidity_map, recent_candles=candles)
    assert any(isinstance(w.message, DeprecationWarning) for w in rec)
    assert isinstance(plan, dict)
    assert plan["entry"] > 0
    assert plan["sl"] < plan["entry"]
    assert len(plan["tp"]) == 3


def test_deprecated_helpers_not_in_public_exports():
    assert "compute_score" not in engine.__all__
    assert "evaluate_smc_sniper" not in engine.__all__
    assert "detect_entry_trigger" not in engine.__all__
    assert "build_trade_plan" not in engine.__all__
    assert "build_trade_plan_for_signal" not in engine.__all__


def test_get_market_data_spread_gate_reachable_and_excessive_blocked():
    price = 100000.0
    realistic = {
        "bids": [[99999.5, 12.0], [99999.0, 10.0]],
        "asks": [[100000.5, 1.0], [100001.0, 1.0]],
    }
    wide = {
        "bids": [[99900.0, 10.0], [99890.0, 9.0]],
        "asks": [[100200.0, 1.0], [100220.0, 1.0]],
    }
    snaps = [realistic, realistic, realistic]
    trades = [{"price": 100000.0, "amount": 2.0, "side": "BUY"} for _ in range(120)]
    out_reachable = engine.get_market_data(realistic, trades=trades, recent_candles=[], price=price, orderbook_snapshots=snaps)
    out_blocked = engine.get_market_data(wide, trades=[], recent_candles=[], price=price, orderbook_snapshots=snaps)
    assert out_reachable["signal"] in {"LONG", "SHORT"}
    assert out_blocked["signal"] == "NONE"


def test_spoof_reliability_requires_minimum_snapshots():
    one_snap = [{"bids": [[100000.0, 5000.0]], "asks": [[100010.0, 5000.0]]}]
    details_unreliable = engine._detect_spoofing_details(one_snap)
    assert details_unreliable["reliable"] is False
    assert details_unreliable["spoof"] is False
    assert details_unreliable["reason"] == "insufficient_snapshots"

    full_history = [
        {"bids": [[100000.0, 5000.0], [99999.0, 100.0], [99998.0, 100.0]], "asks": [[100010.0, 10.0], [100011.0, 10.0], [100012.0, 10.0]]},
        {"bids": [[100000.0, 100.0], [99999.0, 100.0], [99998.0, 100.0]], "asks": [[100010.0, 10.0], [100011.0, 10.0], [100012.0, 10.0]]},
        {"bids": [[100000.0, 50.0], [99999.0, 100.0], [99998.0, 100.0]], "asks": [[100010.0, 10.0], [100011.0, 10.0], [100012.0, 10.0]]},
    ]
    details_reliable = engine._detect_spoofing_details(full_history)
    assert details_reliable["reliable"] is True


def test_market_state_detector_failure_is_fail_closed_with_stable_reason():
    class BadDetector:
        def detect(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    orderbook, trades, candles = _base_inputs()
    out = engine.run_all_engines(
        orderbook=orderbook,
        trades=trades,
        price=100000.0,
        recent_candles=candles,
        current_oi=100.0,
        open_interest=100.0,
        market_state_detector=BadDetector(),
    )
    assert out["allow_trade"] is False
    assert out["market_state"]["reason"] == "market_state_detector_error"
    assert out["reason"] == "market_state_detector_error"


def test_safe_float_consistent_across_modules_and_deterministic():
    bad_obj = object()
    cases = [None, float("nan"), float("inf"), "123.45", "bad", bad_obj]
    for value in cases:
        r1 = engine._safe_float(value, 7.0)
        r2 = main._safe_float(value, 7.0)
        r3 = safe_float(value, 7.0)
        assert r1 == r2 == r3
        assert engine._safe_float(value, 7.0) == r1


def test_main_and_engine_share_alpha_getter_contract():
    assert main.get_shared_alpha_predictor() is engine.get_shared_alpha_predictor()


def test_replay_validation_detects_divergence_for_mutated_history():
    replay = ReplayEngine()
    replay.record_event("update_start", {"price": 1.0, "regime": "A"})
    replay.record_event("update_end", {"regime": "A"})
    replay.record_event("update_start", {"price": 2.0, "regime": "B"})
    replay.record_event("update_end", {"regime": "B"})
    replay.snapshot({"schema_version": "2.3", "equity": 2.0, "confirmed_regime": "A"})
    replay._events[-2]["payload"]["price"] = 200.0

    class E:
        def __init__(self):
            self._strict_replay = True
            self._fsm_error = None
            self._is_replay = False
            self._equity = 0.0
            self._confirmed_regime = "INIT"
            self._rng = np.random.default_rng(7)
        def update(self, payload):
            self._equity += float(payload.get("price", 0.0))
            self._confirmed_regime = payload.get("regime", self._confirmed_regime)
        def _trigger_circuit_breaker(self, _reason): return None
        def _self_heal(self, _error=None): return None
        def serialize_state(self):
            return {"schema_version": "2.3", "equity": self._equity, "confirmed_regime": self._confirmed_regime}
        def load_snapshot(self, snapshot):
            self.load_state(snapshot.get("state", {}))
        def load_state(self, state):
            self._equity = float(state.get("equity", 0.0))
            self._confirmed_regime = str(state.get("confirmed_regime", "INIT"))

    result = replay.validate_replay(E, snapshot_index=-1)
    assert result["diverged"] is True


def test_replay_numeric_arrays_remain_numeric_usable():
    replay = ReplayEngine()
    vec = np.array([1.0, 2.0, np.nan], dtype=np.float64)
    replay.record_event("update_start", {"vec": vec})
    replay.record_event("update_end", {})
    out = list(replay.replay())[0]["payload"]["vec"]
    assert isinstance(out, np.ndarray)
    assert out.dtype == np.float64
    assert not np.isnan(out[:2]).any()

import math
import threading
import inspect

import replay_engine
from replay_engine import ReplayEngine
from advanced_regime_engine import AdvancedRegimeEngine


def _mk_engine():
    return AdvancedRegimeEngine(n_features=3, n_states=3, enable_background_workers=False, load_model_weights_on_init=False)


def _market(i=0):
    return {"price":100.0+i,"volume":1.0,"bid":99.9+i,"ask":100.1+i,"timestamp":i+1}


def test_01_hash_determinism_non_pickle():
    class _CustomObj:
        def __reduce_ex__(self, protocol):
            return (_CustomObj, ())
    re = ReplayEngine()
    obj = _CustomObj()
    h1 = re._state_hash({"obj": obj})
    h2 = re._state_hash({"obj": obj})
    assert h1 == h2
    assert "pickle" not in open("replay_engine.py", "r", encoding="utf-8").read()


def test_02_hash_stability_constants():
    re = ReplayEngine()
    a = {"regime": "TREND", "equity": 1.0, "garch_prob": [0.5, 0.5]}
    b = {"regime": "BEAR", "equity": 0.9, "nested": {"a": 1, "b": 2}}
    assert re._state_hash(a) == re._state_hash(a)
    assert re._state_hash(b) == re._state_hash(b)


def test_03_subprocess_safety_source():
    src = inspect.getsource(ReplayEngine._run_snapshot_restore_with_timeout)
    assert "serialize_state" in src and "engine_class" in src


def test_04_replay_side_effect_suppression():
    src = inspect.getsource(replay_engine)
    assert "logging.disable(logging.CRITICAL)" in src


def test_05_record_event_replay_guard():
    re = ReplayEngine()
    re._is_replaying = True
    re.record_event("test", {})
    assert len(list(re._events)) == 0
    re._is_replaying = False
    re.record_event("test", {})
    assert len(list(re._events)) == 1


def test_06_snapshot_replay_guard():
    re = ReplayEngine()
    re._is_replaying = True
    re.snapshot({"key": "value"})
    assert len(re._snapshots) == 0
    re._is_replaying = False
    re.snapshot({"key": "value"})
    assert len(re._snapshots) == 1


def test_07_concurrent_record_event_during_apply_events():
    re = ReplayEngine()
    re.record_event("update_start", {"x":1})
    re.record_event("update_end", {"tick_id": 1})

    class Stub:
        _strict_replay = True
        def serialize_state(self): return {}
        def load_state(self, s): return None
        def update(self, payload):
            re.record_event("concurrent", {})

    re.apply_events(Stub(), 0)
    assert not any(e.get("type") == "concurrent" for e in re._events)


def test_08_decision_trace_recording():
    re = ReplayEngine()
    re.record_decision_trace({"tick_id": 1, "engine_id":"e", "signal_type":"update_end", "regime_label":"TREND",
                              "regime_confidence":1.0, "position_size":1.0, "signed_position_size":1.0,
                              "execution_mode":"live","execution_side":"buy","edge_score":1.0,"conviction":1.0,
                              "risk_level":0.1,"feed_status":"OK","engine_status":"OK","timestamp_ns":1,
                              "outcome_event_id":None,"return_ema":0.0,"abs_return_ema":0.0,"shock_memory":0.0,
                              "switch_stability_ema":0.0,"loss_streak":0,"equity":1.0,"drawdown":0.0})
    traces = re.get_decision_traces(1)
    assert len(traces) >= 1
    t = traces[-1]
    for k in ["tick_id","engine_id","signal_type","regime_label","regime_confidence","position_size","signed_position_size","execution_mode","execution_side","edge_score","conviction","risk_level","feed_status","engine_status","timestamp_ns","outcome_event_id","return_ema","abs_return_ema","shock_memory","switch_stability_ema","loss_streak","equity","drawdown"]:
        assert k in t
    for k in ["regime_confidence","position_size","signed_position_size","edge_score","conviction","risk_level","return_ema","abs_return_ema","shock_memory","switch_stability_ema","equity","drawdown"]:
        assert math.isfinite(float(t[k]))
    out = re.get_decision_traces(1)
    out.clear()
    assert len(re.get_decision_traces(1)) >= 1


def test_09_outcome_linking():
    re = ReplayEngine()
    re.record_decision_trace({"tick_id": 2, "engine_id":"e", "signal_type":"update_end", "outcome_event_id":None})
    re.record_event("update_end", {"tick_id": 2})
    trace = re.get_decision_traces(1)[-1]
    assert isinstance(trace.get("outcome_event_id"), int)


def test_10_full_replay_determinism():
    re = ReplayEngine()
    for i in range(10):
        re.record_event("update_start", {"tick_id": i})
        re.record_event("update_end", {"tick_id": i})
    assert [e["id"] for e in re.replay_from(0)] == [e["id"] for e in re.replay_from(0)]


def test_11_invariants_battery():
    re = ReplayEngine()
    re.record_event("update_start", {"tick_id": 1})
    e = list(re._events)[0]
    assert set(e.keys()) == {"id","type","ts_ns","ts_monotonic_ns","source","dropped_events_before","payload"}


def test_12_baseline_regression():
    re = ReplayEngine()
    for i in range(20):
        re.record_event("update_start" if i % 2 == 0 else "update_end", {"tick_id": i})
    ev = list(re.replay())
    assert len(ev) == 20
    assert re.dropped_events() == 0 and re.dropped_snapshots() == 0

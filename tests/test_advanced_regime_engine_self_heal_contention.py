import copy
import threading

from advanced_regime_engine import AdvancedRegimeEngine


class _DummyError:
    def __init__(self):
        self.category = "input"
        self.code = "synthetic_error"


def test_self_heal_releases_lock_before_slow_side_effects_and_replay_payload_is_frozen(monkeypatch):
    eng = AdvancedRegimeEngine(enable_background_workers=False)

    block = threading.Event()
    unblock = threading.Event()
    done = threading.Event()
    acquired = threading.Event()
    recorded = []

    def slow_replay(event_type, payload=None):
        recorded.append((event_type, copy.deepcopy(payload)))
        block.set()
        unblock.wait(timeout=1.0)
        done.set()

    monkeypatch.setattr(eng, "_error_category_resolver", lambda _code: _DummyError())
    monkeypatch.setattr(eng, "_replay_record", slow_replay)
    monkeypatch.setattr(eng, "_warn_rate_limited", lambda *args, **kwargs: True)
    monkeypatch.setattr(eng, "_obs_observe", lambda *args, **kwargs: None)

    def healer():
        with eng._lock:
            eng._self_heal(error_code="synthetic_error", context={"k": "v"})

    t1 = threading.Thread(target=healer)
    t1.start()

    assert block.wait(timeout=0.5) is True

    def lock_probe():
        with eng._lock:
            acquired.set()

    t2 = threading.Thread(target=lock_probe)
    t2.start()

    assert acquired.wait(timeout=0.5) is True
    unblock.set()
    assert done.wait(timeout=1.0) is True

    t1.join(timeout=1.0)
    t2.join(timeout=1.0)

    assert eng._healing_count == 1
    assert eng._last_healing_error == "synthetic_error"
    assert eng._last_healing_context == {"k": "v"}
    assert len(eng._cb_trigger_history) >= 0

    assert recorded[0][0] == "self_heal"
    assert recorded[0][1]["error"] == "synthetic_error"
    assert recorded[0][1]["action"] == "RESET_INPUT"
    assert recorded[0][1]["state"]["last_healing_error"] == "synthetic_error"
    assert recorded[0][1]["state"]["last_healing_context"] == {"k": "v"}

    eng._last_healing_context["k"] = "mutated"
    eng._last_healing_error = "mutated_error"
    assert recorded[0][1]["state"]["last_healing_context"] == {"k": "v"}
    assert recorded[0][1]["state"]["last_healing_error"] == "synthetic_error"

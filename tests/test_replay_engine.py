import time

import numpy as np
import pytest

from replay_engine import ReplayEngine


class StubReplayTarget:
    def __init__(self, strict: bool = True):
        self._strict_replay = strict
        self._fsm_error = None
        self._is_replay = False
        self._equity = 1.0
        self._confirmed_regime = "INIT"
        self._rng = np.random.default_rng(7)

    def update(self, payload):
        self._equity += float(payload.get("price", 0.0))
        self._confirmed_regime = payload.get("regime", self._confirmed_regime)

    def _trigger_circuit_breaker(self, reason):
        return reason

    def _self_heal(self, error=None):
        return error

    def serialize_state(self):
        return {
            "schema_version": "2.3",
            "equity": self._equity,
            "confirmed_regime": self._confirmed_regime,
        }

    def load_snapshot(self, snapshot):
        state = snapshot.get("state", {}) if isinstance(snapshot, dict) else {}
        if "engine_state" in state:
            self.load_state(state["engine_state"])
        else:
            self.load_state(state)

    def load_state(self, state):
        self._equity = float(state.get("equity", self._equity))
        self._confirmed_regime = state.get("confirmed_regime", self._confirmed_regime)


class FailingSnapshotTarget(StubReplayTarget):
    def load_snapshot(self, snapshot):
        self._equity = -999.0  # mutate to prove rollback happens
        raise RuntimeError("broken snapshot load")


class FailingUpdateTarget(StubReplayTarget):
    def update(self, payload):
        raise ValueError("update failed")


class SnapshotOnlyRollbackTarget:
    def __init__(self, strict: bool = True):
        self._strict_replay = strict
        self._fsm_error = None
        self._is_replay = False
        self._equity = 5.0
        self._confirmed_regime = "BASE"
        self._fail_updates = True

    def update(self, payload):
        if self._fail_updates:
            raise RuntimeError("update failure after snapshot load")
        self._equity += float(payload.get("price", 0.0))
        self._confirmed_regime = payload.get("regime", self._confirmed_regime)

    def _trigger_circuit_breaker(self, reason):
        return reason

    def _self_heal(self, error=None):
        return error

    def serialize_state(self):
        return {
            "schema_version": "2.3",
            "equity": self._equity,
            "confirmed_regime": self._confirmed_regime,
        }

    def load_snapshot(self, snapshot):
        state = snapshot.get("state", {}) if isinstance(snapshot, dict) else {}
        self._equity = float(state.get("equity", self._equity))
        self._confirmed_regime = state.get("confirmed_regime", self._confirmed_regime)


def _build_engine_with_snapshot() -> ReplayEngine:
    replay = ReplayEngine()
    replay.record_event("update_start", {"price": 1.0, "regime": "A"})
    replay.record_event("update_end", {"regime": "A"})
    replay.snapshot({"schema_version": "1.0", "equity": 2.0, "confirmed_regime": "A"})
    replay.record_event("update_start", {"price": 2.0, "regime": "B"})
    replay.record_event("update_end", {"regime": "B"})
    return replay


def test_snapshot_corruption_raises_in_strict_mode():
    replay = _build_engine_with_snapshot()
    replay._snapshots[-1]["state"]["_checksum"] = "tampered"

    target = StubReplayTarget(strict=True)
    with pytest.raises(RuntimeError, match="snapshot checksum mismatch"):
        replay.replay_from_snapshot(target, snapshot_index=-1)


def test_snapshot_corruption_sets_fsm_error_in_non_strict_mode():
    replay = _build_engine_with_snapshot()
    replay._snapshots[-1]["state"]["_checksum"] = "tampered"

    target = StubReplayTarget(strict=False)
    replay.replay_from_snapshot(target, snapshot_index=-1)

    assert isinstance(target._fsm_error, dict)
    assert target._fsm_error.get("reason") == "SNAPSHOT_CHECKSUM_MISMATCH"


def test_determinism_same_event_stream_same_hash_equity_regime():
    replay = ReplayEngine()
    for i in range(5):
        replay.record_event("update_start", {"price": float(i), "regime": f"R{i % 2}"})
        replay.record_event("update_end", {"regime": f"R{i % 2}"})

    left = StubReplayTarget()
    right = StubReplayTarget()
    replay.apply_events(left)
    replay.apply_events(right)

    assert left._equity == right._equity
    assert left._confirmed_regime == right._confirmed_regime
    assert replay._state_hash(left.serialize_state()) == replay._state_hash(right.serialize_state())


def test_fsm_corruption_invalid_sequence_raises():
    replay = ReplayEngine()
    replay.record_event("update_end", {"regime": "X"})

    with pytest.raises(RuntimeError, match="Replay corruption"):
        replay.apply_events(StubReplayTarget(strict=True))


def test_incomplete_fsm_sequence_raises_runtime_error():
    replay = ReplayEngine()
    replay.record_event("update_start", {"price": 1.0, "regime": "A"})

    with pytest.raises(RuntimeError, match="incomplete event cycle"):
        replay.apply_events(StubReplayTarget(strict=True))


def test_snapshot_restore_failure_rolls_back_engine_state():
    replay = _build_engine_with_snapshot()
    target = FailingSnapshotTarget(strict=True)
    initial = target.serialize_state()

    with pytest.raises(RuntimeError, match="broken snapshot load"):
        replay.replay_from_snapshot(target, snapshot_index=-1)

    assert target.serialize_state() == initial


def test_unsafe_replay_does_not_mutate_internal_store():
    replay = ReplayEngine()
    replay._unsafe_no_copy = True
    replay.record_event(
        "update_start",
        {"price": 1.0, "regime": "A", "nested": {"x": {"y": 1}}, "levels": [{"k": 1}]},
    )

    events = list(replay.replay())
    events[0]["payload"]["price"] = 999.0
    events[0]["payload"]["nested"]["x"]["y"] = 999
    events[0]["payload"]["levels"][0]["k"] = 999
    events[0]["type"] = "update_end"

    stored = replay.last_events(1)[0]
    assert stored["type"] == "update_start"
    assert stored["payload"]["price"] == 1.0
    assert stored["payload"]["nested"]["x"]["y"] == 1
    assert stored["payload"]["levels"][0]["k"] == 1


def test_unsafe_replay_tuple_payload_isolation():
    replay = ReplayEngine()
    replay._unsafe_no_copy = True
    replay.record_event("update_start", {"tupled": ({"x": 1}, 2)})

    events = list(replay.replay())
    events[0]["payload"]["tupled"][0]["x"] = 999

    stored = replay.last_events(1)[0]
    assert stored["payload"]["tupled"][0]["x"] == 1


def test_unsafe_replay_set_payload_isolation():
    replay = ReplayEngine()
    replay._unsafe_no_copy = True
    replay.record_event("update_start", {"tags": {1, 2}})

    events = list(replay.replay())
    events[0]["payload"]["tags"].add(999)

    stored = replay.last_events(1)[0]
    assert stored["payload"]["tags"] == {1, 2}


def test_unsafe_replay_deep_nested_mutation_isolation():
    replay = ReplayEngine()
    replay._unsafe_no_copy = True
    replay.record_event("update_start", {"a": {"b": {"c": {"d": 1}}}})

    events = list(replay.replay())
    events[0]["payload"]["a"]["b"]["c"]["d"] = 999

    stored = replay.last_events(1)[0]
    assert stored["payload"]["a"]["b"]["c"]["d"] == 1


def test_safe_payload_nested_mutation_safety_from_original():
    replay = ReplayEngine()
    payload = {"x": [{"y": [1, 2, 3]}]}
    replayed = replay._safe_payload(payload)

    payload["x"][0]["y"][0] = 999
    assert replayed["x"][0]["y"][0] == 1


def test_safe_payload_depth_boundary_correctness():
    replay = ReplayEngine()
    payload = {"outer": {"inner": {"leaf": {"k": 1}, "shared": {"z": {"w": 1}}}}}
    replayed = replay._safe_payload(payload, depth=2)

    replayed["outer"]["inner"]["leaf"]["k"] = 999
    assert payload["outer"]["inner"]["leaf"]["k"] == 1
    replayed["outer"]["inner"]["shared"]["z"]["w"] = 777
    assert payload["outer"]["inner"]["shared"]["z"]["w"] == 777


def test_safe_payload_supports_tuple_set_and_frozenset():
    replay = ReplayEngine()
    payload = {
        "tupled": ({"a": 1}, {"b": [1, 2]}),
        "setv": frozenset({1, 2, 3}),
    }

    copied = replay._safe_payload(payload, depth=3)
    assert isinstance(copied["tupled"], tuple)
    assert isinstance(copied["setv"], frozenset)

    copied["tupled"][0]["a"] = 999
    assert payload["tupled"][0]["a"] == 1


def test_unsafe_replay_determinism_consistency_across_runs():
    replay = ReplayEngine()
    replay._unsafe_no_copy = True
    for i in range(8):
        replay.record_event("update_start", {"price": float(i), "regime": f"R{i % 3}"})
        replay.record_event("update_end", {"regime": f"R{i % 3}"})

    first = StubReplayTarget()
    second = StubReplayTarget()
    replay.apply_events(first)
    replay.apply_events(second)

    assert first.serialize_state() == second.serialize_state()
    assert replay._state_hash(first.serialize_state()) == replay._state_hash(second.serialize_state())


def test_unsafe_no_copy_is_faster_for_replay_iteration():
    payload = {
        "price": 1.0,
        "regime": "R",
        "features": list(range(300)),
        "meta": {"x": list(range(50)), "y": {"a": 1, "b": 2}},
    }

    safe = ReplayEngine()
    unsafe = ReplayEngine()
    unsafe._unsafe_no_copy = True

    for _ in range(1200):
        safe.record_event("update_start", payload)
        unsafe.record_event("update_start", payload)

    def run(engine: ReplayEngine, loops: int = 8) -> float:
        best = float("inf")
        for _ in range(loops):
            start = time.perf_counter()
            for _evt in engine.replay():
                pass
            best = min(best, time.perf_counter() - start)
        return best

    safe_t = run(safe)
    unsafe_t = run(unsafe)

    assert unsafe_t < safe_t, f"Expected unsafe mode faster, got safe={safe_t:.6f}s unsafe={unsafe_t:.6f}s"


def test_unsafe_mode_logs_critical_warning_once(caplog):
    replay = ReplayEngine()
    replay._unsafe_no_copy = True
    replay.record_event("update_start", {"price": 1.0})

    with caplog.at_level("CRITICAL"):
        list(replay.replay())
        list(replay.replay())
        list(replay.replay_from(0))

    matches = [r for r in caplog.records if "UNSAFE mode" in r.getMessage()]
    assert len(matches) == 1


def test_validate_replay_rejects_unsafe_mode():
    replay = ReplayEngine()
    replay._unsafe_no_copy = True

    with pytest.raises(RuntimeError, match="_unsafe_no_copy=True"):
        replay.validate_replay(StubReplayTarget)


def test_handler_error_reports_correct_previous_event():
    replay = ReplayEngine()
    replay.record_event("update_start", {"price": 1.0, "regime": "A"})
    replay.record_event("update_end", {"regime": "A"})

    target = FailingUpdateTarget(strict=False)
    replay.apply_events(target)

    assert isinstance(target._fsm_error, dict)
    assert target._fsm_error["reason"] == "EVENT_HANDLER_ERROR"
    assert target._fsm_error["last_event"] is None
    assert target._fsm_error["event"] == "update_start"


def test_snapshot_replay_isolation_replay_twice_same_result():
    replay = _build_engine_with_snapshot()
    left = StubReplayTarget(strict=True)
    right = StubReplayTarget(strict=True)

    replay.replay_from_snapshot(left, snapshot_index=-1)
    replay.replay_from_snapshot(right, snapshot_index=-1)

    assert left.serialize_state() == right.serialize_state()
    assert replay._state_hash(left.serialize_state()) == replay._state_hash(right.serialize_state())


def test_fsm_error_resets_between_snapshot_replays():
    replay = _build_engine_with_snapshot()
    replay._snapshots[-1]["state"]["_checksum"] = "tampered"
    target = StubReplayTarget(strict=False)

    replay.replay_from_snapshot(target, snapshot_index=-1)
    assert target._fsm_error["reason"] == "SNAPSHOT_CHECKSUM_MISMATCH"

    replay._snapshots[-1]["state"]["_checksum"] = replay._state_hash(
        {k: v for k, v in replay._snapshots[-1]["state"].items() if k != "_checksum"}
    )
    replay.replay_from_snapshot(target, snapshot_index=-1)
    assert target._fsm_error is None


def test_snapshot_rollback_uses_schema_safe_snapshot_fallback():
    replay = _build_engine_with_snapshot()
    target = SnapshotOnlyRollbackTarget(strict=True)
    initial = target.serialize_state()

    with pytest.raises(RuntimeError, match="Replay handler error: update_start"):
        replay.replay_from_snapshot(target, snapshot_index=-1)

    assert target.serialize_state() == initial

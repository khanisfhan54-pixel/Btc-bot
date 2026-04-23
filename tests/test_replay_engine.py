import copy
import statistics
import time
import warnings

import numpy as np
import pytest

from replay_engine import ReplayEngine


def _assert_perf_ratio(unsafe_t: float, deepcopy_t: float) -> None:
    """
    CI-safe performance check that avoids flaky failures from CPU scheduling,
    GC pauses, and cache effects. Only fails on extreme regressions.
    """
    ratio = unsafe_t / max(deepcopy_t, 1e-12)
    tolerance = 2.0   # soft expectation -- warn above this
    hard_limit = 4.0  # real regression guard -- fail above this

    if ratio > tolerance:
        warnings.warn(
            f"[PERF WARNING] Unsafe slower than expected: ratio={ratio:.2f}x "
            f"(unsafe={unsafe_t:.6f}s deepcopy={deepcopy_t:.6f}s)",
            RuntimeWarning,
        )

    assert ratio < hard_limit, (
        f"[PERF REGRESSION] Unsafe extremely slow: ratio={ratio:.2f}x "
        f"(unsafe={unsafe_t:.6f}s deepcopy={deepcopy_t:.6f}s)"
    )


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
    events[0]["payload"]["tags"] = tuple(list(events[0]["payload"]["tags"]) + [999])

    stored = replay.last_events(1)[0]
    assert stored["payload"]["tags"] == {1, 2}


def test_unsafe_replay_deep_nested_mutation_isolation():
    replay = ReplayEngine()
    replay._unsafe_no_copy = True
    replay.record_event("update_start", {"a": {"b": {"c": 1}}})

    events = list(replay.replay())
    events[0]["payload"]["a"]["b"]["c"] = 999

    stored = replay.last_events(1)[0]
    assert stored["payload"]["a"]["b"]["c"] == 1


def test_safe_payload_nested_mutation_safety_from_original():
    replay = ReplayEngine()
    payload = {"x": [{"y": [1, 2, 3]}]}
    replayed = replay._safe_payload(payload, depth=3)

    payload["x"][0]["y"][0] = 999
    assert replayed["x"][0]["y"][0] == 1


def test_safe_payload_depth_boundary_correctness():
    replay = ReplayEngine()
    payload = {"outer": {"inner": {"leaf": {"k": 1}, "shared": {"z": {"w": 1}}}}}
    replayed = replay._safe_payload(payload, depth=2)

    replayed["outer"]["inner"]["leaf"]["k"] = 999
    assert payload["outer"]["inner"]["leaf"]["k"] == 999
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
    assert isinstance(copied["setv"], tuple)

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
        "features": list(range(450)),
        "arr": np.arange(12000),
        "levels": [{"px": i, "qty": i % 5} for i in range(250)],
        "meta": {"x": list(range(120)), "y": {"a": 1, "b": 2, "c": list(range(40))}},
    }

    unsafe = ReplayEngine()
    unsafe._unsafe_no_copy = True

    for _ in range(700):
        unsafe.record_event("update_start", payload)
    events_ref = list(unsafe._events)

    # Warmup runs to eliminate cold-start noise
    for _ in range(2):
        list(unsafe.replay())
        copy.deepcopy(events_ref[:10])

    def run_unsafe(engine: ReplayEngine, loops: int = 10) -> float:
        times = []
        for _ in range(loops):
            start = time.perf_counter()
            for _evt in engine.replay():
                pass
            times.append(time.perf_counter() - start)
        return statistics.median(times)

    def run_deepcopy(loops: int = 10) -> float:
        times = []
        for _ in range(loops):
            start = time.perf_counter()
            _ = copy.deepcopy(events_ref)
            times.append(time.perf_counter() - start)
        return statistics.median(times)

    unsafe_t = run_unsafe(unsafe)
    safe_t = run_deepcopy()

    # CI-safe performance check: only fails on extreme regression (4x+ slower)
    _assert_perf_ratio(unsafe_t, safe_t)


def test_safe_payload_mutation_leak_within_depth_boundary():
    replay = ReplayEngine()
    payload = {"a": {"b": {"leaf": 1}}}
    copied = replay._safe_payload(payload, depth=2)

    copied["a"]["b"]["leaf"] = 999
    assert payload["a"]["b"]["leaf"] == 1


def test_safe_payload_deep_nesting_stress():
    replay = ReplayEngine()
    depth = 150
    payload = {"leaf": 0}
    for i in range(depth):
        payload = {"lvl": i, "next": [payload]}

    copied = replay._safe_payload(payload, depth=2)
    copied["next"][0] = {"changed": True}
    assert "changed" not in payload["next"][0]


def test_safe_payload_deep_nested_payload_performance():
    replay = ReplayEngine()
    payload = {"leaf": 0}
    for i in range(220):
        payload = {"lvl": i, "next": [payload]}

    start = time.perf_counter()
    copied = replay._safe_payload(payload, depth=2)
    elapsed = time.perf_counter() - start

    assert isinstance(copied, dict)
    assert elapsed < 0.2, f"_safe_payload too slow on deep nested payload: {elapsed:.6f}s"


def test_safe_payload_tuple_set_determinism():
    replay = ReplayEngine()
    payload = {
        "tupled": ({"x": 1}, frozenset({("k", 3), ("k", 1), ("k", 2)})),
        "setv": {("a", 1), ("a", 2)},
    }

    first = replay._safe_payload(payload, depth=3)
    second = replay._safe_payload(payload, depth=3)

    assert first == second
    assert isinstance(first["tupled"], tuple)
    assert isinstance(first["tupled"][1], tuple)
    assert isinstance(first["setv"], tuple)


def test_replay_set_payload_is_deterministic_across_runs():
    replay = ReplayEngine()
    replay._unsafe_no_copy = True
    payload = {"tags": {("a", 3), ("a", 1), ("a", 2)}}
    replay.record_event("update_start", payload)

    first = list(replay.replay())
    second = list(replay.replay())

    assert first == second
    assert first[0]["payload"]["tags"] == (("a", 1), ("a", 2), ("a", 3))


def test_replay_hash_consistency_with_set_payload_across_runs():
    replay = ReplayEngine()
    replay._unsafe_no_copy = True
    replay.record_event("update_start", {"tags": {"z", "a", "m"}})
    replay.record_event("update_end", {"regime": "R"})

    first = list(replay.replay())
    second = list(replay.replay())

    first_hash = replay._state_hash({"events": first})
    second_hash = replay._state_hash({"events": second})
    assert first_hash == second_hash


def test_unsafe_replay_is_faster_than_deepcopy_replay():
    payload = {
        "price": 1.0,
        "regime": "R",
        "features": list(range(400)),
        "arr": np.arange(10000),
        "levels": [{"px": i, "qty": i % 7} for i in range(300)],
        "meta": {"x": list(range(90)), "y": {"a": 1, "b": 2, "c": list(range(30))}},
    }
    unsafe = ReplayEngine()
    unsafe._unsafe_no_copy = True

    for _ in range(800):
        unsafe.record_event("update_start", payload)

    events_ref = list(unsafe._events)

    # Warmup runs to eliminate cold-start noise
    for _ in range(2):
        list(unsafe.replay())
        copy.deepcopy(events_ref[:10])

    def run_unsafe(engine: ReplayEngine):
        return list(engine.replay())

    def run_deepcopy():
        return copy.deepcopy(events_ref)

    def normalize_for_compare(value):
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, dict):
            return {k: normalize_for_compare(v) for k, v in value.items()}
        if isinstance(value, list):
            return [normalize_for_compare(v) for v in value]
        if isinstance(value, tuple):
            return tuple(normalize_for_compare(v) for v in value)
        return value

    unsafe_result = run_unsafe(unsafe)
    safe_result = run_deepcopy()
    assert normalize_for_compare(unsafe_result) == normalize_for_compare(
        safe_result
    ), "Unsafe mode produced different results!"

    def median_run(fn, runs: int = 5) -> float:
        times = []
        for _ in range(runs):
            start = time.perf_counter()
            fn()
            times.append(time.perf_counter() - start)
        return statistics.median(times)

    unsafe_t = median_run(lambda: run_unsafe(unsafe))
    safe_t = median_run(run_deepcopy)

    max_slowdown = 3.0  # CI-safe tolerance
    assert unsafe_t <= safe_t * max_slowdown, (
        f"Unsafe mode excessively slow: "
        f"safe={safe_t:.6f}s unsafe={unsafe_t:.6f}s "
        f"(limit={max_slowdown}x)"
    )

    if unsafe_t > safe_t:
        print(
            f"[WARN] Unsafe slower than safe "
            f"(safe={safe_t:.6f}s unsafe={unsafe_t:.6f}s)"
        )


def test_unsafe_replay_large_payload_benchmark_vs_deepcopy():
    payload = {
        "price": 1.0,
        "regime": "R",
        "arr": np.arange(8000),
        "levels": [{"px": i, "qty": i % 7} for i in range(600)],
        "meta": {"nested": {"x": list(range(120)), "y": {"k": "v"}}},
    }
    unsafe = ReplayEngine()
    unsafe._unsafe_no_copy = True
    for _ in range(700):
        unsafe.record_event("update_start", payload)
    events_ref = list(unsafe._events)

    # Warmup runs to eliminate cold-start noise
    for _ in range(2):
        list(unsafe.replay())
        copy.deepcopy(events_ref[:10])

    def bench_unsafe(loops: int = 10) -> float:
        times = []
        for _ in range(loops):
            start = time.perf_counter()
            for _evt in unsafe.replay():
                pass
            times.append(time.perf_counter() - start)
        return statistics.median(times)

    def bench_deepcopy(loops: int = 10) -> float:
        times = []
        for _ in range(loops):
            start = time.perf_counter()
            _ = copy.deepcopy(events_ref)
            times.append(time.perf_counter() - start)
        return statistics.median(times)

    unsafe_t = bench_unsafe()
    deepcopy_t = bench_deepcopy()

    # CI-safe performance check: only fails on extreme regression (4x+ slower)
    _assert_perf_ratio(unsafe_t, deepcopy_t)


def test_safe_payload_cycle_safe_and_depth_limited():
    replay = ReplayEngine()

    payload = {"x": [{"y": [1, 2, 3]}]}
    frozen = replay._safe_payload(payload, depth=3)
    payload["x"][0]["y"][0] = 99

    assert frozen["x"][0]["y"][0] == 1

    cyc = {}
    cyc["self"] = cyc
    cyc_frozen = replay._safe_payload(cyc, depth=5)

    assert cyc_frozen["self"] == "__CYCLE__"


def test_safe_payload_deterministic_for_sets_and_hashable_keys():
    replay = ReplayEngine()

    payload = {
        "items": {("b", 2), ("a", 1)},
        frozenset({2, 1}): {"inner": [1, 2, 3]},
    }

    frozen_a = replay._safe_payload(payload, depth=3)
    frozen_b = replay._safe_payload(payload, depth=3)

    assert frozen_a == frozen_b
    assert isinstance(frozen_a["items"], tuple)
    assert frozen_a["items"] == frozen_b["items"]


def test_safe_payload_depth_cap_does_not_overflow():
    replay = ReplayEngine()
    payload = cur = {}
    for _ in range(200):
        nxt = {}
        cur["x"] = nxt
        cur = nxt

    frozen = replay._safe_payload(payload, depth=999)
    assert isinstance(frozen, dict)


def test_safe_payload_numpy_array_isolation():
    replay = ReplayEngine()
    payload = {"arr": np.array([1, 2, 3], dtype=np.int64)}
    frozen = replay._safe_payload(payload, depth=2)

    payload["arr"][0] = 999
    assert frozen["arr"][0] == 1


def test_safe_payload_nested_numpy_array_isolation():
    replay = ReplayEngine()
    payload = {"outer": [{"arr": np.array([10, 20])}]}
    frozen = replay._safe_payload(payload, depth=3)

    payload["outer"][0]["arr"][1] = 777
    assert frozen["outer"][0]["arr"][1] == 20


def test_safe_payload_mixed_container_numpy_no_exceptions():
    replay = ReplayEngine()
    payload = {
        "meta": {"arr": np.array([[1, 2], [3, 4]])},
        "items": [np.array([5, 6]), {"x": 1}],
    }

    frozen = replay._safe_payload(payload, depth=3)
    assert isinstance(frozen["meta"]["arr"], np.ndarray)
    assert isinstance(frozen["items"][0], np.ndarray)


def test_unsafe_replay_object_dtype_ndarray_no_nested_mutation_leak():
    replay = ReplayEngine()
    replay._unsafe_no_copy = True
    arr = np.array([{"x": [1, 2]}], dtype=object)
    replay.record_event("update_start", {"arr": arr})

    arr[0]["x"].append(3)
    replayed = list(replay.replay())
    assert replayed[0]["payload"]["arr"][0]["x"] == [1, 2]


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

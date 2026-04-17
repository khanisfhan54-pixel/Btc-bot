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

import threading
import logging
import numpy as np
import hashlib
import json
from typing import Any, Dict, List, Optional
from collections import deque

LOGGER = logging.getLogger(__name__)


class ReplayEngine:
    """
    Production-grade deterministic replay system.

    Features:
    - Event recording (market + internal state)
    - Deterministic replay
    - Snapshot checkpoints
    - Debug rewind capability
    """

    def __init__(self, max_events: int = 100000):
        self._lock = threading.RLock()
        self._events = deque(maxlen=max_events)
        self._snapshots: List[Dict[str, Any]] = []
        self._max_events = max_events
        self._recording = True
        self._event_id = 0
        self._dropped_events = 0

    def _canonicalize(self, obj: Any):
        if isinstance(obj, dict):
            return {str(k): self._canonicalize(obj[k]) for k in sorted(obj)}
        if isinstance(obj, (list, tuple)):
            return [self._canonicalize(v) for v in obj]
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, float):
            return format(obj, ".17g")
        return obj

    def _deep_sort(self, obj):
        if isinstance(obj, dict):
            return {k: self._deep_sort(obj[k]) for k in sorted(obj)}
        if isinstance(obj, list):
            return [self._deep_sort(v) for v in obj]
        return obj

    def _normalize_rng_state(self, rng):
        try:
            state = rng.bit_generator.state
            return {
                "bit_generator": type(rng.bit_generator).__name__,
                "state": self._canonicalize(state.get("state")),
                "inc": state.get("inc", None),
            }
        except Exception:
            return None

    def _state_hash(self, state: Dict[str, Any]) -> str:
        try:
            canonical = self._deep_sort(self._canonicalize(state))
            s = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
            return hashlib.sha256(s.encode()).hexdigest()
        except Exception:
            return "NA"

    # ==========================================
    # RECORDING
    # ==========================================
    def record_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        if not self._recording:
            return

        with self._lock:
            if len(self._events) == self._events.maxlen:
                self._dropped_events += 1
                if self._dropped_events % 1000 == 0:
                    try:
                        LOGGER.warning("[ReplayEngine] Dropped events=%d", self._dropped_events)
                    except Exception:
                        pass
            event = {
                "id": self._event_id,
                "type": event_type,
                "payload": payload if isinstance(payload, dict) else {},
            }
            self._event_id += 1
            self._events.append(event)

    def snapshot(self, state: Dict[str, Any]) -> None:
        with self._lock:
            snapshot_state = dict(state) if isinstance(state, dict) else {}
            snapshot_state.setdefault("schema_version", "1.0")
            # unified checksum (single domain)
            tmp = dict(snapshot_state)
            tmp.pop("_checksum", None)
            snapshot_state["_checksum"] = self._state_hash(tmp)
            self._snapshots.append({
                "id": self._event_id,
                "state": snapshot_state
            })

    # ==========================================
    # REPLAY
    # ==========================================
    def replay(self):
        """
        Generator: yields events in original order
        """
        with self._lock:
            for e in list(self._events):
                yield e

    def replay_from(self, event_id: int):
        with self._lock:
            for e in list(self._events):
                if e.get("id", 0) >= int(event_id):
                    yield e

    def dropped_events(self) -> int:
        with self._lock:
            return int(self._dropped_events)

    def rewind(self, steps: int = 100):
        with self._lock:
            idx = max(len(self._events) - steps, 0)
            return list(self._events)[idx:]

    # ==========================================
    # DEBUG UTILITIES
    # ==========================================
    def last_events(self, n: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._events)[-n:]

    def clear(self):
        with self._lock:
            self._events.clear()
            self._snapshots.clear()

    def apply_events(self, engine, start_id: int = 0):
        last_event = None
        valid_transitions = {
            None: {"update_start"},
            "update_start": {"update_end", "circuit_breaker", "self_heal"},
            "update_end": {"update_start"},
            "circuit_breaker": {"update_start"},
            "self_heal": {"update_start"},
        }
        try:
            setattr(engine, "_is_replay", True)
            for e in self.replay_from(start_id):
                etype = e.get("type")
                payload = e.get("payload", {})

                # STRICT FSM VALIDATION
                if last_event not in valid_transitions or etype not in valid_transitions.get(last_event, {}):
                    if getattr(engine, "_strict_replay", True):
                        raise RuntimeError(f"Replay corruption: {last_event} -> {etype}")
                    else:
                        # FIX: consistent handling (no return shape ambiguity)
                        setattr(self, "_fsm_error", {
                            "diverged": True,
                            "reason": "EVENT_SEQUENCE_CORRUPTION"
                        })
                        return

                last_event = etype

                try:
                    if etype == "update_start":
                        if isinstance(payload, dict):
                            engine.update(dict(payload))

                    elif etype == "circuit_breaker":
                        reason = "replay"
                        if isinstance(payload, dict):
                            reason = payload.get("reason", "replay")
                        engine._trigger_circuit_breaker(reason)

                    elif etype == "self_heal":
                        error = None
                        if isinstance(payload, dict):
                            error = payload.get("error")
                        engine._self_heal(error)
                    elif etype == "update_end":
                        # Replay marker event; no action required.
                        pass
                except Exception as exc:
                    try:
                        LOGGER.debug("Replay error for event_type=%s payload=%s err=%s", etype, payload, exc)
                    except Exception:
                        pass
                    continue
        finally:
            try:
                setattr(engine, "_is_replay", False)
            except Exception:
                pass

    # ==========================================
    # SNAPSHOT RESTORE + REPLAY
    # ==========================================
    def replay_from_snapshot(self, engine, snapshot_index: int = -1):
        with self._lock:
            if not self._snapshots:
                snapshot = None
            else:
                if snapshot_index < 0:
                    snap_idx = len(self._snapshots) + int(snapshot_index)
                else:
                    snap_idx = int(snapshot_index)
                if snap_idx < 0 or snap_idx >= len(self._snapshots):
                    try:
                        LOGGER.warning("Invalid snapshot_index=%s, falling back to full replay", snapshot_index)
                    except Exception:
                        pass
                    snapshot = None
                else:
                    snapshot = dict(self._snapshots[snap_idx])

        if snapshot is None:
            self.apply_events(engine, start_id=0)
            return

        try:
            if hasattr(engine, "load_snapshot"):
                engine.load_snapshot(snapshot)
            elif hasattr(engine, "load_state"):
                state = snapshot.get("state", {})
                if isinstance(state, dict):
                    engine.load_state(state)
            # RNG restore handled ONLY in engine.load_snapshot()
            start_id = int(snapshot.get("id", 0))
            self.apply_events(engine, start_id=start_id)
        except Exception:
            try:
                LOGGER.debug("Replay from snapshot failed at index=%s", snapshot_index)
            except Exception:
                pass

    # ==========================================
    # REPLAY VALIDATION ENGINE
    # ==========================================
    def validate_replay(self, engine_factory, snapshot_index: int = -1) -> Dict[str, Any]:
        result = {
            "diverged": False,
            "reason": None,
            "final_equity": 0.0,
            "final_regime": "",
        }

        try:
            # TRUE BASELINE (FULL REPLAY)
            baseline_engine = engine_factory()
            replay_engine = engine_factory()
            self.apply_events(baseline_engine, start_id=0)

            # Replay from checkpointed snapshot.
            self.replay_from_snapshot(replay_engine, snapshot_index=snapshot_index)
            baseline_equity = float(getattr(baseline_engine, "_equity", 0.0))
            baseline_regime = str(getattr(baseline_engine, "_confirmed_regime", "") or "")
            replay_equity = float(getattr(replay_engine, "_equity", 0.0))
            replay_regime = str(getattr(replay_engine, "_confirmed_regime", "") or "")

            # ==========================================
            # BITWISE STATE HASH VALIDATION
            # ==========================================
            baseline_state = (
                baseline_engine.serialize_state()
                if hasattr(baseline_engine, "serialize_state")
                else {}
            )
            replay_state = (
                replay_engine.serialize_state()
                if hasattr(replay_engine, "serialize_state")
                else {}
            )

            def _safe_rng(rng):
                return self._normalize_rng_state(rng)

            baseline_rng = getattr(baseline_engine, "_rng", None)
            replay_rng = getattr(replay_engine, "_rng", None)
            baseline_combined = {
                "engine": baseline_state,
                "rng": _safe_rng(baseline_rng),
                "schema_version": "2.3",
            }
            replay_combined = {
                "engine": replay_state,
                "rng": _safe_rng(replay_rng),
                "schema_version": "2.3",
            }

            baseline_hash = self._state_hash(baseline_combined)
            replay_hash = self._state_hash(replay_combined)

            result["final_equity"] = replay_equity
            result["final_regime"] = replay_regime
            result["baseline_hash"] = baseline_hash
            result["replay_hash"] = replay_hash

            if baseline_hash != replay_hash:
                result["diverged"] = True
                result["reason"] = "STATE_HASH_MISMATCH"
                return result

            def _vec(values):
                return np.asarray(values, dtype=float)

            def _close(a, b):
                return np.allclose(_vec(a), _vec(b), atol=1e-12)

            baseline_garch = getattr(baseline_engine, "garch_prob", [0.5, 0.5])
            replay_garch = getattr(replay_engine, "garch_prob", [0.5, 0.5])
            baseline_nhhmm = getattr(baseline_engine, "nhhmm_prior", [1 / 3, 1 / 3, 1 / 3])
            replay_nhhmm = getattr(replay_engine, "nhhmm_prior", [1 / 3, 1 / 3, 1 / 3])
            baseline_smooth = getattr(baseline_engine, "_smoothed_garch_prob", [0.5, 0.5])
            replay_smooth = getattr(replay_engine, "_smoothed_garch_prob", [0.5, 0.5])
            baseline_state = getattr(baseline_engine, "_regime_state_probs", [0.25, 0.25, 0.25, 0.25])
            replay_state = getattr(replay_engine, "_regime_state_probs", [0.25, 0.25, 0.25, 0.25])
            baseline_range = float(getattr(baseline_engine, "range_ticks", 0.0))
            replay_range = float(getattr(replay_engine, "range_ticks", 0.0))
            baseline_pos = float(getattr(baseline_engine, "last_signed_position_size", 0.0))
            replay_pos = float(getattr(replay_engine, "last_signed_position_size", 0.0))
            baseline_vol = float(getattr(baseline_engine, "_last_valid_vol", 0.0))
            replay_vol = float(getattr(replay_engine, "_last_valid_vol", 0.0))

            if not (
                abs(baseline_equity - replay_equity) < 1e-12
                and baseline_regime == replay_regime
                and _close(baseline_garch, replay_garch)
                and _close(baseline_nhhmm, replay_nhhmm)
                and _close(baseline_smooth, replay_smooth)
                and _close(baseline_state, replay_state)
                and abs(baseline_range - replay_range) < 1e-12
                and abs(baseline_pos - replay_pos) < 1e-12
                and abs(baseline_vol - replay_vol) < 1e-12
            ):
                result["diverged"] = True
                result["reason"] = "FULL_STATE_MISMATCH"
        except Exception as exc:
            result["diverged"] = True
            result["reason"] = str(exc)

        return result

import threading
import logging
import copy
from dataclasses import is_dataclass, fields, asdict
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

    # Keep these helpers local to the replay engine so payload copying stays
    # deterministic and compatible with existing callers.
    _MAX_SAFE_PAYLOAD_DEPTH = 50

    def __init__(self, max_events: int = 100000):
        self._lock = threading.RLock()
        self._events = deque(maxlen=max_events)
        self._snapshots: List[Dict[str, Any]] = []
        self._max_events = max_events
        self._recording = True
        self._event_id = 0
        self._dropped_events = 0
        self._fsm_error = None
        self._strict_replay = True
        self._unsafe_no_copy = False
        self._unsafe_warning_emitted = False
        self._normalize_floats = True
        self._normalize_floats_strict_only = True
        self._HASH_NAMESPACE = "ADV_REGIME_REPLAY"

    # ==========================================
    # INTERNAL SAFE COPY (UNSAFE MODE PROTECTION)
    # ==========================================
    def _copy_ndarray(self, value: np.ndarray, depth: int, seen: set[int]) -> np.ndarray:
        """
        Copy numpy arrays safely.
        - Numeric arrays: fast buffer copy
        - Object arrays: deep-copy nested Python objects to avoid aliasing
        """
        try:
            if getattr(value, "dtype", None) is not None and value.dtype == object:
                flat = value.ravel()
                out = np.empty(flat.shape, dtype=object)
                for i in range(len(flat)):
                    out[i] = self._freeze(flat[i], depth, seen)
                return out.reshape(value.shape)
            if (
                self._normalize_floats
                and np.issubdtype(value.dtype, np.floating)
                and (not self._normalize_floats_strict_only or self._strict_replay)
            ):
                flat = value.ravel()
                out = np.empty(flat.shape, dtype=object)
                for i in range(len(flat)):
                    v = flat[i]
                    if not np.isfinite(v):
                        if np.isnan(v):
                            out[i] = {"__float__": "NaN"}
                        else:
                            out[i] = {"__float__": "Infinity" if v > 0 else "-Infinity"}
                    else:
                        out[i] = format(v, ".17g")
                return out.reshape(value.shape)
            return np.array(value, copy=True)
        except Exception:
            # Fallback should never mutate caller state.
            try:
                return np.array(value, copy=True)
            except Exception:
                return value

    def _canonical_key_string(self, value: Any) -> str:
        """
        Deterministic string form for dict keys and unsupported leaves.
        """
        try:
            canonical = self._canonicalize(value)
            return json.dumps(
                canonical,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                default=self._json_default,
            )
        except Exception:
            return f"__UNSUPPORTED_KEY__:{type(value).__module__}.{type(value).__qualname__}"

    def _stable_item_key(self, value: Any):
        canonical = self._canonicalize(value)
        return (
            self._canonical_sort_key(canonical),
            type(value).__module__,
            type(value).__qualname__,
            self._canonical_key_string(value),
        )

    def _freeze_key(self, key: Any, depth: int, seen: set[int]):
        container_types = (dict, list, tuple, set, frozenset, np.ndarray)
        if isinstance(key, container_types):
            frozen_key = self._freeze(key, depth, seen)
            try:
                hash(frozen_key)
                return frozen_key
            except Exception:
                return "__KEY__|" + type(frozen_key).__name__ + "|" + self._canonical_key_string(frozen_key)
        return key

    def _freeze_set_like(self, value, depth: int, seen: set[int]):
        frozen_items = [self._freeze(v, depth, seen) for v in value]
        all_hashable = True
        for item in frozen_items:
            try:
                hash(item)
            except Exception:
                all_hashable = False
                break

        if all_hashable:
            try:
                return tuple(sorted(frozen_items, key=self._stable_item_key))
            except Exception:
                pass

        # Deterministic fallback for unhashable frozen members.
        return tuple(sorted(frozen_items, key=self._stable_item_key))

    def _freeze_unknown_object(self, value: Any, cur_depth: int, seen: set[int], freeze_fn=None):
        """
        Best-effort leaf handling for non-container objects.
        Avoids copy.copy() because that can preserve nested aliasing.
        """
        if value is None or isinstance(value, (bool, int, float, str, bytes)):
            return value

        if freeze_fn is None:
            freeze_fn = lambda v, d, _s: self._freeze(v, d, _s)

        obj_id = id(value)
        if obj_id in seen:
            return "__CYCLE__"

        if is_dataclass(value) and not isinstance(value, type):
            seen.add(obj_id)
            try:
                out = {
                    "__dataclass__": f"{type(value).__module__}.{type(value).__qualname__}",
                    "fields": {},
                }
                next_depth = max(0, cur_depth - 1)
                for f in fields(value):
                    try:
                        field_value = getattr(value, f.name)
                        out["fields"][f.name] = freeze_fn(field_value, next_depth, seen)
                    except Exception:
                        out["fields"][f.name] = "__UNAVAILABLE__"
                return out
            finally:
                seen.discard(obj_id)

        if hasattr(value, "__dict__"):
            seen.add(obj_id)
            try:
                next_depth = max(0, cur_depth)
                raw_state = dict(vars(value))
                frozen_state = {
                    k: freeze_fn(v, next_depth, seen)
                    for k, v in raw_state.items()
                }
                return {
                    "__object__": f"{type(value).__module__}.{type(value).__qualname__}",
                    "state": frozen_state,
                }
            finally:
                seen.discard(obj_id)

        return {
            "__type__": f"{type(value).__module__}.{type(value).__qualname__}",
            "__repr__": repr(value),
        }

    def _freeze(self, value, depth: int, seen: set[int]):
        container_types = (dict, list, tuple, set, frozenset, np.ndarray)
        if not isinstance(value, container_types):
            return self._freeze_unknown_object(value, max(0, depth - 1), seen, self._freeze)

        obj_id = id(value)
        if obj_id in seen:
            return "__CYCLE__"

        seen.add(obj_id)
        try:
            if isinstance(value, np.ndarray):
                return self._copy_ndarray(value, depth, seen)

            if depth <= 0:
                if isinstance(value, dict):
                    return {self._freeze_key(k, 0, seen): v for k, v in value.items()}
                if isinstance(value, list):
                    return list(value)
                if isinstance(value, tuple):
                    return tuple(value)
                if isinstance(value, (set, frozenset)):
                    return self._freeze_set_like(value, 0, seen)
                return self._freeze_unknown_object(value, 0, seen, self._freeze)

            next_depth = max(0, depth - 1)
            if isinstance(value, dict):
                return {
                    self._freeze_key(k, next_depth, seen): self._freeze(v, next_depth, seen)
                    for k, v in value.items()
                }
            if isinstance(value, list):
                return [self._freeze(v, next_depth, seen) for v in value]
            if isinstance(value, tuple):
                return tuple(self._freeze(v, next_depth, seen) for v in value)
            if isinstance(value, (set, frozenset)):
                return self._freeze_set_like(value, next_depth, seen)
            if isinstance(value, np.ndarray):
                return self._copy_ndarray(value, next_depth, seen)
            return self._freeze_unknown_object(value, next_depth, seen, self._freeze)
        finally:
            seen.discard(obj_id)

    def _safe_payload(self, p, depth: int = 2):
        """
        Deterministic, depth-limited structural copy.

        Properties:
        - Structural isolation up to the requested depth
        - No deepcopy performance cliffs
        - Cycle-safe behavior
        - Stable handling for nested sets / frozensets and dataclasses
        """
        depth = max(0, min(int(depth), int(self._MAX_SAFE_PAYLOAD_DEPTH)))
        return self._freeze(p, depth, set())

    def _emit_unsafe_mode_warning(self) -> None:
        if not self._unsafe_no_copy or self._unsafe_warning_emitted:
            return
        try:
            LOGGER.critical(
                "ReplayEngine running in UNSAFE mode (_unsafe_no_copy=True). "
                "Determinism is NOT guaranteed."
            )
        except Exception:
            pass
        self._unsafe_warning_emitted = True

    def _canonicalize(self, obj: Any):
        if isinstance(obj, dict):
            return {
                str(k): self._canonicalize(obj[k])
                for k in sorted(obj, key=lambda x: f"{type(x).__name__}:{str(x)}")
            }
        if isinstance(obj, (list, tuple)):
            return [self._canonicalize(v) for v in obj]
        if isinstance(obj, (set, frozenset)):
            canonical_values = [self._canonicalize(v) for v in obj]
            return sorted(canonical_values, key=self._canonical_sort_key)
        if isinstance(obj, np.ndarray):
            return [self._canonicalize(v) for v in obj.tolist()]
        if isinstance(obj, np.generic):
            return self._canonicalize(obj.item())
        if isinstance(obj, float):
            if not np.isfinite(obj):
                if np.isnan(obj):
                    return {"__float__": "NaN"}
                return {"__float__": "Infinity" if obj > 0 else "-Infinity"}
            return format(obj, ".17g")
        return obj

    @staticmethod
    def _canonical_sort_key(value: Any) -> str:
        try:
            return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        except Exception:
            return f"{type(value).__name__}:{str(value)}"

    def _deep_sort(self, obj):
        if isinstance(obj, dict):
            return {
                k: self._deep_sort(obj[k])
                for k in sorted(obj, key=lambda x: f"{type(x).__name__}:{str(x)}")
            }
        if isinstance(obj, list):
            return [self._deep_sort(v) for v in obj]
        return obj

    def _json_default(self, obj: Any):
        if is_dataclass(obj):
            return self._canonicalize(asdict(obj))
        if isinstance(obj, (bytes, bytearray, memoryview)):
            return {"__bytes__": bytes(obj).hex()}
        if isinstance(obj, complex):
            return {"__complex__": [format(obj.real, ".17g"), format(obj.imag, ".17g")]}
        if isinstance(obj, (set, frozenset)):
            return sorted(self._canonicalize(v) for v in obj)
        if hasattr(obj, "__dict__"):
            return self._canonicalize(vars(obj))
        return {"__unsupported__": f"{type(obj).__module__}.{type(obj).__qualname__}"}

    def _normalize_rng_state(self, engine: Any):
        try:
            if engine is None:
                return None

            rng = getattr(engine, "_rng", None)
            if rng is None and hasattr(engine, "bit_generator"):
                rng = engine
            if rng is None:
                return None

            bitgen = getattr(rng, "bit_generator", None)
            if bitgen is None:
                return None

            state = bitgen.state
            return {
                "bit_generator": type(bitgen).__name__,
                "bit_generator_module": type(bitgen).__module__,
                "numpy_version": np.__version__,
                "internal_state": self._canonicalize(state),
            }
        except Exception:
            return None

    def _state_hash(self, state: Dict[str, Any]) -> str:
        try:
            canonical = self._deep_sort(self._canonicalize(state))
            wrapped_payload = {
                "namespace": self._HASH_NAMESPACE,
                "schema_version": str(state.get("schema_version", "1.0")),
                "payload": canonical,
            }
            s = json.dumps(
                wrapped_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                default=self._json_default,
            )
            return hashlib.sha256(f"{self._HASH_NAMESPACE}|{s}".encode()).hexdigest()
        except Exception:
            try:
                LOGGER.error("State hash canonicalization failed", exc_info=True)
            except Exception:
                pass
            return hashlib.sha256(b"STATE_HASH_ERROR").hexdigest()

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
                # Defensive copy prevents post-record mutation from corrupting replay history.
                "payload": copy.deepcopy(payload) if isinstance(payload, dict) else {},
            }
            self._event_id += 1
            self._events.append(event)

    def snapshot(self, state: Dict[str, Any]) -> None:
        with self._lock:
            # Snapshot correctness must never depend on unsafe replay mode.
            snapshot_state = copy.deepcopy(state) if isinstance(state, dict) else {}
            snapshot_state.setdefault("schema_version", "1.0")
            # unified checksum (single domain)
            tmp = copy.deepcopy(snapshot_state)
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
            self._emit_unsafe_mode_warning()
            if self._unsafe_no_copy:
                events = [
                    {
                        "id": e.get("id"),
                        "type": e.get("type"),
                        "payload": self._safe_payload(e.get("payload", {})),
                    }
                    for e in self._events
                ]
            else:
                events = copy.deepcopy(list(self._events))
        for e in events:
            yield e

    def replay_from(self, event_id: int):
        with self._lock:
            self._emit_unsafe_mode_warning()
            if self._unsafe_no_copy:
                events = [
                    {
                        "id": e.get("id"),
                        "type": e.get("type"),
                        "payload": self._safe_payload(e.get("payload", {})),
                    }
                    for e in self._events
                ]
            else:
                events = copy.deepcopy(list(self._events))
        for e in events:
            if e.get("id", 0) >= int(event_id):
                yield e

    def dropped_events(self) -> int:
        with self._lock:
            return int(self._dropped_events)

    def rewind(self, steps: int = 100):
        with self._lock:
            idx = max(len(self._events) - steps, 0)
            return copy.deepcopy(list(self._events)[idx:])

    # ==========================================
    # DEBUG UTILITIES
    # ==========================================
    def last_events(self, n: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(list(self._events)[-n:])

    def clear(self):
        with self._lock:
            self._events.clear()
            self._snapshots.clear()
            self._fsm_error = None

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
            setattr(engine, "_fsm_error", None)
            self._fsm_error = None
            prev_event = None
            for e in self.replay_from(start_id):
                etype = e.get("type")
                payload = e.get("payload", {})
                prev_event = last_event

                # STRICT FSM VALIDATION
                if prev_event not in valid_transitions or etype not in valid_transitions.get(prev_event, {}):
                    err = {"last_event": prev_event, "event": etype, "reason": "EVENT_SEQUENCE_CORRUPTION"}
                    self._fsm_error = err
                    setattr(engine, "_fsm_error", err)
                    if getattr(engine, "_strict_replay", True):
                        raise RuntimeError(f"Replay corruption: {prev_event} -> {etype}")
                    else:
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
                    err = {
                        "last_event": prev_event,
                        "event": etype,
                        "reason": "EVENT_HANDLER_ERROR",
                        "error_type": type(exc).__name__,
                    }
                    self._fsm_error = err
                    setattr(engine, "_fsm_error", err)
                    if getattr(engine, "_strict_replay", True):
                        raise RuntimeError(f"Replay handler error: {etype}") from exc
                    return
        finally:
            try:
                setattr(engine, "_is_replay", False)
            except Exception:
                pass

        # ==========================================
        # 🚨 FSM COMPLETENESS CHECK
        # ==========================================
        if last_event == "update_start":
            err = {"reason": "INCOMPLETE_EVENT_CYCLE"}
            self._fsm_error = err
            setattr(engine, "_fsm_error", err)
            if getattr(engine, "_strict_replay", True):
                raise RuntimeError("Replay corruption: incomplete event cycle")

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
                    snapshot = copy.deepcopy(self._snapshots[snap_idx])

        if snapshot is None:
            self.apply_events(engine, start_id=0)
            return

        # ==========================================
        # 🚨 RESET FSM STATE BEFORE REPLAY
        # ==========================================
        self._fsm_error = None
        try:
            setattr(engine, "_fsm_error", None)
        except Exception:
            pass

        # ==========================================
        # 🚨 SNAPSHOT ROLLBACK SAFETY
        # ==========================================
        backup = None
        backup_mode = None
        if hasattr(engine, "serialize_state"):
            try:
                backup = copy.deepcopy(engine.serialize_state())
                backup_mode = "state"
            except Exception:
                backup = None

        try:
            state = snapshot.get("state", {}) if isinstance(snapshot, dict) else {}
            if isinstance(state, dict):
                expected_checksum = state.get("_checksum")
                if expected_checksum:
                    check_blob = copy.deepcopy(state)
                    check_blob.pop("_checksum", None)
                    actual_checksum = self._state_hash(check_blob)
                    if actual_checksum != expected_checksum:
                        err = {"snapshot_index": snapshot_index, "reason": "SNAPSHOT_CHECKSUM_MISMATCH"}
                        self._fsm_error = err
                        setattr(engine, "_fsm_error", err)
                        if getattr(engine, "_strict_replay", True):
                            raise RuntimeError("Replay corruption: snapshot checksum mismatch")
                        return
            if hasattr(engine, "load_snapshot"):
                engine.load_snapshot(snapshot)
            elif hasattr(engine, "load_state"):
                if isinstance(state, dict):
                    engine.load_state(state)
            # RNG restore handled ONLY in engine.load_snapshot()
            start_id = int(snapshot.get("id", 0))
            self.apply_events(engine, start_id=start_id)
        except Exception as exc:
            # rollback engine to pre-replay state
            if backup:
                try:
                    if hasattr(engine, "load_state") and backup_mode == "state":
                        engine.load_state(backup)
                    elif hasattr(engine, "load_snapshot"):
                        engine.load_snapshot({"state": backup})
                except Exception:
                    pass

            if getattr(engine, "_strict_replay", True):
                raise
            err = {
                "snapshot_index": snapshot_index,
                "reason": "SNAPSHOT_RESTORE_FAILED",
                "error_type": type(exc).__name__,
            }
            self._fsm_error = err
            try:
                setattr(engine, "_fsm_error", err)
            except Exception:
                pass
            try:
                LOGGER.debug("Replay from snapshot failed at index=%s err=%s", snapshot_index, exc)
            except Exception:
                pass
            return

    # ==========================================
    # REPLAY VALIDATION ENGINE
    # ==========================================
    def validate_replay(self, engine_factory, snapshot_index: int = -1) -> Dict[str, Any]:
        with self._lock:
            if self._unsafe_no_copy:
                raise RuntimeError("validate_replay cannot run with _unsafe_no_copy=True")
            result = {
                "diverged": False,
                "reason": None,
                "final_equity": 0.0,
                "final_regime": "",
            }

            try:
                baseline_engine = engine_factory()
                replay_engine = engine_factory()
                baseline_engine._fsm_error = None
                replay_engine._fsm_error = None

                self.apply_events(baseline_engine, start_id=0)
                self.replay_from_snapshot(replay_engine, snapshot_index=snapshot_index)

                baseline_equity = float(getattr(baseline_engine, "_equity", 0.0))
                baseline_regime = str(getattr(baseline_engine, "_confirmed_regime", "") or "")
                replay_equity = float(getattr(replay_engine, "_equity", 0.0))
                replay_regime = str(getattr(replay_engine, "_confirmed_regime", "") or "")

                if getattr(baseline_engine, "_fsm_error", None):
                    result["diverged"] = True
                    result["reason"] = "FSM_CORRUPTION_BASELINE"
                    result["fsm_error"] = baseline_engine._fsm_error
                    return result
                if getattr(replay_engine, "_fsm_error", None):
                    result["diverged"] = True
                    result["reason"] = "FSM_CORRUPTION"
                    result["fsm_error"] = replay_engine._fsm_error
                    return result

                baseline_state = baseline_engine.serialize_state() if hasattr(baseline_engine, "serialize_state") else {}
                replay_state = replay_engine.serialize_state() if hasattr(replay_engine, "serialize_state") else {}

                baseline_hash_payload = {
                    "engine": baseline_state,
                    "rng": self._normalize_rng_state(baseline_engine),
                    "schema_version": str(baseline_state.get("schema_version", "2.3")) if isinstance(baseline_state, dict) else "2.3",
                }
                replay_hash_payload = {
                    "engine": replay_state,
                    "rng": self._normalize_rng_state(replay_engine),
                    "schema_version": str(replay_state.get("schema_version", "2.3")) if isinstance(replay_state, dict) else "2.3",
                }

                baseline_hash = self._state_hash(baseline_hash_payload)
                replay_hash = self._state_hash(replay_hash_payload)

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
                baseline_state_probs = getattr(baseline_engine, "_regime_state_probs", [0.25, 0.25, 0.25, 0.25])
                replay_state_probs = getattr(replay_engine, "_regime_state_probs", [0.25, 0.25, 0.25, 0.25])
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
                    and _close(baseline_state_probs, replay_state_probs)
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

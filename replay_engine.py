import threading
import logging
import copy
import multiprocessing as mp
from dataclasses import is_dataclass, fields, asdict
import numpy as np
import hashlib
import json
import time
from typing import Any, Dict, List, Optional
from collections import deque

LOGGER = logging.getLogger(__name__)
_IN_REPLAY_SUBPROCESS = False




def _replay_callback_worker(conn, engine, callback_name, callback_arg):
    global _IN_REPLAY_SUBPROCESS
    try:
        _IN_REPLAY_SUBPROCESS = True
        setattr(engine, "_is_replay", True)
        logging.disable(logging.CRITICAL)
        if callback_name == "update":
            engine.update(callback_arg)
        elif callback_name == "_trigger_circuit_breaker":
            engine._trigger_circuit_breaker(callback_arg)
        elif callback_name == "_self_heal":
            engine._self_heal(callback_arg)
        else:
            raise RuntimeError(f"Unsupported replay callback: {callback_name}")

        if not hasattr(engine, "serialize_state"):
            raise RuntimeError("Replay timeout guard requires serialize_state")
        state = engine.serialize_state()
        conn.send({"ok": True, "state": state})
    except Exception as exc:
        conn.send({"ok": False, "error_type": type(exc).__name__, "error": str(exc)})
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _snapshot_restore_worker(conn, engine_class, engine_init_params, engine_state_dict, restore_mode, restore_payload):
    global _IN_REPLAY_SUBPROCESS
    try:
        _IN_REPLAY_SUBPROCESS = True
        logging.disable(logging.CRITICAL)
        init_params = dict(engine_init_params or {})
        init_params["enable_background_workers"] = False
        init_params["load_model_weights_on_init"] = False
        engine = engine_class(**init_params)
        setattr(engine, "_is_replay", True)
        if isinstance(engine_state_dict, dict):
            engine.load_state(engine_state_dict)
        if restore_mode == "load_snapshot":
            engine.load_snapshot(restore_payload)
        elif restore_mode == "load_state":
            engine.load_state(restore_payload)
        else:
            raise RuntimeError("invalid restore mode")
        conn.send({"ok": True, "state": engine.serialize_state()})
    except Exception as exc:
        conn.send({"ok": False, "error": str(exc), "error_type": type(exc).__name__})
    finally:
        try:
            conn.close()
        except Exception:
            pass


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
    _MAX_SAFE_PAYLOAD_DEPTH = 512

    def __init__(self, max_events: int = 100000, max_snapshots: int = 1024):
        self._lock = threading.RLock()
        self._events = deque(maxlen=max_events)
        self._snapshots: List[Dict[str, Any]] = []
        self._max_snapshots = max(1, int(max_snapshots))
        self._max_events = max_events
        self._recording = True
        self._event_id = 0
        self._dropped_events = 0
        self._dropped_snapshot_count = 0
        self._fsm_error = None
        self._strict_replay = True
        self._unsafe_no_copy = False
        self._unsafe_warning_emitted = False
        self._normalize_floats = True
        self._normalize_floats_strict_only = True
        self._HASH_NAMESPACE = "ADV_REGIME_REPLAY"
        self._replay_timeout_seconds = 5.0
        self._copy_fidelity_failures = 0
        self._is_replaying = False
        self._decision_traces: deque = deque(maxlen=max_events)

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
                    try:
                        out[i] = copy.deepcopy(flat[i])
                    except Exception:
                        LOGGER.debug("_copy_ndarray: element %d deepcopy failed, using structural freeze. type=%s", i, type(flat[i]).__qualname__)
                        out[i] = self._freeze(flat[i], depth, seen)
                return out.reshape(value.shape)
            return np.array(value, copy=True)
        except Exception:
            # Fallback should never mutate caller state.
            try:
                return np.array(value, copy=True)
            except Exception:
                try:
                    return np.asarray(copy.deepcopy(value), dtype=object)
                except Exception:
                    return np.asarray([repr(value)], dtype=object)

    def _copy_any(self, value: Any):
        try:
            return copy.deepcopy(value)
        except Exception as exc:
            LOGGER.error("ReplayEngine._copy_any: deepcopy failed for type %s; falling back to structural copy. Data fidelity NOT guaranteed. error_type=%s error=%s", type(value).__qualname__, type(exc).__name__, str(exc)[:200], exc_info=True)
            with self._lock:
                self._copy_fidelity_failures += 1
            return self._safe_payload(value, depth=self._MAX_SAFE_PAYLOAD_DEPTH)

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

        if hasattr(value, "__slots__"):
            slots = {}
            for slot in getattr(value, "__slots__", []):
                slots[slot] = freeze_fn(getattr(value, slot, "__UNAVAILABLE__"), max(0, cur_depth - 1), seen)
            return {"__slots_object__": f"{type(value).__module__}.{type(value).__qualname__}", "slots": slots}
        LOGGER.debug("_freeze_unknown_object: using repr fallback for type=%s", type(value).__qualname__)
        return {"__type__": f"{type(value).__module__}.{type(value).__qualname__}", "__repr__": repr(value)[:512]}

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

    def _canonicalize(self, obj: Any, seen: Optional[set[int]] = None):
        if seen is None:
            seen = set()
        obj_id = id(obj)
        if isinstance(obj, (dict, list, tuple, set, frozenset, np.ndarray)) and obj_id in seen:
            return {"__cycle__": type(obj).__name__}
        if isinstance(obj, dict):
            seen.add(obj_id)
            return {
                f"{type(k).__qualname__}:{str(k)}": self._canonicalize(obj[k], seen)
                for k in sorted(obj, key=lambda x: f"{type(x).__name__}:{str(x)}")
            }
        if isinstance(obj, (list, tuple)):
            seen.add(obj_id)
            return [self._canonicalize(v, seen) for v in obj]
        if isinstance(obj, (set, frozenset)):
            seen.add(obj_id)
            canonical_values = [self._canonicalize(v, seen) for v in obj]
            return sorted(canonical_values, key=self._canonical_sort_key)
        if isinstance(obj, np.ndarray):
            seen.add(obj_id)
            return [self._canonicalize(v, seen) for v in obj.tolist()]
        if isinstance(obj, np.generic):
            return self._canonicalize(obj.item(), seen)
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
                return {"status": "ENGINE_NONE"}

            rng = getattr(engine, "_rng", None)
            if rng is None and hasattr(engine, "bit_generator"):
                rng = engine
            if rng is None:
                return {"status": "MISSING"}

            bitgen = getattr(rng, "bit_generator", None)
            if bitgen is None:
                return {"status": "INVALID_BIT_GENERATOR", "rng_type": f"{type(rng).__module__}.{type(rng).__qualname__}"}

            state = bitgen.state
            return {
                "status": "OK",
                "bit_generator": type(bitgen).__name__,
                "bit_generator_module": type(bitgen).__module__,
                "numpy_version": np.__version__,
                "internal_state": self._canonicalize(state),
            }
        except Exception as exc:
            return {
                "status": "ERROR",
                "error_type": type(exc).__name__,
                "error_msg": str(exc)[:200],
                "engine_type": f"{type(engine).__module__}.{type(engine).__qualname__}",
            }

    def _state_hash(self, state: Dict[str, Any]) -> str:
        try:
            canonical = self._deep_sort(self._canonicalize(state))
            wrapped_payload = {
                "namespace": self._HASH_NAMESPACE,
                "schema_version": str(state.get("schema_version", "2.4")),
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
                LOGGER.error("_state_hash fallback activated for state type=%s. Hash fidelity may be reduced.", type(state).__qualname__, exc_info=True)
            except Exception:
                pass
            fallback = {
                "namespace": self._HASH_NAMESPACE,
                "error": "STATE_HASH_ERROR",
                "payload_type": f"{type(state).__module__}.{type(state).__qualname__}",
                "payload": self._safe_payload(state, depth=self._MAX_SAFE_PAYLOAD_DEPTH),
            }
            s = json.dumps(
                fallback,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                default=self._json_default,
            )
            return hashlib.sha256(f"{self._HASH_NAMESPACE}|{s}".encode()).hexdigest()

    # ==========================================
    # RECORDING
    # ==========================================
    def record_event(self, event_type: str, payload: Dict[str, Any], source: str = "advanced_regime_engine") -> None:
        if not self._recording:
            return

        with self._lock:
            if self._is_replaying:
                return
            event_type = str(event_type or "").strip()
            if not event_type:
                raise ValueError("event_type must be a non-empty string")
            if len(self._events) == self._events.maxlen:
                self._dropped_events += 1
                if self._dropped_events == 1 or self._dropped_events % 1000 == 0:
                    try:
                        LOGGER.warning("[ReplayEngine] Dropped events=%d", self._dropped_events)
                    except Exception:
                        pass
            event = {
                "id": self._event_id,
                "type": event_type,
                "ts_ns": int(time.time_ns()),
                "ts_monotonic_ns": int(time.monotonic_ns()),
                "source": str(source or "advanced_regime_engine").strip(),
                "dropped_events_before": int(self._dropped_events),
                # Defensive isolation prevents post-record mutation from corrupting replay history.
                "payload": self._copy_any(payload),
            }
            self._event_id += 1
            self._events.append(event)
            if event_type == "update_end" and isinstance(payload, dict):
                tick_id = payload.get("tick_id")
                for idx in range(len(self._decision_traces) - 1, -1, -1):
                    trace = self._decision_traces[idx]
                    if trace.get("outcome_event_id") is None and trace.get("tick_id") == tick_id:
                        updated = dict(trace)
                        updated["outcome_event_id"] = event["id"]
                        self._decision_traces[idx] = updated
                        break

    def snapshot(self, state: Dict[str, Any]) -> None:
        # Snapshot correctness must never depend on unsafe replay mode.
        snapshot_state = self._copy_any(state) if isinstance(state, dict) else {}
        # unified checksum (single domain)
        tmp = self._copy_any(snapshot_state)
        tmp.pop("_checksum", None)
        snapshot_state["_checksum"] = self._state_hash(tmp)
        with self._lock:
            if self._is_replaying:
                return
            out={"id": self._event_id,"ts_ns": int(time.time_ns()),"ts_monotonic_ns": int(time.monotonic_ns()),"state": snapshot_state,"event_boundary": int(self._event_id),"dropped_events_before": int(self._dropped_events)}
            if isinstance(state, dict):
                out["regime_marker"] = state.get("_confirmed_regime", state.get("regime"))
            self._snapshots.append(out)
            if len(self._snapshots) > self._max_snapshots:
                over_by = len(self._snapshots) - self._max_snapshots
                del self._snapshots[:over_by]
                self._dropped_snapshot_count += int(over_by)

    # ==========================================
    # REPLAY
    # ==========================================
    def replay(self):
        """
        Generator: yields events in original order
        """
        with self._lock:
            self._emit_unsafe_mode_warning()
            source_events = list(self._events)
            unsafe_mode = bool(self._unsafe_no_copy)
        if unsafe_mode:
            events = []
            for e in source_events:
                event_copy = self._copy_any(e)
                if isinstance(event_copy, dict):
                    event_copy["payload"] = self._safe_payload(event_copy.get("payload", {}), depth=64)
                events.append(event_copy)
        else:
            events = self._copy_any(source_events)
        if self._dropped_events > 0:
            LOGGER.warning("ReplayEngine.replay: %d events were dropped before this replay. Replay state may be incorrect.", self._dropped_events)
            yield {"id": None, "type": "__REPLAY_GAP__", "ts_ns": int(time.time_ns()), "source": "replay_engine", "dropped_events_before": self._dropped_events, "payload": {"reason": "EVENTS_DROPPED_BEFORE_REPLAY", "dropped_count": self._dropped_events, "first_surviving_id": events[0].get("id") if events else None}}
        for e in events:
            yield e

    def replay_from(self, event_id: int):
        with self._lock:
            self._emit_unsafe_mode_warning()
            source_events = list(self._events)
            unsafe_mode = bool(self._unsafe_no_copy)
        if unsafe_mode:
            events = []
            for e in source_events:
                event_copy = self._copy_any(e)
                if isinstance(event_copy, dict):
                    event_copy["payload"] = self._safe_payload(event_copy.get("payload", {}), depth=64)
                events.append(event_copy)
        else:
            events = self._copy_any(source_events)
        if events and int(event_id) > 0 and events[0].get("id") != int(event_id):
            LOGGER.warning("ReplayEngine.replay_from: replay gap detected requested_start_id=%s first_surviving_id=%s", event_id, events[0].get("id"))
            yield {"id": None, "type": "__REPLAY_GAP__", "ts_ns": int(time.time_ns()), "source": "replay_engine", "dropped_events_before": self._dropped_events, "payload": {"reason": "REPLAY_GAP_DETECTED", "requested_start_id": int(event_id), "first_surviving_id": events[0].get("id")}}
        for e in events:
            if e.get("id", 0) >= int(event_id):
                yield e

    def dropped_events(self) -> int:
        with self._lock:
            return int(self._dropped_events)

    def rewind(self, steps: int = 100):
        with self._lock:
            idx = max(len(self._events) - steps, 0)
            events_slice = list(self._events)[idx:]
        return self._copy_any(events_slice)

    def record_decision_trace(self, trace: Dict[str, Any]) -> None:
        with self._lock:
            if self._is_replaying:
                return
            entry = dict(trace or {})
            entry["event_id"] = int(self._event_id)
            self._decision_traces.append(entry)

    def get_decision_traces(self, n: int = 100) -> List[dict]:
        with self._lock:
            return self._copy_any(list(self._decision_traces)[-max(0, int(n)):])

    def get_outcome_trace(self, event_id: int) -> Optional[dict]:
        with self._lock:
            for trace in reversed(self._decision_traces):
                if trace.get("outcome_event_id") == int(event_id):
                    return self._copy_any(trace)
        return None

    # ==========================================
    # DEBUG UTILITIES
    # ==========================================
    def last_events(self, n: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            events_slice = list(self._events)[-n:]
        return self._copy_any(events_slice)

    def copy_fidelity_failures(self) -> int:
        with self._lock:
            return int(self._copy_fidelity_failures)

    def dropped_snapshots(self) -> int:
        with self._lock:
            return int(self._dropped_snapshot_count)

    def clear(self, reset_counters: bool = False):
        with self._lock:
            self._events.clear()
            self._snapshots.clear()
            self._fsm_error = None
            if reset_counters:
                self._event_id = 0
                self._dropped_events = 0
                self._dropped_snapshot_count = 0

    def _set_replay_timeout_error(self, engine, prev_event, etype, phase="UNKNOWN"):
        err = {"last_event": prev_event, "event": etype, "reason": "REPLAY_TIMEOUT", "phase": phase}
        self._fsm_error = err
        setattr(engine, "_fsm_error", err)

    def _run_replay_callback_with_timeout(self, engine, callback_name: str, callback_arg: Any, timeout_seconds: float):
        if timeout_seconds <= 0:
            getattr(engine, callback_name)(callback_arg)
            return

        if not hasattr(engine, "serialize_state") or not hasattr(engine, "load_state"):
            getattr(engine, callback_name)(callback_arg)
            return

        warned_non_serializable = getattr(self, "_warned_non_serializable_state_this_apply", False)
        try:
            engine_for_child = copy.deepcopy(engine)
        except Exception as exc:
            LOGGER.error("ReplayEngine: engine is not safely deepcopy-able for subprocess isolation. callback=%s engine_type=%s error=%s", callback_name, type(engine).__qualname__, str(exc), exc_info=True)
            raise RuntimeError("ENGINE_NOT_COPYABLE_FOR_SUBPROCESS") from exc
        try:
            # Use fork on POSIX to avoid spawn-time re-import deadlocks in tight test loops.
            ctx = mp.get_context("fork")
            parent_conn, child_conn = ctx.Pipe(duplex=False)
            process = ctx.Process(
                target=_replay_callback_worker,
                args=(child_conn, engine_for_child, callback_name, self._copy_any(callback_arg)),
                daemon=True,
            )
            process.start()
        except Exception as exc:
            LOGGER.error("ReplayEngine callback watchdog init failed", exc_info=True)
            fault = {"reason": "CALLBACK_WATCHDOG_INIT_FAILED", "callback": callback_name, "error_type": type(exc).__name__, "error": str(exc)}
            self._fsm_error = fault
            setattr(engine, "_fsm_error", fault)
            raise RuntimeError("CALLBACK_WATCHDOG_INIT_FAILED") from exc
        child_conn.close()
        try:
            if not parent_conn.poll(timeout_seconds):
                process.terminate()
                process.join(timeout=1.0)
                raise TimeoutError("Replay callback timed out")
            msg = parent_conn.recv()
        finally:
            parent_conn.close()
            if process.is_alive():
                process.terminate()
            process.join(timeout=1.0)

        if not isinstance(msg, dict) or not msg.get("ok"):
            err_type = msg.get("error_type") if isinstance(msg, dict) else "RuntimeError"
            LOGGER.error("Replay callback error: callback=%s error_type=%s error=%s", callback_name, err_type, msg.get("error") if isinstance(msg, dict) else "invalid response", exc_info=False)
            raise RuntimeError(f"[{err_type or 'UnknownError'}] Replay callback failed: {msg.get('error', '')}")
        engine.load_state(msg["state"])
        if not warned_non_serializable:
            LOGGER.warning("ReplayEngine: engine state restored via serialize/load cycle. Non-serializable state (RNG, caches, C extensions) may not be fully restored. Engine type=%s", type(engine).__qualname__)
            self._warned_non_serializable_state_this_apply = True
        if hasattr(engine, "post_replay_restore"):
            LOGGER.debug("ReplayEngine: calling post_replay_restore hook")
            engine.post_replay_restore()

    def _run_snapshot_restore_with_timeout(self, engine, snapshot: Dict[str, Any], timeout_seconds: float):
        if timeout_seconds <= 0:
            raise RuntimeError("SNAPSHOT_TIMEOUT_INVALID")
        if not hasattr(engine, "serialize_state") or not hasattr(engine, "load_state"):
            raise RuntimeError("SNAPSHOT_WATCHDOG_INIT_FAILED")
        restore_mode = "load_snapshot" if callable(getattr(engine, "load_snapshot", None)) else "load_state"
        restore_payload = snapshot if restore_mode == "load_snapshot" else snapshot.get("state", {})
        try:
            engine_state_dict = copy.deepcopy(engine.serialize_state())
            engine_class = engine.__class__
            engine_init_params = dict(getattr(engine, "_init_params", {}) or {})
            ctx = mp.get_context("fork")
            parent_conn, child_conn = ctx.Pipe(duplex=False)
            process = ctx.Process(
                target=_snapshot_restore_worker,
                args=(child_conn, engine_class, engine_init_params, engine_state_dict, restore_mode, self._copy_any(restore_payload)),
                daemon=True,
            )
            process.start()
        except Exception as exc:
            raise RuntimeError("SNAPSHOT_WATCHDOG_INIT_FAILED") from exc
        child_conn.close()
        try:
            if not parent_conn.poll(timeout_seconds):
                process.terminate()
                process.join(timeout=1.0)
                raise TimeoutError("snapshot restore timed out")
            msg = parent_conn.recv()
        finally:
            parent_conn.close()
            if process.is_alive():
                process.terminate()
            process.join(timeout=1.0)
        if not isinstance(msg, dict) or not msg.get("ok"):
            raise RuntimeError(msg.get("error", "snapshot restore failed"))
        engine.load_state(msg["state"])

    def apply_events(self, engine, start_id: int = 0):
        last_event = None
        valid_transitions = {
            None: {"update_start"},
            "update_start": {"update_end", "circuit_breaker", "self_heal", "error"},
            "update_end": {"update_start"},
            "circuit_breaker": {"update_start"},
            "self_heal": {"update_start"},
            "error": {"error", "update_end", "circuit_breaker", "self_heal", "update_start"},
        }
        supported_event_types = {"update_start", "update_end", "circuit_breaker", "self_heal", "error", "__REPLAY_GAP__"}
        self._warned_non_serializable_state_this_apply = False
        replay_timeout_s = float(getattr(self, "_replay_timeout_seconds", 5.0))
        replay_deadline = time.perf_counter() + replay_timeout_s if replay_timeout_s > 0 else None
        try:
            setattr(engine, "_is_replay", True)
            with self._lock:
                self._is_replaying = True
            setattr(engine, "_fsm_error", None)
            self._fsm_error = None
            prev_event = None
            in_update_cycle = False
            for e in self.replay_from(start_id):
                etype = e.get("type")
                payload = e.get("payload", {})
                prev_event = last_event
                if etype == "__REPLAY_GAP__":
                    LOGGER.warning("ReplayEngine.apply_events: replay gap marker observed payload=%s", payload)
                    err = {"reason": "REPLAY_GAP", "dropped_count": payload.get("dropped_count", 0)}
                    self._fsm_error = err
                    setattr(engine, "_fsm_error", err)
                    if getattr(engine, "_strict_replay", True):
                        raise RuntimeError("REPLAY_GAP_DETECTED")
                    continue
                if etype not in supported_event_types:
                    err = {"last_event": prev_event, "event": etype, "reason": "EVENT_TYPE_UNSUPPORTED"}
                    self._fsm_error = err
                    setattr(engine, "_fsm_error", err)
                    if getattr(engine, "_strict_replay", True):
                        raise RuntimeError(f"Unsupported replay event type: {etype}")
                    LOGGER.warning("ReplayEngine.apply_events [non-strict]: suppressing error and returning. reason=%s last_event=%s current_event=%s error_type=%s", err["reason"], prev_event, etype, None)
                    return

                # STRICT FSM VALIDATION
                if prev_event not in valid_transitions or etype not in valid_transitions.get(prev_event, {}):
                    err = {"last_event": prev_event, "event": etype, "reason": "EVENT_SEQUENCE_CORRUPTION"}
                    self._fsm_error = err
                    setattr(engine, "_fsm_error", err)
                    if getattr(engine, "_strict_replay", True):
                        raise RuntimeError(f"Replay corruption: {prev_event} -> {etype}")
                    else:
                        LOGGER.warning("ReplayEngine.apply_events [non-strict]: suppressing error and returning. reason=%s last_event=%s current_event=%s error_type=%s", err["reason"], prev_event, etype, None)
                        return

                last_event = etype
                if replay_deadline is not None and time.perf_counter() > replay_deadline:
                    err = {"last_event": prev_event, "event": etype, "reason": "REPLAY_TIMEOUT", "phase": "DEADLINE"}
                    self._fsm_error = err
                    setattr(engine, "_fsm_error", err)
                    raise RuntimeError("Replay timeout exceeded")

                try:
                    engine_lock = getattr(engine, "_lock", None)
                    if etype == "update_start":
                        in_update_cycle = True
                        if not isinstance(payload, dict):
                            raise RuntimeError("update_start payload must be dict")
                        if engine_lock is not None:
                            with engine_lock:
                                self._run_replay_callback_with_timeout(engine, "update", self._copy_any(payload), replay_timeout_s)
                        else:
                            self._run_replay_callback_with_timeout(engine, "update", self._copy_any(payload), replay_timeout_s)

                    elif etype == "circuit_breaker":
                        if engine_lock is not None:
                            with engine_lock:
                                self._run_replay_callback_with_timeout(engine, "_trigger_circuit_breaker", self._copy_any(payload), replay_timeout_s)
                        else:
                            self._run_replay_callback_with_timeout(engine, "_trigger_circuit_breaker", self._copy_any(payload), replay_timeout_s)

                    elif etype == "self_heal":
                        if engine_lock is not None:
                            with engine_lock:
                                self._run_replay_callback_with_timeout(engine, "_self_heal", self._copy_any(payload), replay_timeout_s)
                        else:
                            self._run_replay_callback_with_timeout(engine, "_self_heal", self._copy_any(payload), replay_timeout_s)
                    elif etype == "update_end":
                        # Replay marker event; no action required.
                        in_update_cycle = False
                    elif etype == "error":
                        # Observability-only event in live mode; deterministic no-op in replay.
                        pass
                    else:
                        err = {"last_event": prev_event, "event": etype, "reason": "EVENT_TYPE_UNSUPPORTED"}
                        self._fsm_error = err
                        setattr(engine, "_fsm_error", err)
                        raise RuntimeError(f"Unsupported replay event type: {etype}")
                    if replay_deadline is not None and time.perf_counter() > replay_deadline:
                        err = {"last_event": prev_event, "event": etype, "reason": "REPLAY_TIMEOUT", "phase": "DEADLINE"}
                        self._fsm_error = err
                        setattr(engine, "_fsm_error", err)
                        raise RuntimeError("Replay timeout exceeded")
                except Exception as exc:
                    try:
                        LOGGER.debug("Replay error for event_type=%s payload=%s err=%s", etype, payload, exc)
                    except Exception:
                        pass
                    existing_err = getattr(engine, "_fsm_error", None)
                    if isinstance(existing_err, dict) and existing_err.get("reason") in {"REPLAY_TIMEOUT", "EVENT_TYPE_UNSUPPORTED"}:
                        if getattr(engine, "_strict_replay", True):
                            raise
                        LOGGER.warning("ReplayEngine.apply_events [non-strict]: suppressing error and returning. reason=%s last_event=%s current_event=%s error_type=%s", existing_err.get("reason"), prev_event, etype, type(exc).__name__)
                        return
                    if isinstance(exc, TimeoutError):
                        self._set_replay_timeout_error(engine, prev_event, etype, phase="CALLBACK")
                        raise RuntimeError("Replay timeout exceeded")
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
                    LOGGER.warning("ReplayEngine.apply_events [non-strict]: suppressing error and returning. reason=%s last_event=%s current_event=%s error_type=%s", err["reason"], prev_event, etype, type(exc).__name__)
                    return
        finally:
            try:
                setattr(engine, "_is_replay", False)
            except Exception:
                pass
            with self._lock:
                self._is_replaying = False

        # ==========================================
        # 🚨 FSM COMPLETENESS CHECK
        # ==========================================
        if in_update_cycle:
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
                    snapshot = self._snapshots[snap_idx]
        if snapshot is not None:
            snapshot = self._copy_any(snapshot)

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
        _no_backup = object()
        backup = _no_backup
        backup_mode = None
        if hasattr(engine, "serialize_state"):
            try:
                backup = copy.deepcopy(engine.serialize_state())
                backup_mode = "state"
            except Exception:
                backup = _no_backup

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
            self._run_snapshot_restore_with_timeout(
                engine,
                snapshot,
                float(getattr(self, "_replay_timeout_seconds", 5.0)),
            )
            if hasattr(engine, "post_snapshot_restore"):
                LOGGER.debug("ReplayEngine: calling post_snapshot_restore hook")
                engine.post_snapshot_restore()
            # RNG restore handled ONLY in engine.load_snapshot()
            start_id = int(snapshot.get("id", 0))
            self.apply_events(engine, start_id=start_id)
        except Exception as exc:
            # rollback engine to pre-replay state
            if backup is not _no_backup:
                rollback_error = None
                try:
                    if hasattr(engine, "load_state") and backup_mode == "state":
                        engine.load_state(backup)
                        if hasattr(engine, "post_rollback_restore"):
                            LOGGER.debug("ReplayEngine: calling post_rollback_restore hook")
                            engine.post_rollback_restore()
                        LOGGER.warning("ReplayEngine: rollback completed via load_state. Transient and RNG state may not be fully restored. Verify engine integrity after rollback. engine_type=%s", type(engine).__qualname__)
                        try:
                            setattr(engine, "_replay_rollback_occurred", True)
                        except Exception:
                            pass
                    elif hasattr(engine, "load_snapshot"):
                        engine.load_snapshot({"state": backup})
                except Exception as rollback_exc:
                    rollback_error = rollback_exc
                if rollback_error is not None:
                    raise RuntimeError("SNAPSHOT_ROLLBACK_FAILED") from rollback_error


            if isinstance(exc, TimeoutError):
                err = {"snapshot_index": snapshot_index, "reason": "REPLAY_TIMEOUT"}
                self._fsm_error = err
                setattr(engine, "_fsm_error", err)
                if getattr(engine, "_strict_replay", True):
                    raise RuntimeError("Replay timeout exceeded") from exc
                return
            if isinstance(exc, RuntimeError) and str(exc) == "SNAPSHOT_WATCHDOG_INIT_FAILED":
                err = {"snapshot_index": snapshot_index, "reason": "WATCHDOG_INIT_FAILED"}
                self._fsm_error = err
                setattr(engine, "_fsm_error", err)
                if getattr(engine, "_strict_replay", True):
                    raise RuntimeError("Snapshot watchdog init failed") from exc
                return

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
            cloned_events = self._copy_any(list(self._events))
            cloned_snapshots = self._copy_any(self._snapshots)
            cloned_event_id = int(self._event_id)

        local_replay = ReplayEngine(
            max_events=max(1, len(cloned_events) + 1),
            max_snapshots=max(1, len(cloned_snapshots) + 1),
        )
        local_replay._events = deque(cloned_events, maxlen=max(1, len(cloned_events) + 1))
        local_replay._snapshots = cloned_snapshots
        local_replay._event_id = cloned_event_id
        local_replay._HASH_NAMESPACE = self._HASH_NAMESPACE

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

            local_replay.apply_events(baseline_engine, start_id=0)
            local_replay.replay_from_snapshot(replay_engine, snapshot_index=snapshot_index)

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
                "schema_version": str(baseline_state.get("schema_version", "2.4")) if isinstance(baseline_state, dict) else "2.4",
            }
            replay_hash_payload = {
                "engine": replay_state,
                "rng": self._normalize_rng_state(replay_engine),
                "schema_version": str(replay_state.get("schema_version", "2.4")) if isinstance(replay_state, dict) else "2.4",
            }

            baseline_hash = self._state_hash(baseline_hash_payload)
            replay_hash = self._state_hash(replay_hash_payload)
            if baseline_hash_payload["rng"].get("status") == "ERROR" or replay_hash_payload["rng"].get("status") == "ERROR":
                result["diverged"] = True
                result["reason"] = "RNG_STATE_UNAVAILABLE"
                result["rng_baseline"] = baseline_hash_payload["rng"]
                result["rng_replay"] = replay_hash_payload["rng"]
                return result

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

import threading
import logging
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
            self._snapshots.append({
                "id": self._event_id,
                "state": dict(state) if isinstance(state, dict) else {}
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
        for e in self.replay_from(start_id):
            if e.get("type") == "update_start":
                payload = e.get("payload", {})
                if isinstance(payload, dict):
                    try:
                        engine.update(dict(payload))
                    except Exception:
                        continue

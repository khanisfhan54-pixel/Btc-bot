import threading
import time
from typing import Any, Dict, Optional


class ObservabilityController:
    """
    Thread-safe, adaptive observability controller.

    Responsibilities:
    - Adaptive deterministic sampling via ``should_sample``
    - Warning rate limiting via ``should_emit_warning``
    - Traceback verbosity budget control via ``traceback_budget``
    - Health state machine transitions (OK -> DEGRADED -> RISK -> FAIL)
    - Error burst tracking with deterministic decay over time
    """

    HEALTH_OK = "OK"
    HEALTH_DEGRADED = "DEGRADED"
    HEALTH_RISK = "RISK"
    HEALTH_FAIL = "FAIL"

    _SEVERITY_WEIGHT = {
        "low": 0.0,
        "medium": 0.25,
        "high": 1.0,
        "critical": 2.0,
    }

    def __init__(self) -> None:
        self._lock = threading.RLock()

        # Sampling configuration/state.
        self._base_sample_rate = 5
        self._min_sample_rate = 1
        self._sample_rate = self._base_sample_rate
        self._sample_tick = 0

        # Warning rate-limiting state: warning key -> last emit timestamp (epoch seconds).
        self._warning_last_emitted: Dict[str, float] = {}

        # Error-burst state.
        self._error_burst_score = 0.0
        self._last_error_ts = 0.0
        self._last_decay_ts = time.time()
        self._error_decay_half_life_s = 10.0

        # Health state machine.
        self._health = self.HEALTH_OK
        self._last_health_change_ts = self._last_decay_ts

        # Traceback frame budget.
        self._traceback_budget_frames = 3

    def observe(
        self,
        event_type: str,
        severity: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Observe a runtime event and refresh internal control signals.

        Parameters:
            event_type: Event category (for caller metadata; currently informational).
            severity: One of: low, medium, high, critical.
            context: Optional structured metadata, accepted for API stability.
        """
        del event_type, context  # Intentionally accepted for stable external API.

        now = time.time()
        sev = severity.lower().strip()

        with self._lock:
            self._decay_errors(now)
            increment = self._SEVERITY_WEIGHT.get(sev, 0.0)
            if increment > 0.0:
                self._error_burst_score += increment
                self._last_error_ts = now

            self._update_health(now)
            self._update_sampling()
            self._update_traceback_budget()

    def should_sample(self) -> bool:
        """Return True deterministically at a cadence controlled by current sample rate."""
        with self._lock:
            self._sample_tick += 1
            if self._sample_tick >= self._sample_rate:
                self._sample_tick = 0
                return True
            return False

    def should_emit_warning(self, key: str, cooldown_s: float) -> bool:
        """Return True if warning ``key`` is eligible to be emitted now."""
        if cooldown_s < 0:
            raise ValueError("cooldown_s must be >= 0")

        now = time.time()
        with self._lock:
            last = self._warning_last_emitted.get(key)
            if last is None or (now - last) >= cooldown_s:
                self._warning_last_emitted[key] = now
                return True
            return False

    def traceback_budget(self) -> int:
        """Return traceback frame budget based on current health state."""
        with self._lock:
            return self._traceback_budget_frames

    def current_health(self) -> str:
        """Return current health state."""
        with self._lock:
            return self._health

    def snapshot(self) -> Dict[str, Any]:
        """Return an atomic debug snapshot of controller internals."""
        with self._lock:
            return {
                "health": self._health,
                "error_burst_score": round(self._error_burst_score, 6),
                "sample_rate": self._sample_rate,
                "sample_tick": self._sample_tick,
                "traceback_budget": self._traceback_budget_frames,
                "last_error_ts": self._last_error_ts,
                "last_health_change_ts": self._last_health_change_ts,
                "warning_keys_tracked": len(self._warning_last_emitted),
            }

    def _update_sampling(self) -> None:
        """Adjust deterministic sampling cadence from health state."""
        if self._health == self.HEALTH_OK:
            new_rate = self._base_sample_rate
        elif self._health == self.HEALTH_DEGRADED:
            new_rate = max(2, self._base_sample_rate // 2)
        elif self._health == self.HEALTH_RISK:
            new_rate = 1
        else:  # FAIL
            new_rate = self._min_sample_rate

        self._sample_rate = max(self._min_sample_rate, int(new_rate))

    def _update_health(self, now: float) -> None:
        """Compute and apply health state transitions from error burst score."""
        previous = self._health
        score = self._error_burst_score

        if score < 1.0:
            self._health = self.HEALTH_OK
        elif score < 3.0:
            self._health = self.HEALTH_DEGRADED
        elif score < 6.0:
            self._health = self.HEALTH_RISK
        else:
            self._health = self.HEALTH_FAIL

        if self._health != previous:
            self._last_health_change_ts = now

    def _update_traceback_budget(self) -> None:
        """Adjust traceback detail budget from health state."""
        if self._health == self.HEALTH_OK:
            self._traceback_budget_frames = 3
        elif self._health == self.HEALTH_DEGRADED:
            self._traceback_budget_frames = 6
        elif self._health == self.HEALTH_RISK:
            self._traceback_budget_frames = 10
        else:  # FAIL
            self._traceback_budget_frames = 20

    def _decay_errors(self, now: float) -> None:
        """
        Decay burst score using deterministic exponential half-life reduction.

        score *= 0.5 ** (elapsed / half_life)
        """
        if self._error_burst_score <= 0.0:
            self._error_burst_score = 0.0
            self._last_decay_ts = now
            return

        elapsed = now - self._last_decay_ts
        if elapsed <= 0:
            return

        factor = 0.5 ** (elapsed / self._error_decay_half_life_s)
        self._error_burst_score *= factor

        if self._error_burst_score < 1e-6:
            self._error_burst_score = 0.0

        self._last_decay_ts = now

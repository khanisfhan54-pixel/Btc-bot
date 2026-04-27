"""Thread-safe wrappers for shared analysis components.

Global lock ordering policy (highest -> lowest):
    _ANALYSIS_STATE_LOCK > _ALPHA_STATE_LOCK > _warning_lock > ObservabilityController internal locks

Use `assert_lock_order` in debug/test mode before acquiring locks to catch ordering regressions.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_LOCK_RANK = {
    "_ANALYSIS_STATE_LOCK": 400,
    "_ALPHA_STATE_LOCK": 300,
    "_warning_lock": 200,
    "obs_lock": 100,
}
_lock_state = threading.local()


def assert_lock_order(lock_name: str) -> None:
    """Debug utility to enforce global lock-order invariants."""
    stack = getattr(_lock_state, "stack", [])
    next_rank = _LOCK_RANK.get(lock_name, 0)
    if stack and next_rank > stack[-1][1]:
        prev_name, prev_rank = stack[-1]
        raise RuntimeError(
            f"Lock order violation: attempting {lock_name}({next_rank}) while holding {prev_name}({prev_rank})"
        )


@contextmanager
def ordered_lock(lock: threading.RLock | threading.Lock, lock_name: str):
    assert_lock_order(lock_name)
    stack = getattr(_lock_state, "stack", None)
    if stack is None:
        stack = []
        _lock_state.stack = stack
    lock.acquire()
    stack.append((lock_name, _LOCK_RANK.get(lock_name, 0)))
    try:
        yield
    finally:
        stack.pop()
        lock.release()


class _ThreadSafeWrapperBase:
    def __init__(self, wrapped: Any, wrapper_name: str) -> None:
        self._wrapped = wrapped
        self._lock = threading.RLock()
        self._wrapper_name = wrapper_name
        self._owner_thread: Optional[int] = None

    @contextmanager
    def _guard(self):
        current_tid = threading.get_ident()
        if self._owner_thread is not None and self._owner_thread != current_tid:
            logger.warning(
                "%s re-entered from different thread while lock marked owned: owner=%s current=%s",
                self._wrapper_name,
                self._owner_thread,
                current_tid,
            )
        with self._lock:
            self._owner_thread = current_tid
            try:
                yield
            finally:
                self._owner_thread = None

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        method: Callable[..., Any] = getattr(self._wrapped, method_name)
        with self._guard():
            return method(*args, **kwargs)

    def __getattr__(self, item: str) -> Any:
        attr = getattr(self._wrapped, item)
        if callable(attr):
            def _locked_call(*args: Any, **kwargs: Any) -> Any:
                with self._guard():
                    return attr(*args, **kwargs)
            return _locked_call
        with self._guard():
            return attr


class ThreadSafeFeatureEngine(_ThreadSafeWrapperBase):
    def __init__(self, wrapped: Any) -> None:
        super().__init__(wrapped=wrapped, wrapper_name="ThreadSafeFeatureEngine")

    def update(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("update", *args, **kwargs)

    def get_features(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("get_features", *args, **kwargs)

    def reset(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("reset", *args, **kwargs)


class ThreadSafeAlphaPredictor(_ThreadSafeWrapperBase):
    def __init__(self, wrapped: Any) -> None:
        super().__init__(wrapped=wrapped, wrapper_name="ThreadSafeAlphaPredictor")

    def predict(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("predict", *args, **kwargs)

    def update(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("update", *args, **kwargs)

    def reset(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("reset", *args, **kwargs)

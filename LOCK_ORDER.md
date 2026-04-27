# Global Lock Ordering Policy

To avoid deadlocks, locks must only be acquired in this order:

1. `_ANALYSIS_STATE_LOCK`
2. `_ALPHA_STATE_LOCK`
3. `_warning_lock`
4. `ObservabilityController` internal locks

Never acquire a higher-priority lock while holding a lower-priority lock.

Use `thread_safe_wrappers.assert_lock_order()` / `ordered_lock()` in debug and test code to enforce this.

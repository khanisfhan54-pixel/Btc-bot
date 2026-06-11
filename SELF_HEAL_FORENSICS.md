# AdvancedRegimeEngine Self-Heal Forensics

## Scope
This audit covers `AdvancedRegimeEngine.update()`, `_self_heal()`, all observed lock acquisition paths, the warning worker, the cooldown healing path, and the circuit-breaker healing path.

## Ownership model found in code
- `AdvancedRegimeEngine.__init__()` creates a re-entrant state lock as `self._lock = threading.RLock()`.
- Methods decorated with `@_synchronized` run under `self._lock`; this includes `update()`, `serialize_state()`, `save_state()`, and `reset_state()`.
- `report_realized_pnl()` explicitly enters `with self._lock:` before changing portfolio drawdown and circuit-breaker state.
- `_self_heal()` was documented as an internal helper that “must be called while holding self._lock,” but tests and direct callers invoked it without an outer lock.

## Lock lifecycle evidence
- `update()` is synchronized and therefore enters with `self._lock` already held.
- The circuit-breaker cooldown path inside `update()` increments `_healing_counter`; once it exceeds `_HEALING_COOLDOWN_TICKS`, it calls `_self_heal()`.
- `_self_heal()` previously tried to infer caller ownership using a non-blocking acquire. When the acquire succeeded, the caller did **not** own the lock; the function then released the lock immediately and later unconditionally released it around side effects.
- That lifecycle made direct `_self_heal()` calls unsafe: after the initial release there was no guaranteed ownership for the later release.

## Healing lifecycle evidence
- Legacy breaker recovery (`error_code is None`) resets probability vectors, volatility state, regime state, selected PnL state, memory variables, breaker active/reason/tick, `_healing_counter`, `_confidence_collapse_streak`, `_health_status`, and `_last_heal_ts`.
- Error-code healing increments `_healing_count`, stores `_last_healing_error` and `_last_healing_context`, resolves an error category, and chooses one action among `RESET_NUMERICAL`, `RESET_STATE`, `RESET_SMOOTHER`, `SOFT_REBALANCE`, `RESET_INPUT`, `CIRCUIT_BREAK`, or `SKIP_AND_DEGRADE`.
- Replay/logging side effects are intentionally run outside one lock level so slow I/O does not block unrelated lock acquisition.

## Concurrent entry points
- Direct `_self_heal(...)` calls from tests or production utility code.
- `update()` circuit-breaker cooldown healing after `_HEALING_COOLDOWN_TICKS` halted updates.
- Error recovery paths that call `_self_heal(error_code, context)`.
- `report_realized_pnl()` can activate the breaker while other update/heal calls are in flight.
- The warning worker does not acquire `self._lock`; warning accounting uses `self._warning_lock` and queued emission uses `_warning_queue`/`_warning_stop_event`.

## Warning worker
- The warning subsystem owns separate structures: `_warning_last_emitted`, `_warning_first_seen`, `_warning_counts`, and `_warning_lock`.
- `_warn_rate_limited()` updates warning accounting under `_warning_lock` only.
- The background worker consumes `_warning_queue`; it is not a self-heal state owner and does not require `self._lock`.

## Cooldown healing path
- If `_circuit_breaker_active` is true, `update()` increments `_healing_counter`.
- Before the cooldown threshold is exceeded, `update()` emits a halted circuit-breaker output.
- After the threshold is exceeded, `update()` invokes `_self_heal()` and then re-checks whether the breaker remains active before proceeding.

## Circuit-breaker healing path
- `_trigger_circuit_breaker(reason)` activates the breaker, stores the first active reason and trigger tick, resets `_healing_counter`, and appends trigger history.
- `_self_heal(error_code=None)` clears `_circuit_breaker_active`, `_circuit_breaker_reason`, and `_circuit_breaker_trigger_tick`, making a future trigger a new first trigger.

## Exact root cause
The production blocker was a lock ownership mismatch in `_self_heal()`:

1. `_self_heal()` could be called by synchronized callers that already owned `self._lock` and by direct callers that did not.
2. When direct callers did not own the lock, `_self_heal()` successfully acquired it with `blocking=False`, immediately released it, and did not retain a durable ownership token.
3. Later, both the legacy and category-specific healing branches unconditionally called `_lock.release()` before side effects.
4. For direct callers, that later release could run without ownership and raise `RuntimeError: cannot release un-acquired lock`.

## Implemented ownership model
- `_self_heal()` now has a deterministic single-entry ownership model:
  - If the caller owns the re-entrant lock, `_self_heal()` uses that known-owned level.
  - If the caller does not own it, `_self_heal()` acquires exactly one level for the call and releases it in a `finally` block.
  - Side effects release exactly one guaranteed-owned level and reacquire it before returning to stateful code.
- No lock release is attempted unless ownership is established first.
- No new threads, busy waits, or deadlock-prone lock order changes were introduced.

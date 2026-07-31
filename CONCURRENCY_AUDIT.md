# Concurrency Audit

## Areas audited

- `_self_heal()`
- Warning worker lifecycle
- Lock acquisition/release
- Thread lifecycle and weak references
- Cleanup paths

## Findings

### Can a lock be released without ownership?

Yes. `_self_heal()` stores `_lock = self._lock` and calls `_lock.release()` before running side effects, then re-acquires it in `finally`. This assumes the current thread owns the `RLock`. Direct calls to `_self_heal()` from tests or external code do not necessarily hold the lock, producing `RuntimeError: cannot release un-acquired lock`.

Reproduced failures:

- Unknown error fallback direct call.
- Fallback mapping direct call.
- Full self-heal direct call.
- Concurrent self-heal.
- Concurrent self-heal plus update.

### Can self-heal run concurrently?

Yes. `_self_heal()` is not decorated with `_synchronized`; `update()` is synchronized, but direct callers can enter concurrently. The method mutates shared fields (`_healing_count`, `_last_healing_error`, `_last_healing_context`, GARCH/SJM state, circuit-breaker state, equity state) and then manually releases/re-acquires locks around side effects.

Observed impact: concurrent test expected `healing_count >= 1600`, but got `8` because worker threads crashed on lock release.

### Can recovery loops occur?

Yes, conceptually. Circuit-breaker cooldown calls `_self_heal()` from inside `update()`. If `_self_heal()` clears the breaker, the same update can continue to produce non-HALTED output, while tests expect a clean halted tick after healing. If `_self_heal()` re-triggers a risk breaker through category mapping, reason state can be overwritten by later risk checks.

### Can worker threads survive engine destruction?

Yes. The warning worker is intended to receive a weakref, but the failure `eng_ref() is not None` after deletion indicates some lifecycle path retains the engine or delays collection. The finalizer owns stop event, queue, and thread references; the worker loop may temporarily bind a strong `engine` local while waiting/emitting warnings.

## Cleanup path risk

- Worker thread is daemonized, which avoids process hang but can hide lifecycle leaks.
- Finalizer-based shutdown is best effort; if the engine remains strongly reachable, finalization never runs.
- Queue draining may keep worker active past engine lifetime.

## Concurrency verdict

| Question | Answer | Risk |
|---|---|---|
| Lock released without ownership? | Yes | Critical |
| Self-heal can run concurrently? | Yes | Critical |
| Recovery loops possible? | Yes | Medium |
| Worker survives destruction? | Yes / observed | Medium |

## Recommended resolution direction

No code changes were made, but the correct fix direction is to make `_self_heal()` either fully synchronized or ownership-aware, prevent concurrent healing state transitions, and make worker shutdown deterministic without retaining the engine.

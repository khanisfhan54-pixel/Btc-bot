# Circuit Breaker Audit

## Areas audited

- `_trigger_circuit_breaker(reason)`
- Cooldown logic in `update()`
- Drawdown / equity floor / loss streak paths
- Volatility shock paths
- Price-return mismatch path
- Confidence collapse path

## Actual trigger locations

| Breaker | Trigger path | Behavior |
|---|---|---|
| `VOL_SHOCK` | Pre-shock gate before PnL tracking and later final shock gate | Calls `_trigger_circuit_breaker("VOL_SHOCK")`, returns halted output immediately. |
| `EQUITY_FLOOR` | PnL/equity tracking when equity falls below floor | Calls `_trigger_circuit_breaker("EQUITY_FLOOR")`. |
| `MAX_DRAWDOWN` | PnL/equity tracking after drawdown computation | Calls `_trigger_circuit_breaker("MAX_DRAWDOWN")` if breaker not already triggered in that local block. |
| `LOSS_STREAK` | PnL/equity tracking after loss streak threshold | Calls `_trigger_circuit_breaker("LOSS_STREAK")` if local breaker flag has not been set. |
| `CONFIDENCE_COLLAPSE` | Low confidence after warmup and streak threshold | Calls `_trigger_circuit_breaker("CONFIDENCE_COLLAPSE")`, returns halted output. |
| `PRICE_RETURN_MISMATCH` | PnL reconciliation mismatch | Does **not** call `_trigger_circuit_breaker`; emits fail-safe output with feed status `PRICE_RETURN_MISMATCH`. |
| Risk-category self-heal | `_self_heal(... category="risk")` | Calls `_trigger_circuit_breaker(str(err_code))`. |

## Actual precedence order in `update()`

1. If breaker already active at start of tick, increment `_healing_counter`.
2. If cooldown exceeded, call `_self_heal()`; if breaker remains active, return HALTED; if self-heal clears breaker, update may continue.
3. Resolve input/return/MTF and fail-safe early returns.
4. Pre-shock `VOL_SHOCK` check before PnL mutation.
5. Price-return reconciliation; mismatch returns fail-safe but not breaker.
6. Equity floor / drawdown / loss streak checks inside PnL tracking.
7. MTF / NHHMM / SJM classification.
8. Final shock check can still trigger `VOL_SHOCK`.
9. Confidence-collapse check can trigger `CONFIDENCE_COLLAPSE`.

## Multiple-breaker behavior

### Stored reason policy

`_trigger_circuit_breaker()` does this in order:

1. Append history entry.
2. Assign `_circuit_breaker_reason = reason_text`.
3. If `_circuit_breaker_active`, return.
4. If `_circuit_breaker_trigger_tick == current_tick`, return.
5. Activate breaker and reset healing counter.

Therefore, reason assignment is **latest-reason-wins**, even when the breaker was already active or same-tick activation is ignored. Activation/tick state is first-triggered, but the visible reason is overwritten.

### Example: drawdown vs volatility shock

If `MAX_DRAWDOWN` triggers and later `VOL_SHOCK` is evaluated in the same tick, `_circuit_breaker_reason` can become `VOL_SHOCK` even though activation/healing counter belong to the earlier trigger. This is exactly what the reason-preservation tests expose.

### Example: price mismatch vs volatility shock

Price mismatch is not a circuit breaker. If the mismatch path is reached, it returns fail-safe early. If pre-shock fires first, `VOL_SHOCK` wins and returns HALTED. If mismatch is not reached due to stale/anchor policy, downstream classification can continue.

## Cooldown behavior

- Active breaker increments `_healing_counter` each update.
- Healing occurs only when `_healing_counter > _HEALING_COOLDOWN_TICKS`.
- `_self_heal()` clears `_circuit_breaker_active`, `_circuit_breaker_reason`, `_circuit_breaker_trigger_tick`, and `_healing_counter` on full reset.
- After a successful heal, the same update can proceed to normal classification rather than returning one final halted output. This is a policy mismatch with some tests.

## Verdict

Circuit breakers are functional but policy-ambiguous. The critical mismatch is not activation itself; it is reason precedence and post-heal control flow. Risk systems should not expose overwritten breaker reasons without an explicit priority model.

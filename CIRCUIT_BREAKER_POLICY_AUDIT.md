# AdvancedRegimeEngine Circuit-Breaker Policy Audit

## Scope
This audit traces `_trigger_circuit_breaker()`, `update()`, all breaker sources, precedence paths, same-tick behavior, cross-tick behavior, cooldown behavior, and healing behavior.

## Breaker types observed
- `MAX_DRAWDOWN`: engine equity/drawdown breach inside update-time PnL/risk handling.
- `LOSS_STREAK`: consecutive loss threshold breach.
- `VOL_SHOCK`: return/volatility shock detection.
- `CONFIDENCE_COLLAPSE`: low-confidence streak after warmup.
- `PORTFOLIO_DD_*`: portfolio-level drawdown from `report_realized_pnl()`.
- Risk-category self-heal errors: `_self_heal(error_code)` can call `_trigger_circuit_breaker(err_code)` when the error resolver returns category `risk`.

## Trigger paths
- `update()` computes risk state and calls `_trigger_circuit_breaker()` for volatility shocks, drawdown breaches, loss streaks, and confidence collapse.
- `report_realized_pnl()` runs under `self._lock` and can activate the portfolio drawdown breaker.
- `_self_heal()` can route risk-category errors to `_trigger_circuit_breaker()`.

## Previous precedence behavior
- `_trigger_circuit_breaker()` appended history, assigned `_circuit_breaker_reason = reason_text`, and only then checked whether the breaker was already active.
- This meant a later trigger could overwrite the visible active reason even though activation belonged to the earlier trigger.
- Same-tick and cross-tick subsequent triggers had ambiguous audit output because the active reason no longer necessarily matched `_circuit_breaker_trigger_tick`.

## Implemented policy: FIRST_TRIGGER_WINS
- The first trigger that activates an inactive breaker sets `_circuit_breaker_reason` and `_circuit_breaker_trigger_tick`.
- Every trigger is appended to `_cb_trigger_history`.
- If the breaker is already active, subsequent triggers are logged and retained in history but do not overwrite active reason or trigger tick.
- After healing clears the breaker, the next trigger is a new first trigger.

## Same-tick behavior
- First trigger in the tick activates the breaker and records the tick.
- Later triggers in the same tick are retained in history but do not overwrite the active reason.

## Cross-tick behavior
- While the breaker is active, later triggers on later ticks are retained in history but do not overwrite the original active reason/tick.
- After `_self_heal()` clears active breaker fields, a later tick can activate a new breaker reason.

## Cooldown behavior
- While active, `update()` returns halted circuit-breaker outputs and increments `_healing_counter`.
- Once `_healing_counter > _HEALING_COOLDOWN_TICKS`, `update()` invokes `_self_heal()` and then rechecks breaker state.

## Healing behavior
- Full self-heal clears `_circuit_breaker_active`, `_circuit_breaker_reason`, `_circuit_breaker_trigger_tick`, and `_healing_counter`.
- Trigger history is retained through healing, preserving audit trail across recovery.

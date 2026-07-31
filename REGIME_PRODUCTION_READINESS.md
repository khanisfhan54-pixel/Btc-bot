# Regime Production Readiness Assessment

## Scorecard

| Category | Score 0-100 | Justification |
|---|---:|---|
| Weight provenance | 20 | Provenance exists, but current artifact is explicitly synthetic and `production_valid=false`. Engine still treats loaded arrays as calibrated. |
| Calibration | 15 | ARE has internal normalized probabilities but no demonstrated isotonic/Platt/OoF calibration for regime probabilities. Confidence is uncalibrated. |
| Regime correctness | 35 | Multiple classification tests show zero TREND/RANGE recall in scenarios expected to be basic. 3-state artifact is mapped heuristically to 4 labels. |
| Confidence validity | 25 | `confidence=max(probability mass)` and `conviction=1-entropy`; neither is calibrated probability of correctness. |
| Circuit breaker reliability | 55 | Breakers activate and halt, but reason precedence is ambiguous and latest-reason overwrites active breaker reason. Cooldown/post-heal flow is policy-mismatched. |
| Self-heal reliability | 20 | Direct/concurrent self-heal can release un-owned locks and crash threads. This is critical. |
| MTF reliability | 50 | MTF has degradation/fallback paths, but statuses and base-feature failure semantics are inconsistent with tests/integrations. |
| Integration stability | 40 | Main/engine wiring tests show missing exports, unconstructed signal pipeline, and signal-only blocked by live-mode guard. |
| Report integrity | 45 | Reports exist and contain many audit fields, but schema naming and shape drift from tests (`run_status`, `calibration`) is unresolved. |
| Production readiness | 25 | Synthetic weights, uncalibrated confidence, classification failures, self-heal concurrency bug, and circuit-breaker ambiguity block production use. |

## Top 10 actual defects ranked by information gain, risk reduction, and number of failures fixed

1. **Self-heal lock ownership bug** — Critical risk; explains 6 direct failures and thread crashes. Fixing it gives high information gain because downstream self-heal tests can then validate actual recovery semantics.
2. **Regime classifier/calibration mismatch** — High risk; explains TREND/RANGE recall collapse and undermines core subsystem purpose.
3. **Synthetic artifact accepted as calibrated** — High risk; not always a direct test failure, but invalidates production confidence and `signal_valid` semantics.
4. **Circuit-breaker reason precedence ambiguity** — High risk; explains multiple breaker tests and can mislead risk operators about why trading halted.
5. **Price-return mismatch path not reliably fail-safe** — High risk; can allow normal execution mode despite reconciliation mismatch.
6. **SJM last-valid fallback cleared by numerical self-heal** — High risk; removes safety cache when it is most needed.
7. **Warning worker lifecycle retains engine** — Medium risk; indicates cleanup/thread lifecycle leak.
8. **Shock warmup threshold plateau** — Medium risk; risk thresholds do not transition as intended.
9. **Signal pipeline not constructed in `main.py`** — High integration risk; regime/alpha orchestration contract fails.
10. **MTF degradation status ambiguity** — Medium risk; integrations cannot reliably distinguish macro-only fallback, no features, and OK.

## If only 3 issues can be fixed first

### 1. Fix `_self_heal()` lock ownership/concurrency

Why first:

- Critical runtime safety issue.
- Causes hard thread exceptions.
- Blocks accurate evaluation of many other recovery/circuit tests.
- Directly affects production resilience under numerical/input/risk faults.

### 2. Fix regime calibration/classification alignment

Why second:

- This is the core product function: detecting TREND/RANGE/BEAR/TOXIC.
- Current tests show zero TREND/RANGE recall in basic regimes.
- Synthetic 3-state artifact plus heuristic 4-label mapping makes confidence and labels unreliable.
- Fix should include real BTC provenance gating and acceptance metrics.

### 3. Define and implement circuit-breaker precedence policy

Why third:

- Risk controls must be auditable and deterministic.
- Current latest-reason overwrite can hide the original trigger.
- Drawdown, volatility shock, confidence collapse, and mismatch paths need clear priority.
- Lower fix count than calibration, but high risk-reduction value.

## Non-defect cleanup that should not be confused with bugs

- Feed-status string assertions are stale tests against a newer structured schema.
- Several tests pass invalid prices while intending to test return/equity logic; those are wrong expectations under the current fail-fast price contract.
- Report schema tests need a contract decision (`calibration` vs `calibration_status`, scalar vs object `run_status`) before fixes.

## Final readiness verdict

**Not production-ready.** The subsystem should remain blocked for live capital until real-data calibrated weights, calibrated confidence semantics, deterministic circuit-breaker precedence, and thread-safe self-heal behavior are in place.

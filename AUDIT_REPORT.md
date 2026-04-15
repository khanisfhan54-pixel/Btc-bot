| ID | Severity | Description | Fix | Status |
|---|---|---|---|---|
| BUG-1 | CRITICAL | PnL used absolute price delta instead of fractional return | Replaced PnL with `((price-last_price)/last_price) * signed_position`; added finite/zero guards | Fixed |
| BUG-2 | CRITICAL | Self-heal did not reset equity/drawdown state, causing immediate re-halt | `_self_heal()` now resets equity, peak, drawdown, loss streak, position, last price, shock memory | Fixed |
| BUG-3 | CRITICAL | HALTED path leaked stale signed position and accumulated ghost PnL | HALTED branch now hard-sets `self.last_signed_position_size = 0.0` before return | Fixed |
| BUG-4 | CRITICAL | SJM init depended on global RNG, causing non-deterministic regimes | Deterministic centroid initialization and deterministic NHHMM seed-based init | Fixed |
| BUG-5 | HIGH | Warning thread `daemon=False` could block process exit | Warning worker switched to `daemon=True` | Fixed |
| BUG-6 | HIGH | Initial GARCH variance too high vs target vol | GARCH variance now initializes to `target_vol**2` and same on reset/heal | Fixed |
| BUG-7 | MEDIUM | Persistent regime stickiness from init/penalty interactions | Reduced switch penalty pressure and added deterministic/state-aware SJM feature mapping | Mitigated |
| BUG-8 | MEDIUM | PnL kept running while HALTED, re-triggering breaker | PnL now skips during breaker active; halt/self-heal reset paths cleaned | Fixed |

## Monte Carlo stress summary (N=20 trials/scenario)

| Scenario | Total ticks | Toxic rate | Halted rate | CB/trial | TREND | BEAR | RANGE | TOXIC | HALTED | UNKNOWN |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bull | 10000 | 0.0472 | 0.0918 | 2.35 | 5904 | 1276 | 1430 | 472 | 918 | 0 |
| bear | 10000 | 0.0571 | 0.1019 | 2.60 | 1109 | 5941 | 1360 | 571 | 1019 | 0 |
| range | 10000 | 0.0000 | 0.0679 | 1.70 | 5146 | 3859 | 316 | 0 | 679 | 0 |
| vol_shock | 10000 | 0.2528 | 0.1080 | 2.70 | 3047 | 2290 | 1055 | 2528 | 1080 | 0 |
| mixed | 12000 | 0.0324 | 0.0983 | 2.95 | 5748 | 3238 | 1445 | 389 | 1180 | 0 |
| jump | 10000 | 0.1932 | 0.1090 | 2.80 | 3255 | 2712 | 1011 | 1932 | 1090 | 0 |
| low_liquidity | 10000 | 0.9139 | 0.0660 | 1.65 | 1 | 0 | 200 | 9139 | 660 | 0 |

## Regime accuracy summary

Confusion matrix (truth x predicted counts):

| Truth \ Pred | TREND | BEAR | RANGE | TOXIC | HALTED |
|---|---:|---:|---:|---:|---:|
| TREND | 220 | 38 | 39 | 53 | 20 |
| BEAR | 0 | 334 | 25 | 11 | 0 |
| RANGE | 348 | 0 | 2 | 0 | 20 |
| TOXIC | 0 | 0 | 11 | 339 | 20 |

Per-label precision/recall:

| Label | Precision | Recall |
|---|---:|---:|
| TREND | 0.3873 | 0.5946 |
| BEAR | 0.8978 | 0.9027 |
| RANGE | 0.0260 | 0.0054 |
| TOXIC | 0.8412 | 0.9162 |
| HALTED | 0.0000 | 0.0000 |

Additional diagnostics:
- Dominant-label ratio across all synthetic regimes: **0.3838** (no collapse)
- TOXIC frequency on non-shock regimes: **0.0577**
- Systematic miss: range regime is under-recalled and frequently mapped to TREND.

## Parameter change recommendations

1. **Range discrimination calibration**
   - Current model over-labels TREND in low-vol/range data.
   - Recommend adding explicit low-vol gate to bias toward RANGE when `expected_volatility < 0.006` and directional confidence is weak.

2. **Circuit-breaker cadence tuning**
   - CB trigger rates are materially reduced versus pre-fix state but still non-trivial (1.7–2.95 events/trial in most synthetic scenarios).
   - Recommend making `_MAX_CONSECUTIVE_LOSSES` and `_HEALING_COOLDOWN_TICKS` constructor-configurable for market-specific tuning.

3. **Shock gating calibration**
   - Shock-memory persistence improves TOXIC detection in shock scenarios and keeps non-shock TOXIC rates bounded.
   - Recommend calibrating shock memory decay (currently 0.90) per venue/timeframe to match realized volatility clustering.

4. **Regime threshold governance**
   - Current cutoffs (`crisis>0.55`, `bull/bear>0.55`) work for tested synthetic scenarios.
   - Recommend offline calibration with historical labeled data and reporting drift metrics in CI.

## Final verdict

**partially ready**

Core correctness/safety bugs are fixed and full tests pass (`50 passed`).
Remaining production risk is **classification quality in range-like markets** (low RANGE recall and TREND over-assignment). The engine is operationally safer and stable, but should complete a final calibration cycle on representative historical market data before unrestricted production deployment.

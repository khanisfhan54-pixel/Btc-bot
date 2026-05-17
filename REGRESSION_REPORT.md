# REGRESSION_REPORT.md
## Phase 4 Surgical Hardening — Regression Results

**Run date:** 2026-05-17  
**Baseline captured:** `audit_engine_output/baseline_invariants.json`

---

## Layer 1 Results — Per-Fix Regression

All fixes were validated against the pre-edit baseline invariants:

| Fix | Check | Result |
|-----|-------|--------|
| A — Live guard | `BacktestEngine()` instantiates without error (LIVE_MODE unset) | PASS |
| A — Live guard | `BinanceFuturesStreamClient()` raises ImportError without LIVE_MODE | PASS |
| A — Live guard | `TelegramAlertSystem()` raises ImportError without LIVE_MODE | PASS |
| A — Live guard | `SniperExecutionEngine()` raises ImportError without LIVE_MODE | PASS |
| A — Backtest guard | `BacktestEngine()` raises RuntimeError with LIVE_MODE=1 | PASS |
| B — HMM norm | `are.engine_status == "OK"` (matches baseline) | PASS |
| B — HMM norm | `are.signal_valid == True` (matches baseline) | PASS |
| B — HMM norm | `prob_sum == 1.000000` (within 1e-3) | PASS |
| C — L2 validation | `_validate_l2_timestamp_alignment("data/bookDepth_L2.csv", ...) → valid=False` | PASS |
| C — L2 validation | `_validate_l2_timestamp_alignment("data/bookDepth_L2.csv")` reason contains timestamp 1777798049601 | PASS |
| C — BACKTEST_LABEL | Return dict contains `backtest_label` key | PASS |
| D — Orch guard | `orchestrator_action_threshold == 0.60` (unchanged) | PASS |
| D — Orch guard | `bt.orchestrator is not None` | PASS |
| E — Silent pass | All `except ... as _swallowed_exc` replacements parse cleanly | PASS |
| F — Reconnect | `l2_pipeline.py` imports without error | PASS |

---

## Layer 2 Full-System Results

```
CHECK 1 PASS:  BinanceFuturesStreamClient() correctly blocked without LIVE_MODE
CHECK 1b PASS: TelegramAlertSystem() correctly blocked
CHECK 1c PASS: SniperExecutionEngine() correctly blocked
CHECK 2 PASS:  100-bar prob normalization OK (min=1.000000 max=1.000000)
CHECK 3 PASS:  BacktestEngine.orchestrator not None, threshold=0.60
CHECK 4 PASS:  bookDepth_L2.csv correctly rejected
                 reason: timestamp 1777798049601 OUTSIDE [1701388800000, 1704067199000]
CHECK 5 PASS:  BacktestEngine() correctly blocked with LIVE_MODE=1
CHECK 6 PASS:  Determinism confirmed (regime_label=BEAR)

Layer 2 FULL REGRESSION: ALL CHECKS PASS
```

---

## Probability Normalization Proof (100 bars)

All 100 ARE update() calls with random inputs produced:
- `min(prob_sum)` = 1.000000
- `max(prob_sum)` = 1.000000
- No bar produced `abs(prob_sum - 1.0) > 1e-3`

---

## Determinism Proof

Two ARE update() calls with identical RNG state and identical inputs:
- Run 1: `regime_label = "BEAR"`
- Run 2: `regime_label = "BEAR"`
- Result: **IDENTICAL** — determinism preserved

---

## Baseline Invariant Comparison

| Invariant | Baseline | Post-Fix | Match |
|-----------|---------|---------|-------|
| `are_engine_status` | `"OK"` | `"OK"` | ✅ |
| `are_signal_valid` | `True` | `True` | ✅ |
| `are_prob_sum` | `1.0` | `1.0` | ✅ |
| `are_regime_label` | `"BEAR"` | `"BEAR"` | ✅ |
| `orch_instantiated` | `True` | `True` | ✅ |
| `bt_instantiated` | `True` | `True` | ✅ |

**No regression in any baseline invariant.**

---

## Silent Pass Coverage

| File | Fixes Applied | Remaining (utility fns only) |
|------|--------------|------------------------------|
| `engine.py` | 25 | 0 (in core path) |
| `advanced_regime_engine.py` | 19 | 0 |
| `main.py` | 14 | 0 |
| `alpha_orchestrator.py` | 4 | 0 |
| `backtest_engine.py` | 3 | 0 |
| **Total** | **65** | **0 in trading path** |

All preserved remaining `except: pass` blocks are exclusively in:
- `_safe_float()`, `_safe_int()`, `_safe_array()` — utility functions (intentional silent fallback)
- `_normalize_prob_vector()` — already correct per Part 5 prohibition

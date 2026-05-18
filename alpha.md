# Alpha Liquidity Sweep Predictor — Full Audit Report

**Generated:** 2026-05-18 09:01 UTC  
**Verdict:** `WEAK`  
**Production Readiness:** `Research-only`  
**Import Status:** `OK`

---
## 1. Architecture Map

### Modules Discovered
```
alpha_liquidity_sweep_predictor.py
  ├── predict_sweep()            — standalone macro structural predictor
  └── LiquiditySweepAlpha        — main class
        ├── get_signal()          — primary signal entry point
        ├── predict()             — backward-compatible wrapper
        ├── calculate_ofi_zscore()— Welford rolling OFI z-score (L2)
        ├── _update_hawkes()      — Hawkes process intensity update
        ├── _detect_regime()      — 5-label regime classifier
        ├── detect_sweep_state()  — pool proximity + Hawkes state machine
        ├── _predict_next_sweep() — logistic directional model
        ├── _detect_fake_breakout()— rejection scorer
        ├── check_resiliency()    — depth recovery scorer
        ├── _ml_sweep_probability()— lightweight logistic feature scorer
        ├── _liquidity_forecast() — short OFI momentum
        ├── update_liquidity_pools()— pool refresh
        └── get_state_metrics()   — telemetry snapshot
```

### Signal Output Schema
```json
{
  "action": "BUY | SELL | HOLD",
  "confidence": "float [0,1]",
  "state": "NORMAL | PRE_SWEEP_BUILDUP | ACTIVE_SWEEP | POST_SWEEP",
  "regime": "TRENDING_UP | TRENDING_DOWN | RANGING | VOLATILE | LOW_LIQUIDITY | UNKNOWN",
  "ofi_zscore": "float [-10, 10]",
  "hawkes_intensity": "float [0, 100]",
  "logic": "str \u2014 human-readable logic path",
  "micro_prob": "float [0,1]",
  "macro_prob": "float [0,1]",
  "prob_above": "float [0,1]",
  "prob_below": "float [0,1]"
}
```

### Backtest Assumptions
| Parameter | Value |
|---|---|
| Bars tested | 500 |
| Bar size | 5 minutes (synthetic) |
| Date range | T+0 → T+2500 min |
| Fee (round-trip) | 7.5 bps |
| Slippage | 5.0 bps |
| Total cost | 12.5 bps |
| Hold horizon | 6 bars |
| Walk-forward | NOT used (single in-sample pass) |
| Embargo | NOT used |
| Threshold optimisation | NOT used |
| Data source | Synthetic historical — no live exchange |
| L2 source | Synthetic L2 snapshots — no real book data |

---
## 2. Signal Metrics

| Metric | OHLCV Run | L2 Run |
|---|---|---|
| total_bars | 500 | 500 |
| long_count | 277 | 277 |
| short_count | 223 | 223 |
| hold_count | 0 | 0 |
| signal_coverage | 1.0 | 1.0 |
| hold_rate | 0.0 | 0.0 |
| long_precision | 0.439 | 0.439 |
| short_precision | 0.2727 | 0.2727 |
| conf_mean | 0.5082 | 0.5053 |
| conf_median | 0.5 | 0.5 |
| conf_std | 0.0582 | 0.0489 |
| conf_entropy | 0.1591 | 0.1048 |

---
## 3. Trading Metrics

| Metric | OHLCV Run | L2 Run |
|---|---|---|
| n_trades | 74 | 74 |
| win_rate | 0.3649 | 0.3649 |
| avg_win | 0.000691 | 0.000643 |
| avg_loss | -0.00051 | -0.000517 |
| profit_factor | 0.7793 | 0.7142 |
| expectancy | -7.1e-05 | -9.4e-05 |
| sharpe | -4.6823 | -6.5375 |
| sortino | -8.1984 | -10.7812 |
| max_drawdown | 0.006 | 0.0075 |
| avg_hold_bars | 5.72 | 5.72 |
| final_equity | 0.994707 | 0.99306 |

---
## 4. L2 vs OHLCV Comparison

| Metric | OHLCV | L2 | Δ (L2 − OHLCV) |
|---|---|---|---|
| hold_rate | 0.0 | 0.0 | 0.0 |
| signal_coverage | 1.0 | 1.0 | 0.0 |
| n_trades | 74 | 74 | 0 |
| win_rate | 0.3649 | 0.3649 | 0.0 |
| profit_factor | 0.7793 | 0.7142 | -0.0651 |
| expectancy | -7.1e-05 | -9.4e-05 | -2.3e-05 |
| sharpe | -4.6823 | -6.5375 | -1.8552 |
| sortino | -8.1984 | -10.7812 | -2.5828 |
| max_drawdown | 0.006 | 0.0075 | 0.0015 |
| conf_mean | 0.5082 | 0.5053 | -0.0029 |
| conf_entropy | 0.1591 | 0.1048 | -0.0543 |

---
## 5. Regime & State Distribution

### OHLCV Run — Internal Regime Labels
| Regime | Bar Count |
|---|---|
| RANGING | 8 |
| UPTREND | 302 |
| DOWNTREND | 117 |
| VOLATILE | 73 |

### L2 Run — Internal Regime Labels
| Regime | Bar Count |
|---|---|
| RANGING | 8 |
| UPTREND | 302 |
| DOWNTREND | 117 |
| VOLATILE | 73 |

### OHLCV Run — Sweep State Labels
| State | Bar Count |
|---|---|
| NORMAL | 312 |
| PRE_SWEEP_BUILDUP | 10 |
| ACTIVE_SWEEP | 178 |

---
## 6. Issues Found

### 🟠 [HIGH] I-006 — OHLCV
**Problem:** Profit factor=0.78 < 1.0 — system is losing money after costs.  
**Fix:** Check cost assumptions (FEE_BPS+SLIP_BPS) and direction_mode.

### 🟠 [HIGH] I-008 — OHLCV
**Problem:** Sharpe=-4.68 is significantly negative — system destroys value.  
**Fix:** Inspect direction_mode and ensemble threshold calibration.

### 🟡 [MEDIUM] I-010 — OHLCV
**Problem:** Confidence entropy=0.16 — outputs cluster near a single value. Probability calibration (_shrink_prob) may be over-regularising.  
**Fix:** Run isotonic/Platt calibration on held-out OOF labels.

### 🟠 [HIGH] I-006 — L2
**Problem:** Profit factor=0.71 < 1.0 — system is losing money after costs.  
**Fix:** Check cost assumptions (FEE_BPS+SLIP_BPS) and direction_mode.

### 🟠 [HIGH] I-008 — L2
**Problem:** Sharpe=-6.54 is significantly negative — system destroys value.  
**Fix:** Inspect direction_mode and ensemble threshold calibration.

### 🟡 [MEDIUM] I-010 — L2
**Problem:** Confidence entropy=0.10 — outputs cluster near a single value. Probability calibration (_shrink_prob) may be over-regularising.  
**Fix:** Run isotonic/Platt calibration on held-out OOF labels.


---
## 7. Safety & Integrity Checks

| Check | Status | Notes |
|---|---|---|
| Live trading endpoint | ✅ SAFE | None present in predictor |
| Exchange API call | ✅ SAFE | No external connections |
| Real API key usage | ✅ SAFE | No credentials in scope |
| Synthetic OFI fallback | ✅ SAFE | Book fallback uses prev_book copy — valid |
| HOLD-only mode | ✅ OK | OHLCV hold_rate=0.0% |
| Non-finite values | ✅ SAFE | _safe_float guards present throughout |
| Forward-looking leakage | ⚠️ RISK | Forward-return label uses bar[i+HOLD_BARS].close — correct. Pool seeding from future bars would be leakage — not present here. |
| Survivorship bias | N/A | Single-asset test; not applicable |
| Walk-forward contamination | ⚠️ RISK | No walk-forward split used — single in-sample pass overfits to regime sequence |
| Missing calibration | ⚠️ RISK | _shrink_prob uses fixed 0.8 shrinkage — no isotonic/Platt calibration |
| Fake-breakout threshold | ⚠️ RISK | rejection_score=0.5 reached by price position alone — no OFI confirmation required |
| Hawkes branching ratio | ⚠️ CHECK | alpha=0.1, decay=0.5 → ratio=0.2 (stable). Cap fires at 0.9. |
| Missing transaction costs | ⚠️ PARTIAL | Fees+slippage modelled but no funding, borrowing, or impact cost |

---
## 8. Upgrade Recommendations

| ID | Severity | Area | Root Cause | Expected Benefit | Difficulty | Overfit Risk | Safest Next Step |
|---|---|---|---|---|---|---|---|
| U-01 | CRITICAL | Walk-forward validation | All threshold tuning is in-sample. Any reported edge may be pure overfitting. | Implement TimeSeriesSplit with embargo gap (min 2×HOLD_BARS) before reporting Sharpe. | HIGH | False | Add sklearn TimeSeriesSplit wrapper around backtest loop. |
| U-02 | CRITICAL | Probability calibration | _shrink_prob uses a fixed 0.8 scalar — not calibrated to real win rates. | Calibrated probabilities are required for valid Kelly/position sizing. | MEDIUM | False | Collect OOF predictions → fit isotonic regression → store calibrator artifact. |
| U-03 | HIGH | Real L2 data integration | Current L2 path uses synthetic books. OFI z-score will be meaningless on real microstructure. | OFI is the primary edge signal — synthetic data cannot validate it. | HIGH | False | Integrate real L2 CSV replay loader; validate book format before running. |
| U-04 | HIGH | Fake-breakout threshold hardening | rejection_score=0.5 is achieved by price position alone (one component = 0.5). OFI confirmation is optional, making the condition trivially true after any pool breach. | False positives in ACTIVE_SWEEP will generate low-quality fade trades. | LOW | True | Raise threshold to 0.8 and require BOTH price-position AND ofi_z confirmation. |
| U-05 | HIGH | VOLATILE regime threshold calibration | vol_ratio > 0.015 hard threshold is uncalibrated — may fire too often in crypto. | Excessive VOLATILE gating suppresses all signals, causing HOLD dominance. | MEDIUM | False | Measure empirical vol_ratio distribution; set threshold at 95th percentile. |
| U-06 | HIGH | Pool max age expiration logic | pool_max_age_bars=200 evicts pools by bar count only. In thin markets, 200 bars of 5-min data = 16 hours — a stale pool at a structurally irrelevant level. | Stale pools generate false sweep signals. | LOW | False | Add ATR-normalised age scoring: expire pool when price has moved >N×ATR from pool. |
| U-07 | MEDIUM | Regime classifier expansion | _detect_regime uses two EMAs and a fixed vol_ratio threshold. No volume, spread, or market-impact features. | Regime misclassification degrades all downstream gating. | MEDIUM | True | Add volume Z-score and spread-to-ATR features; validate with confusion matrix. |
| U-08 | MEDIUM | Hawkes process empirical calibration | hawkes_alpha=0.1, hawkes_decay=0.5 are hard-coded defaults with no calibration. | Branching ratio determines sweep sensitivity — wrong values mute or flood signals. | MEDIUM | True | MLE-fit alpha/decay on held-out trade event sequences. |
| U-09 | MEDIUM | Risk model / position sizing | No stop-loss, no position sizing, no risk-per-trade limit present. | Max drawdown is unbounded. Cannot go to paper trading without this. | LOW | False | Add ATR-based stop-loss (e.g. 1.5×ATR) and fixed fractional sizing. |
| U-10 | LOW | HOLD fallback telemetry | HOLD outputs do not surface which gate fired (VOLATILE, LOW_LIQUIDITY, warmup, etc.). | Cannot diagnose HOLD dominance without knowing which gate is responsible. | LOW | False | Return logic_path='gate:VOLATILE|warmup_factor=0.3' in every HOLD output. |

---
## 9. Top 5 Priority Fixes

**1. U-01 — Walk-forward validation**  
All threshold tuning is in-sample. Any reported edge may be pure overfitting.  
Next step: Add sklearn TimeSeriesSplit wrapper around backtest loop.

**2. U-02 — Probability calibration**  
_shrink_prob uses a fixed 0.8 scalar — not calibrated to real win rates.  
Next step: Collect OOF predictions → fit isotonic regression → store calibrator artifact.

**3. U-03 — Real L2 data integration**  
Current L2 path uses synthetic books. OFI z-score will be meaningless on real microstructure.  
Next step: Integrate real L2 CSV replay loader; validate book format before running.

**4. U-04 — Fake-breakout threshold hardening**  
rejection_score=0.5 is achieved by price position alone (one component = 0.5). OFI confirmation is optional, making the condition trivially true after any pool breach.  
Next step: Raise threshold to 0.8 and require BOTH price-position AND ofi_z confirmation.

**5. U-05 — VOLATILE regime threshold calibration**  
vol_ratio > 0.015 hard threshold is uncalibrated — may fire too often in crypto.  
Next step: Measure empirical vol_ratio distribution; set threshold at 95th percentile.

---
## 10. Top 5 Biggest Risks

**R-01 — No walk-forward validation**  
Reported Sharpe may be 100% in-sample overfitting.

**R-02 — Synthetic L2 data**  
OFI z-score cannot be validated until real book data is wired.

**R-03 — Uncalibrated probabilities**  
Confidence values are not valid probabilities — Kelly sizing will blow up.

**R-04 — HOLD dominance in VOLATILE regime**  
Crypto frequently triggers the 0.015 vol_ratio gate — strategy may rarely trade.

**R-05 — No stop-loss or position sizing**  
Single unlucky trade could exceed tolerable drawdown.

---
## 11. Production Readiness Assessment

| Assessment | Status |
|---|---|
| Research-only (understand structure) | ✅ YES |
| Paper-trading ready | ✅ YES — with risk model added |
| Live trading ready | ❌ NO — missing calibration, walk-forward validation, and risk model |

---
## 12. Run Errors & Blockers

No runtime errors or blockers encountered.

---
*Report auto-generated by run_backtest.py — 2026-05-18 09:01 UTC*
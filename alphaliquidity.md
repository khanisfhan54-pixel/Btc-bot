# Alpha Liquidity Sweep Predictor — Full Audit Report

**Generated:** 2026-05-24 23:18 UTC
**Verdict:** `BROKEN`
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
| long_count | 304 | 304 |
| short_count | 196 | 196 |
| hold_count | 0 | 0 |
| signal_coverage | 1.0 | 1.0 |
| hold_rate | 0.0 | 0.0 |
| long_precision | 0.6444 | 0.6444 |
| short_precision | 0.5 | 0.5 |
| conf_mean | 0.5006 | 0.5 |
| conf_median | 0.5 | 0.5 |
| conf_std | 0.0084 | 0.0 |
| conf_entropy | 0.0376 | 0.0 |

---
## 3. Trading Metrics

| Metric | OHLCV Run | L2 Run |
|---|---|---|
| n_trades | 73 | 73 |
| win_rate | 0.589 | 0.589 |
| avg_win | 0.000767 | 0.000767 |
| avg_loss | -0.000298 | -0.000298 |
| profit_factor | 3.6837 | 3.6857 |
| expectancy | 0.000329 | 0.000329 |
| sharpe | 24.0664 | 24.0725 |
| sortino | 48.3644 | 48.3584 |
| max_drawdown | 0.0028 | 0.0028 |
| avg_hold_bars | 5.81 | 5.81 |
| final_equity | 1.024283 | 1.024289 |

---
## 4. L2 vs OHLCV Comparison

| Metric | OHLCV | L2 | Δ (L2 − OHLCV) |
|---|---|---|---|
| hold_rate | 0.0 | 0.0 | 0.0 |
| signal_coverage | 1.0 | 1.0 | 0.0 |
| n_trades | 73 | 73 | 0 |
| win_rate | 0.589 | 0.589 | 0.0 |
| profit_factor | 3.6837 | 3.6857 | 0.002 |
| expectancy | 0.000329 | 0.000329 | 0.0 |
| sharpe | 24.0664 | 24.0725 | 0.0061 |
| sortino | 48.3644 | 48.3584 | -0.006 |
| max_drawdown | 0.0028 | 0.0028 | 0.0 |
| conf_mean | 0.5006 | 0.5 | -0.0006 |
| conf_entropy | 0.0376 | 0.0 | -0.0376 |

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
| NORMAL | 456 |
| PRE_SWEEP_BUILDUP | 4 |
| ACTIVE_SWEEP | 40 |

---
## 6. Issues Found

### 🟡 [MEDIUM] I-010 — OHLCV
**Problem:** Confidence entropy=0.04 — outputs cluster near a single value. Probability calibration (_shrink_prob) may be over-regularising.
**Fix:** Run isotonic/Platt calibration on held-out OOF labels.

### 🟡 [MEDIUM] I-010 — L2
**Problem:** Confidence entropy=0.00 — outputs cluster near a single value. Probability calibration (_shrink_prob) may be over-regularising.
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
| Paper-trading ready | ❌ NO |
| Live trading ready | ❌ NO — missing calibration, walk-forward validation, and risk model |

---
## 12. Run Errors & Blockers

### OHLCV Run

### L2 Run
- BLOCKER: [BLOCKER] L2 replay path incomplete: bookDepth.csv contains only relative percentage levels, cannot reconstruct absolute prices for alpha_liquidity_sweep_predictor without synthetic assumptions.

---
*Report auto-generated by run_backtest.py — 2026-05-24 23:18 UTC*
# REMAINING_BLOCKERS.md
## Items Preventing Paper Trading — NOT Fixed By This PR (By Design)

This PR is a SURGICAL HARDENING. It fixes 6 verified engineering issues without
touching strategy logic, calibration, or data acquisition. The following remain
as explicit blockers before paper trading can begin.

---

### BLOCKER 1 — Signal Conviction Below Gate (CRITICAL)

**Status:** NOT addressed — out of scope for surgical hardening  
**Issue:** `conf p99 = 0.48 < gate = 0.60`. Mean = 0.133. Zero gated trades at all resolutions.  
**Root cause:**
- `ofi_z = 0.0` on ALL bars (no Dec-2023 L2 depth available)
- HMM posterior un-normalized contribution to conviction
- Calibrated on a single month (Dec-2023 only)

**Required fix:**
1. Fetch Dec-2023 L2 depth → `data/bookDepth.csv`
2. Re-run calibration with real OFI features
3. Recalibrate on ≥90 days (Oct–Dec 2023 minimum)

---

### BLOCKER 2 — No Dec-2023 L2 Depth Data (CRITICAL)

**Status:** NOT addressed — data acquisition required  
**Issue:** All depth files are wrong-dated (2026-05-03, 2026-03-27, 2024-04-01).  
**Confirmed by:** `_validate_l2_timestamp_alignment` (new Issue C fix) correctly rejects all three.  
**BACKTEST_LABEL emitted:** `NON-PRODUCTION-VALID: l2_data_missing_or_mismatched`

**Required fix:** Run `data_tools/fetch_binance_l2_depth.py` for Dec-2023 window → produces `data/bookDepth.csv`

---

### BLOCKER 3 — Signal Quality Far Below Break-Even (CRITICAL)

**Status:** NOT addressed — strategy improvement required  
**Issue:** Ungated forward-return scoring: PF=0.1313, expectancy=-26.05 bps, Sharpe=-183.3.  
**Required fix:** 15m aggregation (SNR=1.429×); conf≥p75 quintile filter; multi-engine confirmation.

---

### BLOCKER 4 — In-Sample Contamination (HIGH)

**Status:** NOT addressed — walk-forward framework required  
**Issue:** Calibrated on Dec-2023, tested on same Dec-2023 window. No held-out validation period.  
**Required fix:** Walk-forward: calibrate Nov-2023, test Dec-2023. Minimum 30-day embargo period.

---

### BLOCKER 5 — OFI Fully Synthetic (HIGH)

**Status:** NOT addressed — depends on BLOCKER 2  
**Issue:** `ofi_z = 0.0` on ALL bars. All three SJM regime centroids have `ofi_col = [0,0,0]`.  
**Required fix:** Blocked on Blocker 2 (Dec-2023 L2 depth). After data is fetched, re-run calibration.

---

### WARNING — Origin/Main Branch 5 Commits Ahead (MEDIUM)

**Status:** NOT merged — pending review  
**Issue:** PRs #200–204 on origin/main include HMM normalization fix (partially overlaps Issue B)
and strict L2-only calibration path. This PR does not cherry-pick those commits.  
**Recommendation:** After this PR merges, reconcile with origin/main.

---

### WARNING — EMA Gap Clamp (LOW)

**Status:** NOT addressed  
**Issue:** `[EMA] Gap of 300s exceeds max` fires every 5m bar.  
**Required fix:** Scale `EMA.max_gap_s` with resolution: 5m→320s, 15m→960s.

---

### WARNING — Alpha Confidence Narrow Band (LOW)

**Status:** NOT addressed  
**Issue:** `alpha_conf` range [0.261, 0.448] — nearly constant.  
**Required fix:** Audit `OrchestratorConfig` clamping. Check if alpha signals are being compressed.

---

## Summary: Paper Trading Readiness Checklist

| Requirement | This PR | Still Needed |
|-------------|---------|-------------|
| Live execution isolated from backtest | ✅ FIXED | — |
| Invalid replay data rejected | ✅ FIXED | Fetch correct Dec-2023 L2 data |
| HMM posteriors sum to 1.0 | ✅ FIXED | — |
| Orchestration enforced (no bypass) | ✅ FIXED | — |
| Failures observable (no silent swallows) | ✅ FIXED | — |
| L2 feed failures detected | ✅ FIXED | — |
| Real Dec-2023 L2 depth | ❌ Blocked | Fetch data |
| conf p99 ≥ 0.60 | ❌ Blocked | Recalibrate with real OFI |
| Walk-forward validation | ❌ Not implemented | Implement |
| PF > 1.2 on held-out window | ❌ -26 bps | Fix signal quality |
| ≥90 days calibration data | ❌ 31 days | Expand data range |

# AdvancedRegimeEngine — Production Fix & Upgrade Plan

**Document type:** production-grade fix and upgrade roadmap
**Audited commit:** `df70811efd9f86b0a8d21fe3389ec0a24c6272f9`
**Date:** 2026-05-02
**Scope:** `AdvancedRegimeEngine`, `BacktestEngine`, `AlphaOrchestrator`, `SignalEngine`, `LiquiditySweepAlpha`, `FeatureEngine`, `calibrate_regime.py`, `data_tools/l2_to_backtest.py`, `data_tools/l2_pipeline.py`
**Mode:** documentation-only — no trading-logic edits in this revision

---

## 1. Executive Verdict

**Status: RESEARCH-ONLY. Not production-ready. Not paper-trading-ready.**

The `AdvancedRegimeEngine` (ARE) wired through `BacktestEngine` → `AlphaOrchestrator` → `SignalEngine` → `LiquiditySweepAlpha` (LSA) ran end-to-end on a 31-day matched window of real BTCUSDT_240329 data (Dec 2023). The pipeline did not crash, did not emit fail-safe payloads, and produced calibrated outputs from the real `calibrate_regime.py` weight artifact. **However the strategy lost money on every meaningful risk-adjusted measure**: daily-aggregated annualized Sharpe **-17.09** across 30 trading days, win rate **19.38%** on 1,300 trades, max drawdown **94.59%**, and **100% of trades occurred in a single regime label (BEAR)**. This is not a tuning gap; it is a structural signal failure for this market regime, made worse by a backtest that feeds the engine a synthetic order book derived from candles instead of the real `bookDepth.csv` / `bookTicker` microstructure that production would see.

| Stage | Decision | One-line reason |
|-------|----------|-----------------|
| Production / live trading | **BLOCKED** | 1 HIGH + 2 MEDIUM findings; daily Sharpe -17.09; DD 94.6% |
| Paper trading | **BLOCKED** | broken signal would burn paper capital and pollute future calibration data |
| Research / further calibration | **APPROVED** | pipeline plumbing is sound; engineers can iterate safely against real data |
| Phase 5 (live readiness) | **GATE NOT PASSED** | see § 7 Acceptance Criteria below |

**What is working:** schema, calibration loader, threading, fail-safe path, pipeline wiring, L1-TOB ingestion, 99.88% bar↔book alignment.
**What is partial:** L2 depth-beyond-L1 ingestion, GARCH stability guardrail, schema-violation observability, multi-resolution backtest coverage on the 31-day window.
**What is blocking live readiness:** synthetic order book in `BacktestEngine`, backtest-only `orchestrator_action_threshold` override, regime collapse onto a single label for this window, no out-of-sample re-run on a second contract, no thread-safety / save-load round-trip exercise, no IGARCH runtime warning, no Prometheus counter on schema failures.

---

## 2. Current Engine State

| Subsystem | Status | Direct evidence (this audit run) |
|-----------|--------|----------------------------------|
| **Calibration** | WORKING | `weights/advanced_regime_weights.npz` loads with all 5 required keys (`nhhmm_beta(3,3,3)`, `nhhmm_mu(3,)`, `nhhmm_sigma(3,)`, `sjm_centroids(3,3)`, `sjm_feature_weights(3,)`); all values finite; `calibration_status='calibrated'`; `engine_status='OK'`. `calibrate_regime.py` (N_FEATURES=3, features=`[log_return, ofi_z, vol_z]`) produced these weights. |
| **Schema** | WORKING | `_OUTPUT_SCHEMA_VERSION='1.2.0'` consistent across `_build_output` and the fail-safe path (`advanced_regime_engine.py` lines 43, 382, 433). `_validate_output_schema` enforces version, `execution_mode ∈ _VALID_EXECUTION_STRATEGIES ∪ _VALID_OPERATIONAL_MODES`, and `execution_side ∈ _VALID_EXECUTION_SIDE`. Zero schema violations on 39,694 update calls. |
| **Backtest pipeline (plumbing)** | WORKING | `BacktestEngine._run_single_pass` correctly invokes ARE → LSA → `SignalEngine` → `AlphaOrchestrator`. `_build_canonical_are_payload` returns the canonical 4-key dict with `features` shape `(3,)`. `_seed_lsa(data)` warms LSA off the first 25 bars (no permanent NORMAL state). |
| **Backtest economics** | BROKEN | 1,300 trades all SHORT in BEAR regime. Daily Sharpe -17.09. Win 19.38%. Profit factor 0.157. Max DD 94.59%. Best trade +3.15%, worst -1.39%, longest losing streak 44. |
| **Microstructure (L1)** | WORKING | `bookTicker` ingested via `prep_book_dec2023.py` (14.3M raw rows → 87,484 30s snapshots). Real `spread_bps` and L1 imbalance (`bid_qty / (bid_qty + ask_qty)`) computed from TOB. |
| **Microstructure (L2 depth)** | BROKEN | `BacktestEngine._simulate_snapshot_from_candle` builds a 3-level synthetic book from each candle (`best_bid = mid - (h-l)*0.01`, depth = `volume / mid`). Real `bookDepth` is never wired into the backtest pipeline. Any LSA / FeatureEngine logic that depends on depth-imbalance or queue dynamics sees a deterministic projection of the candle, not real microstructure. |
| **Regime stability** | BROKEN (this window) | Per-bar regime distribution: BEAR=39,291 (98.99%), TOXIC=297, HALTED=105, TREND=1, RANGE=0. Regime model collapsed onto a single label for Dec 2023. Conviction in BEAR was 0.994 average, so the collapse is high-confidence — i.e. the model was certain it was wrong. |
| **Signal economics** | BROKEN | Negative expectancy at -0.2235% per trade after 22 bps round-trip cost. 12-bar (12-min) average hold. Signal coverage 3.28% (1,300 / 39,694) — adequate count, terrible quality. |
| **Data alignment** | WORKING | `data_tools/l2_pipeline.py`-style alignment in audit script: 39,648 / 39,695 1m bars matched to a book snapshot (99.88%). Median snapshot age vs bar close < 30s. |
| **Threading & concurrency** | NOT EXERCISED | `threading.RLock` + `@_synchronized` decorator + daemon background workers + finalizer present in `advanced_regime_engine.py`. Single-threaded audit only — no concurrent `update()` calls were issued. |
| **State save/load round-trip** | NOT EXERCISED | `model_signature` produced but no save/load cycle was performed in this audit. |
| **Circuit breakers** | NOT TRIGGERED | `_MAX_DRAWDOWN=0.12`, `_MAX_CONSECUTIVE_LOSSES=7`, `_VOL_SHOCK_MULTIPLIER=3.5`. Zero breaker triggers fired in 39,694 bars — surprising given DD 94.6%, suggesting the engine-internal DD accounting differs from the trade-log DD computation. **This discrepancy must be reconciled before live (see Blocker P-7).** |

---

## 3. Production Blockers

Each blocker is keyed `P-N`. The order is the recommended fix order.

### P-1 — Synthetic order book in `BacktestEngine` *(severity: HIGH)*
- **Component:** `backtest_engine.py` → `_simulate_snapshot_from_candle` (line 176), `_simulate_trades_from_candle` (line 190), called from `_build_canonical_are_payload` (line 341) and `_build_lsa_market_data` (line 365).
- **Symptom:** Every ARE update inside `_run_single_pass` receives `ofi_zscore=0` (constant) and a 3-level book whose prices are deterministic functions of the candle (`mid ± (h-l)*0.01`) and whose depth is `volume / mid`. LSA receives the same synthetic snapshot.
- **Root cause:** `BacktestEngine` was written to be standalone-runnable from OHLCV alone; the optional `bookDepth.csv` / `bookTicker.csv` real-microstructure path was never wired into `_run_single_pass`.
- **Production impact:** in production the engine sees real OFI, real spread, real queue. In backtest it sees a constant. Any alpha learned in calibration that depends on microstructure is **un-validated** by backtest. Backtest results are systematically optimistic about robustness and uninformative about microstructure-driven alpha.
- **Why it blocks live:** the live environment is a different statistical environment than the backtest environment. We have no evidence the engine generalizes from one to the other.
- **Exact fix path:** see § 4 → FIX-1.
- **Verification:** unit test asserts `ofi_zscore` and `spread_bps` vary across bars and equal the values fed in `book_features`; re-run `audit_run_dec2023.py` and confirm `avg_edge_score` and signal coverage change vs the synthetic-book run.

### P-2 — `BacktestConfig.orchestrator_action_threshold` is set to 0.30, production is 0.6 *(severity: MEDIUM)*
- **Component:** `backtest_engine.py` → `BacktestConfig` (line 240): `orchestrator_action_threshold: float = 0.30`. Production default in `AlphaOrchestrator` is `0.6`.
- **Symptom:** Backtest produces ~2× the action frequency that production would, so `signal_coverage_pct=3.28%` does **not** translate to live coverage.
- **Root cause:** the comment in `BacktestConfig` notes this was lowered specifically because the synthetic book produces clamped convictions. With P-1 fixed, the justification disappears.
- **Production impact:** every signal-distribution claim from this backtest overstates live signal frequency; risk per unit time is over-stated.
- **Why it blocks live:** parity between backtest and live config is a non-negotiable correctness requirement.
- **Exact fix path:** see § 4 → FIX-2.
- **Verification:** after FIX-1, restore to 0.6 and confirm signal coverage falls to a level consistent with production conviction distribution. **Do not silently re-lower it** if coverage falls to 0 — that becomes its own finding.

### P-3 — Regime collapse onto a single label *(severity: HIGH for this dataset)*
- **Component:** `AdvancedRegimeEngine` regime classifier (NHHMM + SJM via `nhhmm_beta`, `sjm_centroids`).
- **Symptom:** 98.99% of bars labeled BEAR; TREND=1 bar, RANGE=0 bars over 31 days of BTC data that included real intraday rallies and chop. All 1,300 trades fired in BEAR with average conviction 0.994.
- **Root cause:** unknown without re-running on a second contract, but two leading hypotheses: (a) feature normalization in `_build_canonical_are_payload` is not matched to the calibration-time normalization stored in `weights/advanced_regime_weights.npz`, so `[log_ret, ofi_z, vol_z]` is on the wrong scale and pushes the SJM nearest-centroid step into a single basin; (b) the synthetic-book OFI=0 (P-1) starves the regime classifier of one of its three input features, biasing it toward whichever centroid is closest in the remaining 2-D space.
- **Production impact:** if the live engine collapses on real data the way it did here, every signal will be SHORT — a directional bias the operator did not authorize.
- **Why it blocks live:** at minimum, two non-collapsed regimes must appear in any 30-day production-comparable window before the model can be trusted to react to regime change.
- **Exact fix path:** fix P-1 first (so OFI is real, not 0), then re-run, then if still collapsed, instrument feature normalization and compare to `calibrate_regime.py` conventions. See § 4 → FIX-6.
- **Verification:** after FIX-1, per-bar regime distribution must show ≥ 2 regimes with ≥ 5% of bars each on the same 31-day Dec 2023 window.

### P-4 — Schema-violation observability gap *(severity: LOW)*
- **Component:** `advanced_regime_engine.py` → `_validate_output_schema` (line 185), `_build_output` fail-safe path (line 429+).
- **Symptom:** schema violations are logged via `LOGGER.error` but no Prometheus counter is incremented and no rate-limited operator alarm fires. Existing Prometheus metrics in the file (`REGIME_COUNTER`, `ENGINE_HEALTH`, etc., lines 70-80) cover regime/health/risk/vol/position/feed/MTF/latency but **not** schema violations or fail-safe activations.
- **Root cause:** `_validate_output_schema` was added later than the Prometheus metric block and never received its own counter.
- **Production impact:** a slow drift to fail-safe payloads is observable only by reading logs; no SLO can be defined on it.
- **Why it blocks live:** operators cannot alarm on a failure mode they cannot count.
- **Exact fix path:** see § 4 → FIX-4.
- **Verification:** unit test injects an invalid output, calls `_build_output`, asserts both `regime_schema_violations_total` and `regime_failsafe_emitted_total` increment.

### P-5 — IGARCH runtime guardrail missing *(severity: MEDIUM)*
- **Component:** `advanced_regime_engine.py` → `AdvancedRegimeEngine.__init__` (lines 1207, 1231, 1275-1289) and `_igarch_hard_limit = 1.05` (line 1334).
- **Symptom:** `allow_igarch=False` is the constructor default, and `BacktestEngine` does not flip it, so the system is currently safe. But the constructor only checks `alpha + beta` at init time — once the engine is running, no warning fires if persistence drifts above 0.99 mid-session, and the `_igarch_hard_limit = 1.05` defines a soft window where unstable variance is still tolerated when `allow_igarch=True`.
- **Root cause:** the guardrail was implemented as an init-time gate, not a runtime monitor.
- **Production impact:** if a future calibration produces persistence near 1.0, `expected_volatility` drifts toward unboundedness, miscalibrating `risk_metrics` without operator visibility.
- **Why it blocks live:** unbounded variance means unbounded position sizing if the orchestrator ever conditions on `expected_volatility`.
- **Exact fix path:** see § 4 → FIX-3.
- **Verification:** unit test constructs an ARE with synthetic `alpha=0.6, beta=0.4`, calls `update()`, asserts `_warn_rate_limited("garch_persistence_high", ...)` fires.

### P-6 — `FeatureEngine` payload limited to `[log_ret, ofi_z, vol_z]` (3 features) *(severity: MEDIUM, scope-dependent)*
- **Component:** `_build_canonical_are_payload` in `backtest_engine.py` (line 341) and `calibrate_regime.py` (`N_FEATURES = 3`, line 63).
- **Symptom:** the engine is locked to a 3-feature input by design (`features` shape `(3,)` enforced everywhere). This is consistent across calibration and runtime, so it is **not a bug**, but it caps the information available to the regime classifier and any future feature additions require a coordinated re-calibration.
- **Root cause:** historical design choice (3 features = 3 regimes).
- **Production impact:** none today; future-blocker only.
- **Why it would block future feature work:** adding spread_bps or imbalance as a fourth feature requires re-running `calibrate_regime.py` with `N_FEATURES=4`, regenerating `weights/advanced_regime_weights.npz` with new shapes (`nhhmm_beta(3,3,4)`, `sjm_centroids(3,4)`, `sjm_feature_weights(4,)`), and updating `_validate` in `calibrate_regime.py` (line 270). This is a meaningful refactor, not a config flip.
- **Exact fix path:** out of scope for this audit; flagged for the design backlog.
- **Verification:** N/A — documentation flag only.

### P-7 — Engine-internal drawdown vs trade-log drawdown discrepancy *(severity: MEDIUM)*
- **Component:** `AdvancedRegimeEngine._MAX_DRAWDOWN = 0.12` (line 1016) circuit breaker logic vs `audit_output/phase6_trade_log.csv` cumulative-PnL DD = 94.59%.
- **Symptom:** Phase 5C reports zero circuit-breaker triggers despite the cumulative PnL of the trade log going from $10,000 to $541. Either (a) the engine measures DD on a different signal than realized PnL (e.g. on `expected_volatility` deviation, not on portfolio equity), or (b) the breaker is wired in `BacktestEngine` but never reaches the engine in the audit harness.
- **Root cause:** unknown — needs a code trace from `_MAX_DRAWDOWN` to its read-site.
- **Production impact:** if the breaker never trips in a 94.6% drawdown scenario, it will never trip in production either.
- **Why it blocks live:** circuit breakers exist to prevent ruin. If they cannot detect ruin in backtest they will not detect it in production.
- **Exact fix path:** see § 4 → FIX-7.
- **Verification:** synthetic test: feed the engine 50 consecutive losing 1m bars at -2% each; assert `engine_status` transitions to `CIRCUIT_BREAKER` before the 50th bar.

### P-8 — `data_tools/l2_to_backtest.py` and `data_tools/l2_pipeline.py` not wired through `BacktestEngine.run_backtest()` *(severity: HIGH — same root cause as P-1, separate observability)*
- **Component:** `data_tools/l2_to_backtest.py`, `data_tools/l2_pipeline.py`, `BacktestEngine.run_backtest` (line 439), `BacktestEngine._run_single_pass` (line 493).
- **Symptom:** the L2 helper modules exist but `run_backtest(ohlcv_data)` accepts only OHLCV and offers no `book_data` parameter; the audit had to write its own bookTicker downsampler (`prep_book_dec2023.py`) and the alignment was done in the audit script, not in the engine.
- **Root cause:** the L2 plumbing was added as standalone tooling, never integrated into `BacktestEngine`.
- **Production impact:** anyone running a backtest cannot opt in to real microstructure without re-implementing the alignment.
- **Why it blocks live:** correctness of the live system cannot be validated end-to-end without a backtest path that includes the same data feeds.
- **Exact fix path:** see § 4 → FIX-1 (the API change covers both P-1 and P-8).
- **Verification:** see FIX-1 verification.

### P-9 — Multi-resolution coverage incomplete on the 31-day window *(severity: MEDIUM)*
- **Component:** `BacktestEngine.run_backtest_multi_resolution` (line 442), executed by Phase 5D of the audit.
- **Symptom:** Phase 5D was SKIPPED on this run because iterating ARE over 1m + 5m + 15m on 39,695 bars exceeded the audit time budget. The prior Phase 4 audit's claim that 5m is `production-valid` was made on an 8-hour window only and is **not** carried forward to this verdict.
- **Root cause:** ARE per-bar cost (~80-130 ms) × 39,695 bars × 3 resolutions × no caching = unworkable in a single audit window.
- **Production impact:** we do not yet know whether 5m or 15m is the correct production-target resolution on a real 30-day window with real microstructure.
- **Why it blocks live:** the resolution choice is one of the largest single drivers of strategy economics.
- **Exact fix path:** see § 4 → FIX-5.
- **Verification:** Phase 5D completes; per-resolution Sharpe / DD / win-rate table is published; the resolution with the highest daily-aggregated Sharpe at acceptable DD is selected.

---

## 4. Detailed Fix Plan (priority-ordered)

Each fix lists: **change**, **files**, **intended effect**, **verification**, **rollback risk**, **acceptance criteria**.

### FIX-1 — Wire real L2/L1 book features into `BacktestEngine` *(addresses P-1, P-8)*
- **Change:** add an optional `book_features: Optional[Sequence[BookSnapshot]]` argument to `BacktestEngine._run_single_pass` (and surface it through `BacktestEngine.run_backtest` and `run_backtest_multi_resolution`). When provided, replace `_simulate_snapshot_from_candle` inside both `_build_canonical_are_payload` and `_build_lsa_market_data` with an indexed lookup into `book_features`. When `book_features is None`, keep the synthetic path as a typed fallback (do **not** delete it — research workflows still need OHLCV-only mode).
- **Files:** `backtest_engine.py`, `data_tools/l2_to_backtest.py` (add a public `align_book_to_bars(bars, book) -> List[BookSnapshot]` helper if not present), `data_tools/l2_pipeline.py` (factor the existing alignment logic into a reusable function).
- **Intended effect:** ARE and LSA see real `ofi_zscore`, real `spread_bps`, real depth imbalance on every bar where book data is available. Removes the systematic information advantage of live trading over backtest. Enables P-3 (regime collapse) to be re-investigated under real microstructure.
- **Verification:**
  1. Unit test: `BacktestEngine.run_backtest(bars, book_features=fixture)` — assert that the ARE payload's `ofi_zscore` and `spread_bps` vary across bars and exactly match the fixture values (not the synthetic projection).
  2. Audit re-run: execute `audit_run_dec2023.py` after FIX-1 and confirm `avg_edge_score`, signal coverage, and per-bar regime distribution all change vs the synthetic-book baseline.
- **Rollback risk:** **Low** — new argument is optional and defaults to `None` (synthetic path preserved). No existing call sites need to change to keep working.
- **Acceptance criteria:**
  - Unit test passes.
  - On the same 31-day Dec 2023 window, `ofi_zscore` shows variance > 0 across bars (currently 0).
  - Per-bar regime distribution shows ≥ 2 regimes with ≥ 5% of bars each.

### FIX-2 — Restore `BacktestConfig.orchestrator_action_threshold` to 0.6 *(addresses P-2)*
- **Change:** change the default in `BacktestConfig` (`backtest_engine.py` line 240) from `0.30` back to `0.6`. Update the inline comment to record the lowering date and the FIX-1 commit hash that justified the restore.
- **Files:** `backtest_engine.py`.
- **Intended effect:** backtest action frequency matches production action frequency. Eliminates a known bias inflating signal coverage in research vs live.
- **Verification:** re-run audit; confirm `signal_coverage_pct` drops to a level consistent with production conviction distribution. If it falls to 0, log it and **do not** silently re-lower the threshold — that becomes the next finding (alpha sources are not informative enough).
- **Rollback risk:** **Medium** — may surface that LSA / SignalEngine convictions are systematically below 0.6, requiring conviction recalibration. Acceptable risk because the alternative (silently divergent backtest config) is worse.
- **Acceptance criteria:**
  - `BacktestConfig.orchestrator_action_threshold == 0.6`.
  - Re-run audit shows non-zero signal coverage at the new threshold; if zero, FIX-2A (conviction calibration) is opened as a follow-up.
  - **Must be applied AFTER FIX-1**, never before.

### FIX-3 — IGARCH runtime guardrail *(addresses P-5)*
- **Change:** in `AdvancedRegimeEngine.update`, after GARCH parameters are refit, check `alpha + beta > 0.99`. If true, increment a Prometheus counter `regime_garch_persistence_high_total{engine_id}` and emit `_warn_rate_limited("garch_persistence_high", alpha, beta)` regardless of `allow_igarch` flag. Update the `__init__` docstring to mark `allow_igarch=True` as a research-only flag.
- **Files:** `advanced_regime_engine.py`.
- **Intended effect:** operators see a paging signal before variance drift produces miscalibrated `expected_volatility`. Documents the soft window between 1.0 and `_igarch_hard_limit=1.05`.
- **Verification:** unit test constructs an ARE with synthetic GARCH params at `alpha=0.6, beta=0.4` and asserts the warning fires on the next `update()` call.
- **Rollback risk:** **Negligible** — additive observability change, no behavior change.
- **Acceptance criteria:**
  - Unit test passes.
  - Existing test suite passes unchanged.

### FIX-4 — Prometheus counters for schema failures and fail-safe activations *(addresses P-4)*
- **Change:** add two Prometheus counters next to the existing block (`advanced_regime_engine.py` lines 70-80):
  ```
  REGIME_SCHEMA_VIOLATIONS = PromCounter("regime_schema_violations_total", "...", ["engine_id", "reason"])
  REGIME_FAILSAFE_EMITTED  = PromCounter("regime_failsafe_emitted_total",  "...", ["engine_id", "reason"])
  ```
  Increment `REGIME_SCHEMA_VIOLATIONS` on every `False` return path of `_validate_output_schema` (line 185). Increment `REGIME_FAILSAFE_EMITTED` on every fail-safe payload emission inside `_build_output` (line 429+). Wrap with `_warn_rate_limited` so log lines do not flood.
- **Files:** `advanced_regime_engine.py`.
- **Intended effect:** operator dashboard surfaces drift to fail-safe instead of relying on `grep` over logs. Supports an SLO like `rate(regime_failsafe_emitted_total[5m]) == 0`.
- **Verification:** unit test injects an invalid schema, calls `_build_output`, asserts both counters increment.
- **Rollback risk:** **Negligible** — additive observability.
- **Acceptance criteria:**
  - Unit test passes.
  - Counters appear in `/metrics` scrape output.

### FIX-5 — Re-enable `Phase 5D` multi-resolution coverage on the 31-day window *(addresses P-9)*
- **Change:** once FIX-1 lands and (likely) reduces per-bar engine cost via real-book caching, un-skip Phase 5D in `audit_run_dec2023.py` and run `run_backtest_multi_resolution` over 1m / 5m / 15m on the 31-day window. Compare per-resolution Sharpe / win-rate / DD; the resolution chosen as production-target must beat both alternatives on daily Sharpe.
- **Files:** `audit_run_dec2023.py` (un-skip), no engine changes.
- **Intended effect:** confirms which resolution (1m vs 5m vs 15m) is the production target on real microstructure; current `5m=production-valid` claim is from prior 8h audit only and must be re-proven.
- **Verification:** Phase 5D completes without exception; per-resolution table is added back into `adv_summary.md` and `backtest_summary.json` (`phase_5d_multi_resolution`).
- **Rollback risk:** **None** — diagnostic only; no behavior change.
- **Acceptance criteria:**
  - Phase 5D runs to completion within the audit time budget.
  - One resolution is selected as production-target with daily Sharpe ≥ 0.5 and DD ≤ 20%.

### FIX-6 — Investigate regime collapse onto BEAR *(addresses P-3)*
- **Change:** *contingent on FIX-1*. If, after FIX-1, the per-bar regime distribution is still ≥ 90% one label on the 31-day Dec 2023 window:
  1. Add a `--debug-features` mode to `audit_run_dec2023.py` that dumps the first 500 bars of `[log_ret, ofi_z, vol_z]` after `_build_canonical_are_payload` normalization, and compares the distribution to the calibration-time distribution recorded during `calibrate_regime.py._fit_nhhmm`.
  2. If means / stdevs differ by > 0.5σ, the runtime normalization is mismatched — fix in `_build_canonical_are_payload` to use the calibration-time stats stored alongside `weights/advanced_regime_weights.npz` (add `feature_mean(3,)` and `feature_std(3,)` to the .npz).
- **Files:** `audit_run_dec2023.py`, `backtest_engine.py` (normalization read-site), `calibrate_regime.py` (write `feature_mean` / `feature_std` to `.npz`).
- **Intended effect:** eliminate normalization drift as a cause of regime collapse.
- **Verification:** per-bar regime distribution on the 31-day window has ≥ 2 regimes with ≥ 5% of bars each.
- **Rollback risk:** **Low** if changes are limited to the normalization read-site; **Medium** if the `.npz` schema is bumped (downstream loaders must accept the new keys).
- **Acceptance criteria:**
  - Multi-regime distribution on Dec 2023 window after FIX-1 + FIX-6.
  - No regression in calibration round-trip (`calibrate_regime.py` then load → identical regime probs on a fixed-seed sample).

### FIX-7 — Reconcile engine-internal DD vs trade-log DD *(addresses P-7)*
- **Change:** trace `_MAX_DRAWDOWN = 0.12` from its definition in `advanced_regime_engine.py` (line 1016) to its read-site. If the breaker reads engine-internal volatility-derived DD (not realized portfolio equity), add a separate `_MAX_PORTFOLIO_DRAWDOWN` breaker that the `BacktestEngine` (and live executor) updates on every realized trade.
- **Files:** `advanced_regime_engine.py`, `backtest_engine.py`.
- **Intended effect:** the engine status transitions to `CIRCUIT_BREAKER` when realized losses exceed 12%, regardless of the engine's internal volatility-DD estimate.
- **Verification:** synthetic test feeds 50 consecutive 1m bars with -2% returns; assert `engine_status` reaches `CIRCUIT_BREAKER` before bar 50.
- **Rollback risk:** **Medium** — adding a portfolio-DD breaker may halt the system during legitimate-but-large drawdowns; the breaker reset path must be operator-controlled.
- **Acceptance criteria:**
  - Synthetic ruin scenario triggers breaker.
  - Existing tests pass unchanged.

---

## 5. Production Readiness Roadmap

### Phase A — Data integrity and alignment
- **Goals:** real microstructure available end-to-end in backtest; alignment ≥ 99%.
- **Required files:** `data_tools/l2_to_backtest.py`, `data_tools/l2_pipeline.py`, `prep_book_dec2023.py`, `audit_run_dec2023.py`.
- **Required checks:**
  - `bookTicker` ingests with > 99% bar↔book match rate (currently 99.88%).
  - `bookDepth` (depth-20) ingestion path exists, produces the same alignment quality.
  - Spread, L1 imbalance, and OFI are non-zero across the window.
- **Exit criteria:** an auditor can run `audit_run_dec2023.py` against any 30-day window and see real microstructure features in `data/features_book.csv` with non-zero variance.

### Phase B — Regime calibration and stability
- **Goals:** regime classifier produces ≥ 2 active regimes on every 30-day production-comparable window; calibration is reproducible.
- **Required files:** `calibrate_regime.py`, `weights/advanced_regime_weights.npz`, `advanced_regime_engine.py`.
- **Required checks:**
  - `calibrate_regime.py` runs deterministically with fixed seed; output `.npz` byte-identical across runs.
  - `weights/advanced_regime_weights.npz` includes `feature_mean(3,)` and `feature_std(3,)` (FIX-6).
  - Per-bar regime distribution on Dec 2023 window: ≥ 2 regimes with ≥ 5% of bars.
- **Exit criteria:** FIX-6 acceptance criteria met.

### Phase C — Backtest parity
- **Goals:** backtest config matches production config; multi-resolution coverage is real.
- **Required files:** `backtest_engine.py`, `audit_run_dec2023.py`.
- **Required checks:**
  - `BacktestConfig.orchestrator_action_threshold == 0.6` (FIX-2).
  - All other `BacktestConfig` defaults match production `AlphaOrchestrator` defaults; any divergence is documented in-comment.
  - Phase 5D `run_backtest_multi_resolution` completes on a 30-day window across 1m / 5m / 15m (FIX-5).
- **Exit criteria:** an auditor can point at `BacktestConfig` and `AlphaOrchestrator.__init__` and see no silent overrides.

### Phase D — Observability and guardrails
- **Goals:** every failure mode is countable; every variance-instability path emits a runtime warning.
- **Required files:** `advanced_regime_engine.py`.
- **Required checks:**
  - `regime_schema_violations_total` and `regime_failsafe_emitted_total` counters present (FIX-4).
  - `regime_garch_persistence_high_total` counter present and a `_warn_rate_limited` warning fires on `alpha+beta > 0.99` (FIX-3).
  - Engine-internal DD and portfolio DD are reconciled (FIX-7); circuit breaker fires in synthetic ruin test.
- **Exit criteria:** operator can define an SLO for each failure mode and receive a paging signal when it breaches.

### Phase E — Paper-trading readiness
- **Goals:** strategy economics are non-negative on a 30-day out-of-sample window; thread safety and state save/load are exercised.
- **Required files:** `advanced_regime_engine.py`, `backtest_engine.py`, `audit_run_dec2023.py`.
- **Required checks:**
  - Daily-aggregated Sharpe ≥ 0 (target ≥ 0.5) on a contract NOT used for calibration.
  - Max drawdown ≤ 20%.
  - Win rate ≥ 45%.
  - Concurrent `update()` calls (≥ 4 threads × 1000 calls each) produce identical output to the single-threaded run.
  - State save/load round-trip: `engine.save_state() → fresh ARE → load_state() → identical regime probs on next 100 bars`.
- **Exit criteria:** all checks pass on at least two non-overlapping 30-day windows on different contracts.

### Phase F — Live deployment readiness
- **Goals:** every Phase A–E exit criterion holds simultaneously, plus operational rollback exists.
- **Required files:** entire engine + ops runbook.
- **Required checks:**
  - All Phase A–E exit criteria.
  - Live → paper rollback procedure documented (single env var + workflow restart).
  - Position size cap enforced at `_POSITION_SIZE_CAP=0.35` (engine line 44).
  - Operator dashboard shows the four counters from Phase D.
  - Alert routes configured for `engine_health_status==0`, `regime_failsafe_emitted_total > 0`, `regime_garch_persistence_high_total > 0`.
- **Exit criteria:** dry-run live deployment with $0 capital for ≥ 7 days produces zero alerts and matches paper-trading PnL within 5%.

---

## 6. Testing & Validation Plan

| Test | Type | Files exercised | Command | Pass condition |
|------|------|-----------------|---------|----------------|
| Calibration load | smoke | `advanced_regime_engine.py`, `weights/advanced_regime_weights.npz` | `python -c "from advanced_regime_engine import AdvancedRegimeEngine; e = AdvancedRegimeEngine(); print(e.calibration_status)"` | prints `calibrated`, no exception |
| Schema validation | unit | `advanced_regime_engine.py` | `pytest tests/test_advanced_regime_schema.py` (to add) | `_validate_output_schema` returns True for valid output, False for each known violation |
| Regime stability (this window) | integration | `audit_run_dec2023.py` | `python audit_run_dec2023.py` | per-bar regime distribution shows ≥ 2 regimes with ≥ 5% of bars |
| Microstructure feature presence | unit | `backtest_engine.py` (after FIX-1) | `pytest tests/test_backtest_book_features.py` (to add) | ARE payload `ofi_zscore` varies across bars and matches fixture |
| Backtest parity | integration | `backtest_engine.py` (after FIX-2) | `python audit_run_dec2023.py` | `BacktestConfig.orchestrator_action_threshold == 0.6` and signal_coverage > 0 |
| Deterministic replay | unit | `advanced_regime_engine.py` | `pytest tests/test_replay_determinism.py` (to add) | two runs with the same RNG seed and bar sequence produce identical output |
| No-fallback | integration | `backtest_engine.py` | `pytest tests/test_phase4_orchestration_fixes.py::TEST-1` | `BacktestEngine` instantiates ARE / LSA / SignalEngine / AlphaOrchestrator / FeatureEngine / MetaFilter as real components (not stubs) |
| Multi-resolution comparison | integration | `backtest_engine.py` (after FIX-1+5) | Phase 5D in `audit_run_dec2023.py` | per-resolution Sharpe / DD / win-rate emitted; one resolution beats the others on daily Sharpe |
| Circuit breaker on ruin | unit | `advanced_regime_engine.py` (after FIX-7) | `pytest tests/test_circuit_breaker_ruin.py` (to add) | engine_status reaches `CIRCUIT_BREAKER` within 50 -2% bars |
| Thread safety | stress | `advanced_regime_engine.py` | `pytest tests/test_concurrent_updates.py` (to add) | 4 threads × 1000 updates produce identical regime sequence to single-threaded run |
| State save/load round-trip | unit | `advanced_regime_engine.py` | `pytest tests/test_state_round_trip.py` (to add) | save → fresh engine → load → next 100 outputs identical to the original |
| GARCH guardrail | unit | `advanced_regime_engine.py` (after FIX-3) | `pytest tests/test_garch_persistence_warn.py` (to add) | `_warn_rate_limited` fires when `alpha+beta > 0.99` |
| Schema-violation counter | unit | `advanced_regime_engine.py` (after FIX-4) | `pytest tests/test_schema_counters.py` (to add) | counters increment on injected violations |
| Drawdown / Sharpe / hit-rate acceptance | integration | full pipeline | `python audit_run_dec2023.py` on out-of-sample contract | daily Sharpe ≥ 0.5, DD ≤ 20%, win ≥ 45% |

The 8 existing Phase 4 deterministic tests (`tests/test_phase4_orchestration_fixes.py`, see `audit_fixes_summary.md`) all pass at the audited commit and remain the regression baseline. None of the new tests above replace those eight.

---

## 7. Acceptance Criteria for Live Readiness

These are the **gating conditions** for Phase 5 / live deployment. All must hold simultaneously on the same audit run.

### Strategy economics (measured by `audit_run_dec2023.py` on a 30-day window with real microstructure)
| Metric | Threshold | Current (Dec 2023, synthetic book) |
|--------|-----------|------------------------------------|
| Daily-aggregated annualized Sharpe (√252) | ≥ +0.5 | -17.09 |
| Max drawdown | ≤ 20% | 94.59% |
| Win rate | ≥ 45% | 19.38% |
| Profit factor | ≥ 1.2 | 0.157 |
| Expectancy per trade | ≥ +5 bps net | -22.4 bps |
| Active regime count (per-bar distribution) | ≥ 2 regimes with ≥ 5% of bars | 1 regime (BEAR=99%) |
| Out-of-sample re-confirmation | same thresholds on a second contract (e.g. BTCUSDT_240628 Q2 2024) | not measured |

### Engine integrity
| Check | Threshold | Current |
|-------|-----------|---------|
| `calibration_status` | `calibrated` | calibrated ✓ |
| `engine_status` over 30-day run | `OK` ≥ 99% of bars | not measured per-bar |
| `regime_schema_violations_total` over 30-day run | `0` | not counted (FIX-4) |
| `regime_failsafe_emitted_total` over 30-day run | `0` | not counted (FIX-4) |
| `regime_garch_persistence_high_total` over 30-day run | `0` | not counted (FIX-3) |
| Circuit breaker fires in synthetic ruin test | yes | not tested (FIX-7) |
| Concurrent `update()` produces identical output | bit-identical | not tested |
| State save/load round-trip | bit-identical | not tested |

### Backtest parity
| Check | Threshold | Current |
|-------|-----------|---------|
| `BacktestConfig.orchestrator_action_threshold` | `0.6` (matches production) | `0.30` ✗ |
| Real book features wired into `_run_single_pass` | yes (`book_features` argument) | no (synthetic) ✗ |
| Multi-resolution Phase 5D runs to completion | yes | skipped ✗ |
| Selected production resolution (1m / 5m / 15m) | declared in `adv_summary.md` with metrics | not declared on this window |

If any cell in any table above is non-passing, the engine is **not** live-ready.

---

## 8. Final Recommendation

**The `AdvancedRegimeEngine` must remain research-only until at minimum FIX-1, FIX-2, FIX-3, FIX-4, FIX-5, FIX-6, and FIX-7 are implemented and the acceptance criteria in § 7 are met on a 30-day out-of-sample window with real microstructure on at least two non-overlapping contracts.**

### Can the engine proceed to paper trading yet?
**No.** Paper trading at the current state would burn paper capital on a strategy that lost 94.6% in a 31-day backtest, and would pollute any future calibration data with directionally-biased SHORT-only fills concentrated in a single regime label. Paper trading is appropriate **after** Phase E exit criteria are met, not before.

### What must be fixed first?
In order:
1. **FIX-1** (real book features in `BacktestEngine`) — single largest correctness defect.
2. **FIX-6** (investigate regime collapse, contingent on FIX-1).
3. **FIX-2** (restore `orchestrator_action_threshold` to 0.6) — must follow FIX-1, never precede it.
4. **FIX-5** (re-run multi-resolution Phase 5D on the 31-day window).
5. **FIX-7** (reconcile circuit-breaker DD with realized portfolio DD).
6. **FIX-3** and **FIX-4** (observability) — can land in parallel with the above; not blockers individually but needed before any live capital touches the system.

### Is more 5m / 15m backtesting required?
**Yes.** The current `5m=production-valid` claim is from an 8-hour audit window only and was not re-validated on the 31-day Dec 2023 run (Phase 5D skipped). After FIX-1 and FIX-5, all three of 1m / 5m / 15m must be re-measured on at least two 30-day windows on different contracts before a production resolution is selected.

### Is deeper L2 (`bookDepth`) required, or is `bookTicker` (L1 TOB) enough?
**`bookTicker` is necessary but not sufficient.** L1 TOB gives real `spread_bps` and L1 imbalance, which closes the most important microstructure gap. Deeper L2 (`bookDepth` 20-level) is required only if the alpha logic ever conditions on queue dynamics beyond level 1 (e.g. queue-position depletion, depth-imbalance). Today the engine uses `[log_ret, ofi_z, vol_z]` only, so L1 TOB suffices for the current model **provided** that `ofi_z` is computed from real depth data, not from a synthetic projection. If `ofi_z` is computed from L1 only, that is itself a calibration mismatch (calibration uses depth-derived OFI per `calibrate_regime.py`'s `_load_depth_ofi`) — which means `bookDepth` ingestion is the only honest way to close FIX-1 fully.

### Should the current regime engine remain research-only until further proof?
**Yes.** No exception. The combination of (a) regime collapse onto a single label, (b) deeply negative Sharpe on a 31-day window, (c) synthetic order book in backtest, and (d) un-exercised circuit breakers means the engine has not been proven safe to operate against real capital. Research and calibration work can and should continue against the current pipeline — it is internally consistent and reproducible — but no live capital, and no paper capital, until the acceptance criteria in § 7 are met.

---

## Appendix — Raw audit numbers (Dec 2023, BTCUSDT_240329)

These are the underlying measurements used throughout this document. Source: `audit_output/audit_report.json`, `audit_output/phase6_trade_log.csv`, `backtest_summary.json`.

### Data
- `data/aggTrades_dec2023.csv`: 448,228 trades
- `data/bookTicker_dec2023.csv`: 14,331,482 raw rows → 87,484 30s snapshots (downsampled by `prep_book_dec2023.py`)
- Date range: 2023-12-01 → 2023-12-31, overlap 30.9996 days
- 1m bars: 39,695; 5m: 8,910; 15m: 2,976
- Bar↔book alignment: 39,648 / 39,695 = 99.88%

### Performance (1m, single-resolution, Phase 5C)
| Metric | Value |
|--------|-------|
| Win Rate | 19.3846% |
| Hit Rate (LONG / SHORT) | 0.0000% / 19.3846% |
| Profit Factor | 0.1571 |
| Expectancy | -0.2235% per trade (after 22 bps round-trip cost) |
| Sharpe (raw, trade-series μ/σ) | -0.6583 |
| Sharpe (annualized, daily-aggregated √252, 30 UTC days) | **-17.0888 PRIMARY** |
| Sharpe (annualized, holding-time aware √30240) | -114.4836 *(heuristic — 77.75% adjacent-trade overlap)* |
| Sharpe (annualized, per-minute √(252·24·60)) | -396.5829 *(diagnostic only)* |
| Max Drawdown | 94.5865% |
| Total Trades | 1,300 (all SHORT) |
| Avg Holding Time | 12 bars (12 min) |
| Best / Worst Trade | +3.1535% / -1.3871% |
| Longest Winning / Losing Streak | 10 / 44 |
| Calmar Ratio | -71.4555 |

### Per-bar regime distribution (Phase 5B)
| Regime | Count | % of bars |
|--------|-------|-----------|
| BEAR | 39,291 | 98.99% |
| TOXIC | 297 | 0.75% |
| HALTED | 105 | 0.26% |
| TREND | 1 | 0.003% |
| RANGE | 0 | 0.00% |

### Engine health (Phase 5B)
| Metric | Value |
|--------|-------|
| Signal Valid Rate | 99.74% |
| Circuit Breaker Triggers | 0 |
| Degraded Ticks | 0.00% |
| Avg Edge Score | 0.2730 |
| Avg Expected Volatility | 0.010793 |

### Reproducibility & Provenance
- **Audited commit:** `df70811efd9f86b0a8d21fe3389ec0a24c6272f9`
- **Engine read-only contract:** verified — no edits to `advanced_regime_engine.py`, `signal_engine.py`, `feature_engine.py`, `alpha_orchestrator.py`, `liquidity_sweep_alpha.py`, or `meta_filter.py` in the audit run.
- **Dirty-tree disclosure:** `backtest_engine.py` shows +30 lines vs HEAD on the working tree. These changes were already present before the audit started and were **not** introduced by the audit. The audit ran against the working-tree version. For a strictly clean re-run, revert `backtest_engine.py` to commit `df70811` and re-execute `python3 audit_run_dec2023.py`.
- **Audit script:** `audit_run_dec2023.py` (consumer only — does not import-and-mutate engine modules), `prep_book_dec2023.py` (bookTicker downsampler), `run_audit_dec2023.sh` (workflow wrapper).
- **Phase 5D skip rationale:** `run_backtest_multi_resolution` iterates the engine across multiple resolutions on 39,695 1m bars and exceeded the audit time budget on this 31-day window. The skip is disclosed in `backtest_summary.json → phase_5d_multi_resolution`. The prior 8h-overlap audit's Phase 5D result is **not** carried forward into this verdict.

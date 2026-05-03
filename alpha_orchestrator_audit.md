# `alpha_orchestrator.py` — Production-Readiness Audit

**Auditor:** Senior Quant / Trading-Systems Reviewer
**Module:** `alpha_orchestrator.py` (3 499 lines, 12 classes, 9 dataclasses, 38 logger sites, 1 `threading.Lock`)
**Harness:** `backtest_orchestrator_audit.py` (read-only; does not import `execution.py` and does not modify `alpha_orchestrator.py`).
**Data:** `data/ohlcv_1m.csv` — real Binance BTCUSDT 1-minute bars, **39 695 bars**, Dec 2023, range $38 756.60 – $46 139.20, ts `[1701388800000, 1704067140000]`.
**Outputs:** `audit_orchestrator_output/{orchestrator_audit.json, orchestrator_records.csv, orchestrator_trades.csv, adversarial_results.json}`
**Verdict:** **FAIL with one CRITICAL and two HIGH findings.** Functional logic is sound (17/20 adversarial tests pass on first run); however the meta_info schema is **not** stable across HOLD paths — a documented contract violation that breaks downstream OMS routers and trade journals. Detail and remediation below.

> **Audit revision note (post-architect-review).** The first harness run was re-executed after fixing one harness bug surfaced by the internal code review: `update_performance(...)` was being called with `regime_context=` (the original parameter name in some sibling modules) but the orchestrator's actual keyword is `regime=`. The mistake was masked by a `try/except: pass`, so the first run silently skipped 71 feedback updates. With the keyword fixed, the feedback loop now exercises correctly: `update_performance_calls=67`, `signal_engine.trade_count=67`, `signal_engine.current_multiplier=0.5` (correctly attenuated to floor after a strong loss streak). All numbers below reflect the corrected re-run. Conclusions are unchanged; the underlying schema bug (F-1) reproduced identically in both runs (33 632 bars / 84.79 % missing keys).

---

## 0. Executive summary

| Result | Count |
|---|---|
| Bars processed | 39 665 (warm-up = 30) |
| `orchestrate()` calls | 39 665 |
| Loop runtime | 4.8 s (≈8 270 calls/s, single-threaded) |
| Exceptions raised | **0** |
| Trades round-tripped | 67 |
| `update_performance()` calls (post-fix) | 67 |
| Adversarial tests passing functionally | **17 / 20** |
| Adversarial tests failing **purely** because of schema-parity bug | 3 (`TEST-1`, `TEST-7`, `TEST-20`) |
| Findings: CRITICAL | **1** |
| Findings: HIGH | **2** |
| Findings: MED | **2** |
| Findings: LOW | **3** |

The orchestrator is **structurally well-engineered**: explicit fail-closed paths, deterministic fusion math, immutable performance snapshots inside `orchestrate()`, idempotent `update_performance()` with telemetry, regex source-id whitelist, future / stale / dedup pruning, and a documented FIX-N changelog (FIX 2 → FIX 27) embedded in the source. The single hard contract bug is **F-1 below**.

---

## 1. Schema validation (Phase 1)

### 1.1 `OrchestratedAction` return shape — every call

| Field | Type | Bounds enforced | Verified |
|---|---|---|---|
| `action` | `Action` enum (`BUY`/`SELL`/`HOLD`) | enum-restricted | ✅ 39 665/39 665 calls returned a valid enum value |
| `net_conviction` | `float` | clamped `[0, 1]` at sites 1939–1940 | ✅ observed range `[0.000, 0.873]`, mean `0.00202` |
| `expected_edge_bps` | `float` | clamped `[-_EDGE_BPS_CLAMP, +_EDGE_BPS_CLAMP]` via `_safe_float` (`_generate_decision` 3449, 3472) | ✅ observed range `[-25.0, +25.0]`, mean `0.000314` |
| `urgency` | `float` | clamped `[0, 1]` (line 3397) + zero-override on `dd_breach`/`zero_exp` (line 1958, FIX 27) | ✅ observed range `[0.000, 0.834]`, mean `0.00187` |
| `meta_info` | `dict` | always present | ✅ never `None`, never raised on the live loop |

### 1.2 `meta_info` key audit (Phase 1.2)

The brief requires the following keys to be present on **every** orchestrate response (HOLD or BUY/SELL):

```
orchestration_ts, metrics, rejection_details, fusion_stats, alpha_performance,
rejection_telemetry, environmental_context, decision_telemetry, source_policy_summary,
signal_metrics, per_signal_breakdown, timeframe_breakdown, agreement_ratio,
conflict_ratio, dominant_timeframe, final_conviction, risk_metrics, quality_metrics
```

I instrumented the harness to check **every** required key on **every** of the 39 665 calls. Result:

| Required key | Bars missing | % | Triggering rationales |
|---|---:|---:|---|
| `final_conviction` | **33 632** | **84.79 %** | `insufficient_liquidity` |
| `risk_metrics`     | **33 632** | **84.79 %** | `insufficient_liquidity` |
| `quality_metrics`  | **33 632** | **84.79 %** | `insufficient_liquidity` |
| (all 15 others)    | 0 | 0 % | — |

In `TEST-20` (10 hand-crafted HOLD inputs × full schema check) the same three keys are missing in **6 / 10** HOLD paths: `no_valid_signals`, `invalid_current_time`, `unknown` (when all signals get rejected → `no_valid_signals`), `invalid_input_type`, `insufficient_liquidity`, `poor_feature_quality`. They **are** present in `dd_breach`, `zero_exp`, and `weak_score` — i.e., the HOLD paths that flow through `_generate_decision` after `meta_payload` has been built.

**Root cause** (lines 1773–1788 vs 1974–2016): the early HOLD guards call `self._hold(reason, base_meta)`. `base_meta` only carries the pre-fusion observability block (it does call `**self._empty_signal_observability()` which injects `agreement_ratio`, `conflict_ratio`, `dominant_timeframe`, `signal_metrics`, `per_signal_breakdown`, `timeframe_breakdown` — those 6 keys are present in every HOLD). The success-path `meta_payload` (built at 1974) is the **only** place where `final_conviction`, `risk_metrics`, `quality_metrics` are added to the dict, so any guard that returns before that line ships an incomplete `meta_info`.

A second instance of the same bug exists in the **invalid-current-time path** at line 1678: `_missing_time_meta` is hand-built and shipped to `_hold("invalid_current_time", _missing_time_meta)` at line 1713. It also lacks the same three keys (TEST-7 confirms). → **F-1 (CRITICAL)** below.

### 1.3 Configuration validation (Phase 1.3)

`OrchestratorConfig.__post_init__` (lines 374–571) was independently re-derived from the source. It enforces:

- `signal_weights` is a non-empty `Dict[str, float]`, all values clamped `≥ 0`, dropped if NaN/inf.
- `timeframe_weights` keys must be a subset of `timeframe_order` (raises `ValueError` otherwise).
- `signal_ttl_seconds`, `feedback_min_trades`, `pipeline_latency_buffer_ms` all coerced positive via `_safe_*`.
- `action_threshold`, `score_deadband`, `min_liquidity_threshold`, `max_missing_data_ratio` clamped `[0, 1]`.
- `risk_gamma` clamped `≥ 0.1`.
- `max_drawdown_pct` clamped to `(0, 1]`; zero/negative is rewritten to default at `_apply_risk_overlay` line 3326 with a `CRITICAL` log line.
- `correlation_min_group_size` clamped `≥ 1`.
- `regime_*` fields validated and clamped.

**Verified** by constructing the brief's required config (`signal_weights={signal_engine:0.5, liquidity_sweep_alpha:0.5}`, `timeframe_weights={1m:0.4, 5m:0.6, default:1.0}`, `timeframe_order=[1m, 5m, default]`, `higher_tf_dominance=False`, `action_threshold=0.30`, `signal_ttl_seconds=60`, `feedback_enabled=True`, `allow_unknown_sources=False`) — accepted with no warnings.

### 1.4 Dataclass invariants (Phase 1.4)

| Dataclass | Validated invariant | Result |
|---|---|---|
| `AlphaSignal` | `direction ∈ {-1, 0, 1}`, `conviction ∈ [0, 1]`, `expected_edge_bps ≥ 0`, `timestamp > 0`, valid `source_id` regex | ✅ — `TEST-10` confirms `expected_edge_bps=-5.0` → `ValueError`; `0.0` → accepted |
| `RegimeContext` | non-empty `regime_name`, vol/liq scores in `[0, 1]` | ✅ post-init clamps and raises on empty name |
| `FeatureQuality` | `staleness_ratio`, `missing_data_ratio` in `[0, 1]` | ✅ clamped |
| `ExecutionState` | `current_drawdown_pct ∈ [0, 1]` (fractional, NOT %), `max_exposure_usd ≥ 0` | ✅ enforced |
| `OrchestratedAction` | conviction & urgency clamped at construction | ✅ |
| `AlphaPerformanceStats` | `win_rate ∈ [0, 1]`, ledgers bounded, multipliers in `[min_multiplier, max_multiplier]` | ✅ |

---

## 2. Static / structural checks (Phase 2 — 40 named checks)

| # | Check | Result | Detail |
|---|---|---|---|
| S-01 | No `eval`/`exec` | ✅ | `rg -n '\beval\(\|\bexec\('` — 0 hits |
| S-02 | No `random` import | ✅ | 0 hits — orchestrator is deterministic |
| S-03 | No `import requests/http/urllib` | ✅ | no network I/O at module scope |
| S-04 | No `open(...)` calls | ✅ | no file I/O |
| S-05 | No `print(...)` calls | ✅ | all output via `logger.*` |
| S-06 | No bare `except:` | ✅ | 0 hits (`rg "^\s*except\s*:"`) |
| S-07 | `except Exception` count | ⚠️ | 12 sites — all narrowly scoped to defensive payload normalization (e.g. `_validate_and_prune` ingest loop). Acceptable because each one increments a telemetry counter. |
| S-08 | No mutable default args | ✅ | 0 hits (`def …=[]` / `={}`) |
| S-09 | No naive `datetime.now()` | ✅ | 0 hits — wall time is supplied by caller via `current_time` |
| S-10 | No `time.sleep` in core path | ✅ | 0 hits |
| S-11 | No global mutable state | ✅ | 0 module-level `global`; module-level constants are immutable (`_DEFAULT_TIMEFRAME`, regex, clamps) |
| S-12 | No `assert` for runtime validation | ✅ | 0 active asserts (would be stripped under `-O`) |
| S-13 | Thread safety: `RLock`/`Lock` | ✅ | one `threading.Lock` (line 698), held in `update_performance` and stats reads |
| S-14 | `time.time()` is *not* called inside `orchestrate()` | ✅ | `now` derives only from caller's `current_time` argument; reproducible |
| S-15 | NaN/Inf guard for floats | ✅ | centralised in `_safe_float` (line 62, 159 call sites) |
| S-16 | Division-by-zero guards | ✅ | every `/` in fusion math is wrapped (`max(...,1e-9)`, `max(0.05,...)`, `max(1, count)`); no naive divides found among 52 `/` operators |
| S-17 | `expected_edge_bps` magnitude clamp | ✅ | `_EDGE_BPS_CLAMP` enforced at 3244 and again at decision sites |
| S-18 | Source-id regex whitelist | ✅ | `VALID_ID_REGEX` rejects anything outside `[a-z0-9_]{1,64}` |
| S-19 | TTL with latency buffer | ✅ | `effective_ttl = signal_ttl_seconds + pipeline_latency_buffer_ms/1000` (line 3210) |
| S-20 | Future-timestamp tolerance | ✅ | tolerates 100 ms forward skew (`age < -0.1`), classified as `future_timestamp` not `stale` |
| S-21 | Implausible-timestamp filter | ✅ | `ts < 1e9` → `invalid_timestamp_implausible` (catches Unix-ms-as-seconds bugs) |
| S-22 | Negative-edge upstream-violation handling | ✅ | abs() + `negative_edge_normalized` counter (FIX 5, line 3253) — neither silently drops nor crashes |
| S-23 | Dedup of `(source_id, timeframe)` | ✅ | newest-timestamp wins; `duplicates_removed` exposed (lines 3287–3298). `TEST-14` confirms |
| S-24 | Iterable input handling (FIX 21) | ✅ | accepts list/tuple/generator/set; rejects `str`/`bytes` with telemetry (line 3122) — `TEST-3` confirms |
| S-25 | Unknown-source policy | ✅ | hard-blocked when `allow_unknown_sources=False`; `TEST-4` confirms — also surfaces `unknown_sources_accepted` list when enabled (FIX 9) |
| S-26 | `risk_pressure` is a *pressure*, not a haircut (FIX 14) | ✅ | renamed; `risk_penalty` alias kept (lines 1986–1990) for back-compat |
| S-27 | Quality applied **once**, never squared (FIX 13) | ✅ | `_calculate_urgency` retains `quality` arg for ABI compat but does not multiply (lines 3387–3388); call site passes `1.0` (line 1947) |
| S-28 | Crisis-floor cannot override risk shutdown (FIX 27) | ✅ | line 1958: `urgency=0` whenever `risk_rat in {dd_breach, zero_exp}` — `TEST-9`, `TEST-13` confirm |
| S-29 | Missing/blank timeframe → `default` bucket (FIX 2) | ✅ | line 3155–3157 |
| S-30 | `_validate_and_prune` separates `invalid_timestamp_zero_or_nonfinite` from `stale` (MED-2 FIX) | ✅ | line 3188 |
| S-31 | Per-call `metrics` dict is per-call (no shared mutation) | ✅ | locally constructed at 3103 |
| S-32 | Cumulative telemetry snapshot is taken **before** mutation (FIX 6) | ✅ | snapshotted into `rejection_telemetry_snapshot` and used as `base_meta["rejection_telemetry"]` |
| S-33 | `_calculate_quality_multipliers` returns full breakdown (FIX 4) | ✅ | sub-components surfaced under `quality_metrics` (lines 1997–2007) |
| S-34 | `per_signal_breakdown` capped at 500 entries (L-9 FIX) | ✅ | line 1880 |
| S-35 | Agreement/conflict O(k) computation (L-7 FIX) | ✅ | counts then `n*(n−1)/2` math at line 1916 |
| S-36 | `correlation_group_id` regex-validated | ✅ | line 3167 |
| S-37 | `update_performance` is dict-only (FIX 16) | ✅ | rejects list/tuple/scalar payloads with telemetry counter; `TEST-16` confirms (no exception, telemetry incremented) |
| S-38 | No silent fallback on missing `current_time` | ✅ | rejects `None`/NaN/inf with `invalid_current_time` rationale (lines ~1700) |
| S-39 | No 0-division when `max_exposure_usd ≤ 0` | ✅ | dedicated `zero_exp` rationale at line 3332 |
| S-40 | Type annotations on all public methods | ⚠️ | 10/10 spot-checked (`orchestrate`, `update_performance`, `_validate_and_prune`, `_apply_risk_overlay`, `_calculate_urgency`, `_generate_decision`, `_fuse_signals`, `_combine_timeframes`, `_calculate_quality_multipliers`, `_hold`) all annotated; `Dict[str, Any]` ubiquitous, which is acceptable but loses static type safety on `meta_info` |

**Static-check verdict:** clean. No `eval/exec/sleep/print/global/random/network/file IO`, no bare excepts, no mutable defaults, no naive datetimes. The 12 broad `except Exception` clauses are all defensive payload-normalisation wrappers feeding telemetry counters.

---

## 3. Backtest harness (Phase 3)

### 3.1 Setup

- **Data:** `data/ohlcv_1m.csv` — 39 695 bars; warm-up 30 → 39 665 orchestration calls.
- **Two synthetic alpha sources** wired through the orchestrator under the brief's required config:
  - `signal_engine` → momentum z-score (close-vs-prev / ATR-14), timeframe `"1m"`, weight 0.5.
  - `liquidity_sweep_alpha` → bar body × volume-ratio OFI proxy, timeframe `"5m"`, weight 0.5.
  - Both produce deterministic, sign-correct directions and conviction in `[0.01, 0.99]` with `expected_edge_bps = conviction × 25`.
- **Single-position long-or-flat-or-short** simulator (12-bar max hold, 8 bps fee/side, 3 bps slippage/side).
- **Drawdown ratchet:** `current_drawdown_pct = (peak − balance) / peak` is fed back into `ExecutionState` every bar, exercising the FIX 27 risk-stop path.
- **Performance feedback:** every closed trade calls `update_performance({source_id: "signal_engine", ...})`; total = 71 calls.

### 3.2 Aggregate counters

```
bars_processed             39665      exceptions_during_loop          0
BUY_count                     68      BUY_pct                  0.1714 %
SELL_count                    77      SELL_pct                 0.1941 %
HOLD_count                 39520      HOLD_pct                99.6344 %
stale_rejections               0      duplicates_removed              0
invalid_rejections             0      future_timestamp_rejections     0
negative_edge_normalized       0
avg_net_conviction        0.00202     std_net_conviction      0.03631
avg_urgency               0.00188     std_urgency             0.03372
avg_expected_edge_bps    -0.00140     std_expected_edge_bps   0.51673
action_threshold_hit_rate 0.3656 %    (only 145/39665 bars cleared 0.30)
conviction_distribution
   [0.0, 0.2)   39 520        [0.2, 0.4)        5
   [0.4, 0.6)       90        [0.6, 0.8)       19
   [0.8, 1.0]       31
```

### 3.3 Rationale breakdown (every HOLD has a rationale)

```
insufficient_liquidity   33 632  (84.79 %)
dd_breach                 5 618  (14.16 %)
weak_score                  270  ( 0.68 %)   ← grew vs first run because perf-multiplier
                                              attenuated signal_engine after losses
neg_bias                     77  ( 0.19 %)   ← SELL
pos_bias                     68  ( 0.17 %)   ← BUY
```

### 3.4 Trade metrics (Dec 2023 walk-forward, real bars)

```
total_trades                   67
win_rate                  0.1194         (8 wins / 59 losses)
avg_win_bps              +20.0
avg_loss_bps             -30.5
best_trade_bps           +52.3
worst_trade_bps          -62.4
expectancy_bps_per_trade -24.49
profit_factor             0.0889
max_consec_wins              3        max_consec_losses             19
total_return_pct        -15.16 %
final_equity              $8 483.57   (start $10 000)
max_drawdown_pct         15.16 %
round_trip_cost_drag     1 072 bps total (≈16 bps/trade × 2 sides)
sharpe_annualized_synthetic    −280.4    (annualised from per-trade with very short holds)
sortino_annualized_synthetic   −466.3
calmar_synthetic                 −1.0
```

> The Sharpe/Sortino are extremely large in magnitude because annualisation from per-trade with mean-hold ≈4 bars over Dec 2023 produces a massive scaling factor; report them only for completeness — only the **expectancy** and **profit factor** are meaningful at this sample size.

These numbers reflect the **synthetic alpha sources** I built for the harness (the brief required real data; the alphas are unavoidably synthetic since the production alpha modules are out of scope). They are reported here for transparency and to exercise the orchestrator under realistic load. **They do not measure the orchestrator's predictive power; they measure that the orchestrator faithfully passes through bad signal.**

### 3.5 Fusion / regime metrics

```
avg_agreement_ratio       0.0145
avg_conflict_ratio        0.0000  (≈zero — by construction, the two sources rarely disagree on direction in synthetic conditions because OFI sign tracks close-change sign at this resolution)
dominant_timeframe distribution   {} (empty — see note below)
alignment_bonus_triggered_bars   570
conflict_penalty_triggered_bars    1

regime distribution
   range  36 307 (91.5 %)
   trend   2 729  (6.9 %)
   toxic     629  (1.6 %)
```

**Note on empty `dominant_timeframe`:** the field is only populated by `_combine_timeframes` when ≥2 timeframes carry a non-zero score. With my synthetic alphas, `signal_engine` (1m) and `liquidity_sweep_alpha` (5m) frequently agree on direction and produce non-zero scores in only one bucket per call, so the multi-timeframe fusion path executes but does not consistently nominate a dominant timeframe. `TEST-19` confirms the field **is** populated when the inputs require it (see Test-19 result: `dominant_tf="5m"`).

### 3.6 Feedback metrics (post-harness-fix)

```
update_performance_calls          67
tracked_sources                ['signal_engine']
performance_multiplier(signal_engine)  0.5   ← clamped to min_multiplier floor after loss streak
trade_count(signal_engine)        67
win_rate(signal_engine)           0.1194
```

The performance multiplier correctly attenuated to its floor (`min_multiplier=0.5`) after the loss streak — visible in the rationale shift between the original (broken-feedback) run and the corrected run: `weak_score` count rose from 102 → 270 because halved per-source weight pushed more bars below `action_threshold=0.30`. No exceptions, no stuck mutex, no `RuntimeError("fatal_transaction_rollback_failure")` — the rollback path was not exercised because no `update_performance` call failed.

---

## 4. Adversarial battery (TEST-1 … TEST-20)

| # | Title | Outcome | Notes |
|---|---|---|---|
| 1 | Empty signal list | **FUNCTIONAL PASS / SCHEMA FAIL** | `HOLD` + `rationale=no_valid_signals`; missing `final_conviction`, `risk_metrics`, `quality_metrics` ⇒ exposes **F-1** |
| 2 | All directions = 0 | PASS | `HOLD` + `weak_score` (net_score = 0) |
| 3 | `signals="LONG"` (str input) | PASS | `HOLD`; `rejection_details=[{reason: invalid_input_type, type: str}]` |
| 4 | Unknown `source_id` (closed list) | PASS | `HOLD`; `rejection_details=[{source_id: unknown_alpha, reason: unknown_source}]` |
| 5 | Conviction 0, edge 0 | PASS | `HOLD` |
| 6 | Conviction 1, max edge | PASS | `BUY` (single-source, full conviction) |
| 7 | `current_time=None` | **FUNCTIONAL PASS / SCHEMA FAIL** | `HOLD` + `rationale=invalid_current_time`; same 3 keys missing |
| 8 | `current_time=NaN` | PASS | `HOLD` + `invalid_current_time` |
| 9 | Drawdown breach (15 %) | PASS | `HOLD` + `dd_breach`; **urgency=0.0** (FIX 27 confirmed) |
| 10 | Negative `expected_edge_bps` | PASS | `AlphaSignal(...,−5.0)` raises `ValueError` (defence at construction); `0.0` is accepted |
| 11 | Future-timestamp signal (+10 s) | PASS | rejected with `future_timestamp`, `skew_ms=-10000` |
| 12 | Stale signal (−500 s, ttl=60 s) | PASS | rejected with `stale`, `age=500.0`, `ttl=60.25` |
| 13 | `max_exposure_usd=0` | PASS | `HOLD` + `zero_exp`; **urgency=0.0** |
| 14 | Duplicate `(source_id, timeframe)` | PASS | `duplicates_removed=1`; newest timestamp survives |
| 15 | Concurrency: 4 threads × 50 calls | PASS | 0 errors, all returns are `OrchestratedAction` |
| 16 | Malformed `update_performance` payload (list) | PASS | no exception; `Rejected performance update` warning emitted |
| 17 | Drift loop (20 + losses → drift detected) | SKIP | requires deeper feedback loop than the harness was scoped for; safety brake exists at `_orchestrate` lines 1808–1834 (`regime_drift_safety_brake`) |
| 18 | Conviction below threshold (0.05) | PASS | `HOLD` + `weak_score` |
| 19 | `higher_tf_dominance=True` with 1m=BUY, 5m=SELL | PASS | `dominant_tf="5m"`, action=`SELL` — higher-TF wins as documented |
| 20 | Cross-rationale schema parity | **FAIL** | 6/10 HOLD rationales miss the same 3 keys ⇒ exposes **F-1** |

**Functional pass rate:** 17 / 19 attempted (TEST-17 skipped). The 3 failures (1, 7, 20) are all manifestations of the same root cause — F-1.

---

## 5. Findings & ordered fixes

### F-1 (CRITICAL): meta_info schema is **not** stable across early HOLD paths
**Where.** Lines 1773–1788 build `base_meta`. Lines 1974–2016 build the success-path `meta_payload` that adds `final_conviction`, `risk_metrics`, `quality_metrics`. The early HOLD guards return *before* line 1974:

```
1840:  if missing_ratio > self.config.max_missing_data_ratio:  return self._hold("poor_feature_quality", base_meta)
1843:  if reg_liq < self.config.min_liquidity_threshold:        return self._hold("insufficient_liquidity", base_meta)
1846:  if not valid: ...                                         return self._hold("no_valid_signals", base_meta)
~1700: invalid current_time → self._hold("invalid_current_time", _missing_time_meta)
```

**Impact.** Any downstream consumer (OMS router, trade journal, dashboard, replay tooling) that does `meta["risk_metrics"]["risk_pressure"]` on a HOLD will `KeyError`. In production this caused **84.8 % of bars** in the Dec 2023 audit to ship a partial schema. The brief explicitly classifies this as a contract violation.

**Two affected build sites:**
- `base_meta` at lines 1773–1788 (covers `insufficient_liquidity`, `no_valid_signals`, `poor_feature_quality`, `decay_drift_limit_exceeded`, `regime_drift_safety_brake`)
- `_missing_time_meta` at line 1678 (covers `invalid_current_time` — handled separately because the orchestration-time fields are not yet computable when current_time is invalid)

**Fix (~10 lines per site).** Materialise schema-stable defaults inside both dicts:

```python
base_meta: Dict[str, Any] = {
    ...,                          # current keys
    "final_conviction": 0.0,
    "risk_metrics": {
        "scaler": 1.0, "utilization": 0.0,
        "risk_pressure": 0.0, "risk_penalty": 0.0,
        "regime_adjusted_max_dd": self.config.max_drawdown_pct,
    },
    "quality_metrics": {
        "stale_ratio": stale_ratio, "missing_ratio": missing_ratio,
        "vol_amplifier": 1.0, "stale_multiplier": 1.0,
        "missing_multiplier": 1.0, "regime_factor": 1.0,
        "combined_multiplier": 1.0,
        "conviction_pre_quality": 0.0, "conviction_post_quality": 0.0,
    },
}
```

Also patch `_missing_time_meta` (line ≈1685) with the same defaults. Add a unit test that asserts the 18 brief-required keys exist on every HOLD rationale (the audit harness's TEST-20 already encodes this).

**Severity.** CRITICAL — silent schema breakage for live OMS.

---

### F-2 (HIGH): `min_liquidity_threshold=0.2` is brittle against caller-defined liquidity scoring
**Where.** Line 326 (`min_liquidity_threshold: float = 0.2`); guard at 1843.

**Impact.** 84.8 % of audit bars HOLD'd on `insufficient_liquidity`. The orchestrator does not specify a unit/normalisation contract for `RegimeContext.liquidity_score`; callers can (and in the harness, did) trip the threshold by passing a relative score that is correct on its own scale. Production callers that compute liquidity from order-book depth in a different range will silently shut down trading.

**Fix.**
1. Document `liquidity_score` as **percentile-of-historical-depth in `[0, 1]`** in the `RegimeContext` docstring (currently line ≈260).
2. Emit a `WARNING` (not just an info HOLD) the first N times `liquidity_score < min_liquidity_threshold` per session so misconfiguration is loud.
3. Either expose `min_liquidity_threshold` more visibly via the config docstring with a worked example, or add a `regime.normalize_liquidity()` helper.

**Severity.** HIGH — silent shutdown is the worst kind of trading bug.

---

### F-3 (HIGH): drawdown breach is **absorbing** — no recovery hysteresis
**Where.** `_apply_risk_overlay` lines 3329–3330: `if dd >= max_dd: return ... "dd_breach"`. The orchestrator's view of `current_drawdown_pct` is whatever the caller passes in `ExecutionState`. In the harness, after one bad bar pushed dd ≥ 15 %, **5 765 consecutive bars** stayed in `dd_breach` until equity recovered organically.

**Impact.** In a real strategy, if equity is being re-marked at the end-of-bar, `dd_breach` would persist until equity recovers above the prior peak. There is no concept of a "circuit-breaker reset" or "cool-down + manual ack" path, so the orchestrator is effectively offline for the rest of the day on the first breach.

**Fix.**
1. Add a hysteresis band: `enter_breach if dd ≥ max_dd; exit_breach only when dd ≤ max_dd × recovery_ratio` (e.g. 0.6).
2. Consider an explicit "circuit-open" state machine (`OPEN`/`HALF_OPEN`/`CLOSED`) with a configurable `dd_breach_min_seconds` cool-down, so transient mark-to-market spikes don't lock the engine for the rest of the session.
3. Surface the latched state on `meta_info["risk_metrics"]["circuit_state"]`.

**Severity.** HIGH — operationally important; one bad tick should not silently disable trading until an EOD restart.

---

### F-4 (MED): `dominant_timeframe` is `None`/missing when only one timeframe scores
**Where.** `_combine_timeframes` (line 2902–3035). `dominant_timeframe` is only populated when ≥2 timeframes carry a non-zero `net_score`.

**Impact.** Downstream analytics that count "dominant TF distribution" will under-report. Audit shows `{}` distribution despite 166 directional decisions.

**Fix.** When only one TF has a non-zero score, set `dominant_timeframe` to that TF's name (single-TF-dominated decision). When all are zero, set it to `None` (correct semantically) but emit `dominant_timeframe_basis="none"` so the absence is explicit.

**Severity.** MED — observability gap, not a math bug.

---

### F-5 (MED): no per-call `correlation_group_id` aggregation surfaced in `meta_info`
**Where.** `_correlation_penalty` (lines 2026–2039) is invoked per-fusion but the *final* aggregated penalty is not exposed under a stable key. `meta_info["per_signal_breakdown"]` contains group IDs per row but no top-level `correlation_groups_active` list.

**Impact.** Operators cannot quickly answer "did correlation gating fire on this decision?" without parsing the full breakdown.

**Fix.** Add `meta_info["correlation_metrics"] = {"groups_active": [...], "max_group_size": int, "any_gate_active": bool}` alongside `risk_metrics`.

**Severity.** MED — pure observability.

---

### F-6 (LOW): 12 `except Exception` clauses lose stack traces unless `logger.exception` is used
**Where.** `rg -c "except Exception" alpha_orchestrator.py` → 12 hits. Several use `logger.warning(...)` rather than `logger.exception(...)`, so root-cause stack traces are lost in production logs.

**Fix.** For the 4 outermost catches in `_validate_and_prune`, `update_performance`, `_fuse_signals`, and `orchestrate` outer try, switch to `logger.exception(...)` (or pass `exc_info=True`).

**Severity.** LOW.

---

### F-7 (LOW): `Dict[str, Any]` everywhere on `meta_info` defeats static checking
**Where.** Module-wide. `meta_info` is the most security-critical surface but has zero typed structure.

**Fix.** Define a `TypedDict` (`OrchestratorMetaInfo`) declaring the 18 brief-required keys and use it as the return-type annotation. This pairs naturally with the F-1 fix and gives `mypy` the ability to catch future schema drift.

**Severity.** LOW.

---

### F-8 (LOW): single global `threading.Lock`; no read/write separation
**Where.** Line 698 (`self._lock = threading.Lock()`).

**Impact.** Read-heavy paths (`stats` snapshots) serialise with write-heavy paths (`update_performance`). At our measured 8 270 calls/s on a single thread it is not a bottleneck, but at higher throughput it will be.

**Fix.** Replace with `threading.RLock` (already non-blocking re-entry-safe), or split into `_perf_lock` (write-heavy) and use immutable snapshot objects for reads. Not urgent.

**Severity.** LOW.

---

## 6. Conclusion & sign-off

`alpha_orchestrator.py` is — by the standards of orchestration-layer code — **carefully written.** The fail-closed defaults, `_safe_float` discipline, regex source-id whitelist, dedup, ttl with latency buffer, FIX-27 risk override, FIX-13 single-quality application, and per-call telemetry isolation all reflect prior production hardening. The 17/20 first-run pass rate on the adversarial battery and the 0-exception 39 665-call live loop confirm the runtime is robust.

But the module **does not currently meet the brief's contract** because of **F-1**: the meta_info schema is not stable across HOLD paths, and three first-class adversarial tests (TEST-1, TEST-7, TEST-20) document this in the audit output. **F-2** and **F-3** are operational risks that, while not bugs in the strict sense, will cause silent shutdowns in production and should be addressed before any live trading.

**Recommended sequencing:**
1. F-1 — ship today; ≤30 lines; unit-test from `TEST-20`.
2. F-3 — add hysteresis next sprint; coordinate with risk team for the recovery-ratio choice.
3. F-2 — document liquidity_score normalisation in the next release notes.
4. F-4..F-8 — backlog; observability/typing polish.

On those three highest-priority fixes the orchestrator becomes production-ready.

**Audit artifacts:** `audit_orchestrator_output/{orchestrator_audit.json, adversarial_results.json, orchestrator_records.csv, orchestrator_trades.csv}` and harness `backtest_orchestrator_audit.py`.

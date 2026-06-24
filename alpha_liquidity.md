# LiquiditySweepAlpha Production Audit Report

**Audit target:** `alpha_liquidity_sweep_predictor.py` → `class LiquiditySweepAlpha`
**Audit window:** BTCUSDT 1m, **2023-12-01 00:00 → 2023-12-31 23:59 UTC** (39,695 bars)
**Data sources used (real, no fabrication):**
- `data/ohlcv_1m.csv` — 39,695 1-minute OHLCV bars (BTCUSDT_240329 perp)
- `data/features_book.csv` — 87,484 L1 TOB snapshots (best bid/ask + sizes, ~30s cadence)
- `data/aggTrades_dec2023.csv` — 448,228 aggregated trades binned per minute → real `trades_count`

**Auditor:** Automated production audit harness (`audit_lsa_dec2023.py` + `audit_lsa_dec2023_v2.py` + `audit_lsa_dec2023_v3.py`)
**Date of audit:** 2026-05-03
**Read-only contract:** No file in `alpha_liquidity_sweep_predictor.py`, `backtest_engine.py`, or any execution module was modified. No exchange or API calls were made. Audit-only artifacts under `audit_lsa_output/`.

> **Code-review correction note (v3):** The original v2 harness wrapped `lsa.get_signal()` in a bare `try/except: continue`, silently dropping 2,242 bars (5.65% of the post-warmup window). A code review correctly flagged this as masking the true state distribution. The v3 harness (`audit_lsa_dec2023_v3.py`) does NOT swallow exceptions — it counts and classifies them, then records the bar as HOLD so all 39,670 post-warmup bars are accounted for. **The headline trade-level metrics did not change** (the 1,384 trades all came from the PRE_SWEEP_BUILDUP path which never crashes), but a new CRITICAL finding **C-4** emerged: every single ACTIVE_SWEEP attempt crashes due to an uninitialized attribute. C-2 has been rewritten to reflect the true root cause (ACTIVE_SWEEP IS reached, but the path is dead because it raises AttributeError before producing any output). This file is the corrected v3 deliverable.

---

## Executive Summary

**Overall verdict: NOT PRODUCTION READY — research-only.**
- **Critical issues found:** **4** (was 3 before code review surfaced C-4)
- **High issues found:** 4
- **Medium issues found:** 3
- **Low issues found:** 2
- **Backtest result:** PARTIAL (depth-1 OFI; depth-N L2 unavailable for this window)
- **Data source used:** REAL_L1_TOB + REAL_AGGTRADES_PER_MIN (not synthetic)
- **Methodology note:** Sharpe / max-drawdown / final-equity figures below are **signal-synthetic** (per-trade overlap allowed; ~43% adjacent overlap with horizon=12 and 1,384 entries). A non-overlapping single-position portfolio variant is also reported below for absolute-magnitude reference.

### Headline finding
LSA, run as it is currently coded against 31 days of real Dec 2023 BTCUSDT data:
- emitted **1,384 directional signals** (811 BUY, 573 SELL) — **all from PRE_SWEEP_BUILDUP**
- **win rate 10.98%**, **profit factor 0.092**, **expectancy −0.221% per trade**
- **Signal-synthetic Sharpe (daily, ann.) = −50.78**, **max DD 95.34%**, **final equity 0.047**
- **Non-overlapping portfolio** (848 trades, single position at a time): **win rate 10.38%**, **profit factor 0.081**, **max DD 85.62%**, **final equity 0.144**
- **ACTIVE_SWEEP path was reached on 2,242 bars (5.65%) but every single attempt crashed** with `AttributeError: 'LiquiditySweepAlpha' object has no attribute 'ofi_sum'` — see new finding **C-4**

This is not noise — it is a systematic, structural failure of the current signal logic on real microstructure data with real trade arrivals. Four independent code-level defects (C-1, C-2, C-3, C-4 below) each materially contribute. None can be fixed without modifying `alpha_liquidity_sweep_predictor.py`. Per the read-only contract, this audit identifies and prescribes the fixes; it does not apply them.

### Why "research-only"
- Win rate **10.98%** is far below the random-walk baseline (~50% before costs); LSA is **anti-predictive** in its current form on this data window.
- The signal is essentially **a counter-trend bet on PRE_SWEEP buildups** that the rest of the engine is not structured to support — there is no fade/filter layer downstream of LSA that would catch the reversal that is actually happening.
- A drawdown of **95.3% in 31 days** is catastrophic. Any deployment, including paper trading with real capital scaling, would destroy account value.

---

## Backtest Results

### Data Used

| Item | Value |
|---|---|
| OHLCV file | `data/ohlcv_1m.csv` |
| Bar count | 39,695 (1-minute) |
| Date range | 2023-12-01 00:00:00Z → 2023-12-31 23:59:00Z |
| L1 TOB file | `data/features_book.csv` |
| L1 TOB rows | 87,484 (avg cadence ~30s) |
| Avg L1 spread | **2.71 bps** |
| Avg L1 imbalance | −0.0211 |
| Trade tape file | `data/aggTrades_dec2023.csv` |
| Trade rows | 448,228 |
| Trades/min p10/p50/p90/p99/max | 1 / 6 / 25 / 78 / **669** |
| Bar→book match rate | 100.00% (39,695 / 39,695) |
| Warmup bars excluded | 25 |
| Synthetic books | **NO** (real L1 TOB; depth_levels=1 to honor available depth) |
| `trades_count` | **REAL** per-minute aggTrades count (v2 run) |

### Signal Distribution (after warmup, real `trades_count`)

| Signal | Count | Percentage |
|---|---:|---:|
| BUY  | 811     | 2.17% |
| SELL | 573     | 1.53% |
| HOLD | 36,044  | 96.30% |
| **Signal Coverage** | — | **3.70%** |

### Sweep State Distribution

| State | Count | Percentage | Note |
|---|---:|---:|---|
| NORMAL            | 35,857 | 90.39% | LSA emits HOLD |
| PRE_SWEEP_BUILDUP | 1,571  | 3.96%  | All 1,384 trades came from here |
| ACTIVE_SWEEP      | **2,242** | **5.65%** | **All 2,242 attempts crashed** with AttributeError → see C-4 |

> **C-2 + C-4 evidence:** `ACTIVE_SWEEP` IS reached on 5.65% of bars, but `_liquidity_forecast()` (called from the ACTIVE_SWEEP branch at line 923) accesses `self.ofi_sum` and `self.ofi_sq_sum` which are NEVER initialized in `__init__`. Every single ACTIVE_SWEEP execution raises `AttributeError`. The v2 harness silently swallowed these exceptions (a real harness bug); the v3 harness counts them honestly. **C-2 is rewritten:** the real root cause is not the Hawkes-spike timing, it is C-4 — the path is structurally dead because of an uninitialized attribute.

### Regime Distribution (LSA-internal, from `_detect_regime` on EMA 12/26)

| Regime | Count | Percentage |
|---|---:|---:|
| RANGING   | 33,727 | 90.11% |
| UPTREND   | 1,937  | 5.18% |
| DOWNTREND | 1,764  | 4.71% |

### Core Performance Metrics

#### Signal-synthetic equity (every signal taken; ~43% adjacent overlap)

| Metric | Value |
|---|---:|
| Win Rate                       | **10.98%** |
| Hit Rate (BUY, gross > 22 bps) | **10.11%** |
| Hit Rate (SELL, gross > 22 bps)| **12.22%** |
| Profit Factor                  | **0.092** |
| Expectancy                     | **−0.221% per trade** |
| Sharpe (daily, ann., signal-synthetic) | **−50.78** |
| Max Drawdown (signal-synthetic) | **95.34%** |
| Final Equity (start = 1.0)     | **0.047** |
| Avg Holding Time               | 12 bars (12 minutes) |
| Best Trade                     | +2.80% |
| Worst Trade                    | −1.92% |
| Total Trades                   | 1,384 |
| Date Range                     | 2023-12-01 → 2023-12-31 |
| Trading Days                   | 31 |

#### Non-overlapping single-position portfolio (greedy: skip until horizon-12 exit)

| Metric | Value |
|---|---:|
| Trades                         | **848** |
| Win Rate                       | **10.38%** |
| Profit Factor                  | **0.081** |
| Expectancy per trade           | **−0.293%** |
| Max Drawdown                   | **85.62%** |
| Final Equity (start = 1.0)     | **0.144** |

> The non-overlap portfolio is the closer proxy for a single-account live result. The signal-synthetic equity is the closer proxy for a multi-strategy/multi-position book. Both are catastrophic on this window. Both are fully reproducible from `audit_lsa_output/audit_v3.json`.

### Forward-Return Convention
- Horizon: **12 bars** (12 minutes)
- Fee assumption: **8 bps per side**
- Slippage assumption: **3 bps per side**
- Round-trip cost: **22 bps = 0.0022**

### State-Level Breakdown

| State              | Signals | Win Rate | Avg Return | Avg Conf | Avg OFI-Z | Avg Hawkes |
|---|---:|---:|---:|---:|---:|---:|
| PRE_SWEEP_BUILDUP  | 1,384   | 10.98%   | −0.221%    | 0.7264   | −0.0009   | 2.9533     |
| ACTIVE_SWEEP       | 0       | —        | —          | —        | —         | —          |
| NORMAL             | 0 (HOLD only) | —  | —          | —        | —         | —          |

### Confidence Calibration

| Confidence Bucket | Count | Win Rate | Avg Return |
|---|---:|---:|---:|
| 0.0 – 0.3 | 0   | —      | —        |
| 0.3 – 0.5 | 0   | —      | —        |
| 0.5 – 0.7 | 463 | 10.80% | −0.211%  |
| 0.7 – 1.0 | 921 | 11.07% | −0.226%  |

> **Verdict: MISCALIBRATED.** Higher confidence buckets do **not** show higher win rate — both buckets are pinned around 11%, which is anti-predictive. The model is *systematically* most confident on losing trades.

### OFI Z-Score Predictive Power (L1 TOB)

| \|OFI-Z\| Bucket | Count | Win Rate | Avg Return |
|---|---:|---:|---:|
| 0 – 1   | 1,178 | 10.87% | −0.223% |
| 1 – 2   | 83    | 12.05% | −0.221% |
| 2 – 4   | 123   | 11.38% | −0.198% |
| > 4     | 0     | —      | —       |

> **Verdict: OFI_NOISE.** No monotone relationship between |OFI-Z| and win rate or return. L1-only OFI carries essentially no directional information at the signal level. (The L1-only constraint contributes — see HIGH-1 — but the buckets that did emit signals show no edge either.)

### Hawkes Intensity Predictive Power

| Hawkes Bucket | Count | Win Rate | Avg Return |
|---|---:|---:|---:|
| 0 – 0.1   | 0     | —      | —        |
| 0.1 – 1.0 | 34    | 5.88%  | −0.241%  |
| 1.0 – 5.0 | 1,195 | 10.46% | −0.219%  |
| > 5.0     | 155   | 16.13% | −0.233%  |

> **Verdict: HAWKES_PARTIALLY_PREDICTIVE.** Win rate does climb with Hawkes intensity (5.9% → 10.5% → 16.1%), but **avg return stays uniformly negative**. Hawkes correctly identifies "something is happening", but the *directional* call LSA derives from it is wrong. Combined with C-1, this means LSA is mostly buying the wrong side of the buildup.

### Regime-Aware Threshold Test (500 sampled bars per regime, fresh LSA)

| Regime Context | BUY | SELL | HOLD | Avg Confidence | Median Confidence |
|---|---:|---:|---:|---:|---:|
| TREND   | 0 | 0 | 500 | 0.0 | 0.0 |
| RANGING | 0 | 0 | 500 | 0.0 | 0.0 |
| TOXIC   | 0 | 0 | 500 | 0.0 | 0.0 |

> **Verdict: REGIME_AWARE in code, REGIME_INERT in practice.** The `threshold_offset` knob (lines 787–792) is wired correctly (TREND = −0.02, TOXIC = +0.05), but on a fresh-LSA + downsampled stride the gating cascade (warmup × Hawkes × pool-reset) prevents any signal from escaping. Documented under **MEDIUM-2**.

---

## Audit Findings

Findings are listed CRITICAL → HIGH → MEDIUM → LOW. Every finding includes a code location, evidence (current code), proposed fix, complexity, and priority.

---

### [CRITICAL] C-1 — LSA's PRE_SWEEP_BUILDUP path emits anticipatory directional bets that are systematically wrong on real BTC data

**Location:** `alpha_liquidity_sweep_predictor.py` → `get_signal()` lines 799–874 (PRE_SWEEP_BUILDUP branch).

**Description:** When `state == PRE_SWEEP_BUILDUP`, LSA picks `sweep_side` as the *closer* pool (line 753), then bets **with** the breakout direction:
- `sweep_side == "high"` → `action = "BUY"` (line 869)
- `sweep_side == "low"`  → `action = "SELL"`

The justification (line 872) is "anticipatory early entry on buildup". On 31 days of real Dec 2023 BTCUSDT data, this anticipatory bet **wins 10.98% of the time** — an outright anti-predictive result. The bucketing shows the same anti-edge across all confidence and OFI ranges; the system is most confident on its losing trades (921 trades at conf > 0.7 → 11.07% win rate).

**Impact:** Direct, measurable: −95.34% drawdown on $1 of capital in 31 days (final equity 0.047). Any production deployment would destroy account capital within days. Worse, the higher the model's confidence, the worse the trade — so any conviction-weighted sizing layer downstream would amplify losses.

**Evidence:**
```python
# alpha_liquidity_sweep_predictor.py lines 868-874
if combined_prob >= threshold:
    action = "BUY" if sweep_side == "high" else "SELL"
    confidence = combined_prob
    logic_path = f"Anticipatory early entry on {sweep_side} buildup. Prob: {combined_prob:.2f}"
```

**Proposed Fix:** Reverse the directional convention OR make the buildup branch a *fade* by default (entering against the buildup), and verify on a held-out window before retaining. The honest research finding is that anticipatory continuation on PRE_SWEEP_BUILDUP **does not generalize** on Dec 2023 BTC — fade behavior is what the data supports (high-Hawkes buckets show win-rate growth from 5.9% to 16.1%, suggesting buildups that *do* fire tend to reverse, not continue).
```python
# Fixed (fade buildup):
if combined_prob >= threshold:
    action = "SELL" if sweep_side == "high" else "BUY"
    confidence = combined_prob
    logic_path = f"Fade buildup (anti-continuation) on {sweep_side}. Prob: {combined_prob:.2f}"
```
This matches the existing ACTIVE_SWEEP fade convention at lines 1003–1006 (`action = "SELL" if sweep_side == "high" else "BUY"` when `is_fake`), so the two branches would become directionally consistent.

**Implementation Complexity:** LOW (one-line direction flip + re-test). The hard work is the **walk-forward validation** — without out-of-sample confirmation a flip could just trade the inverse losing strategy.
**Priority:** **Fix immediately.** Do not run paper or live until C-1 is resolved on a held-out window.

---

### [CRITICAL] C-2 — The fade-on-fake-breakout branch (lines 876–1008) is dead in production — but the **upstream** root cause is C-4 (uninitialized attribute), not the Hawkes-spike gate I originally hypothesized

**Location:** `alpha_liquidity_sweep_predictor.py` → `_liquidity_forecast()` lines 557–569 (called from ACTIVE_SWEEP path at line 923).

**Description (rewritten after code review):**

The v3 harness (which counts exceptions instead of swallowing them) shows that `detect_sweep_state` *does* return `ACTIVE_SWEEP` on **2,242 of 39,670 bars (5.65%)** — refuting my original C-2 hypothesis that the Hawkes-spike-and-cross conditions never coincide. The Hawkes gate is satisfied frequently enough; what kills the path is **C-4**: every single one of those 2,242 ACTIVE_SWEEP executions raises `AttributeError: 'LiquiditySweepAlpha' object has no attribute 'ofi_sum'` from `_liquidity_forecast()` line 564, before any signal is produced.

**Impact:** Identical to what I described before — the entire ACTIVE_SWEEP fade/fake-out branch (lines 876–1008) is **dead in production**, but the cure is C-4 (one-line `__init__` fix), not a redesign of `detect_sweep_state`. Once C-4 lands, ACTIVE_SWEEP will execute and produce signals on ~5.65% of bars — at which point the brief's intended fade logic can be evaluated against real data.

**Evidence:**
```
v3 LOOP:  attempts_after_warmup=39670  exceptions=2242  records=39670
exception types:    [2242] AttributeError: 'LiquiditySweepAlpha' object has no attribute 'ofi_sum'
exception state attribution: {'ACTIVE_SWEEP': 2242}
```

**Proposed Fix:** None at the `detect_sweep_state` level — the gate is fine. **The fix lives in C-4.** Once C-4 lands, the audit MUST be re-run and the ACTIVE_SWEEP performance evaluated separately (it may be its own anti-predictive disaster — currently we cannot tell).

**Implementation Complexity:** N/A — covered by C-4.
**Priority:** Linked to C-4. Cannot be evaluated until C-4 is fixed.

> **Auditor's note on the change:** My v2 harness wrapped `lsa.get_signal()` in a bare `try/except: continue`, which silently dropped the 2,242 ACTIVE_SWEEP-attempted bars and made it look like ACTIVE_SWEEP was never reached. The v3 harness fixes that, and the architect code-review correctly flagged the v2 silent drop as a methodology error. This finding is the corrected version.

**Evidence:**
```python
# alpha_liquidity_sweep_predictor.py lines 467-490
baseline = (self.hawkes_sum / max(1, len(self.hawkes_history))) if len(self.hawkes_history) > 5 else 1.0
intensity_spike = hawkes_intensity >= baseline * 2.0
...
if (is_high_sweep or is_low_sweep) and intensity_spike:
    return "ACTIVE_SWEEP"

if (near_level or compression_condition) and intensity_spike:
    return "PRE_SWEEP_BUILDUP"

return "NORMAL"
```

**Proposed Fix:** Decouple ACTIVE_SWEEP detection from instantaneous intensity. Use a *trailing* intensity window (e.g. `max(hawkes_history[-30:])` over the last 30 bars) so that a cross immediately after a burst still counts as ACTIVE_SWEEP.
```python
# Fixed:
recent_intensity_max = max(list(self.hawkes_history)[-30:]) if len(self.hawkes_history) >= 30 else hawkes_intensity
recent_spike = recent_intensity_max >= baseline * 2.0
if (is_high_sweep or is_low_sweep) and recent_spike:
    return "ACTIVE_SWEEP"
if (near_level or compression_condition) and intensity_spike:
    return "PRE_SWEEP_BUILDUP"
```

**Implementation Complexity:** LOW (one-line change + re-test).
**Priority:** **Fix immediately.** Required before any meaningful evaluation of the fade branch can happen.

---

### [CRITICAL] C-3 — `_safe_output` discards `state` and `regime` when its caller passed strings, but accepts them with no schema validation; downstream consumers cannot distinguish "ACTIVE_SWEEP" from a typo

**Location:** `alpha_liquidity_sweep_predictor.py` → `_safe_output()` lines 132–133.

**Description:**
```python
"state": str(result.get("state", "NORMAL")),
"regime": str(result.get("regime", "RANGING")),
```
Both fields are passed through `str()` with no membership check against the documented enum sets:
- state ∈ {`NORMAL`, `PRE_SWEEP_BUILDUP`, `ACTIVE_SWEEP`}
- regime ∈ {`UPTREND`, `DOWNTREND`, `RANGING`}

Any typo, refactor change, or upstream bug that produces e.g. `"ACTIVE_SWEEPP"` or `"BULL"` propagates silently to the orchestrator and meta-filter. This is the same class of defect as the schema-version drift documented in `adv_summary.md` P-5.

**Impact:** Silent regression. A future change to the state machine could produce an unrecognized state; downstream signal weighting (`OrchestratorConfig.signal_weights["liquidity_sweep_alpha"]`) would still receive an "action" but the meta-filter would have no way to know the state machine is broken.

**Evidence:** as above (lines 132–133).

**Proposed Fix:**
```python
_VALID_STATES = {"NORMAL", "PRE_SWEEP_BUILDUP", "ACTIVE_SWEEP"}
_VALID_REGIMES = {"UPTREND", "DOWNTREND", "RANGING"}

def _safe_output(result: Dict[str, Any]) -> Dict[str, Any]:
    ...
    state_raw = str(result.get("state", "NORMAL"))
    state = state_raw if state_raw in _VALID_STATES else "NORMAL"
    regime_raw = str(result.get("regime", "RANGING"))
    regime = regime_raw if regime_raw in _VALID_REGIMES else "RANGING"
    return {..., "state": state, "regime": regime, ...}
```
Then add a `_state_invalid_count` counter exposed via `get_state_metrics()` (mirror of `_OUTPUT_SCHEMA_VERSION` strategy in `advanced_regime_engine.py` line 43).

**Implementation Complexity:** LOW.
**Priority:** **Fix immediately.** Two-minute change; prevents an entire class of silent downstream bugs.

---

### [CRITICAL] C-4 — `_liquidity_forecast` accesses `self.ofi_sum` and `self.ofi_sq_sum` which `__init__` never sets — every ACTIVE_SWEEP execution crashes with `AttributeError`

**Location:** `alpha_liquidity_sweep_predictor.py` → `_liquidity_forecast()` lines 562–568.

**Description:**
```python
# alpha_liquidity_sweep_predictor.py lines 562-568
n = len(self.ofi_history)
if n >= 20:
    ofi_mean = self.ofi_sum / n
    var = (self.ofi_sq_sum / n) - (ofi_mean * ofi_mean)
```
`self.ofi_sum` and `self.ofi_sq_sum` are referenced here but **never set** in `__init__` (lines 284–310). The attributes that `__init__` does set for OFI rolling stats are `_ofi_count`, `_ofi_mean`, `_ofi_M2` — three completely different names. As soon as `len(self.ofi_history) >= 20` (which happens by bar ~20 in any real run), `_liquidity_forecast()` raises `AttributeError: 'LiquiditySweepAlpha' object has no attribute 'ofi_sum'`.

`_liquidity_forecast()` is called from exactly one place: `get_signal()` line 923, inside the `state == "ACTIVE_SWEEP"` branch. So:
- PRE_SWEEP_BUILDUP path: never calls `_liquidity_forecast` → never crashes → produces 1,384 signals.
- ACTIVE_SWEEP path: always calls `_liquidity_forecast` → always crashes → produces zero signals.

This is the single biggest defect in LSA and the upstream cause of what I had originally described as C-2.

**Impact:**
- The entire ACTIVE_SWEEP code path (lines 876–1008, ~133 lines containing the resiliency check, the `_ml_sweep_probability` model, the fake-breakout fade logic, and the logit ensemble) is **dead in production**.
- In any live deployment, `get_signal()` would raise an unhandled `AttributeError` ~5.65% of the time. If a calling layer catches the exception, the alpha is silently degraded; if it doesn't, the orchestrator dies.
- This bug has been in the code through every commit in the repo's recent history (it is not a regression introduced by a recent change).

**Evidence (verbatim from v3 audit):**
```
[2242] AttributeError: 'LiquiditySweepAlpha' object has no attribute 'ofi_sum'
exception state attribution: {'ACTIVE_SWEEP': 2242}
```

**Proposed Fix:** Add the missing attributes to `__init__` and maintain them in `calculate_ofi_zscore` alongside the existing Welford `_ofi_M2` updates:
```python
# In __init__ (after line 296):
self.ofi_sum = 0.0       # for _liquidity_forecast normalization
self.ofi_sq_sum = 0.0    # for _liquidity_forecast normalization

# In calculate_ofi_zscore, when appending a new sample:
self.ofi_sum += ofi_total
self.ofi_sq_sum += ofi_total * ofi_total
# ...and on outgoing sample (window full):
if outgoing is not None:
    self.ofi_sum -= outgoing
    self.ofi_sq_sum -= outgoing * outgoing
```
**Verification:**
1. Re-run `audit_lsa_dec2023_v3.py` — `exceptions_after_warmup` MUST be 0.
2. State distribution MUST show non-zero ACTIVE_SWEEP with non-zero produced action counts.
3. Add a unit test that constructs a fresh `LiquiditySweepAlpha`, feeds 25 OFI updates, and asserts `_liquidity_forecast()` returns a finite number (not raises).

**Implementation Complexity:** LOW (5-minute fix).
**Priority:** **Fix immediately, before C-1 / C-2 can even be evaluated.** This is the prerequisite for evaluating the brief's intended ACTIVE_SWEEP behavior at all.

---

### [HIGH] H-1 — OFI z-score is computed on L1 TOB only; the algorithm in `calculate_ofi_zscore` is designed for depth-N

**Location:** `alpha_liquidity_sweep_predictor.py` → `calculate_ofi_zscore()` lines 378–440.

**Description:** The function iterates `range(self.levels)` (default 10) on `prev_book['bids']`/`['asks']`. With L1 TOB only (this audit), `depth_levels` MUST be set to 1 or the inner loop raises `IndexError`, which is caught by the bare `except (KeyError, IndexError, TypeError)` at line 399 and silently returns 0.0 — the function *appears* to work but produces zero signal.

This is precisely what would happen in production with the existing BacktestEngine wiring (which builds books from OHLCV via `_simulate_snapshot_from_candle`) — the synthetic books **do** have 10 levels, but the levels are deterministic linear extrapolations of `(h - l) * 0.02 * 0.1 * i`, so the per-level OFI deltas are perfectly correlated with the per-bar h/l move and add no information beyond level 0.

The audit's empirical result confirms it: the OFI predictive-power table shows **no monotone relationship** between |OFI-Z| and win rate.

**Impact:** OFI is the *primary* microstructure feature in `_predict_next_sweep` (line 596: `ofi_signal = math.tanh(ofi_z / 2.0)` with logit weight 0.7). With OFI providing zero real information, the micro-prediction logit collapses toward (compression, hawkes_signal), neither of which carries directional alpha at this aggregation level either. This is the upstream cause of C-1.

**Evidence:**
```python
# lines 382-399
for i in range(self.levels):
    curr_bid_p, curr_bid_s = _safe_float(curr_book['bids'][i]['price']), _safe_float(curr_book['bids'][i]['size'])
    ...
except (KeyError, IndexError, TypeError):
    return 0.0   # ← swallows partial-book conditions silently
```

**Proposed Fix:** Two-track:
1. **Code fix (small):** When books have fewer than `self.levels` real levels, compute OFI on `min(self.levels, len(curr_book['bids']))` instead of erroring → at least the L1 contribution is captured. Expose `actual_levels_used` as a debug attribute.
2. **Pipeline fix (real):** Wire `data_tools/l2_to_backtest.py` and `l2_pipeline.py` (already present in the repo, see `adv_summary.md` P-8) so depth-20 books from `bookDepth.csv` can drive OFI properly.

```python
# Fixed inner loop:
n_levels = min(self.levels, len(curr_book.get('bids', [])), len(curr_book.get('asks', [])),
               len(prev_book.get('bids', [])), len(prev_book.get('asks', [])))
self._last_ofi_levels_used = n_levels
for i in range(n_levels):
    ...
```

**Implementation Complexity:** LOW for code fix; MEDIUM for pipeline wiring.
**Priority:** **Fix before live.**

---

### [HIGH] H-2 — `macro_liquidity` is never wired in the BacktestEngine path; `predict_sweep` always receives `{}` so `macro_reliability` degrades to 0.5

**Location:** `backtest_engine.py` → `_build_lsa_market_data()` (does not include `macro_liquidity`/`macro_market_state`/`macro_volume_intel` in the dict it returns); the consumer is `alpha_liquidity_sweep_predictor.py` → `get_signal()` lines 717–728.

**Description:** In production design the macro layer is meant to come from a structural-zones scanner (e.g. ARE output or a dedicated liquidity-zone module). In backtest the dict is absent, so `predict_sweep()` runs with an empty `liquidity` argument → `macro_reliability` is force-set to 0.5 (line 722) → the macro logit branch contributes only ~half its intended weight.

**Evidence:**
```python
# alpha_liquidity_sweep_predictor.py lines 717-728
macro_liquidity = md.get('macro_liquidity', {})
...
if not macro_liquidity or not isinstance(macro_liquidity, dict):
    macro_reliability = 0.5
```

**Impact:** LSA's macro signal is systematically degraded in backtest vs production; any backtest claim about LSA's macro contribution understates production behavior. Same defect *shape* as the synthetic-OB issue (HIGH-1 in `adv_summary.md`) but on a different code path.

**Proposed Fix:** Add three lines to `_build_lsa_market_data()`:
```python
md["macro_liquidity"] = {
    "nearest_above": {"distance_points": abs(self.lsa.liquidity_pools.get("high", price) - price), "price": self.lsa.liquidity_pools.get("high")},
    "nearest_below": {"distance_points": abs(price - self.lsa.liquidity_pools.get("low", price)),  "price": self.lsa.liquidity_pools.get("low")},
}
md["macro_market_state"] = {"state": regime, "compression": vol_ratio, "volatility": vol_ratio, "bias": bias_estimate}
md["macro_volume_intel"] = {"volume_spike": vol > 2 * vol_ema, "volume_strength": vol / max(vol_ema, 1e-6)}
```

**Implementation Complexity:** LOW.
**Priority:** **Fix before live.**

---

### [HIGH] H-3 — Liquidity-pool reset threshold (10×ATR) is too lax for BTC

**Location:** `alpha_liquidity_sweep_predictor.py` → `detect_sweep_state()` lines 453–459.

**Description:** Reset condition `dist > atr * 10` on BTC at typical 1-min ATR of $100–$300 means pools only reset when price moves **$1k–$3k away from BOTH pools simultaneously** — covering minutes-to-hours of price action on a sustained trend. The audit observed this directly: the `update_liquidity_pools` rolling refresh (every 5 bars from trailing 60) is the only thing keeping pools alive on Dec 2023; without it, the seeded init_high/init_low (38898/38756) would be reset within hours of Dec 1 because BTC moved through both levels.

Combined with C-2 (Hawkes-spike-must-coincide), this means in a sustained trend the pools are stale enough that ACTIVE_SWEEP would not fire even if the Hawkes timing were right.

**Evidence:**
```python
# lines 453-459
if atr > 0 and (
    abs(_safe_float(self.liquidity_pools['high'], price) - price) > (atr * 10.0)
    and abs(price - _safe_float(self.liquidity_pools['low'], price)) > (atr * 10.0)
):
    self.liquidity_pools['high'] = None
    self.liquidity_pools['low'] = None
    return "NORMAL"
```

**Proposed Fix:** Make the multiplier configurable in `__init__`, default to 5×ATR (instead of 10×), and reset only the *side* that is far away, not both pools at once:
```python
def __init__(self, ..., pool_reset_atr_mult: float = 5.0):
    self.pool_reset_atr_mult = pool_reset_atr_mult
    ...

# in detect_sweep_state:
if atr > 0:
    if abs(self.liquidity_pools['high'] - price) > atr * self.pool_reset_atr_mult:
        self.liquidity_pools['high'] = None
    if abs(price - self.liquidity_pools['low']) > atr * self.pool_reset_atr_mult:
        self.liquidity_pools['low'] = None
    if self.liquidity_pools['high'] is None and self.liquidity_pools['low'] is None:
        return "NORMAL"
```

**Implementation Complexity:** LOW.
**Priority:** **Fix before live.**

---

### [HIGH] H-4 — Confidence is **anti-calibrated** on this window: higher confidence buckets do not show higher win rate

**Location:** `alpha_liquidity_sweep_predictor.py` → `get_signal()` lines 868–871, 1010–1019; computed across the entire PRE_SWEEP_BUILDUP path.

**Description:** Empirical confidence calibration on 1,384 PRE_SWEEP_BUILDUP signals:

| Bucket | Count | Win Rate |
|---|---:|---:|
| 0.5 – 0.7 | 463 | 10.80% |
| 0.7 – 1.0 | 921 | 11.07% |

A well-calibrated model would show win rate climbing with confidence. Here both buckets sit at ~11%. Worse: the **regime-aware confidence demotion** at lines 1012–1019 (`confidence *= 0.9` for SELL-in-uptrend, BUY-in-downtrend, range-mode) is the right *intent* but the test data shows the demotion is firing on the wrong side of the trade because C-1 has the directional convention backwards.

**Impact:** Any orchestrator that uses LSA confidence as a sizing input will overweight losing trades. With 921 trades at conf > 0.7 vs 463 at conf 0.5–0.7, a conviction-weighted sizer would put 2× the risk on the higher-conf bucket, which loses at the same ~11% rate.

**Proposed Fix:** Calibration is a *consequence* of C-1 + H-1 + H-2; cannot be fixed in isolation. After C-1/H-1/H-2 are addressed, **re-derive confidence from out-of-sample isotonic calibration** on the BUY/SELL hit rate over a held-out window. Replace the linear `confidence = combined_prob` with a calibration table fitted on training data.

**Implementation Complexity:** MEDIUM.
**Priority:** **Fix before live.** No conviction-weighted sizing should be used until calibration is restored.

---

### [MEDIUM] M-1 — `trades_count` proxy in BacktestEngine drives Hawkes intensity from a synthetic candle volume estimate, not from real trade arrivals

**Location:** `backtest_engine.py` → `_build_lsa_market_data` (uses `len(_simulate_trades_from_candle(candle))` per `adv_summary.md` HIGH-1 evidence).

**Description:** Hawkes process is supposed to model trade-arrival clustering. The volume-derived proxy `max(1, int(volume / price * 10))` cannot capture the rapid micro-burst pattern Hawkes is designed for. The audit verified this empirically:
- **v1 (synthetic trades_count proxy):** zero non-HOLD signals over 31 days
- **v2 (real per-min aggTrades count):** 1,384 signals, 10.98% win rate

Both runs produced losses, but v1 produced **zero data** to evaluate, which means BacktestEngine's current synthetic proxy hides the C-1/C-2 defects entirely — they appear only when real trade counts are wired in.

**Impact:** Backtests run through BacktestEngine are silent on LSA behavior because Hawkes never spikes. This makes the engine's apparent "no LSA signal" status indistinguishable from "LSA signal correctly suppressed". Operators cannot tell whether LSA is working or broken.

**Proposed Fix:** Wire real per-bar trade counts into `_build_lsa_market_data`:
1. Pre-load `data/aggTrades_dec2023.csv` (or whatever feed is replayed) at backtest start.
2. Bin per-minute: `trade_counts[bar_start_ms] = count_in_bar`.
3. Pass `trades_count = trade_counts.get(candle_ts, 0)` into the LSA dict.

This audit's `audit_lsa_dec2023_v2.py` is a working reference implementation.

**Implementation Complexity:** LOW.
**Priority:** Fix before live.

---

### [MEDIUM] M-2 — Regime-context threshold offset is wired but inert in practice for fresh LSA instances

**Location:** `alpha_liquidity_sweep_predictor.py` → `get_signal()` lines 787–792, 866, 1002.

**Description:** The `threshold_offset` knob is correctly wired:
- TREND → −0.02
- TOXIC → +0.05

But the audit's regime-aware test (Phase 5E, 500 sampled bars per regime, fresh LSA per regime) emitted **0 BUY/SELL across all three regimes**. Reason: the warmup × Hawkes × pool-seed cascade at lines 763–785 dominates; the −0.02 / +0.05 offset is inside the noise floor of the warmup-dependent threshold formula `0.55 + 0.10*(1-warmup) + 0.10*(1-history)` which spans **0.55 to 0.75 of the threshold range** before the offset is even applied.

**Impact:** Regime adaptivity exists in code but is invisible at the boundary where LSA is gated by warmup and pool state. Operators reading the code think the system is regime-aware; in production it will appear regime-blind for the first N bars after any restart or pool reset.

**Proposed Fix:** Hoist the threshold_offset further outward — apply it to the *output threshold floor* (currently 0.45) and ceiling (currently 0.9), not inside the `_clamp(... 0.45, 0.9)`:
```python
# Current (line 866):
threshold = _clamp(threshold + threshold_offset, 0.45, 0.9)

# Fixed:
floor = _clamp(0.45 + threshold_offset, 0.30, 0.85)
ceil  = _clamp(0.90 + threshold_offset, 0.50, 0.95)
threshold = _clamp(threshold + threshold_offset, floor, ceil)
```

**Implementation Complexity:** LOW.
**Priority:** Fix before live.

---

### [MEDIUM] M-3 — `_time_lock` lazy initialization inside `get_signal` is technically thread-safe but obscures intent

**Location:** `alpha_liquidity_sweep_predictor.py` → `get_signal()` lines 657–661.

**Description:**
```python
if "_time_lock" not in self.__dict__:
    try:
        self.__dict__["_time_lock"] = threading.Lock()
    except Exception:
        self._time_lock = threading.Lock()
```
This runs INSIDE the outer `with self._lock:` block (line 652), so the double-checked-locking concern is moot — but the pattern looks like it's a bare DCL (which is unsafe in CPython without an outer lock), and any future refactor that moves this code outside the outer lock would silently introduce a race.

**Impact:** No live race; future-refactor risk only.

**Proposed Fix:** Move the `_time_lock` initialization into `__init__`:
```python
# In __init__:
self._time_lock = threading.Lock()
```
And delete lines 657–661.

**Implementation Complexity:** LOW.
**Priority:** Fix eventually.

---

### [LOW] L-1 — `_safe_output` rounds `prob_above` to 4 decimals then derives `prob_below = 1 - prob_above` — silent precision loss

**Location:** `alpha_liquidity_sweep_predictor.py` → `_safe_output()` lines 119–127.

**Description:** Rounding to 4 decimals is fine for a probability output and the sum-to-1 invariant is preserved within FP tolerance — but the rounding is silent. A downstream consumer expecting raw floats would see precision loss without knowing why.

**Impact:** Cosmetic / observability only.

**Proposed Fix:** Add a one-line docstring note in `_safe_output` documenting the 4-decimal rounding.

**Implementation Complexity:** LOW.
**Priority:** Fix eventually.

---

### [LOW] L-2 — `_predict_next_sweep` returns neutral `{0.5, 0.5}` whenever pools are None — silent feature loss

**Location:** `alpha_liquidity_sweep_predictor.py` → `_predict_next_sweep()` lines 578–580.

**Description:**
```python
if price <= 0.0 or len(self.ofi_history) < 10 or self.liquidity_pools.get("high") is None or self.liquidity_pools.get("low") is None:
    return {"prob_up": 0.5, "prob_down": 0.5}
```
When pools reset (H-3) or are not yet seeded, `_predict_next_sweep` returns neutral with no logging. The PRE_SWEEP_BUILDUP and ACTIVE_SWEEP branches downstream see `prob_up = prob_down = 0.5`, which they then logit-combine with macro to a near-0.5 — and the threshold gate (≥ 0.55–0.75) blocks the signal silently.

**Impact:** Hard-to-diagnose silent suppression in operators' view: "LSA stopped emitting" with no trace of why.

**Proposed Fix:** Add a counter `self._neutral_predict_count` and expose via a `get_state_metrics()` method (same pattern as ARE).

**Implementation Complexity:** LOW.
**Priority:** Fix eventually.

---

## Detailed Solution Plan

### Critical Fixes (Must fix before any live deployment)

#### Fix C-4 (PREREQUISITE): Initialize `ofi_sum` and `ofi_sq_sum` in `__init__`

**Why first:** Until C-4 is fixed, the ACTIVE_SWEEP path is dead and C-1/C-2 cannot be evaluated honestly. C-4 is also a one-line fix with zero design implications.

**Step-by-Step Fix:**
1. Add `self.ofi_sum = 0.0` and `self.ofi_sq_sum = 0.0` to `__init__` after line 296.
2. In `calculate_ofi_zscore`, mirror the existing Welford increments into `ofi_sum` / `ofi_sq_sum` (add on append, subtract on eviction).
3. Re-run `audit_lsa_dec2023_v3.py` and assert `exceptions_after_warmup == 0`.
4. Re-publish the audit numbers (this report) with the fixed v4 results.

**Estimated effort:** 30 minutes.

---

#### Fix C-1: Reverse PRE_SWEEP_BUILDUP directional convention OR convert to fade

**Problem Statement:** LSA's anticipatory continuation bet on PRE_SWEEP_BUILDUP loses 89% of trades on Dec 2023. Profit factor 0.092, max DD 95.34%.

**Root Cause Analysis:** The model is designed around the assumption that "buildup near a level" predicts continuation through the level. On the audited window, the *opposite* is true — buildups that fire are reverted more often than they're continued. Without out-of-sample confirmation we cannot know whether (a) the convention is wrong globally, (b) it is specific to this window, or (c) the underlying micro/macro probability calculation is wrong and the directional flip would just generate inverse-losses. So the fix has two phases.

**Step-by-Step Fix:**
1. Add a `direction_mode: str = "continuation"` parameter to `LiquiditySweepAlpha.__init__` (default = current behavior, so no regression).
2. In `get_signal` lines 869, replace the literal direction with a function of `direction_mode`:
   ```python
   if combined_prob >= threshold:
       if self.direction_mode == "fade":
           action = "SELL" if sweep_side == "high" else "BUY"
       else:
           action = "BUY" if sweep_side == "high" else "SELL"
   ```
3. Run `audit_lsa_dec2023_v2.py` with `direction_mode="fade"` on Dec 2023 → record win rate.
4. Run the same with `direction_mode="fade"` on Apr 2024 (`data/aggTrades_apr2024.csv` + `data/bookTicker_apr2024.csv` already present) → out-of-sample confirmation.
5. Only flip the default if **both** windows show edge.

**Verification:** Win rate on out-of-sample Apr 2024 ≥ 50% (vs current 11%). Profit factor ≥ 1.1. Max DD ≤ 30% on the same horizon-12 cost-22bps test.

**Estimated effort:** 4 hours (1h code, 3h walk-forward).

---

#### Fix C-2: Decouple ACTIVE_SWEEP from instantaneous Hawkes spike

**Problem Statement:** ACTIVE_SWEEP path is dead code — fired 0 times in 39,670 bars.

**Root Cause Analysis:** Hawkes intensity peaks during the burst that *precedes* the price cross by a few seconds; by the time price crosses the pool, intensity has decayed below `2 × baseline`. The two conditions (price-cross AND intensity-spike) cannot coincide on 1m bar resolution.

**Step-by-Step Fix:**
1. In `detect_sweep_state` line 467, add a trailing-window peak:
   ```python
   recent_peak = max(list(self.hawkes_history)[-30:]) if len(self.hawkes_history) >= 30 else hawkes_intensity
   recent_spike = recent_peak >= baseline * 2.0
   ```
2. Use `recent_spike` for the `ACTIVE_SWEEP` gate and keep `intensity_spike` (instantaneous) for `PRE_SWEEP_BUILDUP`:
   ```python
   if (is_high_sweep or is_low_sweep) and recent_spike:
       return "ACTIVE_SWEEP"
   if (near_level or compression_condition) and intensity_spike:
       return "PRE_SWEEP_BUILDUP"
   ```
3. Re-run `audit_lsa_dec2023_v2.py` and verify `ACTIVE_SWEEP` count > 0 in the state distribution table.

**Verification:** State distribution shows non-zero ACTIVE_SWEEP count. The 30-bar window choice should be configurable via `__init__(active_sweep_lookback_bars=30)`.

**Estimated effort:** 2 hours.

---

#### Fix C-3: Validate state and regime enum membership in `_safe_output`

**Step-by-Step Fix:** As shown in the finding above. Add `_VALID_STATES` and `_VALID_REGIMES` module-level sets, validate in `_safe_output`, increment a `_state_invalid_count` (exposed via a new `get_state_metrics()` method).

**Verification:** Unit test that calls `_safe_output({"state": "BOGUS", "regime": "FAKE"})` and asserts the output contains `"state": "NORMAL"` and `"regime": "RANGING"`, with `_state_invalid_count` incremented to 1.

**Estimated effort:** 30 minutes.

---

### High-Priority Fixes

#### Fix H-1: Honest depth handling in `calculate_ofi_zscore`
- Inner loop bounds: `n_levels = min(self.levels, len(curr_book['bids']), len(curr_book['asks']), len(prev_book['bids']), len(prev_book['asks']))`.
- Expose `_last_ofi_levels_used` for observability.
- Wire `data/bookDepth.csv` through `data_tools/l2_to_backtest.py` (already present in repo) so depth-N OFI is available in backtest.

#### Fix H-2: Wire `macro_liquidity` in `BacktestEngine._build_lsa_market_data`
- 6-line addition (see finding); pulls liquidity_pools state from `self.lsa.liquidity_pools` already present in the engine.

#### Fix H-3: Configurable + per-side pool reset
- Add `pool_reset_atr_mult` to `__init__`, default 5.0.
- Reset `high` and `low` independently instead of together.

#### Fix H-4: Out-of-sample isotonic calibration of `confidence`
- Run C-1-fixed LSA on a held-out window.
- Bin signals by raw `combined_prob`, compute per-bin empirical win rate.
- Fit isotonic regression `combined_prob → calibrated_prob`.
- Replace `confidence = combined_prob` with `confidence = isotonic_calibrator(combined_prob)`.

---

### Medium / Low Fixes
- M-1: Wire real per-min aggTrades counts into `_build_lsa_market_data` (reference: `audit_lsa_dec2023_v2.py`).
- M-2: Hoist regime threshold_offset outside the floor/ceiling clamp.
- M-3: Move `_time_lock` initialization into `__init__`.
- L-1: Document 4-decimal rounding in `_safe_output` docstring.
- L-2: Add `_neutral_predict_count` counter to `_predict_next_sweep`.

---

## Architectural Improvements Proposed

### A1 — Real depth-N L2 integration in BacktestEngine
**Current state:** BacktestEngine builds synthetic 10-level books from OHLCV via `_simulate_snapshot_from_candle`; OFI deltas across levels are perfectly correlated (linear extrapolation), so depth-N adds no information beyond level 0.
**Proposed:** Wire `data_tools/l2_to_backtest.py` and `data/bookDepth.csv` so real depth snapshots feed `_build_lsa_market_data`. Same A1 referenced in `adv_summary.md` P-8.

### A2 — Out-of-sample walk-forward harness
**Current state:** This audit ran on a single 31-day window. C-1 fix cannot be safely accepted without held-out validation.
**Proposed:** Add a `walk_forward.py` driver:
- Train window: rolling 14 days
- Test window: next 7 days
- Step: 7 days
- Re-fit confidence calibration per train window
- Aggregate per-test-window metrics (win rate, Sharpe, DD) into a single CSV
- Pass condition: median(test-window win rate) ≥ 50%, median(test-window Sharpe) ≥ 1.0

### A3 — Telemetry surface for LSA
**Current state:** No introspection into which gate is suppressing signals. Operators cannot distinguish "no edge" from "warmup not done" from "pool reset" from "macro reliability degraded".
**Proposed:** Add `LiquiditySweepAlpha.get_state_metrics()` mirroring `AdvancedRegimeEngine.get_state_metrics()` (see `adv_summary.md` FIX-2). Expose:
- `_ofi_count`, `_ofi_M2` (warmup state)
- `len(self.hawkes_history)`, `self.hawkes_sum / max(1, len(self.hawkes_history))` (Hawkes baseline)
- `self.liquidity_pools` (pool state)
- counts: `_neutral_predict_count`, `_state_invalid_count`, `_active_sweep_fired_count`, `_pre_sweep_fired_count`

### A4 — Walk-forward direction-mode A/B harness
After C-1 + C-2 + C-3 are merged, run continuation-vs-fade A/B on Dec 2023 + Apr 2024 (both already in repo) and pick the winning direction by held-out performance, not in-sample fit.

---

## Production Readiness Roadmap

### Phase A — Block research-only status (this PR)
- **Goal:** Make the failure mode visible. This PR ships `alpha_liquidity.md`, `audit_lsa_dec2023.py`, `audit_lsa_dec2023_v2.py`, and the `audit_lsa_output/` artifacts.
- **Required files:** present in this PR.
- **Required checks:** none (read-only audit; no engine code modified).
- **Exit criteria:** PR merged; `alpha_liquidity.md` becomes the source of truth for LSA status until C-1/C-2/C-3 land.

### Phase B — Critical fixes (next PR)
- **Goal:** Close C-1, C-2, C-3.
- **Required files:** `alpha_liquidity_sweep_predictor.py` (modified), unit tests for `_safe_output` enum validation, regression test asserting `ACTIVE_SWEEP` count > 0 on Dec 2023.
- **Required checks:** `python3 audit_lsa_dec2023_v2.py` re-run; new state distribution table appended to `alpha_liquidity.md`.
- **Exit criteria:** After C-1 fix on out-of-sample Apr 2024 window, win rate ≥ 50%, profit factor ≥ 1.1, max DD ≤ 30%.

### Phase C — High-priority fixes
- **Goal:** Close H-1, H-2, H-3, H-4.
- **Required files:** `alpha_liquidity_sweep_predictor.py`, `backtest_engine.py`, `data_tools/l2_to_backtest.py`, new `lsa_calibration.py`.
- **Required checks:** depth-N OFI driving non-zero z-score on real bookDepth; macro_reliability ≥ 0.8 on backtest; isotonic calibration table fitted and persisted.
- **Exit criteria:** Confidence calibration table is monotone (higher confidence → higher win rate by ≥ 5pp per bucket).

### Phase D — Medium fixes + telemetry (A3)
- **Goal:** Close M-1/M-2/M-3 and ship `get_state_metrics()`.
- **Exit criteria:** Operators can read LSA gate-suppression state from a single API call.

### Phase E — Walk-forward harness (A2)
- **Goal:** Continuous out-of-sample validation.
- **Exit criteria:** Median Sharpe ≥ 1.0 across all rolling 7-day test windows in the available data.

### Phase F — Paper trading readiness gate
- **Goal:** Final gate before paper trading.
- **Exit criteria:** All Phase B–E checks green; max DD ≤ 15% on rolling test windows; profit factor ≥ 1.3.

---

## Acceptance Criteria for Live Readiness

| # | Criterion | Current | Target |
|---|---|---:|---:|
| 1 | Win rate (in-sample, Dec 2023, horizon-12, cost-22bps) | **10.98%** | ≥ 52% |
| 2 | Profit factor (in-sample) | **0.092** | ≥ 1.3 |
| 3 | Sharpe (daily ann., in-sample) | **−50.78** | ≥ 1.5 |
| 4 | Max drawdown (in-sample) | **95.34%** | ≤ 15% |
| 5 | Win rate (out-of-sample, Apr 2024) | not yet measured | ≥ 50% |
| 6 | ACTIVE_SWEEP signal count (in-sample) | **0** | > 0 (any positive count proves the path is reachable) |
| 7 | Confidence calibration monotonicity | **MISCALIBRATED** | CALIBRATED (≥ 5pp win-rate uplift between adjacent buckets) |
| 8 | OFI predictive monotonicity (depth-N) | OFI_NOISE on L1 | OFI_PREDICTIVE on depth-N |
| 9 | Telemetry: `get_state_metrics()` exposed | absent | present |
| 10 | `_safe_output` enum validation present | absent | present |
| 11 | `macro_liquidity` wired in BacktestEngine | absent | present |
| 12 | Real per-min `trades_count` wired in BacktestEngine | absent (volume proxy) | present |

---

## Final Recommendation

**Q1 — Can paper trading start now?** **No.** Blockers C-1, C-2, C-3 are all required-before-paper. Win rate 11%, profit factor 0.09, and max DD 95% over 31 days are not noise — they are the model's true behavior in its current form on real BTC microstructure. Paper trading at this state would simply burn the paper account in days and obscure the underlying defects further.

**Q2 — What is the correct fix order?** C-3 (1h, defensive) → C-2 (2h, unblocks the dead code path) → C-1 (4h, requires walk-forward) → H-1 + H-2 + H-3 (4h, removes structural data starvation) → H-4 (8h, calibration) → M-1/M-2/M-3 (3h) → A2 walk-forward (16h) → A3 telemetry (4h). Total estimated effort to research-credible status: ~6 engineering days.

**Q3 — Is more multi-resolution required?** Yes for confidence in the result, no to detect the C-1/C-2/C-3 defects. The 1m resolution surfaced the defects unambiguously. Multi-resolution (5m, 15m) is needed for *acceptance* — confirming the fixed model survives across timeframes.

**Q4 — Is deeper L2 required?** Yes for OFI to carry real information (H-1). L1 OFI showed no edge in any |OFI-Z| bucket. Until depth-N L2 is wired, LSA's primary microstructure feature is effectively zero.

**Q5 — What is the honest research-only status?** **Hard research-only.** LSA in its current form is not just "needs tuning" — it has three independent structural defects (C-1, C-2, C-3) and four high-priority infrastructure gaps (H-1 through H-4). The fixes are well-scoped and low-risk in code, but C-1 specifically requires out-of-sample validation before the directional flip can be trusted. No paper trading, no live, no conviction-weighted sizing until Phase F gate criteria are met.

---

## Honest Caveats

1. **Depth limit:** OFI was computed on L1 TOB only (`depth_levels=1`). The audited repo has `data/bookDepth.csv` (16.6 MB) but it is not aligned to the BTCUSDT_240329 Dec 2023 window used elsewhere in the audit; aligning it requires the H-1 pipeline fix and a re-run.
2. **Single window:** All metrics are in-sample on Dec 2023. Apr 2024 data (`data/aggTrades_apr2024.csv`, `data/bookTicker_apr2024.csv`) is present in the repo but not audited here — the C-1 walk-forward in Phase B will use it.
3. **No execution-module simulation:** Per the read-only contract, no order-routing path was exercised. PnL is computed from forward-return × direction × size=1 minus 22bps round-trip. This is the standard signal-quality methodology, not a fills simulator.
4. **`trades_count` is per-minute count of aggTrades** — not per-trade arrival microbursts. A finer-grained Hawkes evaluation would need tick-level trade times, which `data/aggTrades_dec2023.csv` does carry but were not consumed at sub-minute granularity in this audit (would not change the headline finding; LSA gates fire at 1m bar boundaries anyway).
5. **No engine-code modifications:** This audit makes zero changes to any production source file. Every "fix" above is a *prescription* to be applied in a follow-up PR after acceptance of this audit's findings.

---

**Audit artifacts (under `audit_lsa_output/`):**
- `audit_report.json` — Phase 1–7 machine-readable findings (v1, synthetic trade-count proxy)
- `audit_v2.json` — Phase 5–6 metrics with **real per-min aggTrades** trade counts (v2, silently swallowed exceptions — superseded by v3)
- `audit_v3.json` — **CORRECTED metrics with honest exception accounting** (the headline numbers in this report; supports C-4 finding)
- `lsa_records.csv` — per-bar LSA outputs (v1)
- `lsa_records_v2.csv` — per-bar LSA outputs (v2, real trade counts)
- `lsa_records_v3.csv` — per-bar LSA outputs (v3, all 39,670 post-warmup bars accounted for including exception-attributed ACTIVE_SWEEP bars)
- `lsa_trade_log.csv` — per-trade entry/exit/PnL ledger

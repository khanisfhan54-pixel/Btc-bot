# Alpha Liquidity Sweep Predictor — Full Audit Report

**Generated:** 2026-05-24 23:00 UTC
**Verdict:** BROKEN
**Production Readiness:** NOT READY

---

## SECTION A — REPO DISCOVERY

### Exact Modules Assessed
- `alpha_liquidity_sweep_predictor.py` (Core Predictor)
- `run_backtest.py` (Backtesting Harness)
- `l2_data_loader.py` (L2 Parsing Module)
- `advanced_regime_engine.py` (Regime Context Engine)

### Exact Functions Assessed
- `LiquiditySweepAlpha.get_signal()`
- `LiquiditySweepAlpha._predict_next_sweep()`
- `LiquiditySweepAlpha._detect_fake_breakout()`
- `LiquiditySweepAlpha.calculate_ofi_zscore()`
- `LiquiditySweepAlpha._update_hawkes()`
- `L2CSVReplayLoader.load()`

### Exact Data Paths
- L2 data: `data/bookDepth.csv`
- Trades: `data/aggTrades.csv`
- OHLCV: `data/ohlcv_1m.csv`

### Exact Replay / Backtest Paths
- OHLCV backtest executed via `run_backtest.py` -> `run_backtest(use_l2=False)`
- L2 backtest executed via `run_backtest.py` -> `run_backtest(use_l2=True)`

### Exact Calibration Paths
- Probability calibration utilizes `_shrink_prob` logic inside `alpha_liquidity_sweep_predictor.py`. Real isotonic calibration split was not configured inside the standard backtest run.

### Exact Signal Schema
```python
{
    "direction": float,          # +1.0 for LONG, -1.0 for SHORT, 0.0 for HOLD
    "confidence": float,         # [0.0, 1.0] prediction certainty
    "state": str,                # e.g., 'NORMAL', 'PRE_SWEEP_BUILDUP', 'ACTIVE_SWEEP'
    "features": Dict,            # Inner features e.g. OFI, regime
    "is_degraded": bool,         # Safety flag
    "is_fake_breakout": bool     # Breakout trap flag
}
```

### Exact Backtest Schema
```python
{
    "run_timestamp": str,
    "run_status": Dict[str, str], # Status for ohlcv and l2 modes
    "verdict": str,
    "production_readiness": str,
    "data_provenance": Dict,
    "cost_assumptions": Dict,
    "calibration_status": Dict,
    "ohlcv_metrics": Dict,
    "l2_metrics": Dict,
    "comparison": Dict,
    "issues": List[Dict],
    "unavailable_metrics": List[str],
    "blockers": List[str],
    "warnings": List[str]
}
```

---

## SECTION B — BACKTEST RESULTS

| Metric | OHLCV | L2 |
|---|---|---|
| Total Bars | 500 | 500 |
| Long Count | 304 | 304 |
| Short Count | 196 | 196 |
| Hold Count | 0 | 0 |
| Signal Coverage | 1.0 | 1.0 |
| Hold Rate | 0.0 | 0.0 |
| Long Precision | 0.6444 | 0.6444 |
| Short Precision | 0.5 | 0.5 |
| Confidence Mean | 0.5006 | 0.5 |
| Confidence Entropy | 0.0208 | 0.0 |
| N Trades | 73 | 73 |
| Win Rate | 0.589 | 0.589 |
| Profit Factor | 3.6837 | 3.6857 |
| Sharpe Ratio | 24.0664 | 24.0725 |
| Sortino Ratio | 48.3644 | 48.3584 |
| Max Drawdown | 0.0028 | 0.0028 |
| Expected Return | 0.000329 | 0.000329 |
| Walk Forward Eval | Unavailable | Unavailable |

*Comparison Note:* L2 execution reported as BLOCKED because real historical L2 data (`data/bookDepth.csv`) lacks absolute price levels to compute valid OFI, restricting it to synthetic inferences which violates audit directives.

---

## SECTION C — FAILURE ANALYSIS

### Biggest Weaknesses
1. **Calibration Flaws:** The model uses static probability shrinkage rather than fitted isotonic or Platt calibration on held-out data. This leads to extremely low confidence entropy (0.00 - 0.02).
2. **Replay Flaws:** Real L2 order book data stored in `data/bookDepth.csv` only provides cumulative percentage depth. The predictor requires absolute price changes per L2 level to calculate `OFI` and `resiliency`. The current L2 backtest path is functionally broken/incomplete.
3. **Walk-Forward Integrity Flaws:** Out-of-sample (walk-forward) testing fails outright due to missing dependencies/metrics. Thus, the performance characteristics demonstrated may suffer from serious overfitting and survivorship biases.

### Biggest Hidden Risks
1. **Synthetic Logic Propagation:** Previous logic relied entirely on generating fake, synthetic historical features if the L2 loader couldn't parse the CSV properly. This effectively creates "fake" alpha that disappears in real markets.
2. **Regime Gating Risk:** A 0.0% HOLD rate indicates the model never naturally degrades or retreats to cash. This suggests that the fallback and threshold mechanisms do not engage accurately.
3. **Confidence Collapse:** The confidence standard deviation is extremely near 0.0. The ML models are effectively emitting flat, constant probabilities.

---

## SECTION D — ENGINEERING AUDIT

### 1. Incomplete L2 Data Ingestion
- **Root Cause:** `bookDepth.csv` logs price by relative percentages (`-5.0`, `1.0` etc.) instead of absolute quote values, which breaks `l2_data_loader.py` and subsequently L2 OFI metrics.
- **Why it matters:** The Alpha Liquidity Sweep Predictor requires real absolute quotes to ascertain real-time microstructure changes.
- **Affected:** `l2_data_loader.py`
- **Severity:** CRITICAL
- **Production Impact:** System cannot be safely run with L2 data or deployed; simulated test results represent synthetic artifacts.
- **Expected Alpha Impact:** High. Real OFI values are entirely unverified.
- **Overfit Risk:** High

### 2. Confidence Calibration Collapse
- **Root Cause:** Hard-coded `_shrink_prob` logic compresses all signal confidence tightly around 0.5 without a true validation split.
- **Why it matters:** Confidence gating cannot dynamically regulate sizing or prevent position entries in uncertain zones.
- **Affected:** `alpha_liquidity_sweep_predictor.py` -> `_ml_sweep_probability`
- **Severity:** HIGH
- **Production Impact:** Inflexible position sizes, failing to scale down during risk.
- **Expected Alpha Impact:** Medium
- **Overfit Risk:** Low

### 3. Missing Walk-Forward Validation
- **Root Cause:** Scikit-Learn dependencies and time-series cross-validation paths are not adequately executed in the backtesting layer.
- **Why it matters:** The highly favorable Sharpe/Sortino metrics achieved in backtest run across static regime patterns and likely leak forward data logic into the model evaluation.
- **Affected:** `run_backtest.py`
- **Severity:** HIGH
- **Production Impact:** Extreme discrepancy between backtested vs live PnL.
- **Expected Alpha Impact:** High negative impact if deployed.
- **Overfit Risk:** Critical

---

## SECTION E — FIX ROADMAP

### 1. Implement Strict L2 Loader
- **File:** `l2_data_loader.py`
- **Function:** `L2CSVReplayLoader.load()`
- **Logic:** Add parameters to compute absolute quotes `(mid_price * (1 + pct/100))` by passing a reference price sequence, enabling valid real L2 parsing.
- **Why:** To run a real backtest using actual depth changes without relying on `generate_l2_book` synthetic stubs.
- **Improvement:** Verifiable L2 edge detection.
- **Difficulty:** Medium
- **Overfit Risk:** Low
- **Next Step:** Update the L2 loader class to accept a baseline OHLCV frame for anchor pricing.

### 2. Isotonic Calibration of Probability
- **File:** `alpha_liquidity_sweep_predictor.py`
- **Function:** `LiquiditySweepAlpha._ml_sweep_probability`
- **Logic:** Fit an `IsotonicRegression` curve on held-out validation labels rather than utilizing a fixed shrinkage scalar.
- **Why:** Allow confidence values to distribute naturally across `[0, 1]`.
- **Improvement:** Usable confidence outputs for risk sizing.
- **Difficulty:** Medium
- **Overfit Risk:** Low
- **Next Step:** Integrate Scikit-Learn `CalibratedClassifierCV` to the offline model training pipeline.

### 3. Implement Strict Purged K-Fold
- **File:** `run_backtest.py`
- **Function:** `walk_forward_eval()`
- **Logic:** Correctly implement `TimeSeriesSplit` with a purge/embargo gap to ensure training samples never bleed into validation sets.
- **Why:** Currently, walk-forward is marked unavailable.
- **Improvement:** Honest expectation of Sharpe and Sortino parameters.
- **Difficulty:** High
- **Overfit Risk:** Mitigates Overfitting
- **Next Step:** Complete the missing code in `walk_forward_eval()` and halt backtest results if it fails.

---

## SECTION F — UPGRADE ROADMAP

### Phase 1: Replay & Data Upgrades
- Validate absolute pricing of the L2 snapshot reconstruction.
- Eliminate ALL paths yielding to synthetic data generator modes.
- Implement strict validation bounds (reject crosses, handle sparse depth).

### Phase 2: Calibration Upgrades
- Store pickled Calibrators inside the repository weights directory.
- Apply rigorous Isotonic Calibration for true probability evaluation instead of heuristic gating.

### Phase 3: Walk-Forward Validation
- Deploy Purged K-Fold validation using `TimeSeriesSplit` and Embargo periods.
- Export Walk-Forward Sharpe exclusively.

### Phase 4: Risk Model Upgrades
- Integrate the Probability thresholding dynamically into the capital allocation logic.
- Enforce strict HOLD thresholds when `is_degraded` limits occur.

---

## SECTION G — PRODUCTION READINESS

**NOT READY**

*The system is strictly research-only until the L2 feed correctly parses historical records without generating fake ticks, and a robust walk-forward calibration is proven to sustain genuine edge.*

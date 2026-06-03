# SHPE Final Research Verdict

## Final verdict: FAIL

| Criterion | Status |
| --- | --- |
| Dataset Quality | PASS |
| Leakage Audit | PASS |
| Calibration | FAIL |
| Regime Stability | FAIL |
| Trading Expectancy | FAIL |
| Overfitting Risk | PASS |

## Scores

- Production Readiness: 0/100
- Confidence Score: 70/100

## Required acceptance criteria

- No leakage detected: YES
- Walk-forward passes: YES
- Expectancy > 0: NO (`-0.000652`)
- Profit factor > 1.1: NO (`0.423646`)
- Brier improves vs baseline: NO (model `0.456566`, baseline `0.160000`)
- Performance stable across regimes: NO
- No major overfitting evidence: YES

## Research conclusion

SHPE must **not** be marked successful on this repository dataset. The audit found no timestamp leakage in the constructed research dataset and walk-forward validation did run, but the available BTC history is short, several exogenous SHPE data sources are absent, and the acceptance criteria are not all satisfied.

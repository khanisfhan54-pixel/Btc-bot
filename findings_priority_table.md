# TOP ISSUES (Ranked by Severity & Risk)

| Rank | Issue | Severity | Alpha Impact | Production Risk | Complexity |
|---|---|---|---|---|---|
| 1 | L2 data schema (`bookDepth.csv`) lacks absolute pricing, breaking OFI calculation. | CRITICAL | HIGH | CRITICAL | Medium |
| 2 | Synthetic microstructure fallback silently generates fake signals during L2 ingestion failures. | CRITICAL | HIGH | CRITICAL | Low |
| 3 | Lack of out-of-sample walk-forward validation (causes massive Sharpe inflation). | HIGH | HIGH | HIGH | Medium |
| 4 | Confidence predictions collapse to a ~0.50 constant due to weak calibration logic. | HIGH | MEDIUM | HIGH | Medium |
| 5 | HOLD-rate is 0.0%, indicating broken fallback/degradation safety toggles. | HIGH | MEDIUM | HIGH | Low |
| 6 | No handling of orderbook crosses during historical ingestion. | MEDIUM | LOW | MEDIUM | Low |
| 7 | Stale state warnings from Hawkes module parameters not being initialized. | MEDIUM | LOW | MEDIUM | Low |

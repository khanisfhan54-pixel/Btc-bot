# Switch Strength Component Ablation

| formula | accept delta | reject delta | duration delta | churn delta | TOXIC exit delta | precision | recall | false switches | missed switches |
|---|---|---|---|---|---|---|---|---|---|
| no_conviction | -157 | 6 | 26.53 | -71.36 | -5 | 0.966 | 0.966 | 48 | 48 |
| no_edge | -175 | 19 | 42.24 | -79.55 | -13 | 0.967 | 0.970 | 46 | 42 |
| no_volatility | -73 | -5 | 5.14 | -33.18 | -1 | 0.962 | 0.892 | 49 | 151 |
| conviction_only | -175 | 19 | 42.24 | -79.55 | -13 | 0.967 | 0.970 | 46 | 42 |
| edge_only | -175 | 19 | 42.24 | -79.55 | -13 | 0.967 | 0.970 | 46 | 42 |
| volatility_only | -175 | 19 | 42.24 | -79.55 | -13 | 0.967 | 0.970 | 46 | 42 |

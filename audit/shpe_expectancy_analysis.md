# SHPE Expectancy Analysis

## Cost and fill assumptions

- Taker fees: 4.0 bps per side.
- Spread: 1.0 bps round trip.
- Slippage: 2.0 bps round trip.
- Latency: 250 ms with 0.5 bps adverse-selection penalty.
- Total modeled round-trip cost: 11.50 bps.

## Research-only event-driven strategy

LONG when calibrated probability is greater than threshold. SHORT when calibrated probability is less than `1 - threshold`. Entry uses the audited event bar close after features are available; exit uses the SHPE label horizon close. No perfect fills are assumed because costs are subtracted from every trade.

| Threshold | Trades | Win rate | Avg win | Avg loss | Expectancy | Profit factor | Sharpe | Max DD | NEGATIVE_EXPECTANCY |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.55 | 43 | 0.418605 | 0.001067 | 0.002117 | -0.000784 | 0.363053 | -2.101867 | -0.033704 | YES |
| 0.60 | 42 | 0.428571 | 0.001067 | 0.002172 | -0.000784 | 0.368514 | -2.052962 | -0.032920 | YES |
| 0.65 | 41 | 0.439024 | 0.001067 | 0.002126 | -0.000724 | 0.392879 | -1.873874 | -0.030323 | YES |
| 0.70 | 39 | 0.435897 | 0.001099 | 0.002004 | -0.000652 | 0.423646 | -1.666484 | -0.026051 | YES |
| 0.75 | 38 | 0.421053 | 0.001104 | 0.002004 | -0.000696 | 0.400450 | -1.744380 | -0.027074 | YES |

Formula: `E=(P_win × AvgWin)-((1-P_win) × AvgLoss)`.

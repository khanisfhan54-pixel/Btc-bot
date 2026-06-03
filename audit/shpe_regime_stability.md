# SHPE Regime Stability Analysis

## REGIME_DEPENDENCY: YES

Overall calibrated Brier: 0.456566

| Regime | Samples | Precision | Recall | Brier | ECE | Win rate |
| --- | --- | --- | --- | --- | --- | --- |
| trend_up | 10 | 0.000000 | 0.000000 | 0.217158 | 0.254772 | 0.700000 |
| trend_down | 2 | 0.000000 | 0.000000 | 0.384747 | 0.613420 | 0.000000 |
| range | 7 | 1.000000 | 0.500000 | 0.077610 | 0.162040 | 0.857143 |
| high_vol | 26 | 0.058824 | 0.166667 | 0.656197 | 0.723653 | 0.192308 |
| low_vol | 0 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |

## Interpretation

Major regimes are those with at least 10 walk-forward predictions. A regime dependency is flagged when a major regime has Brier more than 1.5x the overall Brier or classification win rate below 45%.

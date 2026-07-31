# Switch Strength Audit

Source: transient switch-evaluated records (`switch_gate >= 0`) from `PYTHONPATH=/workspace/Btc-bot python /tmp/full_audit.py`. Contribution terms are reconstructed from the production formula: `edge_contribution = 0.48 * regime_edge_smoothed`, `conviction_contribution = 0.34 * conviction`, and `vol_contribution = switch_strength - edge_contribution - conviction_contribution` (residual includes any shock add-on).

Switch evaluations measured: **1401**.

## Average and median contribution percentages

| component | average % | median % | p10 | p50 | p90 |
|---|---:|---:|---:|---:|---:|
| edge | 41.536265 | 28.398875 | 19.399734 | 28.398875 | 68.158486 |
| conviction | 26.037987 | 28.943373 | 12.170361 | 28.943373 | 35.816898 |
| vol | 32.425748 | 36.709066 | 11.085400 | 36.709066 | 49.894987 |

## Correlations

| metric pair | correlation |
|---|---:|
| corr(switch_strength, conviction) | 0.211606 |
| corr(switch_strength, edge_score) | 0.330834 |
| corr(switch_strength, directional_margin) | 0.308951 |

## Edge-present switch failures

Cases where `edge_score > 0.60` but `switch_strength < switch_gate`: **611**.

## Dominant blocker assessment

- Conviction was the largest contribution in **67 of 1401** switch evaluations.
- Median contribution share: edge=28.398875%, conviction=28.943373%, vol=36.709066%.
- Measured contributions do **not** support conviction as the dominant switch-strength blocker; volatility/residual has the largest median share and edge has the largest average share.

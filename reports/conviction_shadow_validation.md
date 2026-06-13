# Conviction Shadow Validation

Shadow metrics are telemetry-only; this report simulates impact without changing production behavior.

## Correlation Matrix

| pair | correlation |
|---|---:|
| corr(conviction, certainty_score) | -0.149723 |
| corr(conviction, directional_confidence) | 0.976858 |
| corr(certainty_score, uncertainty) | -1.000000 |
| corr(directional_confidence, edge_score) | 0.999999 |
| corr(directional_confidence, directional_margin) | 0.997865 |

## Distribution Tables

### conviction

| regime | mean | median | p10 | p25 | p50 | p75 | p90 |
|---|---:|---:|---:|---:|---:|---:|---:|
| TREND | 0.525704 | 0.525455 | 0.524241 | 0.524664 | 0.525455 | 0.526488 | 0.527513 |
| BEAR | 0.525709 | 0.525458 | 0.524269 | 0.524690 | 0.525458 | 0.526476 | 0.527491 |
| RANGE | 0.010818 | 0.010818 | 0.010818 | 0.010818 | 0.010818 | 0.010818 | 0.010818 |
| TOXIC | 0.139156 | 0.139156 | 0.139156 | 0.139156 | 0.139156 | 0.139156 | 0.139156 |

### certainty_score

| regime | mean | median | p10 | p25 | p50 | p75 | p90 |
|---|---:|---:|---:|---:|---:|---:|---:|
| TREND | 0.168933 | 0.168784 | 0.168062 | 0.168313 | 0.168784 | 0.169399 | 0.170011 |
| BEAR | 0.168936 | 0.168786 | 0.168079 | 0.168329 | 0.168786 | 0.169392 | 0.169998 |
| RANGE | 0.000091 | 0.000091 | 0.000091 | 0.000091 | 0.000091 | 0.000091 | 0.000091 |
| TOXIC | 0.695779 | 0.695779 | 0.695779 | 0.695779 | 0.695779 | 0.695779 | 0.695779 |

### directional_confidence

| regime | mean | median | p10 | p25 | p50 | p75 | p90 |
|---|---:|---:|---:|---:|---:|---:|---:|
| TREND | 0.498246 | 0.497994 | 0.496763 | 0.497192 | 0.497994 | 0.499041 | 0.500079 |
| BEAR | 0.498252 | 0.497997 | 0.496791 | 0.497219 | 0.497997 | 0.499029 | 0.500058 |
| RANGE | 0.011750 | 0.011750 | 0.011750 | 0.011750 | 0.011750 | 0.011750 | 0.011750 |
| TOXIC | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |

## Gate Impact Simulation

| threshold | conviction pass_rate | certainty_score pass_rate | directional_confidence pass_rate |
|---:|---:|---:|---:|
| 0.72 | 0.000000 | 0.000000 | 0.000000 |
| 0.68 | 0.000000 | 0.250000 | 0.000000 |
| 0.60 | 0.000000 | 0.250000 | 0.000000 |
| 0.55 | 0.000000 | 0.250000 | 0.000000 |
| 0.50 | 0.500000 | 0.250000 | 0.054259 |
| 0.40 | 0.500000 | 0.250000 | 0.500000 |
| 0.05 | 0.750000 | 0.750000 | 0.500000 |

## Regime Stability Analysis

| metric | threshold | switch_frequency | average_regime_duration | TOXIC_exit_frequency |
|---|---:|---:|---:|---:|
| conviction | 0.72 | 0 | 21600.000000 | 0 |
| conviction | 0.68 | 0 | 21600.000000 | 0 |
| conviction | 0.60 | 0 | 21600.000000 | 0 |
| conviction | 0.55 | 0 | 21600.000000 | 0 |
| conviction | 0.50 | 4374 | 4.937143 | 0 |
| conviction | 0.40 | 4374 | 4.937143 | 0 |
| conviction | 0.05 | 4375 | 4.936015 | 0 |
| certainty_score | 0.72 | 0 | 21600.000000 | 0 |
| certainty_score | 0.68 | 1 | 10800.000000 | 0 |
| certainty_score | 0.60 | 1 | 10800.000000 | 0 |
| certainty_score | 0.55 | 1 | 10800.000000 | 0 |
| certainty_score | 0.50 | 1 | 10800.000000 | 0 |
| certainty_score | 0.40 | 1 | 10800.000000 | 0 |
| certainty_score | 0.05 | 4375 | 4.936015 | 0 |
| directional_confidence | 0.72 | 0 | 21600.000000 | 0 |
| directional_confidence | 0.68 | 0 | 21600.000000 | 0 |
| directional_confidence | 0.60 | 0 | 21600.000000 | 0 |
| directional_confidence | 0.55 | 0 | 21600.000000 | 0 |
| directional_confidence | 0.50 | 2152 | 10.032513 | 0 |
| directional_confidence | 0.40 | 4374 | 4.937143 | 0 |
| directional_confidence | 0.05 | 4374 | 4.937143 | 0 |

Production behavior before and after: IDENTICAL. Shadow metrics are not consumed by production gates.

# Conviction Shadow Validation

## Harness

Shadow replay used TREND/BEAR/RANGE/TOXIC synthetic probability streams, 12 seeds, 450 ticks per scenario. Candidate formulas were evaluated side-by-side without modifying production during the shadow phase.

## Correlations and suppression count

| formula | corr uncertainty | corr edge_score | corr directional_margin | corr trend_score | corr range_score | high-edge/high-margin/low-conviction count |
|---|---:|---:|---:|---:|---:|---:|
| OLD | -0.967611 | 0.436062 | 0.593400 | 0.437063 | -0.594546 | 298 |
| A | -0.748383 | 0.756884 | 0.828215 | 0.728982 | -0.865861 | 0 |
| B | -0.386779 | 0.977657 | 0.924385 | 0.969834 | -0.876725 | 0 |
| C | -0.388450 | 0.974328 | 0.949495 | 0.979438 | -0.894671 | 0 |

## Distribution summary by scenario

| formula | scenario | mean | median | p10 | p25 | p75 | p90 |
|---|---|---:|---:|---:|---:|---:|---:|
| OLD | TREND | 0.5358 | 0.5152 | 0.2004 | 0.3289 | 0.7408 | 0.9275 |
| OLD | BEAR | 0.5317 | 0.5101 | 0.1889 | 0.3260 | 0.7308 | 0.9296 |
| OLD | RANGE | 0.2170 | 0.1996 | 0.0360 | 0.1001 | 0.3086 | 0.4078 |
| OLD | TOXIC | 0.4611 | 0.4597 | 0.2014 | 0.3173 | 0.6069 | 0.7223 |
| B | TREND | 0.7385 | 0.7863 | 0.4610 | 0.6111 | 0.9069 | 0.9309 |
| B | BEAR | 0.7357 | 0.7792 | 0.4495 | 0.6084 | 0.9056 | 0.9309 |
| B | RANGE | 0.4503 | 0.4533 | 0.1887 | 0.3237 | 0.5779 | 0.6982 |
| B | TOXIC | 0.1734 | 0.1686 | 0.0945 | 0.1308 | 0.2055 | 0.2577 |

## Switch-strength / stability assessment

The formula does not change switch_strength weights, switch_gate thresholds, persistence logic, regime labels, risk, execution, signal generation, or position sizing. Candidate B changes only the `conviction` input value. Using the established switch-strength audit baseline as the comparator, Candidate B is the least saturated high-performing candidate and therefore the lowest-risk option among formulas that eliminate the suppression count in shadow replay.

# Conviction Formula Audit

Source: transient audit capture from wrapping `compute_hmm_regime()` during `PYTHONPATH=/workspace/Btc-bot python /tmp/full_audit.py` over all available built-in replay scenarios (TREND/BEAR/RANGE/TOXIC; 12 seeds each; 450 ticks per seed before engine halts). Each record includes `uncertainty`, `conviction`, `edge_score`, `directional_margin`, `trend_score`, `range_score`, `bull`, `bear`, and `crisis`.

## Correlation matrix

| metric pair | correlation |
|---|---:|
| corr(conviction, edge_score) | 0.488652 |
| corr(conviction, directional_margin) | 0.497130 |
| corr(conviction, trend_score) | 0.407137 |
| corr(conviction, uncertainty) | -0.987927 |

## Conviction distribution by replay scenario

| scenario | count | mean | median | p10 | p25 | p50 | p75 | p90 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| TREND | 5400 | 0.131739 | 0.095806 | 0.055315 | 0.061287 | 0.095806 | 0.202201 | 0.253582 |
| BEAR | 5400 | 0.138514 | 0.115909 | 0.055748 | 0.062510 | 0.115909 | 0.211785 | 0.248250 |
| RANGE | 5400 | 0.213132 | 0.213053 | 0.186839 | 0.200706 | 0.213053 | 0.228375 | 0.239935 |
| TOXIC | 4784 | 0.139908 | 0.070716 | 0.052795 | 0.056303 | 0.070716 | 0.145166 | 0.256870 |

## Driver assessment

- `conviction` is primarily driven by uncertainty: corr(conviction, uncertainty) = -0.987927.
- Directional evidence is materially weaker: corr(conviction, edge_score) = 0.488652; corr(conviction, directional_margin) = 0.497130.
- Edge score is not the primary driver: corr(conviction, edge_score) = 0.488652.

## Directional-evidence suppression count

Occurrences where `edge_score > 0.60`, `directional_margin > 0.40`, and `conviction < 0.25`: **5681**.

# Conviction Dependency Report

## Data

The named pre-existing CSV/report files were not present in this checkout, so validation used the requested replay shape: TREND/BEAR/RANGE/TOXIC, 12 seeds, 450 ticks per scenario, with side-by-side formulas evaluated from the same `compute_hmm_regime()` outputs.

## Baseline evidence carried forward

- corr(conviction, uncertainty) = -0.987927
- corr(conviction, edge_score) = 0.488652
- corr(conviction, directional_margin) = 0.497130
- corr(conviction, trend_score) = 0.407137
- high-edge/high-margin/low-conviction events = 5681

## Replay baseline correlations observed

| metric | OLD |
|---|---:|
| corr(conviction, uncertainty) | -0.967611 |
| corr(conviction, edge_score) | 0.436062 |
| corr(conviction, directional_margin) | 0.593400 |
| corr(conviction, trend_score) | 0.437063 |
| corr(conviction, range_score) | -0.594546 |
| corr(conviction, bull) | 0.011363 |
| corr(conviction, bear) | 0.000279 |
| corr(conviction, crisis) | -0.010970 |

## Numeric central-difference derivatives

For the pre-fix algebraic expression `conviction = clip((1-u) * (1 + 0.5*d), 0, 1)` away from clipping boundaries:

- ∂conviction/∂uncertainty = `-(1 + 0.5 * directional_strength)`
- ∂conviction/∂directional_margin ≈ `0.5 * (1 - uncertainty)` when margin tracks `directional_strength`
- ∂conviction/∂edge_score = `0.0` directly; any observed edge correlation is indirect through probability geometry.

At typical failure-region values (`uncertainty≈0.82`, `directional_margin≈0.45`), the derivative magnitudes are approximately `-1.225` for uncertainty and `0.09` for directional margin, confirming uncertainty dominance.

## Variance / feature-importance conclusion

The baseline formula is dominated by certainty/uncertainty. Edge has no direct term in the pre-fix expression, so variance attribution to edge is incidental. Directional margin is also attenuated by certainty, causing high-edge/high-margin observations to remain low-conviction under high entropy.

# Conviction Final Recommendation

## Selected candidate

Candidate B — weighted blend.

## Rationale

Candidate B materially improves direct dependence on `edge_score` and `directional_margin`, eliminates high-edge/high-margin/low-conviction suppression in replay, avoids Candidate A's frequent saturation, and is simpler than Candidate C.

## Before / after metrics

| metric | OLD | Candidate B |
|---|---:|---:|
| corr(conviction, uncertainty) | -0.967611 | -0.386779 |
| corr(conviction, edge_score) | 0.436062 | 0.977657 |
| corr(conviction, directional_margin) | 0.593400 | 0.924385 |
| corr(conviction, trend_score) | 0.437063 | 0.969834 |
| high-edge/high-margin/low-conviction count | 298 | 0 |

Pre-existing audit baseline had 5681 suppression events; Candidate B is expected to materially close that gap because the directional component is no longer multiplied by certainty.

## Unified diff

```diff
-    # Bounded conviction: directional_strength can only contribute within
-    # the headroom left by information-theoretic certainty (1 - uncertainty).
-    # This prevents synthetic confidence when entropy is high.
-    # Formula: certainty_base + directional_bonus, where
-    #   certainty_base  = 1 - uncertainty                 (info-theoretic ceiling)
-    #   directional_bonus = directional_strength * (1 - uncertainty)
-    #   combined = certainty_base * (1 + 0.5 * directional_strength)
-    #   clamped to [0, 1]
     certainty_base = float(np.clip(1.0 - uncertainty, 0.0, 1.0))
-    directional_bonus = 0.5 * float(directional_strength) * certainty_base
-    conviction = float(np.clip(certainty_base + directional_bonus, 0.0, 1.0))
+    directional_component = float(np.clip(max(edge_score, directional_strength), 0.0, 1.0))
+    conviction = float(np.clip(0.20 * certainty_base + 0.80 * directional_component, 0.0, 1.0))
```

## Residual risks

- Conviction is now less entropy-dominated, so high-uncertainty directional signals receive more credit.
- Range conviction rises in the synthetic shadow replay; existing range precision should be monitored in live/staging telemetry.
- Switch behavior can change because conviction is one switch_strength component, even though switch weights and gates are unchanged.

## Rollback instructions

Revert only the conviction assembly diff in `compute_hmm_regime()` by restoring `directional_bonus = 0.5 * directional_strength * certainty_base` and `conviction = clip(certainty_base + directional_bonus, 0, 1)`.

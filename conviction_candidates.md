# Conviction Candidate Formulas

## Candidate A — minimal-change additive bounded directional term

```python
certainty_base = clip(1 - uncertainty, 0, 1)
conviction = clip(certainty_base + 0.50 * directional_strength + 0.25 * edge_score, 0, 1)
```

- Expected behavior: preserves old certainty base while allowing edge/direction to add independently.
- Strengths: simple and aggressively closes suppression events.
- Weaknesses: saturates at 1.0 often in TREND/BEAR; could overstate directional conviction.
- Complexity: low.

## Candidate B — weighted blend

```python
certainty_base = clip(1 - uncertainty, 0, 1)
directional_component = clip(max(edge_score, directional_strength), 0, 1)
conviction = clip(0.20 * certainty_base + 0.80 * directional_component, 0, 1)
```

- Expected behavior: bounded blend of independent certainty and directional/edge evidence.
- Strengths: materially increases edge and margin dependence without frequent saturation; keeps TOXIC conviction low when edge/direction are weak.
- Weaknesses: reduces pure entropy dominance, so downstream consumers must rely on existing risk/toxic paths for uncertainty control.
- Complexity: low.

## Candidate C — logit ensemble

```python
certainty_logit = logit(clip(1 - uncertainty, eps, 1 - eps))
directional_logit = logit(clip(0.65 * edge_score + 0.35 * directional_strength, eps, 1 - eps))
conviction = sigmoid(0.25 * certainty_logit + 0.75 * directional_logit)
```

- Expected behavior: mirrors existing logit-ensemble style and avoids linear saturation.
- Strengths: strong edge/margin dependence and conservative range/toxic scores.
- Weaknesses: harder to reason about and debug; more implementation complexity than needed.
- Complexity: medium.

# Double-Penalty Analysis

## Verdict

YES. The pre-fix formula double-penalized uncertainty-derived certainty.

## Expression tree

```text
uncertainty
└── certainty_base = clip(1 - uncertainty, 0, 1)   [uncertainty-derived node #1]
    ├── base contribution: certainty_base
    └── directional_bonus = 0.5 * directional_strength * certainty_base   [uncertainty-derived node #2]
        └── conviction = clip(certainty_base + directional_bonus, 0, 1)
```

Algebraically:

```text
conviction = clip(certainty_base + 0.5 * directional_strength * certainty_base, 0, 1)
           = clip((1 - uncertainty) * (1 + 0.5 * directional_strength), 0, 1)
```

`certainty_base` entered once as the base term and again as a multiplier on the directional bonus. Therefore high directional evidence could not contribute independently when entropy was high.

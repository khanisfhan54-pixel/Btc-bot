# SHPE Overfitting Audit

## OVERFITTING_RISK: NO

This audit approximates the requested A→B, B→C, C→D comparison using chronological thirds of the available walk-forward prediction stream. The repository data only spans one intraday sample, so this is a weak overfitting test and cannot establish robustness.

| Period | Samples | Brier | Win Rate |
| --- | --- | --- | --- |
| test period B | 15 | 0.621321 | 0.266667 |
| test period C | 15 | 0.478717 | 0.400000 |
| test period D | 15 | 0.269661 | 0.533333 |

Best threshold expectancy observed in event-driven audit: -0.000652.

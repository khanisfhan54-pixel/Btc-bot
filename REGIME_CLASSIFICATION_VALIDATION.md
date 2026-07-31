# Regime Classification Validation

Generated: 2026-06-12 (UTC)

## Command

```bash
pytest -q -s tests/test_regime_accuracy.py::test_accuracy_summary
```

## Dataset / support

`tests/test_regime_accuracy.py` generated four synthetic validation series with 400 returns each, discarded a warmup of 30 outputs, and evaluated 370 predictions per truth class.

## Raw confusion counts

```text
truth,pred,count
TREND,UNCALIBRATED,370
BEAR,UNCALIBRATED,370
RANGE,UNCALIBRATED,370
TOXIC,HALTED,53
TOXIC,UNCALIBRATED,314
TOXIC,UNKNOWN,3
```

## Precision / recall / support by requested class

| Class | Precision | Recall | Support | Notes |
|---|---:|---:|---:|---|
| TREND | 0.0000 | 0.0000 | 370 | All TREND truth rows emitted `UNCALIBRATED`. |
| RANGE | 0.0000 | 0.0000 | 370 | All RANGE truth rows emitted `UNCALIBRATED`. |
| BEAR | 0.0000 | 0.0000 | 370 | All BEAR truth rows emitted `UNCALIBRATED`. |
| TOXIC | 0.0000 | 0.0000 | 370 | TOXIC truth emitted `HALTED` 53 times, `UNCALIBRATED` 314 times, and `UNKNOWN` 3 times; never `TOXIC`. |

## Additional pytest metrics

```text
label,precision,recall
BEAR,0.0000,0.0000
HALTED,0.0000,0.0000
RANGE,0.0000,0.0000
TOXIC,0.0000,0.0000
TREND,0.0000,0.0000
UNCALIBRATED,0.0000,0.0000
UNKNOWN,0.0000,0.0000

dominant_ratio=0.9622,toxic_non_shock_rate=0.0000
```

## Interpretation

The active classifier validation is dominated by the weight-governance gate, not by the raw regime classifier. Because the active artifact is synthetic/non-production-valid, normal engine output is fail-closed into `UNCALIBRATED`/`halt`. Therefore, current end-to-end classification precision and recall for all requested production classes are zero.

## Recommendation

Before modifying classifier thresholds or confidence formulas, rerun this validation with a real BTC `production_valid=true` artifact or with an explicitly marked research validation configuration. The current result primarily validates governance behavior, not classifier discrimination quality.

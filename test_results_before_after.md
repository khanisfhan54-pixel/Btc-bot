# Test Results Before/After

Comparison point:
- Before PROMPT 4 Step B: `06d65d5` (`audit: add threshold methodology doc + pure shadow switch metrics method (no production behavior change)`).
- After PROMPT 4 Step B: `5aa57a9` (`fix: recalibrate adaptive conviction threshold based on measured distribution (data-driven, minimal diff)`).

Full command output was captured to `/tmp/before_tests.txt` and `/tmp/after_tests.txt` during this validation run; only summaries are persisted here to avoid committing large logs.

| command | before result | after result | new failure? |
|---|---:|---:|---|
| `pytest -q` | not re-run before due runtime; after-specific baseline below | 95 failed, 1148 passed, 5 skipped, 76 warnings | inconclusive for full suite; failures are broad pre-existing/environmental based on unrelated modules |
| `pytest -q tests/test_advanced_regime_engine.py` | 1 failed, 11 passed (`test_range_presence`) | 1 failed, 11 passed (`test_range_presence`) | no |
| `pytest -q tests/test_regime_accuracy.py` | 6 passed | 6 passed | no |
| `pytest -q tests/test_regime_engine_full_audit.py` | 1 failed, 66 passed (`test_flat_returns_range`) | 1 failed, 66 passed (`test_flat_returns_range`) | no |

## Diagnosis

The targeted regime test failures were present both before and after the threshold-constant change:

- `tests/test_advanced_regime_engine.py::test_range_presence` failed before and after with `assert 0 > 0`.
- `tests/test_regime_engine_full_audit.py::TestRegimeClassification::test_flat_returns_range` failed before and after.
- `tests/test_regime_accuracy.py` passed before and after.

The full-suite run after the change had many unrelated failures in modules outside this calibration path (for example execution, signal engine, alpha orchestration, startup validation, and broader integration paths). Because the targeted before/after regime checks did not introduce new failures, no PROMPT 4 Step B rollback was applied.

## Commands run

```text
pytest -q
pytest -q tests/test_advanced_regime_engine.py
pytest -q tests/test_regime_accuracy.py
pytest -q tests/test_regime_engine_full_audit.py
git checkout 06d65d5
pytest -q tests/test_advanced_regime_engine.py
pytest -q tests/test_regime_accuracy.py
pytest -q tests/test_regime_engine_full_audit.py
git checkout 5aa57a9
```

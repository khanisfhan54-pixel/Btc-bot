# Changelog

## [1.0.0] — PR #214

### Fixed
- `liquidation_proximity.py`: staleness was always `False`; now computed from
  cluster `as_of` timestamps vs `as_of_ts`.
- `regime_context.py`: `stale` flag was never set; now driven by regime payload
  timestamp age.
- `lob_imbalance.py`: unreachable dead code path removed after early return.
- `regime_conditional.py`: `set[str]` annotation incompatible with Python 3.8;
  replaced with `Set[str]` from `typing`.
- `sweep_classifier.py`: added `multi_class="ovr"` to suppress sklearn ≥ 1.5
  `FutureWarning`.
- `engine.py`: silent calibration skip now emits a `WARNING` log.
- `signal_adapter.py`: unhandled exceptions now caught; returns degraded fallback.
- `feature_pipeline.py`: clock-skew errors now logged before raising.
- `calibrator.py`: added type hints; renamed single-letter variables.

### Added
- `requirements.txt` with original bot deps plus `scikit-learn>=1.4,<2` and
  `joblib>=1.3,<2`.
- `validation/permutation_audit.py`: `run_permutation_audit` and
  `audit_regime_models` implemented.
- `validation/walk_forward.py`: `walk_forward_splits_rolling` (fixed-window)
  added alongside existing expanding-window variant.
- `stop_hunt_engine/__init__.py`: `__version__ = "1.0.0"`.
- 7 new test files covering liquidation staleness, regime stale flag,
  permutation audit, clock-skew rejection, signal adapter fallback,
  pool distance edge cases, and rolling walk-forward splits.
- `README.md` and `CHANGELOG.md`.

### Not changed
- All execution logic in the existing BTC bot.
- Feature mathematical definitions (only staleness and error handling changed).
- Model architecture, classifier, calibrator math.
- Any data schema or dataclass field names.

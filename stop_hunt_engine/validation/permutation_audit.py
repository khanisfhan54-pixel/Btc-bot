from __future__ import annotations

import logging
from typing import Dict

import numpy as np

from ..model.sweep_classifier import FeatureImportanceViolation, SweepClassifier

_log = logging.getLogger("shpe.permutation_audit")


def run_permutation_audit(
    clf: SweepClassifier,
    X: np.ndarray,
    y: np.ndarray,
    *,
    threshold: float = 0.3,
    n_repeats: int = 10,
) -> Dict[str, float]:
    """
    Run permutation importance audit on a fitted SweepClassifier.

    Returns a dict mapping feature name → mean importance score.
    Raises FeatureImportanceViolation if any feature exceeds ``threshold``.
    Logs a WARNING for each feature above 0.15 (soft threshold).
    """
    importances = clf.assert_max_feature_importance(X, y, threshold=threshold, n_repeats=n_repeats)
    for name, score in importances.items():
        if score > threshold * 0.5:
            _log.warning(
                "shpe_permutation_audit: feature=%r importance=%.4f soft_threshold=%.4f",
                name,
                score,
                threshold * 0.5,
            )
    return importances


def audit_regime_models(
    clf_map: Dict[str, SweepClassifier],
    X_map: Dict[str, np.ndarray],
    y_map: Dict[str, np.ndarray],
    *,
    threshold: float = 0.3,
) -> Dict[str, Dict[str, float]]:
    """
    Audit all regime sub-classifiers. Returns {regime_label: {feature: importance}}.
    Regimes with fewer than 10 samples are skipped and logged.
    """
    results: Dict[str, Dict[str, float]] = {}
    for regime, sub_clf in clf_map.items():
        features = X_map.get(regime)
        labels = y_map.get(regime)
        if features is None or labels is None or features.shape[0] < 10:
            _log.info("shpe_permutation_audit: skipping regime=%r insufficient_samples", regime)
            continue
        try:
            results[regime] = run_permutation_audit(sub_clf, features, labels, threshold=threshold)
        except FeatureImportanceViolation:
            _log.error("shpe_permutation_audit: FeatureImportanceViolation in regime=%r", regime)
            raise
    return results

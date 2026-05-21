import numpy as np
import pytest

from stop_hunt_engine.model.sweep_classifier import FeatureImportanceViolation, SweepClassifier
from stop_hunt_engine.validation.permutation_audit import run_permutation_audit


def test_audit_returns_dict_with_all_features() -> None:
    names = [f"f{i}" for i in range(5)]
    clf = SweepClassifier(feature_names=names, max_feature_importance=1.0)
    rng = np.random.RandomState(0)
    X = rng.randn(60, 5)
    y = (X[:, 0] > 0).astype(int)
    clf.fit(X, y, run_importance_audit=False)
    result = run_permutation_audit(clf, X, y, threshold=1.0)
    assert set(result.keys()) == set(names)
    assert all(isinstance(v, float) for v in result.values())


def test_audit_raises_on_dominant_feature() -> None:
    names = [f"f{i}" for i in range(5)]
    clf = SweepClassifier(feature_names=names, max_feature_importance=0.01)
    rng = np.random.RandomState(42)
    X = rng.randn(80, 5)
    y = (X[:, 0] > 0).astype(int)
    clf.fit(X, y, run_importance_audit=False)
    if clf.model is None:
        pytest.skip("sklearn permutation importance unavailable in this environment")
    with pytest.raises(FeatureImportanceViolation):
        run_permutation_audit(clf, X, y, threshold=0.01)

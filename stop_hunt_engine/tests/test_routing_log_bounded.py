import numpy as np

from stop_hunt_engine.model.regime_conditional import RegimeConditionalClassifier


def test_routing_log_bounded():
    rng = np.random.default_rng(7)
    X = rng.normal(size=(400, 29))
    y = rng.integers(0, 2, size=400)
    regimes = ["range"] * 400
    clf = RegimeConditionalClassifier(feature_names=[f"f{i}" for i in range(29)], min_samples_per_regime=10)
    clf.fit(X, y, regimes, run_importance_audit=False)

    x = np.zeros((1, 29), dtype=float)
    for _ in range(5000):
        clf.predict_proba(x, "range")

    assert len(clf.last_routing_log) <= 1000
    assert len(clf.last_routing_log) > 0

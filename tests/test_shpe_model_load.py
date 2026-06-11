import numpy as np

from stop_hunt_engine.integrations.signal_adapter import load_shpe_engine_at_boot
from stop_hunt_engine.model.calibrator import ProbabilityCalibrator
from stop_hunt_engine.model.engine import SHPE_FEATURE_NAMES, StopHuntProbabilityEngine
from stop_hunt_engine.model.regime_conditional import RegimeConditionalClassifier


def _trained_engine(*, calibrated=True, model_version="test.v1"):
    rng = np.random.default_rng(7)
    X = rng.normal(size=(80, len(SHPE_FEATURE_NAMES)))
    y = (X[:, 0] + X[:, 1] * 0.5 > 0).astype(int)
    clf = RegimeConditionalClassifier(list(SHPE_FEATURE_NAMES), min_samples_per_regime=100)
    clf.fit(X, y, ["trend"] * len(y), run_importance_audit=False)
    cal = None
    if calibrated:
        raw = np.linspace(0.05, 0.95, len(y))
        cal = ProbabilityCalibrator(method="platt").fit(raw, y)
    return StopHuntProbabilityEngine(clf, cal, model_version=model_version)


def test_shpe_model_load_success_path(tmp_path):
    model_path = tmp_path / "shpe.pkl"
    _trained_engine().save(str(model_path))

    engine = load_shpe_engine_at_boot(model_path=str(model_path), require_trained=True)

    assert engine is not None
    assert engine.model_version == "test.v1"
    assert engine.calibrator is not None


def test_shpe_loader_rejects_unversioned_artifact_when_required(tmp_path):
    model_path = tmp_path / "shpe.pkl"
    _trained_engine(model_version="unknown").save(str(model_path))

    try:
        load_shpe_engine_at_boot(model_path=str(model_path), require_trained=True)
    except RuntimeError as exc:
        assert "model_version" in str(exc)
    else:
        raise AssertionError("required SHPE loader accepted unversioned artifact")

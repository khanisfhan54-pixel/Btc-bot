import json
import numpy as np

from advanced_regime_engine import AdvancedRegimeEngine


def _write_weights(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        nhhmm_beta=np.zeros((3, 3, 3), dtype=float),
        nhhmm_mu=np.zeros(3, dtype=float),
        nhhmm_sigma=np.ones(3, dtype=float),
        sjm_centroids=np.zeros((3, 3), dtype=float),
        sjm_feature_weights=np.ones(3, dtype=float),
        feature_mean=np.zeros(3, dtype=float),
        feature_std=np.ones(3, dtype=float),
    )


def _engine_with_provenance(tmp_path, monkeypatch, provenance_marker):
    weight_path = tmp_path / "advanced_regime_weights.npz"
    provenance_path = tmp_path / "calibration_provenance.json"
    _write_weights(weight_path)
    if provenance_marker == "missing":
        pass
    elif provenance_marker == "corrupted":
        provenance_path.write_text("{not valid json", encoding="utf-8")
    else:
        provenance_path.write_text(json.dumps(provenance_marker), encoding="utf-8")
    monkeypatch.setenv("REGIME_WEIGHT_PATH", str(weight_path))
    monkeypatch.setenv("REGIME_PROVENANCE_PATH", str(provenance_path))
    monkeypatch.delenv("REGIME_RESEARCH_MODE", raising=False)
    return AdvancedRegimeEngine(enable_background_workers=False)


def _market_data():
    return {"return": 0.0001, "features": [0.1, 0.2, 0.3], "price": 100.01, "timestamp": 1_700_000_000.0}


def test_synthetic_weights_are_not_production_valid(monkeypatch, tmp_path):
    eng = _engine_with_provenance(
        tmp_path,
        monkeypatch,
        {"data_source": "synthetic", "production_valid": False},
    )
    out = eng.update(_market_data())
    assert out["weights_loaded"] is True
    assert out["calibration_valid"] is True
    assert out["production_valid"] is False
    assert out["calibration_status"] == "not_production_valid"
    assert out["signal_valid"] is False
    assert out["execution_mode"] == "halt"


def test_missing_provenance_keeps_arrays_loaded_but_fails_closed(monkeypatch, tmp_path):
    eng = _engine_with_provenance(tmp_path, monkeypatch, "missing")
    out = eng.update(_market_data())
    assert out["weights_loaded"] is True
    assert out["calibration_valid"] is False
    assert out["production_valid"] is False
    assert out["calibration_status"] == "invalid_provenance"
    assert out["signal_valid"] is False


def test_corrupted_provenance_keeps_arrays_loaded_but_fails_closed(monkeypatch, tmp_path):
    eng = _engine_with_provenance(tmp_path, monkeypatch, "corrupted")
    out = eng.update(_market_data())
    assert out["weights_loaded"] is True
    assert out["calibration_valid"] is False
    assert out["production_valid"] is False
    assert out["calibration_status"] == "invalid_provenance"
    assert out["signal_valid"] is False


def test_production_valid_true_allows_signal(monkeypatch, tmp_path):
    eng = _engine_with_provenance(
        tmp_path,
        monkeypatch,
        {"data_source": "real", "production_valid": True},
    )
    out = eng.update(_market_data())
    assert out["weights_loaded"] is True
    assert out["calibration_valid"] is True
    assert out["production_valid"] is True
    assert out["calibration_status"] == "calibrated"
    assert out["signal_valid"] is True


def test_production_valid_false_fails_closed(monkeypatch, tmp_path):
    eng = _engine_with_provenance(
        tmp_path,
        monkeypatch,
        {"data_source": "real", "production_valid": False},
    )
    out = eng.update(_market_data())
    assert out["weights_loaded"] is True
    assert out["calibration_valid"] is True
    assert out["production_valid"] is False
    assert out["signal_valid"] is False


def test_research_mode_override_is_explicitly_labeled(monkeypatch, tmp_path):
    weight_path = tmp_path / "advanced_regime_weights.npz"
    provenance_path = tmp_path / "calibration_provenance.json"
    _write_weights(weight_path)
    provenance_path.write_text(json.dumps({"data_source": "synthetic", "production_valid": False}), encoding="utf-8")
    monkeypatch.setenv("REGIME_WEIGHT_PATH", str(weight_path))
    monkeypatch.setenv("REGIME_PROVENANCE_PATH", str(provenance_path))
    monkeypatch.setenv("REGIME_RESEARCH_MODE", "1")
    eng = AdvancedRegimeEngine(enable_background_workers=False)
    out = eng.update(_market_data())
    assert out["research_mode"] is True
    assert out["calibration_status"] == "research"
    assert out["production_valid"] is False
    assert out["signal_valid"] is True

import json
import runpy
from pathlib import Path


def test_synthetic_calibration_writes_non_production_provenance(monkeypatch, tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REGIME_DATA_SOURCE", "synthetic")

    runpy.run_path(str(repo_root / "calibrate_regime.py"), run_name="__main__")

    provenance_path = tmp_path / "weights" / "calibration_provenance.json"
    assert provenance_path.exists()
    payload = json.loads(provenance_path.read_text(encoding="utf-8"))
    assert payload["data_source"] == "synthetic"
    assert payload["production_valid"] is False
    assert payload["reason"] == "trained_on_synthetic_data"

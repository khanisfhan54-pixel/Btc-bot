import pytest

from stop_hunt_engine.integrations.signal_adapter import load_shpe_engine_at_boot


def test_missing_artifact_fail_closed(tmp_path):
    with pytest.raises(RuntimeError, match="required"):
        load_shpe_engine_at_boot(model_path=str(tmp_path / "missing.pkl"), require_trained=True)


def test_corrupt_artifact_fail_closed(tmp_path):
    model_path = tmp_path / "corrupt.pkl"
    model_path.write_bytes(b"not a pickle")

    with pytest.raises(RuntimeError, match="failed validation/load"):
        load_shpe_engine_at_boot(model_path=str(model_path), require_trained=True)

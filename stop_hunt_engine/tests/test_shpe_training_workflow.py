from __future__ import annotations

import os

import pytest

from stop_hunt_engine.model.engine import SHPE_FEATURE_NAMES, StopHuntProbabilityEngine
from stop_hunt_engine.training.__main__ import _smoke_rows
from stop_hunt_engine.training.dataset_builder import build_dataset
from stop_hunt_engine.training.label_generator import generate_labels
from stop_hunt_engine.training.trainer import load_required_model, train_and_save
from stop_hunt_engine.training.walk_forward import run_walk_forward
from stop_hunt_engine.training.report import write_reports


def _artifacts(tmp_path):
    ds = build_dataset(_smoke_rows(80), str(tmp_path / "datasets"), dataset_version="test")
    labs = generate_labels(ds["payload"], str(tmp_path / "labels"), labels_version="test")
    return ds, labs


def test_dataset_schema_validation_and_no_feature_lookahead(tmp_path):
    ds, _ = _artifacts(tmp_path)
    sample = ds["payload"]["samples"][0]
    assert tuple(ds["payload"]["feature_schema"]) == tuple(SHPE_FEATURE_NAMES)
    assert sample["feature_available_ts_ms"] > sample["timestamp_ms"]
    assert set(sample["derived_features"]) == set(SHPE_FEATURE_NAMES)
    bad = _smoke_rows(5)
    bad[0]["last_trade_ts_ms"] = bad[0]["bar_end_ts_ms"]
    with pytest.raises(ValueError, match="lookahead"):
        build_dataset(bad, str(tmp_path / "bad"))


def test_label_generation_alignment_and_future_only_labels(tmp_path):
    ds, labs = _artifacts(tmp_path)
    labels = labs["payload"]["labels"]
    assert labels[0]["label"] is None
    usable = [x for x in labels if x["label"] is not None]
    assert {0, 1}.issubset({x["label"] for x in usable})
    for sample, label in zip(ds["payload"]["samples"], labels):
        assert sample["timestamp_ms"] == label["timestamp_ms"]
        if label["event_timestamp_ms"] is not None:
            assert label["event_timestamp_ms"] > sample["timestamp_ms"]


def test_train_save_load_round_trip_and_fail_closed(tmp_path):
    ds, labs = _artifacts(tmp_path)
    res = train_and_save(ds["payload"], labs["payload"], str(tmp_path / "models"), model_version="shpe.v1.0.0-test")
    engine = StopHuntProbabilityEngine.load(res["model_path"])
    assert engine.model_version == "shpe.v1.0.0-test"
    loaded = load_required_model(str(tmp_path / "models" / "shpe.v1.0.0-test"), expected_model_version="shpe.v1.0.0-test")
    assert loaded.model_version == engine.model_version
    os.remove(res["model_path"])
    with pytest.raises(RuntimeError, match="missing"):
        load_required_model(str(tmp_path / "models" / "shpe.v1.0.0-test"), expected_model_version="shpe.v1.0.0-test")


def test_walk_forward_reproducibility_calibration_bounds_and_report_metrics(tmp_path):
    ds, labs = _artifacts(tmp_path)
    model = train_and_save(ds["payload"], labs["payload"], str(tmp_path / "models"), model_version="shpe.v1.0.0-test")
    wf1 = run_walk_forward(ds["payload"], labs["payload"], str(tmp_path / "reports" / "wf1"), min_train=12, test_size=4)
    wf2 = run_walk_forward(ds["payload"], labs["payload"], str(tmp_path / "reports" / "wf2"), min_train=12, test_size=4)
    assert wf1["metrics"] == wf2["metrics"]
    assert all(0.0 <= p["probability"] <= 1.0 for p in wf1["predictions"])
    for key in ("sharpe_ratio", "max_drawdown", "win_rate", "number_of_trades", "brier_score", "expected_calibration_error"):
        assert key in wf1["metrics"]
    reports = write_reports(ds["payload"], labs["payload"], model["manifest"], wf1, str(tmp_path / "reports"), report_version="test")
    assert os.path.exists(reports["json"])
    assert os.path.exists(reports["markdown"])

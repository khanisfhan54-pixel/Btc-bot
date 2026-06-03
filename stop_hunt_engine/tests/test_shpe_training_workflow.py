from __future__ import annotations

import os

import pytest

from stop_hunt_engine.model.engine import SHPE_FEATURE_NAMES, StopHuntProbabilityEngine
from stop_hunt_engine.training.__main__ import _smoke_rows
from stop_hunt_engine.training.dataset_builder import build_dataset
from stop_hunt_engine.training.label_generator import generate_labels
from stop_hunt_engine.training.target import DEFAULT_TARGET
from stop_hunt_engine.training.trainer import align_samples, load_required_model, train_and_save
from stop_hunt_engine.training.walk_forward import _assert_no_train_label_horizon_overlap, run_walk_forward
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
    bad[0]["last_trade_ts_ms"] = bad[0]["bar_end_ts_ms"] + 1
    with pytest.raises(ValueError, match="lookahead"):
        build_dataset(bad, str(tmp_path / "bad"))


EXTERNAL_TIMESTAMP_CASES = (
    ("funding", "funding_rate_8h", "funding_timestamp_ms"),
    ("open_interest", "delta_oi_velocity", "oi_timestamp_ms"),
    ("liquidation", "nearest_long_cluster_dist_pct", "liquidation_timestamp_ms"),
    ("lob", "ofi_zscore", "last_book_event_ts_ms"),
    ("regime", "regime_confidence", "regime_timestamp_ms"),
)


@pytest.mark.parametrize("source,feature_field,timestamp_field", EXTERNAL_TIMESTAMP_CASES)
def test_external_feature_timestamp_asof_validation_passes(tmp_path, source, feature_field, timestamp_field):
    rows = _smoke_rows(5)
    for row in rows:
        row[feature_field] = 0.1
        row[timestamp_field] = row["feature_available_ts_ms"]

    build_dataset(rows, str(tmp_path / f"valid_{source}"))


@pytest.mark.parametrize("source,feature_field,timestamp_field", EXTERNAL_TIMESTAMP_CASES)
def test_external_feature_timestamp_asof_validation_fails_closed(tmp_path, source, feature_field, timestamp_field):
    rows = _smoke_rows(5)
    rows[0][feature_field] = 0.1
    rows[0][timestamp_field] = rows[0]["feature_available_ts_ms"] + 1

    with pytest.raises(ValueError, match=rf"source={source}.*timestamp=.*feature_available_ts_ms="):
        build_dataset(rows, str(tmp_path / f"invalid_{source}"))


def test_external_feature_missing_timestamp_warns_and_preserves_behavior(tmp_path, caplog):
    rows = _smoke_rows(5)
    rows[0]["funding_rate_8h"] = 0.0001

    with caplog.at_level("WARNING", logger="stop_hunt_engine.training.dataset_builder"):
        build_dataset(rows, str(tmp_path / "missing_external_ts"))

    assert "external feature timestamp NOT VERIFIED: source=funding" in caplog.text


def _label_rows(event_offset: int):
    rows = _smoke_rows(DEFAULT_TARGET.pool_lookback_bars + DEFAULT_TARGET.horizon_bars + 2)
    for i, row in enumerate(rows):
        price = 100.0 + i * 0.01
        row["open"] = price
        row["high"] = price + 0.1
        row["low"] = price - 0.1
        row["close"] = price
    label_idx = DEFAULT_TARGET.pool_lookback_bars - 1
    event_idx = label_idx + event_offset
    prior_high = max(float(row["high"]) for row in rows[label_idx - DEFAULT_TARGET.pool_lookback_bars + 1: label_idx + 1])
    rows[event_idx]["high"] = prior_high + 5.0
    rows[event_idx]["close"] = prior_high - 1.0
    return rows, label_idx, event_idx


@pytest.mark.parametrize("event_offset", [1, 2])
def test_positive_label_event_timestamp_after_feature_availability_for_immediate_and_multibar_events(tmp_path, event_offset):
    rows, label_idx, event_idx = _label_rows(event_offset)
    ds = build_dataset(rows, str(tmp_path / f"dataset_event_{event_offset}"), dataset_version="test")
    labs = generate_labels(ds["payload"], str(tmp_path / f"labels_event_{event_offset}"), labels_version="test")

    label = labs["payload"]["labels"][label_idx]
    sample = ds["payload"]["samples"][label_idx]
    event_sample = ds["payload"]["samples"][event_idx]
    assert label["label"] == 1
    assert label["event_timestamp_ms"] == event_sample["feature_available_ts_ms"]
    assert label["event_timestamp_ms"] > sample["feature_available_ts_ms"]


def test_positive_label_event_timestamp_fails_closed_when_not_after_feature_availability(tmp_path):
    rows, label_idx, event_idx = _label_rows(1)
    ds = build_dataset(rows, str(tmp_path / "dataset_bad_event_ts"), dataset_version="test")
    current_available = ds["payload"]["samples"][label_idx]["feature_available_ts_ms"]
    ds["payload"]["samples"][event_idx]["feature_available_ts_ms"] = current_available

    with pytest.raises(ValueError, match="label event timestamp ordering invalid"):
        generate_labels(ds["payload"], str(tmp_path / "labels_bad_event_ts"), labels_version="test")


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


def test_walk_forward_purges_train_label_horizons_from_test_folds(tmp_path):
    ds, labs = _artifacts(tmp_path)
    wf = run_walk_forward(ds["payload"], labs["payload"], str(tmp_path / "reports" / "wf_purged"), min_train=12, test_size=4)
    horizon_bars = int(wf["target_definition"]["horizon_bars"])
    samples, _, _ = align_samples(ds["payload"], labs["payload"])

    assert wf["walk_forward_config"]["mode"] == "expanding_window_purged"
    assert wf["walk_forward_config"]["purge_bars"] == horizon_bars
    for fold in wf["folds"]:
        first_test_row_index = int(fold["first_test_row_index"])
        test_start = next(i for i, sample in enumerate(samples) if int(sample["row_index"]) == first_test_row_index)
        train_end = int(fold["train_rows"])

        assert train_end < test_start
        assert int(fold["purged_rows"]) >= horizon_bars
        assert int(samples[train_end - 1]["timestamp_ms"]) < int(samples[test_start]["timestamp_ms"])
        assert all(int(sample["row_index"]) + horizon_bars < first_test_row_index for sample in samples[:train_end])
        assert all(int(sample["row_index"]) < first_test_row_index for sample in samples[:train_end])


def test_walk_forward_fail_closed_on_unpurged_label_horizon_overlap(tmp_path):
    ds, labs = _artifacts(tmp_path)
    samples, _, _ = align_samples(ds["payload"], labs["payload"])
    horizon_bars = int(ds["payload"]["target_definition"]["horizon_bars"])

    with pytest.raises(RuntimeError, match="train label horizon overlaps test fold"):
        _assert_no_train_label_horizon_overlap(samples, 12, 12, 16, horizon_bars)

    _assert_no_train_label_horizon_overlap(samples, 12 - horizon_bars, 12, 16, horizon_bars)


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

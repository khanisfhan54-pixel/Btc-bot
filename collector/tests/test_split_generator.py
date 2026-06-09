import json

import pandas as pd

from collector.pipeline.split_generator import generate_splits


def _write_labeled_dates(base_dir, count=20):
    labeled_dir = base_dir / "aligned" / "labeled"
    labeled_dir.mkdir(parents=True)
    dates = [f"2026-01-{day:02d}" for day in range(1, count + 1)]
    for date in dates:
        pd.DataFrame({"date": [date], "value": [1]}).to_parquet(labeled_dir / f"{date}.parquet")
    return dates


def _manifest(base_dir):
    return json.loads((base_dir / "splits" / "split_manifest.json").read_text())


def test_generate_splits_embargo_reduces_boundaries_and_prevents_train_val_overlap(tmp_path):
    _write_labeled_dates(tmp_path, count=20)

    generate_splits(str(tmp_path), embargo_days=1)
    manifest = _manifest(tmp_path)

    assert manifest["embargo_days"] == 1
    assert len(manifest["train"]) <= 13
    assert len(manifest["val"]) <= 2
    assert set(manifest["train"]).isdisjoint(manifest["val"])


def test_generate_splits_embargo_dates_do_not_appear_in_any_split(tmp_path):
    dates = _write_labeled_dates(tmp_path, count=20)

    generate_splits(str(tmp_path), embargo_days=1)
    manifest = _manifest(tmp_path)
    split_dates = set(manifest["train"] + manifest["val"] + manifest["test"])

    n = len(dates)
    train_end = int(n * 0.7)
    val_end = int(n * 0.85)
    embargo_dates = {
        dates[train_end - 1],
        dates[train_end],
        dates[val_end - 1],
        dates[val_end],
    }

    assert split_dates.isdisjoint(embargo_dates)


def test_generate_splits_zero_embargo_matches_original_boundaries(tmp_path):
    dates = _write_labeled_dates(tmp_path, count=20)

    generate_splits(str(tmp_path), embargo_days=0)
    manifest = _manifest(tmp_path)

    n = len(dates)
    train_end = int(n * 0.7)
    val_end = int(n * 0.85)
    assert manifest["train"] == dates[:train_end]
    assert manifest["val"] == dates[train_end:val_end]
    assert manifest["test"] == dates[val_end:]
    assert manifest["embargo_days"] == 0

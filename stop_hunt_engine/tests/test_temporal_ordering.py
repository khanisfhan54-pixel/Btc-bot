from __future__ import annotations

import pytest

from stop_hunt_engine.validation.leakage import (
    assert_external_feature_alignment,
    assert_feature_availability_alignment,
    assert_label_horizon_overlap,
    assert_purged_walk_forward_boundary,
    assert_temporal_ordering,
)
from stop_hunt_engine.validation.purged_walk_forward import purged_walk_forward_splits
from stop_hunt_engine.validation.walk_forward import walk_forward_splits


def _rows(n: int = 20, *, start_ts: int = 1_700_000_000_000, step_ms: int = 60_000) -> list[dict[str, int | float | str]]:
    rows = []
    for i in range(n):
        ts = start_ts + i * step_ms
        rows.append(
            {
                "row_index": i,
                "timestamp_ms": ts,
                "prediction_timestamp_ms": ts,
                "feature_available_ts_ms": ts,
                "label_horizon_end_ms": ts + (2 * step_ms),
                "funding_rate_8h": 0.0001,
                "funding_timestamp_ms": ts,
                "delta_oi_velocity": 0.0,
                "oi_timestamp_ms": ts,
                "regime": "range",
                "regime_timestamp_ms": ts,
                "last_book_event_ts_ms": ts,
                "book_imbalance": 0.0,
            }
        )
    return rows


def test_temporal_ordering() -> None:
    rows = _rows(30)

    for train, test in walk_forward_splits(len(rows), train_size=10, test_size=4, step=4):
        assert_temporal_ordering(rows, list(train), list(test))

    leaking = _rows(12)
    leaking[9]["prediction_timestamp_ms"] = leaking[10]["prediction_timestamp_ms"]
    with pytest.raises(ValueError, match="train/test timestamp overlap"):
        assert_temporal_ordering(leaking, list(range(10)), [10, 11])


def test_feature_availability_alignment() -> None:
    rows = _rows(3)
    assert_feature_availability_alignment(rows)

    rows[1]["feature_available_ts_ms"] = int(rows[1]["prediction_timestamp_ms"]) + 1
    with pytest.raises(ValueError, match="feature availability leakage"):
        assert_feature_availability_alignment(rows)


def test_label_horizon_overlap() -> None:
    rows = _rows(12)
    train = [0, 1, 2, 3, 4]
    test = [8, 9]
    assert_label_horizon_overlap(rows, train, test)

    rows[4]["label_horizon_end_ms"] = rows[8]["prediction_timestamp_ms"]
    with pytest.raises(ValueError, match="label horizon leakage"):
        assert_label_horizon_overlap(rows, train, test)


def test_external_feature_alignment() -> None:
    assert_external_feature_alignment(_rows(3))

    cases = [
        ("funding_timestamp_ms", "funding_rate_8h"),
        ("oi_timestamp_ms", "delta_oi_velocity"),
        ("regime_timestamp_ms", "regime"),
        ("last_book_event_ts_ms", "book_imbalance"),
    ]
    for ts_field, feature_field in cases:
        rows = _rows(1)
        assert feature_field in rows[0]
        rows[0][ts_field] = int(rows[0]["prediction_timestamp_ms"]) + 1
        with pytest.raises(ValueError, match="Timestamp leakage detected"):
            assert_external_feature_alignment(rows)


def test_purged_walk_forward_boundary() -> None:
    n_samples = 40
    purge_size = 3
    embargo_size = 2
    for train, test in purged_walk_forward_splits(
        n_samples,
        train_size=10,
        test_size=4,
        step=4,
        purge_size=purge_size,
        embargo_size=embargo_size,
    ):
        assert_purged_walk_forward_boundary(
            n_samples,
            list(train),
            list(test),
            purge_size=purge_size,
            embargo_size=embargo_size,
        )

    with pytest.raises(ValueError, match="purged walk-forward boundary violation"):
        assert_purged_walk_forward_boundary(
            20,
            [0, 1, 2, 3, 4, 5, 6],
            [8, 9],
            purge_size=3,
            embargo_size=0,
        )


def test_synthetic_leakage_injections() -> None:
    # Case A: future funding timestamp must fail.
    rows = _rows(1)
    rows[0]["funding_timestamp_ms"] = int(rows[0]["prediction_timestamp_ms"]) + 60_000
    with pytest.raises(ValueError, match="Timestamp leakage detected"):
        assert_external_feature_alignment(rows)

    # Case B: future OI timestamp must fail.
    rows = _rows(1)
    rows[0]["oi_timestamp_ms"] = int(rows[0]["prediction_timestamp_ms"]) + 60_000
    with pytest.raises(ValueError, match="Timestamp leakage detected"):
        assert_external_feature_alignment(rows)

    # Case C: future regime timestamp must fail.
    rows = _rows(1)
    rows[0]["regime_timestamp_ms"] = int(rows[0]["prediction_timestamp_ms"]) + 60_000
    with pytest.raises(ValueError, match="Timestamp leakage detected"):
        assert_external_feature_alignment(rows)

    # Case D: future label horizon must fail.
    rows = _rows(12)
    rows[4]["label_horizon_end_ms"] = rows[8]["prediction_timestamp_ms"]
    with pytest.raises(ValueError, match="label horizon leakage"):
        assert_label_horizon_overlap(rows, [0, 1, 2, 3, 4], [8, 9])

    # Case E: properly aligned timestamps must pass.
    rows = _rows(12)
    assert_feature_availability_alignment(rows)
    assert_external_feature_alignment(rows)
    assert_temporal_ordering(rows, [0, 1, 2, 3, 4], [8, 9])
    assert_label_horizon_overlap(rows, [0, 1, 2, 3, 4], [8, 9])

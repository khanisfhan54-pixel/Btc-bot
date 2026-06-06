from __future__ import annotations

from typing import Any, List, Mapping, Optional, Sequence, Tuple

from .timestamp_alignment_audit import assert_no_timestamp_leakage, normalize_training_rows

_TIMESTAMP_FIELDS: Tuple[str, ...] = (
    "prediction_timestamp_ms",
    "prediction_ts_ms",
    "prediction_ts",
    "timestamp_ms",
)

_LABEL_HORIZON_FIELDS: Tuple[str, ...] = (
    "label_horizon_end",
    "label_horizon_end_ms",
    "label_horizon_end_ts_ms",
    "label_horizon_end_timestamp_ms",
)


def _present(row: Mapping[str, Any], key: str) -> bool:
    return key in row and row.get(key) not in (None, "")


def _parse_int(value: Any, *, row_idx: int, field: str) -> int:
    try:
        parsed = int(float(value))
    except Exception as exc:
        raise ValueError(f"temporal leakage validation invalid timestamp: row={row_idx} field={field} value={value!r}") from exc
    if parsed < 0:
        raise ValueError(f"temporal leakage validation invalid timestamp: row={row_idx} field={field} value={parsed}")
    return parsed


def _prediction_ts(row: Mapping[str, Any], *, row_idx: int) -> int:
    for field in _TIMESTAMP_FIELDS:
        if _present(row, field):
            return _parse_int(row[field], row_idx=row_idx, field=field)
    if _present(row, "feature_available_ts_ms"):
        return _parse_int(row["feature_available_ts_ms"], row_idx=row_idx, field="feature_available_ts_ms")
    raise ValueError(f"temporal leakage validation missing prediction timestamp: row={row_idx}")


def _feature_available_ts(row: Mapping[str, Any], *, row_idx: int) -> int:
    if not _present(row, "feature_available_ts_ms"):
        raise ValueError(f"temporal leakage validation missing feature_available_ts_ms: row={row_idx}")
    return _parse_int(row["feature_available_ts_ms"], row_idx=row_idx, field="feature_available_ts_ms")


def _as_rows(payload_or_rows: Any) -> List[Mapping[str, Any]]:
    return normalize_training_rows(payload_or_rows)


def assert_feature_availability_alignment(payload_or_rows: Any) -> None:
    """Fail closed when a row's feature availability is after prediction time."""
    for idx, row in enumerate(_as_rows(payload_or_rows)):
        prediction_ts = _prediction_ts(row, row_idx=idx)
        available_ts = _feature_available_ts(row, row_idx=idx)
        if available_ts > prediction_ts:
            raise ValueError(
                "feature availability leakage detected: "
                f"row={idx} feature_available_ts_ms={available_ts} prediction_timestamp_ms={prediction_ts}"
            )


def assert_external_feature_alignment(payload_or_rows: Any) -> None:
    """Fail closed when any audited external source timestamp is after row prediction time."""
    assert_no_timestamp_leakage(payload_or_rows)


def assert_temporal_ordering(samples: Sequence[Mapping[str, Any]], train_indices: Sequence[int], test_indices: Sequence[int]) -> None:
    """Validate strict train/test timestamp ordering for one fold."""
    if not train_indices or not test_indices:
        raise ValueError("temporal ordering validation requires non-empty train and test indices")
    max_train_ts = max(_prediction_ts(samples[i], row_idx=i) for i in train_indices)
    min_test_ts = min(_prediction_ts(samples[i], row_idx=i) for i in test_indices)
    if max_train_ts >= min_test_ts:
        raise ValueError(
            "train/test timestamp overlap detected: "
            f"max_train_timestamp_ms={max_train_ts} min_test_timestamp_ms={min_test_ts}"
        )


def _label_horizon_end_ts(samples: Sequence[Mapping[str, Any]], sample_pos: int, horizon_bars: Optional[int]) -> int:
    row = samples[sample_pos]
    for field in _LABEL_HORIZON_FIELDS:
        if _present(row, field):
            return _parse_int(row[field], row_idx=sample_pos, field=field)
    if horizon_bars is None:
        raise ValueError(f"label horizon validation missing horizon end timestamp: row={sample_pos}")
    end_pos = sample_pos + int(horizon_bars)
    if end_pos >= len(samples):
        raise ValueError(
            "label horizon validation cannot derive horizon end: "
            f"row={sample_pos} horizon_bars={horizon_bars} sample_count={len(samples)}"
        )
    return _prediction_ts(samples[end_pos], row_idx=end_pos)


def assert_label_horizon_overlap(
    samples: Sequence[Mapping[str, Any]],
    train_indices: Sequence[int],
    test_indices: Sequence[int],
    *,
    horizon_bars: Optional[int] = None,
) -> None:
    """Validate that every train label horizon ends strictly before the test period."""
    if not train_indices or not test_indices:
        raise ValueError("label horizon validation requires non-empty train and test indices")
    first_test_ts = min(_prediction_ts(samples[i], row_idx=i) for i in test_indices)
    leaking = []
    for i in train_indices:
        horizon_end = _label_horizon_end_ts(samples, i, horizon_bars)
        if horizon_end >= first_test_ts:
            leaking.append((i, horizon_end))
    if leaking:
        raise ValueError(
            "label horizon leakage detected: "
            f"first_test_timestamp_ms={first_test_ts} leaking_train_horizons={leaking}"
        )


def assert_purged_walk_forward_boundary(
    n_samples: int,
    train_indices: Sequence[int],
    test_indices: Sequence[int],
    *,
    purge_size: int,
    embargo_size: int,
) -> None:
    """Validate purged walk-forward fold boundaries using sample-index units."""
    if n_samples < 1:
        raise ValueError("purged walk-forward validation requires n_samples >= 1")
    if purge_size < 0 or embargo_size < 0:
        raise ValueError("purge_size and embargo_size must be non-negative")
    if not train_indices or not test_indices:
        raise ValueError("purged walk-forward validation requires non-empty train and test indices")
    train_set = set(int(i) for i in train_indices)
    test_set = set(int(i) for i in test_indices)
    if train_set & test_set:
        raise ValueError(f"train/test index overlap detected: overlap={sorted(train_set & test_set)}")
    test_start = min(test_set)
    test_end = max(test_set)
    if max(train_set) >= test_start:
        raise ValueError(
            "purged walk-forward chronological boundary violation: "
            f"max_train_index={max(train_set)} test_start={test_start}"
        )
    purge_start = max(0, test_start - int(purge_size))
    embargo_end = min(n_samples - 1, test_end + int(embargo_size))
    forbidden = set(range(purge_start, embargo_end + 1))
    leaking_train = sorted(train_set & forbidden)
    if leaking_train:
        raise ValueError(
            "purged walk-forward boundary violation: "
            f"forbidden_interval=[{purge_start}, {embargo_end}] leaking_train_indices={leaking_train}"
        )
    observed_purge = test_start - max(train_set) - 1
    if observed_purge < purge_size:
        raise ValueError(
            "purge_size not applied: "
            f"required={purge_size} observed={observed_purge} test_start={test_start} max_train_index={max(train_set)}"
        )

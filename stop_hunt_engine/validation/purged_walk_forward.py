from __future__ import annotations

from typing import Iterator, Tuple


def purged_walk_forward_splits(
    n_samples: int,
    train_size: int,
    test_size: int,
    step: int,
    purge_size: int,
    embargo_size: int = 0,
) -> Iterator[Tuple[range, range]]:
    """
    Fixed-window purged walk-forward splits with an optional embargo.

    Each fold is laid out as::

        train | purge | test | embargo

    No training index can fall inside ``[test_start - purge_size,
    test_end + embargo_size]``.  The next fold starts only after the
    current fold's embargo has elapsed, unless ``step`` advances farther.
    """
    if n_samples < 1:
        raise ValueError(f"purged_walk_forward_splits: n_samples={n_samples} must be >= 1")
    if purge_size < 0:
        raise ValueError(f"purged_walk_forward_splits: purge_size={purge_size} must be >= 0")
    if embargo_size < 0:
        raise ValueError(f"purged_walk_forward_splits: embargo_size={embargo_size} must be >= 0")
    if train_size < 1:
        raise ValueError(f"purged_walk_forward_splits: train_size={train_size} must be >= 1")
    if test_size < 1:
        raise ValueError(f"purged_walk_forward_splits: test_size={test_size} must be >= 1")
    if step < 1:
        raise ValueError(f"purged_walk_forward_splits: step={step} must be >= 1")

    fold_start = 0
    while True:
        train_start = fold_start
        train_end = train_start + train_size
        test_start = train_end + purge_size
        test_end = test_start + test_size
        if test_end > n_samples:
            break
        yield range(train_start, train_end), range(test_start, test_end)
        fold_start = max(fold_start + step, test_end + embargo_size)

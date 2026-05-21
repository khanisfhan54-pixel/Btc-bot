from __future__ import annotations

from typing import Iterator, Tuple


def walk_forward_splits(
    n_samples: int,
    train_size: int,
    test_size: int,
    step: int,
) -> Iterator[Tuple[range, range]]:
    """
    Expanding-window walk-forward splits (anchored origin).

    Training set always starts at index 0 and grows with each fold.
    Use this when earlier history should always be visible to the model.
    No train/test overlap is possible by construction.

    Yields
    ------
    (train_indices, test_indices) with max(train) < min(test).
    """
    split_index = train_size
    while split_index + test_size <= n_samples:
        yield range(0, split_index), range(split_index, split_index + test_size)
        split_index += step


def walk_forward_splits_rolling(
    n_samples: int,
    train_size: int,
    test_size: int,
    step: int,
) -> Iterator[Tuple[range, range]]:
    """
    Fixed-window (rolling) walk-forward splits.

    Training window is always exactly ``train_size`` bars, sliding forward
    by ``step`` each fold. Use this for regime-sensitive models where
    distant history may harm rather than help.

    Yields
    ------
    (train_indices, test_indices) with max(train) < min(test).
    """
    split_index = train_size
    while split_index + test_size <= n_samples:
        yield range(split_index - train_size, split_index), range(split_index, split_index + test_size)
        split_index += step

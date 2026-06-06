from __future__ import annotations

import pytest

from stop_hunt_engine.validation.purged_walk_forward import purged_walk_forward_splits


def _as_lists(n_samples=270, train_size=100, test_size=20, step=1, purge_size=10, embargo_size=10):
    return [
        (list(train), list(test))
        for train, test in purged_walk_forward_splits(
            n_samples,
            train_size,
            test_size,
            step,
            purge_size,
            embargo_size,
        )
    ]


def test_purged_walk_forward_excludes_purge_and_embargo_regions() -> None:
    folds = _as_lists()
    train, test = folds[0]

    assert train == list(range(0, 100))
    assert test == list(range(110, 130))
    assert set(train).isdisjoint(test)
    assert max(train) < min(test)
    assert set(train).isdisjoint(range(min(test) - 10, min(test)))
    assert set(train).isdisjoint(range(max(test) + 1, max(test) + 1 + 10))


def test_purged_walk_forward_multiple_folds_begin_after_embargo() -> None:
    folds = _as_lists()

    assert len(folds) == 2
    assert folds[0][0] == list(range(0, 100))
    assert folds[0][1] == list(range(110, 130))
    assert folds[1][0] == list(range(140, 240))
    assert folds[1][1] == list(range(250, 270))


def test_purged_walk_forward_no_train_inside_forbidden_interval() -> None:
    purge_size = 5
    embargo_size = 7
    for train, test in _as_lists(220, 40, 10, 15, purge_size, embargo_size):
        forbidden = set(range(min(test) - purge_size, max(test) + 1 + embargo_size))
        assert set(train).isdisjoint(forbidden)
        assert set(train).isdisjoint(test)
        assert max(train) < min(test)


def test_purged_split_prevents_future_leakage() -> None:
    purge_size = 10
    for train, test in _as_lists(purge_size=purge_size):
        assert max(train) < min(test) - purge_size


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"purge_size": -1}, "purge_size"),
        ({"embargo_size": -1}, "embargo_size"),
        ({"train_size": 0}, "train_size"),
        ({"test_size": 0}, "test_size"),
        ({"step": 0}, "step"),
    ],
)
def test_purged_walk_forward_defensive_validation(kwargs, match) -> None:
    params = dict(n_samples=50, train_size=10, test_size=5, step=5, purge_size=2, embargo_size=0)
    params.update(kwargs)
    with pytest.raises(ValueError, match=match):
        list(purged_walk_forward_splits(**params))

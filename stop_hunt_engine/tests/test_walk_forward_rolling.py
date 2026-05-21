from stop_hunt_engine.validation.walk_forward import walk_forward_splits_rolling


def test_rolling_window_fixed_size() -> None:
    for train, test in walk_forward_splits_rolling(120, 40, 10, 10):
        assert len(train) == 40
        assert max(train) < min(test)
        assert set(train).isdisjoint(set(test))

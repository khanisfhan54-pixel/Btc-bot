from stop_hunt_engine.validation.walk_forward import walk_forward_splits

def test_non_overlapping_windows():
    for tr,te in walk_forward_splits(120,60,20,20):
        assert set(tr).isdisjoint(set(te))

from stop_hunt_engine.validation.walk_forward import walk_forward_splits

def test_walk_forward_no_leakage():
    for tr,te in walk_forward_splits(100,50,10,10):
        assert max(tr) < min(te)

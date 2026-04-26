from engine import compute_sma

def test_compute_sma_insufficient():
    assert compute_sma([], 20) is None
    assert compute_sma([100.0]*5, 20) is None

def test_compute_sma_valid():
    assert compute_sma([100.0]*20, 20) == 100.0

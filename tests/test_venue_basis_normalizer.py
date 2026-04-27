from venue_basis import VenueBasisNormalizer


def test_same_venue_is_ready_and_zero_basis():
    n = VenueBasisNormalizer(halt_threshold_pct=0.5)
    n.set_venues("binance", "binance")
    status = n.validate()
    assert status.ok is True
    assert status.ready is True
    assert n.basis == 0.0


def test_cross_venue_seed_update_and_convert():
    n = VenueBasisNormalizer(halt_threshold_pct=0.5)
    n.set_venues("okx", "binance")
    n.seed(50000.0, 50150.0)
    n.update(50010.0, 50160.0)
    status = n.validate()
    assert status.ok is True
    assert status.ready is True
    assert abs(n.analysis_to_execution(50000.0) - 50150.0) < 10.0
    assert abs(n.execution_to_analysis(50150.0) - 50000.0) < 10.0


def test_basis_halt_when_dislocated():
    n = VenueBasisNormalizer(halt_threshold_pct=0.1)
    n.set_venues("okx", "binance")
    n.seed(50000.0, 50600.0)
    status = n.validate()
    assert status.ok is False
    assert status.reason == "basis_too_large"

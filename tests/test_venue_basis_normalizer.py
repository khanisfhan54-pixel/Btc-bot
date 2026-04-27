import math

import pytest

from venue_basis import VenueBasisNormalizer


def test_same_venue_is_ready_and_zero_basis():
    n = VenueBasisNormalizer(halt_threshold_pct=0.5)
    n.set_venues("binance", "binance")
    status = n.validate()
    assert status.ok is True
    assert status.reason == "ok_same_venue"
    assert status.ready is True
    assert n.basis == 0.0
    assert n.analysis_to_execution(50000.0) == 50000.0
    assert n.ready is True


def test_cross_venue_seed_update_and_convert():
    n = VenueBasisNormalizer(halt_threshold_pct=0.5)
    n.set_venues("okx", "binance")
    n.seed(analysis_mid=50000.0, execution_mid=50150.0)
    status = n.validate()
    assert status.ok is True
    assert status.reason in {"ok", "ok_same_venue"}
    assert n.analysis_to_execution(50000.0) == pytest.approx(50150.0, abs=5.0)
    assert n.execution_to_analysis(50150.0) == pytest.approx(50000.0, abs=5.0)
    assert n.okx_to_binance(50000.0) == pytest.approx(50150.0, abs=5.0)
    assert n.binance_to_okx(50150.0) == pytest.approx(50000.0, abs=5.0)
    assert math.isfinite(n.basis)
    assert math.isfinite(n.basis_pct)


def test_basis_halt_when_dislocated():
    n = VenueBasisNormalizer(halt_threshold_pct=0.1)
    n.set_venues("okx", "binance")
    n.seed(50000.0, 50600.0)
    status = n.validate()
    assert status.ok is False
    assert status.reason == "basis_too_large"


def test_basis_conversions_fail_closed_before_ready():
    n = VenueBasisNormalizer(halt_threshold_pct=0.5)
    n.set_venues("okx", "binance")
    with pytest.raises(RuntimeError, match="basis_unavailable"):
        n.analysis_to_execution(50000.0)
    with pytest.raises(RuntimeError, match="basis_unavailable"):
        n.execution_to_analysis(50150.0)
    with pytest.raises(RuntimeError, match="basis_unavailable"):
        n.okx_to_binance(50000.0)
    with pytest.raises(RuntimeError, match="basis_unavailable"):
        n.binance_to_okx(50150.0)
    assert n.validate().ok is False
    assert n.validate().reason == "basis_unavailable"

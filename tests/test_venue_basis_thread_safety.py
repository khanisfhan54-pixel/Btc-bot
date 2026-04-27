import math
import threading

import pytest

from venue_basis import VenueBasisNormalizer


def test_venue_basis_normalizer_thread_safe_state_and_snapshot():
    n = VenueBasisNormalizer(halt_threshold_pct=0.5)
    n.set_venues("okx", "binance")
    with pytest.raises(RuntimeError, match="basis_unavailable"):
        n.analysis_to_execution(50000.0)

    errs = []

    def worker(i: int) -> None:
        try:
            if i % 7 == 0:
                n.set_venues("binance", "binance")
            else:
                n.set_venues("okx", "binance")
            a = 50_000.0 + float(i % 17)
            e = a + 150.0
            n.seed(a, e)
            n.update(a + 1.0, e + 1.0)
            _ = n.validate()
            _ = n.analysis_to_execution(a)
            _ = n.execution_to_analysis(e)
        except Exception as exc:  # pragma: no cover
            errs.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errs
    assert math.isfinite(n.basis)
    assert math.isfinite(n.basis_pct)
    assert n.validate().reason in {"ok", "ok_same_venue", "basis_unavailable", "basis_too_large"}
    assert isinstance(n.validate().ok, bool)

    n.set_venues("binance", "binance")
    n.update(50000.0, 50000.0)
    assert n.basis == 0.0
    assert n.ready is True

    snap = n.snapshot()
    assert snap["basis"] == n.basis
    assert snap["basis_pct"] == n.basis_pct
    assert snap["ready"] == n.ready

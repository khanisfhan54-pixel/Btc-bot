from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from advanced_regime_engine import compute_hmm_regime


def test_hmm_score_map_sum_is_one() -> None:
    result = compute_hmm_regime(np.array([0.5, 0.3, 0.2]))
    sm = result["score_map"]
    assert set(sm) == {"TREND", "BEAR", "RANGE", "TOXIC"}
    assert all(np.isfinite(v) and v >= 0.0 for v in sm.values())
    assert abs(sum(sm.values()) - 1.0) < 1e-9
    assert isinstance(result["metadata"]["score_sum"], float)


def test_hmm_uniform_fallback_branch_runtime() -> None:
    import advanced_regime_engine as _are

    original_isfinite = _are.np.isfinite

    def _scoped_isfinite(x):
        arr = np.asarray(x)
        if arr.ndim == 0:
            # Force scalar finiteness checks to fail, which deterministically
            # drives compute_hmm_regime into the uniform fallback branch.
            return False
        return original_isfinite(x)

    with patch.object(_are.np, "isfinite", side_effect=_scoped_isfinite):
        result = compute_hmm_regime(np.array([0.5, 0.3, 0.2]))

    score_map = result.get("score_map")
    assert isinstance(score_map, dict)
    assert set(score_map) == {"TREND", "BEAR", "RANGE", "TOXIC"}
    values = np.asarray(list(score_map.values()), dtype=float)
    assert np.all(np.isfinite(values))
    assert np.allclose(values, 0.25, atol=0.0, rtol=0.0)
    assert abs(float(values.sum()) - 1.0) < 1e-12


def test_calibration_missing_book_raises_blocker(tmp_path: Path) -> None:
    pytest.importorskip("pandas")
    import calibrate_regime_5m as _cal5m

    bars_1m = [[1701388800000 + i * 60000, 1, 1, 1, 1, 1] for i in range(3000)]
    with patch.object(_cal5m, "_L1_BOOK_PATH", str(tmp_path / "missing.csv")):
        with pytest.raises(RuntimeError) as exc:
            _cal5m.calibrate_5m_artifacts(
                bars_1m=bars_1m,
                out_path=str(tmp_path / "o.npz"),
                meta_path=str(tmp_path / "m.json"),
            )
    assert str(exc.value).startswith("BLOCKER:")

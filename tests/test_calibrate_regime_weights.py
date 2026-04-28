import numpy as np
import pytest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import calibrate_regime
from advanced_regime_engine import AdvancedRegimeEngine
from model_weights import ModelWeightManager


def _write_valid_ohlcv(path, rows=40):
    data = []
    for i in range(rows):
        ts = 1_700_000_000 + i * 60
        close = 100.0 + (0.15 * i)
        data.append([ts, close - 1.0, close + 1.0, close - 2.0, close, 20.0 + i])
    np.savetxt(path, np.asarray(data, dtype=float), delimiter=",")


def test_calibrate_outputs_engine_loadable_weight_shapes(tmp_path):
    csv_path = tmp_path / "ohlcv.csv"
    weights_path = tmp_path / "advanced_regime_weights.npz"
    _write_valid_ohlcv(csv_path)

    calibrate_regime.calibrate(str(csv_path), str(weights_path))
    weights = ModelWeightManager.load_weights("advanced_regime", str(weights_path))
    assert weights is not None

    for key in ("nhhmm_beta", "nhhmm_mu", "nhhmm_sigma", "sjm_centroids"):
        assert key in weights

    beta = np.asarray(weights["nhhmm_beta"], dtype=float)
    mu = np.asarray(weights["nhhmm_mu"], dtype=float)
    sigma = np.asarray(weights["nhhmm_sigma"], dtype=float)
    centroids = np.asarray(weights["sjm_centroids"], dtype=float)

    assert beta.ndim == 3
    assert beta.shape[:2] == (3, 3)
    assert mu.shape == (3,)
    assert sigma.shape == (3,)
    assert centroids.shape[0] == 3
    assert centroids.ndim == 2
    assert beta.shape[2] == centroids.shape[1]

    for arr in (beta, mu, sigma, centroids):
        assert np.isfinite(arr).all()

    engine = AdvancedRegimeEngine(
        n_states=3,
        n_features=centroids.shape[1],
        enable_background_workers=False,
        seed=42,
    )
    engine.nhhmm.load_weights(beta, mu, sigma)


def test_calibrate_rejects_insufficient_ohlcv_rows(tmp_path):
    csv_path = tmp_path / "too_small.csv"
    weights_path = tmp_path / "unused.npz"
    _write_valid_ohlcv(csv_path, rows=3)

    with pytest.raises(ValueError, match="at least"):
        calibrate_regime.calibrate(str(csv_path), str(weights_path))


def test_calibrate_rejects_non_finite_close_prices(tmp_path):
    csv_path = tmp_path / "non_finite_close.csv"
    weights_path = tmp_path / "unused.npz"
    _write_valid_ohlcv(csv_path, rows=8)
    data = np.loadtxt(csv_path, delimiter=",", ndmin=2)
    data[3, 4] = np.nan
    np.savetxt(csv_path, data, delimiter=",")

    with pytest.raises(ValueError, match="non-finite close prices"):
        calibrate_regime.calibrate(str(csv_path), str(weights_path))


def test_calibrate_rejects_non_finite_volumes(tmp_path):
    csv_path = tmp_path / "non_finite_volume.csv"
    weights_path = tmp_path / "unused.npz"
    _write_valid_ohlcv(csv_path, rows=8)
    data = np.loadtxt(csv_path, delimiter=",", ndmin=2)
    data[3, 5] = np.inf
    np.savetxt(csv_path, data, delimiter=",")

    with pytest.raises(ValueError, match="non-finite volumes"):
        calibrate_regime.calibrate(str(csv_path), str(weights_path))


def test_calibrate_rejects_non_positive_close_prices(tmp_path):
    csv_path = tmp_path / "non_positive_close.csv"
    weights_path = tmp_path / "unused.npz"
    _write_valid_ohlcv(csv_path, rows=8)
    data = np.loadtxt(csv_path, delimiter=",", ndmin=2)
    data[2, 4] = 0.0
    np.savetxt(csv_path, data, delimiter=",")

    with pytest.raises(ValueError, match="strictly positive close prices"):
        calibrate_regime.calibrate(str(csv_path), str(weights_path))

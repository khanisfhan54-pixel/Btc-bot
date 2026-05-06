"""
diagnose_engine_state.py — Rapid health check for AdvancedRegimeEngine.

Run immediately after engine construction and after load_state() in any
test harness or pre-flight script. All values must be True before live trading.

Usage:
    from advanced_regime_engine import AdvancedRegimeEngine
    from diagnose_engine_state import diagnose_engine_state

    engine = AdvancedRegimeEngine(n_states=3, n_features=3)
    report = diagnose_engine_state(engine)
    assert report["all_ok"], f"Engine pre-flight FAILED: {report}"
"""
from __future__ import annotations

import logging
from typing import Any, Dict, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from advanced_regime_engine import AdvancedRegimeEngine

LOGGER = logging.getLogger(__name__)


def diagnose_engine_state(engine: "AdvancedRegimeEngine") -> Dict[str, Any]:
    """
    Rapid health check. Run this after construction and after load_state().
    Any False value means Phase 1 correctness fixes are not fully active.

    Gate 1 passes when:
        - all_ok is True
        - Regime distribution entropy on live data is between 0.8 and 1.5 nats
        - Accuracy on triple-barrier labels exceeds 20%
    """
    # SJM centroids must be explicitly loaded (not the symmetric-zero fallback)
    sjm_ok = (
        engine.sjm.means is not None
        and not getattr(engine.sjm, "_default_params_initialized", True)
        and engine.sjm.means.shape[0] == engine.K
    )

    # NHHMM must have calibrated longitudinal moments injected
    nhhmm_norm_ok = (
        getattr(engine.nhhmm, "_feature_mean", None) is not None
        and getattr(engine.nhhmm, "_feature_std",  None) is not None
    )

    # GARCH must be stationary in all regimes
    garch_stationary = bool(
        np.all(engine.garch.alpha + engine.garch.beta_garch < 0.999)
    )

    weights_loaded      = bool(engine._weights_loaded)
    feature_norm_source = str(getattr(engine, "_feature_norm_source", "rolling"))

    # BTC-specific persistence range: alpha+beta must be in [0.88, 0.97].
    # Equity defaults produce [0.95, 0.90] which passes garch_stationary
    # but is WRONG for 1-minute BTCUSDT. This check catches the gap.
    _EQUITY_DEFAULT_ALPHA    = np.array([0.05, 0.20])
    _EQUITY_DEFAULT_BETA     = np.array([0.90, 0.70])
    _BTC_PERSISTENCE_LO      = 0.88
    _BTC_PERSISTENCE_HI      = 0.97

    garch_persistence_vals = engine.garch.alpha + engine.garch.beta_garch
    garch_btc_range_ok = bool(
        np.all(garch_persistence_vals >= _BTC_PERSISTENCE_LO)
        and np.all(garch_persistence_vals <= _BTC_PERSISTENCE_HI)
    )
    garch_not_equity_defaults = not (
        np.allclose(engine.garch.alpha,      _EQUITY_DEFAULT_ALPHA, atol=1e-6)
        and np.allclose(engine.garch.beta_garch, _EQUITY_DEFAULT_BETA,  atol=1e-6)
    )
    garch_calibrated = bool(garch_btc_range_ok and garch_not_equity_defaults)

    report: Dict[str, Any] = {
        "weights_loaded":          weights_loaded,
        "sjm_centroids_valid":     sjm_ok,
        "nhhmm_moments_set":       nhhmm_norm_ok,
        "garch_stationary":        garch_stationary,
        "garch_btc_range_ok":      garch_btc_range_ok,
        "garch_not_equity_defaults": garch_not_equity_defaults,
        "garch_calibrated":        garch_calibrated,
        "feature_norm_source":     feature_norm_source,
        "garch_alpha":             engine.garch.alpha.tolist(),
        "garch_beta":              engine.garch.beta_garch.tolist(),
        "garch_persistence":       garch_persistence_vals.tolist(),
        "all_ok": all([
            weights_loaded,
            sjm_ok,
            nhhmm_norm_ok,
            garch_stationary,
            garch_calibrated,       # NEW: equity defaults now cause all_ok=False
        ]),
    }

    if not report["all_ok"]:
        LOGGER.critical("ENGINE DIAGNOSTIC FAILED: %s", report)
    else:
        LOGGER.info("ENGINE DIAGNOSTIC PASSED: %s", report)

    return report

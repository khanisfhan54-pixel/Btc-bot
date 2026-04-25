import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from advanced_regime_engine import AdvancedRegimeEngine


def _base_engine() -> AdvancedRegimeEngine:
    return AdvancedRegimeEngine(
        n_states=3,
        n_features=3,
        mtf_weights={"1m": 1.0, "5m": 1.0},
        strict_mtf_keys=False,
        seed=7,
    )


def test_mtf_graceful_degradation_survives_base_feature_corruption():
    eng = _base_engine()

    payload = {
        "timestamp": 1_700_000_000.0,
        "price": 100.0,
        "return": 0.001,
        "mtf": {
            "base": {"return": 0.001, "features": [np.nan, np.nan]},
            "1m": {"return": 0.002, "features": [0.1, 0.2, 0.3]},
            "5m": {"return": 0.0015, "features": [0.2, 0.1, 0.0]},
        },
    }

    out = eng.update(payload)

    assert out["regime_label"] != "UNKNOWN"
    assert out["risk_metrics"]["feed_status"] == "MTF_FUSED_BASE_FEATURE_INVALID"
    assert out["signal_valid"] is True


def test_range_persistence_after_five_minutes():
    eng = _base_engine()

    def _forced_range(*args, **kwargs):
        return 0, np.array([0.5, 0.5, 0.0], dtype=float)

    eng.sjm.online_predict = _forced_range  # type: ignore[assignment]
    eng.last_signed_position_size = 0.08
    eng._range_anchor_size = 0.08

    out = None
    ts0 = 1_700_100_000.0
    for i in range(301):
        out = eng.update(
            {
                "timestamp": ts0 + i,
                "price": 100.0 + 0.01 * i,
                "return": 0.0,
                "features": [0.0, 0.0, 0.0],
            }
        )

    assert out is not None
    assert out["regime_label"] == "RANGE"
    assert out["risk_metrics"]["range_ticks"] >= 300
    decay_multiplier = float(np.exp(-eng._RANGE_SIGNED_DECAY_LAMBDA * eng.range_ticks))
    assert decay_multiplier > 0.95


def test_restart_pnl_safety_two_hour_gap_first_tick_zero_pnl():
    eng = _base_engine()
    eng.load_state(
        {
            "state_version": eng._STATE_VERSION,
            "model_signature": eng.get_state()["model_signature"],
            "equity": 1.05,
            "equity_peak": 1.10,
            "last_price": 100.0,
            "last_price_timestamp": 1_700_000_000.0,
            "last_signed_position_size": 0.2,
        }
    )

    pre_equity = eng._equity
    out = eng.update(
        {
            "timestamp": 1_700_007_200.0,
            "price": 130.0,
            "return": 0.30,
            "features": [0.1, 0.0, 0.1],
        }
    )

    assert out is not None
    assert eng._equity == pre_equity


def test_lock_safety_concurrent_self_heal_and_update():
    eng = _base_engine()
    failures = []
    lock = threading.Lock()

    def _run_update(i: int):
        try:
            eng.update(
                {
                    "timestamp": 1_700_200_000.0 + i,
                    "price": 100.0 + i * 0.01,
                    "return": 0.0001,
                    "features": [0.1, 0.2, 0.3],
                }
            )
        except Exception as exc:  # pragma: no cover
            with lock:
                failures.append(exc)

    def _run_heal(i: int):
        try:
            eng._self_heal("E200", {"i": i})
        except Exception as exc:  # pragma: no cover
            with lock:
                failures.append(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for i in range(80):
            pool.submit(_run_update, i)
            pool.submit(_run_heal, i)

    assert failures == []
    state = eng.get_state()
    assert np.isfinite(state["equity"])
    assert np.isfinite(state["last_valid_vol"])

import numpy as np
import pytest

from advanced_regime_engine import AdvancedRegimeEngine


def bull_market(n=300, seed=1):
    rng = np.random.default_rng(seed)
    return 0.0008 + rng.normal(0, 0.003, n)


def bear_market(n=300, seed=2):
    rng = np.random.default_rng(seed)
    return -0.0008 + rng.normal(0, 0.003, n)


def range_market(n=300, seed=3):
    rng = np.random.default_rng(seed)
    return rng.normal(0, 0.0015, n)


def shock_market(n=300, seed=4):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.0015, n)
    for i in [50, 150, 250]:
        r[i] = rng.choice([-1, 1]) * rng.uniform(0.05, 0.12)
    return r


@pytest.fixture
def engine():
    eng = AdvancedRegimeEngine(n_states=3, n_features=3)
    yield eng
    eng._shutdown_warning_worker()


def run_engine(engine, returns):
    price = 100.0
    outputs = []
    for i, r in enumerate(returns):
        price *= (1 + r)
        md = {
            "timestamp": float(i),
            "return": float(r),
            "features": np.array([0.2, 0.1, 0.05]),
            "price": float(price),
        }
        outputs.append(engine.update(md))
    return outputs


def _iter_numeric(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from _iter_numeric(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _iter_numeric(v)
    elif isinstance(x, (int, float, np.floating, np.integer)) and not isinstance(x, bool):
        yield float(x)


def test_no_nan_inf(engine):
    for series in (bull_market(), bear_market(), range_market(), shock_market()):
        outputs = run_engine(engine, series)
        assert outputs
        for out in outputs:
            nums = list(_iter_numeric(out))
            assert nums
            assert np.all(np.isfinite(nums))


def test_position_bounds(engine):
    outputs = run_engine(engine, bull_market())
    for o in outputs:
        assert 0.0 <= o["position_size"] <= 0.35


def test_shock_triggers_toxic(engine):
    outputs = run_engine(engine, shock_market())
    toxic_ratio = sum(o["regime_label"] == "TOXIC" for o in outputs) / len(outputs)
    assert toxic_ratio > 0.05


def test_engine_stability_under_noise(engine):
    outputs = run_engine(engine, range_market())
    assert len(outputs) > 0
    assert all("regime_label" in o for o in outputs)


def test_reset_state(engine):
    run_engine(engine, bull_market())
    engine.reset_state()
    outputs = run_engine(engine, bull_market())
    assert len(outputs) > 0


def test_bull_bias(engine):
    outputs = run_engine(engine, bull_market())
    trend_count = sum(o["regime_label"] == "TREND" for o in outputs)
    assert trend_count > 0


def test_bear_bias(engine):
    outputs = run_engine(engine, bear_market())
    bear_count = sum(o["regime_label"] == "BEAR" for o in outputs)
    assert bear_count > 0


def test_range_presence(engine):
    outputs = run_engine(engine, range_market())
    range_count = sum(o["regime_label"] == "RANGE" for o in outputs)
    assert range_count > 0


def test_circuit_breaker_recovery(engine):
    engine._trigger_circuit_breaker("TEST")
    price = 100.0
    labels = []
    for i in range(30):
        r = 0.0001
        price *= (1 + r)
        out = engine.update(
            {
                "timestamp": float(i),
                "return": r,
                "features": np.array([0.2, 0.1, 0.05]),
                "price": price,
            }
        )
        labels.append(out["regime_label"])
    assert "HALTED" in labels
    assert labels[-1] != "HALTED"


def test_pnl_tracking_scale(engine):
    engine.last_signed_position_size = 0.35
    price = 100.0
    r_path = [0.0, 0.001, -0.0005, 0.002]

    expected_equity = 1.0
    out = engine.update(
        {
            "timestamp": 0.0,
            "return": r_path[0],
            "features": np.array([0.2, 0.1, 0.05]),
            "price": price,
        }
    )
    assert out["schema_version"] == "1.2.0"

    for i, r in enumerate(r_path[1:], start=1):
        engine.last_signed_position_size = 0.35
        price *= (1 + r)
        expected_equity += r * 0.35
        engine.update(
            {
                "timestamp": float(i),
                "return": float(r),
                "features": np.array([0.2, 0.1, 0.05]),
                "price": float(price),
            }
        )

    assert engine._equity == pytest.approx(expected_equity, rel=1e-7, abs=1e-9)


def test_deterministic_classification():
    returns = bull_market(n=120, seed=123)

    labels_across_seeds = []
    for seed in [0, 1, 2, 3, 4]:
        np.random.seed(seed)
        eng = AdvancedRegimeEngine(n_states=3, n_features=3)
        outputs = run_engine(eng, returns)
        labels_across_seeds.append([o["regime_label"] for o in outputs])
        eng._shutdown_warning_worker()

    first = labels_across_seeds[0]
    for labels in labels_across_seeds[1:]:
        assert labels == first


def test_halted_zeros_position(engine):
    engine._trigger_circuit_breaker("TEST")
    out = engine.update(
        {
            "timestamp": 1.0,
            "return": 0.001,
            "features": np.array([0.2, 0.1, 0.05]),
            "price": 100.1,
        }
    )
    assert out["regime_label"] == "HALTED"
    assert out["signed_position_size"] == 0.0
    assert engine.last_signed_position_size == 0.0

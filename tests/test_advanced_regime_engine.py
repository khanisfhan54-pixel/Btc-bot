import numpy as np
import pytest

from advanced_regime_engine import AdvancedRegimeEngine


# ==========================================
# Fixtures
# ==========================================
@pytest.fixture
def engine():
    return AdvancedRegimeEngine(n_states=3, n_features=3)


# ==========================================
# Synthetic Data Generators
# ==========================================
def bull_market(n=300):
    rng = np.random.default_rng(1)
    return 0.0008 + rng.normal(0, 0.003, n)

def bear_market(n=300):
    rng = np.random.default_rng(2)
    return -0.0008 + rng.normal(0, 0.003, n)

def range_market(n=300):
    rng = np.random.default_rng(3)
    return rng.normal(0, 0.0015, n)

def shock_market(n=300):
    rng = np.random.default_rng(4)
    r = rng.normal(0, 0.0015, n)
    for i in [50, 150, 250]:
        r[i] = rng.choice([-1, 1]) * rng.uniform(0.05, 0.12)
    return r


# ==========================================
# Helper Runner
# ==========================================
def run_engine(engine, returns):
    price = 100.0
    outputs = []

    for i, r in enumerate(returns):
        price *= (1 + r)

        md = {
            "timestamp": float(i),
            "return": float(r),
            "features": np.array([0.2, 0.1, 0.05]),
            "price": price,
        }

        outputs.append(engine.update(md))

    return outputs


# ==========================================
# CORE TESTS
# ==========================================

def test_no_nan_inf(engine):
    """Engine should NEVER output NaN/Inf"""
    outputs = run_engine(engine, bull_market())

    for o in outputs:
        assert np.isfinite(o["trend_strength"])
        assert np.isfinite(o["risk_metrics"]["expected_volatility"])
        assert np.isfinite(o["position_size"])


def test_position_bounds(engine):
    """Position size must be capped"""
    outputs = run_engine(engine, bull_market())

    for o in outputs:
        assert 0.0 <= o["position_size"] <= 0.35


def test_shock_triggers_toxic(engine):
    """Shock must produce TOXIC regime"""
    outputs = run_engine(engine, shock_market())

    toxic_ratio = sum(
        o["regime_label"] == "TOXIC" for o in outputs
    ) / len(outputs)

    assert toxic_ratio > 0.1   # minimum threshold


def test_engine_stability_under_noise(engine):
    """Random noise should not crash engine"""
    outputs = run_engine(engine, range_market())

    assert len(outputs) > 0
    assert all("regime_label" in o for o in outputs)


def test_reset_state(engine):
    """State reset must not break engine"""
    run_engine(engine, bull_market())
    engine.reset_state()

    outputs = run_engine(engine, bull_market())

    assert len(outputs) > 0


# ==========================================
# REGIME BEHAVIOR (SOFT TESTS)
# ==========================================

def test_bull_bias(engine):
    outputs = run_engine(engine, bull_market())

    trend_count = sum(o["regime_label"] == "TREND" for o in outputs)

    # Not strict because model is untrained
    assert trend_count > 0


def test_bear_bias(engine):
    outputs = run_engine(engine, bear_market())

    bear_count = sum(o["regime_label"] == "BEAR" for o in outputs)

    assert bear_count > 0


def test_range_presence(engine):
    outputs = run_engine(engine, range_market())

    range_count = sum(o["regime_label"] == "RANGE" for o in outputs)

    assert range_count > 0

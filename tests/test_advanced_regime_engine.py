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


class TestEdgeEMATrap:
    @staticmethod
    def _regime_scores(edge_score=0.73):
        return {
            "regime": "TREND",
            "directional_label": "TREND",
            "conviction": max(edge_score, 0.73),
            "directional_margin": 0.30,
            "trend_score": 0.90,
            "range_score": 0.10,
            "edge_score": edge_score,
            "risk_level": 0.10,
            "trend_strength": 0.80,
            "confidence": 0.80,
            "bull": 0.80,
            "bear": 0.10,
            "crisis": 0.10,
            "uncertainty": 0.10,
            "certainty_score": 0.80,
            "directional_confidence": 0.80,
        }

    @staticmethod
    def _engine():
        eng = AdvancedRegimeEngine(
            target_vol=0.02,
            load_model_weights_on_init=False,
            enable_background_workers=False,
        )
        eng._weights_loaded = True
        eng._calibration_valid = True
        eng._production_valid = True
        eng._calibration_status = "production_valid"
        eng.garch_var = np.array([2.5e-7, 2.5e-7], dtype=float)
        eng.garch_prob = np.array([0.5, 0.5], dtype=float)
        eng._smoothed_garch_prob = np.array([0.5, 0.5], dtype=float)
        eng._last_valid_vol = 0.0005
        return eng

    @staticmethod
    def _feed(eng, n, ret=0.0001):
        price = 100.0
        outputs = []
        for i in range(n):
            price *= 1.0 + ret
            outputs.append(
                eng.update(
                    {
                        "timestamp": float(i),
                        "return": ret,
                        "features": np.array([0.2, 0.1, 0.05]),
                        "price": price,
                    }
                )
            )
        return outputs

    def test_ema_does_not_collapse_under_persistent_low_vol_penalty(self, monkeypatch):
        monkeypatch.setattr(
            "advanced_regime_engine.compute_hmm_regime",
            lambda *args, **kwargs: self._regime_scores(edge_score=0.73),
        )
        eng = self._engine()
        try:
            self._feed(eng, 50)
            assert eng._last_edge_score > 0.30
        finally:
            eng._shutdown_warning_worker()

    def test_edge_score_escapes_zero_after_warmup(self, monkeypatch):
        monkeypatch.setattr(
            "advanced_regime_engine.compute_hmm_regime",
            lambda *args, **kwargs: self._regime_scores(edge_score=0.73),
        )
        eng = self._engine()
        try:
            outputs = self._feed(eng, 16)
            assert outputs[15]["alpha"]["edge_score"] > 0.0
        finally:
            eng._shutdown_warning_worker()

    def test_ema_ceiling_convergence_without_penalty_feedback(self, monkeypatch):
        monkeypatch.setattr(
            "advanced_regime_engine.compute_hmm_regime",
            lambda *args, **kwargs: self._regime_scores(edge_score=0.73),
        )
        eng = self._engine()
        eng._EDGE_VOL_PENALTY = 0.0
        try:
            self._feed(eng, 100, ret=0.01)
            assert eng._last_edge_score == pytest.approx(0.73, rel=0.05)
        finally:
            eng._shutdown_warning_worker()

    def test_position_sizing_activates_after_edge_warmup(self, monkeypatch):
        monkeypatch.setattr(
            "advanced_regime_engine.compute_hmm_regime",
            lambda *args, **kwargs: self._regime_scores(edge_score=0.90),
        )
        eng = self._engine()
        try:
            outputs = self._feed(eng, 30)
            assert outputs[25]["position_size"] > 0.0
        finally:
            eng._shutdown_warning_worker()

    def test_execution_gate_becomes_reachable(self, monkeypatch):
        monkeypatch.setattr(
            "advanced_regime_engine.compute_hmm_regime",
            lambda *args, **kwargs: self._regime_scores(edge_score=0.90),
        )
        eng = self._engine()
        try:
            outputs = self._feed(eng, 60)
            assert outputs[50]["execution_side"] in ("long", "short")
        finally:
            eng._shutdown_warning_worker()

    def test_penalty_still_applies_to_edge_score_output(self, monkeypatch):
        monkeypatch.setattr(
            "advanced_regime_engine.compute_hmm_regime",
            lambda *args, **kwargs: self._regime_scores(edge_score=0.50),
        )
        eng = self._engine()
        eng.garch_var = np.array([(0.03) ** 2, (0.03) ** 2], dtype=float)
        eng._smoothed_garch_prob = np.array([0.5, 0.5], dtype=float)
        eng.garch_prob = np.array([0.5, 0.5], dtype=float)
        eng._last_valid_vol = 0.03
        try:
            output = self._feed(eng, 1, ret=0.0005)[0]
            assert output["alpha"]["edge_score"] < eng._last_edge_score
        finally:
            eng._shutdown_warning_worker()

    def test_last_edge_score_state_survives_round_trip(self, monkeypatch):
        monkeypatch.setattr(
            "advanced_regime_engine.compute_hmm_regime",
            lambda *args, **kwargs: self._regime_scores(edge_score=0.73),
        )
        eng = self._engine()
        reloaded = self._engine()
        try:
            self._feed(eng, 20)
            before = eng._last_edge_score
            reloaded.load_state(eng.get_state())
            assert reloaded._last_edge_score == pytest.approx(before)
            self._feed(reloaded, 5)
            assert reloaded._last_edge_score >= before
        finally:
            eng._shutdown_warning_worker()
            reloaded._shutdown_warning_worker()

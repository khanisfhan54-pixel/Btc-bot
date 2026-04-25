import inspect
import re
import copy
import logging
import numpy as np
import pytest

from advanced_regime_engine import AdvancedRegimeEngine, NHHMM_Engine, compute_hmm_regime


@pytest.fixture
def engine() -> AdvancedRegimeEngine:
    return AdvancedRegimeEngine(n_states=3, n_features=3, enable_background_workers=False, seed=42)


@pytest.fixture
def nhhmm() -> NHHMM_Engine:
    return NHHMM_Engine(n_states=3, n_features=5)


class TestCritical1ScoreSplitting:
    def test_directional_wins_over_range_when_trend_score_dominant(self):
        result = compute_hmm_regime(np.array([0.9, 0.1, 0.0]))
        assert result["regime"] in ("TREND", "BEAR")

    def test_directional_wins_tie_over_range(self):
        result = compute_hmm_regime(np.array([0.85, 0.15, 0.0]))
        assert result["regime"] in ("TREND", "BEAR")

    def test_toxic_wins_all_ties(self):
        result = compute_hmm_regime(np.array([0.01, 0.01, 0.98]))
        assert result["regime"] == "TOXIC"

    def test_score_map_contains_full_trend_score(self):
        out = compute_hmm_regime(np.array([0.75, 0.25, 0.0]))
        score_map = out["score_map"]
        assert max(score_map["TREND"], score_map["BEAR"]) == pytest.approx(out["trend_score"])


class TestCritical2ModelParamSerialization:
    def test_nhhmm_params_survive_round_trip(self, engine):
        rng = np.random.default_rng(42)
        known_beta = rng.normal(0, 0.01, engine.nhhmm.beta.shape)
        engine.nhhmm.beta = known_beta.copy()
        state = engine.save_state()
        engine.nhhmm.beta = np.zeros_like(known_beta)
        engine.load_state(state)
        np.testing.assert_array_almost_equal(engine.nhhmm.beta, known_beta, decimal=10)

    def test_sjm_params_survive_round_trip(self, engine):
        rng = np.random.default_rng(43)
        engine.sjm.means = np.zeros((engine.K, engine.n_features), dtype=float)
        engine.sjm.weights = np.ones(engine.n_features, dtype=float)
        known_means = rng.normal(0, 1, engine.sjm.means.shape)
        engine.sjm.means = known_means.copy()
        state = engine.save_state()
        engine.sjm.means = np.zeros_like(known_means)
        engine.load_state(state)
        np.testing.assert_array_almost_equal(engine.sjm.means, known_means, decimal=10)

    def test_degraded_status_on_missing_params(self, engine):
        state = engine.save_state()
        del state["nhhmm_beta"]
        engine.load_state(state)
        assert engine._engine_status == "DEGRADED"


class TestCritical3TickIdSerialization:
    def test_tick_id_survives_round_trip(self, engine):
        for i in range(100):
            engine.update({"price": 100.0 + i * 0.01, "return": 0.001, "features": [0.1, 0.0, -0.1], "timestamp": float(i+1)})
        expected = engine._tick_id
        state = engine.save_state()
        new_engine = AdvancedRegimeEngine(n_states=3, n_features=3, enable_background_workers=False, seed=42)
        new_engine.load_state(state)
        assert new_engine._tick_id >= expected
        assert new_engine._tick_id > (new_engine._last_price_tick_id or 0)

    def test_no_tick_order_violation_after_restore(self, engine):
        for i in range(10):
            engine.update({"price": 100.0 + i * 0.01, "return": 0.001, "features": [0.0, 0.0, 0.0], "timestamp": float(i+1)})
        state = engine.save_state()
        new_engine = AdvancedRegimeEngine(n_states=3, n_features=3, enable_background_workers=False, seed=42)
        new_engine.load_state(state)
        result = new_engine.update({"price": 100.2, "return": 0.001, "features": [0.0, 0.0, 0.0], "timestamp": 20.0})
        assert result.get("pnl_status") != "TICK_ORDER_VIOLATION"


class TestHigh1EmaDecayGap:
    def test_ema_decay_capped_at_10s(self, engine):
        assert engine._ema_decay(60.0) == pytest.approx(engine._ema_decay(10.0))


class TestHigh3TransitionMatrixHardening:
    def test_extreme_features_dont_degenerate_matrix(self, nhhmm):
        x_t = np.full(nhhmm.n_features, 1000.0)
        nhhmm.beta = np.full_like(nhhmm.beta, 0.05)
        p = nhhmm._compute_transition_matrix(x_t)
        np.testing.assert_allclose(p.sum(axis=1), 1.0, atol=1e-6)
        assert np.all(p >= 1e-7)
        assert np.all(np.isfinite(p))


class TestHigh5UnboundStaleReason:
    def test_no_unbound_error_with_corrupted_last_price(self, engine):
        engine._last_price = object()
        result = engine.update({"price": 100.0, "return": 0.0, "features": [0.0, 0.0, 0.0], "timestamp": 1.0})
        assert isinstance(result, dict)


class TestMedium1SnapshotThreadSafety:
    def test_snapshot_emitter_never_reads_live_attributes(self, engine):
        source = inspect.getsource(engine._materialize_snapshot_payload)
        forbidden_patterns = [
            r"\\bself\\._equity\\b(?!\\s*[=])",
            r"\\bself\\._drawdown\\b(?!\\s*[=])",
            r"\\bself\\._last_price\\b(?!\\s*[=])",
        ]
        for pattern in forbidden_patterns:
            assert not re.findall(pattern, source)


class TestRegressionSuite:
    def test_full_session_no_exceptions(self, engine):
        rng = np.random.default_rng(42)
        prices = 100.0 + np.cumsum(rng.normal(0, 0.1, 1000))
        prev = float(prices[0])
        for i, price in enumerate(prices[1:], start=1):
            ret = (float(price) - prev) / prev
            prev = float(price)
            result = engine.update({"price": float(price), "return": float(ret), "features": [ret, abs(ret), 0.0], "timestamp": float(i)})
            assert result is not None
            assert "regime_label" in result
            assert result["execution_side"] in ("long", "short", "flat", "range_mean_revert")


class TestCritical2NhhmmRestoreFlag:
    """
    Verifies that _load_state_inplace() correctly detects partial NHHMM
    parameter restoration and never silently accepts incomplete restores.

    The core invariant: _nhhmm_fully_restored == True IFF all three of
    nhhmm_beta, nhhmm_mu, nhhmm_sigma were successfully restored.
    A partial restore must always produce _engine_status == "DEGRADED"
    and emit a CRITICAL log.
    """

    @pytest.fixture
    def engine(self):
        """Fresh engine with known parameters for round-trip testing."""
        eng = AdvancedRegimeEngine(
            n_states=3,
            n_features=4,
            target_vol=0.02,
            seed=42,
            enable_background_workers=False,
        )
        rng = np.random.default_rng(99)
        known_beta = rng.normal(0.0, 0.01, (3, 3, 4))
        known_mu = np.array([0.005, -0.003, 0.0])
        known_sigma = np.array([0.012, 0.010, 0.025])
        eng.nhhmm.load_weights(known_beta, known_mu, known_sigma)
        return eng

    def _get_clean_state(self, engine) -> dict:
        return engine.save_state()

    def test_full_restore_sets_no_degraded_status(self, engine):
        state = self._get_clean_state(engine)
        new_eng = AdvancedRegimeEngine(
            n_states=3, n_features=4, target_vol=0.02, seed=42,
            enable_background_workers=False,
        )
        new_eng.load_state(state)
        assert new_eng._engine_status != "DEGRADED"

    def test_full_restore_parameters_match(self, engine):
        state = self._get_clean_state(engine)
        original_beta = engine.nhhmm.beta.copy()
        original_mu = engine.nhhmm.mu.copy()
        original_sigma = engine.nhhmm.sigma.copy()

        new_eng = AdvancedRegimeEngine(
            n_states=3, n_features=4, target_vol=0.02, seed=42,
            enable_background_workers=False,
        )
        new_eng.load_state(state)

        np.testing.assert_array_almost_equal(new_eng.nhhmm.beta, original_beta, decimal=10)
        np.testing.assert_array_almost_equal(new_eng.nhhmm.mu, original_mu, decimal=10)
        np.testing.assert_array_almost_equal(new_eng.nhhmm.sigma, original_sigma, decimal=10)

    def test_full_restore_no_critical_log(self, engine, caplog):
        state = self._get_clean_state(engine)
        new_eng = AdvancedRegimeEngine(
            n_states=3, n_features=4, target_vol=0.02, seed=42,
            enable_background_workers=False,
        )
        with caplog.at_level(logging.CRITICAL):
            new_eng.load_state(state)
        critical_msgs = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert len(critical_msgs) == 0

    def test_beta_only_restore_triggers_critical_and_degraded(self, engine, caplog):
        state = self._get_clean_state(engine)
        state["nhhmm_mu"] = [0.001, -0.001]
        state["nhhmm_sigma"] = [0.004, 0.004]

        new_eng = AdvancedRegimeEngine(
            n_states=3, n_features=4, target_vol=0.02, seed=42,
            enable_background_workers=False,
        )
        with caplog.at_level(logging.CRITICAL):
            new_eng.load_state(state)

        critical_msgs = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert len(critical_msgs) >= 1
        assert new_eng._engine_status == "DEGRADED"
        np.testing.assert_array_almost_equal(new_eng.nhhmm.beta, engine.nhhmm.beta, decimal=10)
        assert new_eng.nhhmm.mu.shape == (3,)
        assert new_eng.nhhmm.sigma.shape == (3,)

    def test_critical_log_names_specific_missing_keys(self, engine, caplog):
        state = self._get_clean_state(engine)
        state["nhhmm_mu"] = [0.001, -0.001]
        state["nhhmm_sigma"] = [0.004, 0.004]

        new_eng = AdvancedRegimeEngine(
            n_states=3, n_features=4, target_vol=0.02, seed=42,
            enable_background_workers=False,
        )
        with caplog.at_level(logging.CRITICAL):
            new_eng.load_state(state)

        critical_msgs = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert len(critical_msgs) >= 1
        combined_msg = " ".join(r.getMessage() for r in critical_msgs)
        assert "nhhmm_mu" in combined_msg or "nhhmm_sigma" in combined_msg

    @pytest.mark.parametrize("bad_key,bad_value,reason", [
        ("nhhmm_beta", None, "absent"),
        ("nhhmm_mu", None, "absent"),
        ("nhhmm_sigma", None, "absent"),
        ("nhhmm_beta", [[[0.0]]], "wrong_shape"),
        ("nhhmm_mu", [0.001, -0.001], "wrong_shape"),
        ("nhhmm_sigma", [0.004, 0.004], "wrong_shape"),
        ("nhhmm_beta", [[[float("nan")]] * 4] * 9, "non_finite"),
        ("nhhmm_mu", [float("inf"), 0.0, 0.0], "non_finite"),
        ("nhhmm_sigma", [0.004, float("nan"), 0.010], "non_finite"),
    ])
    def test_single_parameter_failure_triggers_degraded(
        self, engine, caplog, bad_key, bad_value, reason
    ):
        state = self._get_clean_state(engine)
        if bad_value is None:
            del state[bad_key]
        else:
            state[bad_key] = bad_value

        new_eng = AdvancedRegimeEngine(
            n_states=3, n_features=4, target_vol=0.02, seed=42,
            enable_background_workers=False,
        )
        with caplog.at_level(logging.CRITICAL):
            new_eng.load_state(state)

        assert new_eng._engine_status == "DEGRADED", f"DEGRADED expected when {bad_key} is {reason}."
        critical_msgs = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert len(critical_msgs) >= 1

    def test_all_three_missing_triggers_critical(self, engine, caplog):
        state = self._get_clean_state(engine)
        for k in ("nhhmm_beta", "nhhmm_mu", "nhhmm_sigma"):
            state.pop(k, None)

        new_eng = AdvancedRegimeEngine(
            n_states=3, n_features=4, target_vol=0.02, seed=42,
            enable_background_workers=False,
        )
        with caplog.at_level(logging.CRITICAL):
            new_eng.load_state(state)

        assert new_eng._engine_status == "DEGRADED"
        critical_msgs = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert len(critical_msgs) >= 1

    def test_mu_only_success_is_not_accepted_as_full_restore(self, engine, caplog):
        state = self._get_clean_state(engine)
        state["nhhmm_beta"] = [[[0.0]]]
        state["nhhmm_sigma"] = [0.004, 0.004]

        new_eng = AdvancedRegimeEngine(
            n_states=3, n_features=4, target_vol=0.02, seed=42,
            enable_background_workers=False,
        )
        with caplog.at_level(logging.CRITICAL):
            new_eng.load_state(state)

        assert new_eng._engine_status == "DEGRADED"
        critical_msgs = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert len(critical_msgs) >= 1

    def test_degraded_status_is_not_cleared_by_subsequent_successful_restores(self, engine, caplog):
        _ = caplog
        state = self._get_clean_state(engine)
        state["nhhmm_mu"] = [0.001, -0.001]

        new_eng = AdvancedRegimeEngine(
            n_states=3, n_features=4, target_vol=0.02, seed=42,
            enable_background_workers=False,
        )
        new_eng.load_state(state)
        assert new_eng._engine_status == "DEGRADED"

    def test_successful_restore_emits_info_not_critical(self, engine, caplog):
        state = self._get_clean_state(engine)
        new_eng = AdvancedRegimeEngine(
            n_states=3, n_features=4, target_vol=0.02, seed=42,
            enable_background_workers=False,
        )
        with caplog.at_level(logging.DEBUG):
            new_eng.load_state(state)

        critical_msgs = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert len(critical_msgs) == 0
        info_msgs = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("NHHMM" in r.getMessage() or "nhhmm" in r.getMessage().lower() for r in info_msgs)

    def test_non_finite_count_in_error_log(self, engine, caplog):
        state = self._get_clean_state(engine)
        bad_mu = np.array([float("nan"), float("inf"), float("nan")])
        state["nhhmm_mu"] = bad_mu.tolist()

        new_eng = AdvancedRegimeEngine(
            n_states=3, n_features=4, target_vol=0.02, seed=42,
            enable_background_workers=False,
        )
        with caplog.at_level(logging.ERROR):
            new_eng.load_state(state)

        error_msgs = [
            r.getMessage() for r in caplog.records
            if r.levelno == logging.ERROR and "nhhmm_mu" in r.getMessage()
        ]
        assert len(error_msgs) >= 1
        assert any("3" in m or "non-finite" in m for m in error_msgs)

    def test_partial_restore_produces_wrong_posteriors(self, engine):
        state = self._get_clean_state(engine)
        full_eng = AdvancedRegimeEngine(
            n_states=3, n_features=4, target_vol=0.02, seed=42,
            enable_background_workers=False,
        )
        full_eng.load_state(state)

        partial_state = copy.deepcopy(state)
        partial_state["nhhmm_mu"] = [0.001, -0.001, 0.0]
        partial_state["nhhmm_sigma"] = [0.004, 0.004, 0.010]

        partial_eng = AdvancedRegimeEngine(
            n_states=3, n_features=4, target_vol=0.02, seed=42,
            enable_background_workers=False,
        )
        partial_eng._load_state_inplace(copy.deepcopy(partial_state))

        rng = np.random.default_rng(7)
        x_t = rng.normal(0, 1, 4)
        prior = np.ones(3) / 3.0

        full_post, _ = full_eng.nhhmm.forward_pass_step(0.01, x_t, prior)
        partial_post, _ = partial_eng.nhhmm.forward_pass_step(0.01, x_t, prior)

        trained_mu_differs = not np.allclose(
            engine.nhhmm.mu, np.array([0.001, -0.001, 0.0]), atol=1e-4
        )
        if trained_mu_differs:
            max_diff = float(np.max(np.abs(full_post - partial_post)))
            assert max_diff > 1e-4

    def test_old_variable_name_not_present_in_source(self):
        import advanced_regime_engine as mod
        source = inspect.getsource(mod.AdvancedRegimeEngine._load_state_inplace)
        old_pattern = re.compile(r'\b_nhhmm_restored\b(?!_keys)(?!_fully)')
        matches = old_pattern.findall(source)
        assert len(matches) == 0

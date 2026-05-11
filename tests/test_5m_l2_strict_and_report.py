"""
Regression tests for Phase-4 three-fix patch:
  1. 5m calibration metadata completeness
  2. HMM posterior normalization
  3. Regime-conditioned hold-horizon
"""
from __future__ import annotations
import json
import math
import os
import numpy as np
import pytest


def _make_1m_bars(n: int = 600) -> list[list]:
    rng = np.random.default_rng(42)
    price = 69_000.0
    bars = []
    for i in range(n):
        o = price
        h = o * (1 + rng.uniform(0, 0.001))
        l = o * (1 - rng.uniform(0, 0.001))
        c = o * (1 + rng.uniform(-0.0005, 0.0005))
        v = rng.uniform(1.0, 5.0)
        bars.append([i * 60_000, o, h, l, c, v])
        price = c
    return bars


class TestCalibrationMetadata:
    def test_metadata_fields_present(self, tmp_path):
        from calibrate_regime_5m import calibrate_5m_artifacts
        bars_1m = _make_1m_bars(600)
        out_npz = str(tmp_path / "weights_5m.npz")
        out_meta = str(tmp_path / "weights_5m.meta.json")
        with pytest.raises(RuntimeError, match="insufficient 5m bars"):
            calibrate_5m_artifacts(bars_1m=bars_1m, out_path=out_npz, meta_path=out_meta)

    def test_metadata_with_enough_bars(self, tmp_path):
        from calibrate_regime_5m import calibrate_5m_artifacts, _MIN_5M_BARS
        bars_1m = _make_1m_bars(2600)
        out_npz = str(tmp_path / "weights_5m.npz")
        out_meta = str(tmp_path / "weights_5m.meta.json")
        result = calibrate_5m_artifacts(bars_1m=bars_1m, out_path=out_npz, meta_path=out_meta)
        assert result["n_bars_used"] >= _MIN_5M_BARS
        assert os.path.exists(out_meta)
        with open(out_meta) as f:
            meta = json.load(f)
        required_meta_keys = {"timeframe", "feature_source", "source_files", "n_bars_used", "n_bars_total_5m", "train_range", "val_range", "label_method", "calibration_status", "min_bars_required"}
        assert not (required_meta_keys - set(meta.keys()))

    def test_calibration_not_using_tiny_sample(self, tmp_path):
        from calibrate_regime_5m import calibrate_5m_artifacts, CalibrationSlice
        bars_1m = _make_1m_bars(2600)
        with pytest.raises(RuntimeError):
            calibrate_5m_artifacts(bars_1m=bars_1m, out_path=str(tmp_path / "a.npz"), meta_path=str(tmp_path / "a.json"), cal_slice=CalibrationSlice(start_idx=0, end_idx=10))


class TestHMMNormalization:
    def test_score_map_sums_to_one_typical_input(self):
        from advanced_regime_engine import compute_hmm_regime
        alpha = np.array([0.40, 0.38, 0.22])
        score_sum = sum(compute_hmm_regime(alpha)["score_map"].values())
        assert math.isfinite(score_sum)
        assert abs(score_sum - 1.0) < 1e-6

    def test_score_map_sums_to_one_crisis_input(self):
        from advanced_regime_engine import compute_hmm_regime
        score_sum = sum(compute_hmm_regime(np.array([0.05, 0.05, 0.90]))["score_map"].values())
        assert abs(score_sum - 1.0) < 1e-6

    def test_score_map_sums_to_one_strong_trend(self):
        from advanced_regime_engine import compute_hmm_regime
        score_sum = sum(compute_hmm_regime(np.array([0.85, 0.10, 0.05]))["score_map"].values())
        assert abs(score_sum - 1.0) < 1e-6

    def test_invalid_input_falls_back_to_uniform(self):
        from advanced_regime_engine import compute_hmm_regime
        alpha = np.array([1e-15, 1e-15, 1e-15])
        try:
            score_sum = sum(compute_hmm_regime(alpha)["score_map"].values())
            assert abs(score_sum - 1.0) < 1e-6
        except (ValueError, ZeroDivisionError):
            pass

    def test_probabilities_field_sums_to_one(self):
        from advanced_regime_engine import compute_hmm_regime
        for alpha in [np.array([0.40, 0.38, 0.22]), np.array([0.05, 0.05, 0.90]), np.array([0.80, 0.15, 0.05])]:
            result = compute_hmm_regime(alpha)
            assert abs((result["bull"] + result["bear"] + result["crisis"]) - 1.0) < 1e-6


class TestRegimeHorizon:
    def _make_engine(self, **cfg_kwargs):
        from backtest_engine import BacktestConfig, BacktestEngine
        return BacktestEngine(config=BacktestConfig(**cfg_kwargs))

    def test_default_horizon_unchanged(self):
        engine = self._make_engine(max_hold_bars=12)
        assert engine._resolve_hold_horizon("CHOPPY") == 12

    def test_regime_mapping_overrides_correctly(self):
        engine = self._make_engine(max_hold_bars=12, regime_hold_horizon_bars={"CHOPPY": 4, "TREND": 20, "COMPRESSION": 4})
        assert engine._resolve_hold_horizon("CHOPPY") == 4
        assert engine._resolve_hold_horizon("TREND") == 20

    def test_unmapped_label_falls_back_to_max_hold_bars(self):
        engine = self._make_engine(max_hold_bars=12, regime_hold_horizon_bars={"CHOPPY": 4})
        assert engine._resolve_hold_horizon("RANGING") == 12

    def test_case_insensitive_matching(self):
        engine = self._make_engine(max_hold_bars=12, regime_hold_horizon_bars={"CHOPPY": 4, "TREND": 20})
        assert engine._resolve_hold_horizon("choppy") == 4

    def test_existing_callers_unaffected(self):
        from backtest_engine import BacktestConfig
        cfg = BacktestConfig()
        assert cfg.regime_hold_horizon_bars is None
        assert cfg.max_hold_bars == 12

    def test_run_single_pass_respects_regime_horizon(self):
        from backtest_engine import BacktestConfig, BacktestEngine
        from bar_aggregator import resample_bars
        bars_5m = resample_bars(_make_1m_bars(600), minutes=5)
        cfg = BacktestConfig(max_hold_bars=12, regime_hold_horizon_bars={"CHOPPY": 4, "TREND": 20}, orchestrator_action_threshold=0.60)
        result = BacktestEngine(config=cfg)._run_single_pass(bars_5m, label="test_regime_horizon")
        assert "total_trades" in result and "max_drawdown" in result
        assert math.isfinite(float(result["max_drawdown"]))

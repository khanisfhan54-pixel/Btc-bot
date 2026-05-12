import json
import os

import numpy as np
import pytest

from advanced_regime_engine import compute_hmm_regime
REQUIRED_META_KEYS = {
    "timeframe", "real_book_only", "feature_source", "ofi_source",
    "blocker_on_missing_data", "source_files", "n_bars_total_5m",
    "n_bars_used", "n_bars_ofi_nonzero", "train_range", "val_range",
    "label_method", "calibration_status", "min_bars_required",
}


class TestComputeHmmRegimeNormalization:

    @pytest.mark.parametrize("alpha", [
        np.array([1/3, 1/3, 1/3]),
        np.array([0.8, 0.15, 0.05]),
        np.array([0.05, 0.80, 0.15]),
        np.array([0.05, 0.05, 0.90]),
        np.array([0.99, 0.005, 0.005]),
        np.array([0.001, 0.001, 0.998]),
        np.array([0.5, 0.5, 0.0]),
    ])
    def test_score_map_sums_to_one(self, alpha):
        alpha_norm = alpha / alpha.sum()
        result = compute_hmm_regime(alpha_norm)
        sm = result["score_map"]
        total = sum(sm.values())
        assert abs(total - 1.0) < 1e-9

    def test_score_map_values_are_finite(self):
        alpha = np.array([0.4, 0.4, 0.2])
        result = compute_hmm_regime(alpha)
        for _, v in result["score_map"].items():
            assert np.isfinite(v)

    def test_score_map_values_non_negative(self):
        alpha = np.array([0.6, 0.3, 0.1])
        result = compute_hmm_regime(alpha)
        for _, v in result["score_map"].items():
            assert v >= 0.0

    def test_metadata_score_sum_is_raw_pre_normalization(self):
        alpha = np.array([0.8, 0.15, 0.05])
        result = compute_hmm_regime(alpha)
        raw_sum = result["metadata"]["score_sum"]
        assert isinstance(raw_sum, float)
        assert np.isfinite(raw_sum)
        assert raw_sum > 0.0

    def test_degenerate_near_zero_sum_falls_back_to_uniform(self):
        alpha = np.array([0.001, 0.001, 0.998])
        result = compute_hmm_regime(alpha)
        sm = result["score_map"]
        total = sum(sm.values())
        assert abs(total - 1.0) < 1e-9
        for v in sm.values():
            assert np.isfinite(v) and v >= 0.0

    def test_invalid_input_raises_value_error(self):
        with pytest.raises(ValueError):
            compute_hmm_regime(np.array([np.nan, 0.5, 0.5]))
        with pytest.raises(ValueError):
            compute_hmm_regime(np.array([0.5, 0.5]))

    def test_zero_sum_input_raises_value_error(self):
        with pytest.raises(ValueError):
            compute_hmm_regime(np.array([0.0, 0.0, 0.0]))

    def test_negative_probability_raises_value_error(self):
        with pytest.raises(ValueError):
            compute_hmm_regime(np.array([-0.1, 0.6, 0.5]))

    def test_regime_label_is_valid(self):
        for alpha in [
            np.array([0.8, 0.1, 0.1]),
            np.array([0.1, 0.8, 0.1]),
            np.array([0.1, 0.1, 0.8]),
        ]:
            result = compute_hmm_regime(alpha)
            assert result["regime"] in {"TREND", "BEAR", "RANGE", "TOXIC"}


def _make_synthetic_bars(n=600):
    rng = np.random.default_rng(0)
    price = 40000.0
    rows = []
    for i in range(n):
        o = price
        h = o * (1 + rng.uniform(0, 0.003))
        l = o * (1 - rng.uniform(0, 0.003))
        c = o * (1 + rng.uniform(-0.002, 0.002))
        v = rng.uniform(10, 100)
        rows.append([i * 300_000, o, h, l, c, v])
        price = c
    return rows


class TestCalibrateRegime5mBlockers:
    def test_5m_meta_has_required_keys(self, tmp_path):
        if not os.path.exists("data/ohlcv_1m.csv") or not os.path.exists("data/bookDepth.csv"):
            pytest.skip("requires local fixture files")
        from calibrate_regime_5m import calibrate_5m_artifacts, _load_1m_csv
        out_meta = str(tmp_path / "meta.json")
        calibrate_5m_artifacts(bars_1m=_load_1m_csv("data/ohlcv_1m.csv"), out_path=str(tmp_path / "w.npz"), meta_path=out_meta)
        meta = json.load(open(out_meta))
        assert REQUIRED_META_KEYS.issubset(meta.keys()), f"missing: {REQUIRED_META_KEYS - meta.keys()}"

    def test_n_bars_ofi_nonzero_when_book_present(self):
        if not os.path.exists("weights/advanced_regime_weights_5m.meta.json"):
            pytest.skip("meta not present")
        meta = json.load(open("weights/advanced_regime_weights_5m.meta.json"))
        assert meta["n_bars_ofi_nonzero"] > 0

    def test_calibration_blocks_on_missing_book(self, tmp_path, monkeypatch):
        pytest.importorskip("pandas")
        import calibrate_regime_5m as m
        monkeypatch.setattr(m, "_L2_BOOK_PATH", str(tmp_path / "no_l2.csv"))
        with pytest.raises(RuntimeError, match="BLOCKER"):
            m.calibrate_5m_artifacts(
                bars_1m=_make_synthetic_bars(3000),
                out_path=str(tmp_path / "w.npz"),
                meta_path=str(tmp_path / "m.json"),
            )

    def test_missing_l2_book_raises_blocker(self, tmp_path, monkeypatch):
        pytest.importorskip("pandas")
        import calibrate_regime_5m as m
        monkeypatch.setattr(m, "_L2_BOOK_PATH", str(tmp_path / "no_l2.csv"))
        with pytest.raises(RuntimeError, match="BLOCKER"):
            m._build_5m_features(_make_synthetic_bars(600), _make_synthetic_bars(600))

    def test_empty_l2_book_raises_blocker(self, tmp_path, monkeypatch):
        pytest.importorskip("pandas")
        import calibrate_regime_5m as m
        empty = tmp_path / "empty_l2.csv"
        empty.write_text("timestamp,bidPrice0,bidPrice1,askPrice0,askPrice1\n")
        monkeypatch.setattr(m, "_L2_BOOK_PATH", str(empty))
        with pytest.raises(RuntimeError, match="BLOCKER"):
            m._build_5m_features(_make_synthetic_bars(600), _make_synthetic_bars(600))

    def test_malformed_l2_book_raises_blocker(self, tmp_path, monkeypatch):
        pytest.importorskip("pandas")
        import calibrate_regime_5m as m
        bad = tmp_path / "bad_l2.csv"
        bad.write_text(
            "timestamp,bidPrice0,bidPrice1,askPrice0,askPrice1,"
            "bidQty0,bidQty1,askQty0,askQty1\n"
            "1,100,99,101,102,bad,1,1,1\n"
        )
        monkeypatch.setattr(m, "_L2_BOOK_PATH", str(bad))
        with pytest.raises(RuntimeError, match="BLOCKER"):
            m._build_5m_features(_make_synthetic_bars(600), _make_synthetic_bars(600))

    def test_no_synthetic_ofi_string_in_source(self):
        pytest.importorskip("pandas")
        import inspect, calibrate_regime_5m as m
        src = inspect.getsource(m)
        forbidden = ["ofi_z=0", "ohlcv_synthetic", "synthetic_fallback", "ofi_z = 0", "ofi_z=0.0"]
        for tok in forbidden:
            assert tok not in src

    def test_meta_contains_required_provenance_fields(self, tmp_path):
        required_fields = {
            "real_book_only", "feature_source", "ofi_source",
            "blocker_on_missing_data", "n_bars_ofi_nonzero",
            "n_bars_total_5m", "n_bars_used", "train_range", "val_range",
            "label_method", "calibration_status", "min_bars_required",
            "source_files",
        }
        if not os.path.exists("data/bookDepth.csv"):
            pytest.skip("Real L2 book file absent — integration test skipped")
        if not os.path.exists("data/ohlcv_1m.csv"):
            pytest.skip("Real 1m OHLCV absent — integration test skipped")

        from calibrate_regime_5m import calibrate_5m_artifacts, _load_1m_csv
        bars_1m = _load_1m_csv("data/ohlcv_1m.csv")
        out_npz = str(tmp_path / "test_weights.npz")
        out_meta = str(tmp_path / "test_meta.json")
        calibrate_5m_artifacts(bars_1m=bars_1m, out_path=out_npz, meta_path=out_meta)
        with open(out_meta) as f:
            meta = json.load(f)
        missing = required_fields - set(meta.keys())
        assert not missing
        assert meta["real_book_only"] is True
        assert meta["blocker_on_missing_data"] is True
        assert meta["ofi_source"] == "real_l2_depth"
        assert meta["feature_source"] == "real_l2_depth"
        assert meta["calibration_status"] == "calibrated"
        assert isinstance(meta["n_bars_ofi_nonzero"], int)
        assert meta["n_bars_ofi_nonzero"] > 0


class TestRunValidationReportHonesty:
    def test_report_comparison_fields_present(self):
        if not os.path.exists("backtest_summary.json"):
            pytest.skip("backtest_summary.json absent")
        report = json.load(open("backtest_summary.json"))
        assert "prior_run_comparison" in report
        comp = report["prior_run_comparison"]
        for field in ["total_trades", "win_rate", "profit_factor",
                      "max_drawdown", "sharpe", "net_expectancy"]:
            assert field in comp, f"missing comparison field: {field}"

    def test_report_contains_run_status_field(self):
        if not os.path.exists("backtest_summary.json"):
            pytest.skip("backtest_summary.json not present — run run_5m_research_validation.py first")
        with open("backtest_summary.json") as f:
            report = json.load(f)
        assert "run_status" in report
        assert report["run_status"] in {"BLOCKED", "OK", "PARTIAL"}

    def test_report_calibration_section_present(self):
        if not os.path.exists("backtest_summary.json"):
            pytest.skip("backtest_summary.json absent")
        with open("backtest_summary.json") as f:
            report = json.load(f)
        assert "calibration" in report
        cal = report["calibration"]
        assert "status" in cal
        assert cal["status"] in {"ok", "failed", "blocked"}

    def test_blockers_list_present(self):
        if not os.path.exists("backtest_summary.json"):
            pytest.skip("backtest_summary.json absent")
        with open("backtest_summary.json") as f:
            report = json.load(f)
        assert "blockers" in report
        assert isinstance(report["blockers"], list)

    def test_unavailable_metrics_field_present(self):
        if not os.path.exists("backtest_summary.json"):
            pytest.skip("backtest_summary.json absent")
        report = json.load(open("backtest_summary.json"))
        assert "unavailable_metrics" in report
        assert isinstance(report["unavailable_metrics"], list)
        names = [u.get("metric") for u in report["unavailable_metrics"]]
        assert "macro_f1" in names

    def test_report_does_not_hide_blocker_when_cal_failed(self):
        if not os.path.exists("backtest_summary.json"):
            pytest.skip("backtest_summary.json absent")
        with open("backtest_summary.json") as f:
            report = json.load(f)
        cal_status = report.get("calibration", {}).get("status", "ok")
        if cal_status == "failed":
            assert report.get("run_status") != "OK"
    def test_unavailable_metrics_not_blocker(self):
        if not os.path.exists("backtest_summary.json"):
            pytest.skip("backtest_summary.json absent")
        report = json.load(open("backtest_summary.json"))
        # unavailable_metrics must be a separate list, not inside blockers
        assert "unavailable_metrics" in report
        assert isinstance(report["unavailable_metrics"], list)
        metric_names = [u.get("metric") for u in report["unavailable_metrics"]]
        assert "macro_f1" in metric_names
        # blockers must not contain macro_f1
        blocker_reasons = " ".join(
            str(b.get("reason", "")) for b in report.get("blockers", [])
        )
        assert "macro_f1" not in blocker_reasons, (
            "macro_f1 must appear in unavailable_metrics, not in blockers"
        )


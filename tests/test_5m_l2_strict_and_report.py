import csv
import json
import sys
import types
from pathlib import Path

from data_tools.l2_to_backtest import align_book_to_bars, load_l2_csv


def _write_csv(path: Path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def test_load_l2_csv_raises_on_missing_ofi_z(tmp_path: Path):
    p = tmp_path / "book.csv"
    _write_csv(p, ["timestamp", "bidPrice", "askPrice", "bidQty", "askQty"], [[1000, 10, 11, 1, 1]])
    try:
        load_l2_csv(str(p))
        assert False, "expected ValueError"
    except ValueError as e:
        assert "ofi_z" in str(e)


def test_align_book_to_bars_fails_closed_on_unalignable():
    bars = [[1000, 1, 1, 1, 1, 1]]
    try:
        align_book_to_bars(bars, [])
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_valid_rows_load_and_align_deterministically(tmp_path: Path):
    p = tmp_path / "book.csv"
    _write_csv(
        p,
        ["timestamp", "bidPrice", "askPrice", "bidQty", "askQty", "ofi_z"],
        [[1000, 10, 11, 1, 1, 0.1], [2000, 10, 11, 2, 1, 0.2]],
    )
    snaps = load_l2_csv(str(p))
    bars = [[1500, 1, 1, 1, 1, 1], [2500, 1, 1, 1, 1, 1]]
    a1 = align_book_to_bars(bars, snaps)
    a2 = align_book_to_bars(bars, snaps)
    assert [s.ofi_z for s in a1] == [0.1, 0.2]
    assert [s.timestamp for s in a1] == [s.timestamp for s in a2]


def test_report_contract_fields_present():
    src = Path("run_5m_research_validation.py").read_text(encoding="utf-8")
    for key in [
        "threshold_selection_mode",
        "research_only",
        "selection_cost_basis",
        "production_parity",
        "metrics_table",
        "baseline_1m",
        "candidate_5m",
    ]:
        assert key in src
    assert "4.0" not in src


def _import_validation_module_with_pandas_stub(csv_rows):
    import importlib
    import pytest

    pytest.importorskip("numpy")

    class _DF:
        def __init__(self, rows):
            self._rows = rows

        def __getitem__(self, cols):
            class _Slice:
                def __init__(self, rows, cols):
                    class _Vals(list):
                        def tolist(self):
                            return list(self)

                    self.values = _Vals([[r[c] for c in cols] for r in rows])

            return _Slice(self._rows, cols)

    pandas_stub = types.SimpleNamespace(read_csv=lambda path: _DF(csv_rows))
    sys.modules["pandas"] = pandas_stub
    sys.modules.pop("run_5m_research_validation", None)
    return importlib.import_module("run_5m_research_validation")


def test_report_build_writes_json_without_nameerror(monkeypatch, tmp_path: Path):
    mod = _import_validation_module_with_pandas_stub([{"timestamp": i * 1000, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(200)])

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    _write_csv(
        tmp_path / "data" / "ohlcv_1m.csv",
        ["timestamp", "open", "high", "low", "close", "volume"],
        [[i * 1000, 1, 1, 1, 1, 1] for i in range(200)],
    )

    monkeypatch.setattr(mod, "resample_bars", lambda bars, minutes: [[i * 1000, 1, 1, 1, 1, 1] for i in range(700)])
    monkeypatch.setattr(mod, "calibrate_5m_artifacts", lambda **kwargs: {"output_path": "weights/mock.npz"})
    monkeypatch.setattr(mod, "_select_threshold", lambda *args, **kwargs: {"threshold": 0.5, "threshold_selection_mode": "research_only", "selection_cost_basis": {"fee_bps": 0.0, "slippage_bps": 0.0, "round_trip_cost_bps": 0.0, "formula": "x"}, "production_parity": False})

    class DummyEngine:
        def __init__(self, config=None, weight_path=None):
            pass

        def _run_single_pass(self, bars, label=""):
            return {"total_trades": 0, "win_rate": 0.0, "expectancy": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "trade_log": []}

    monkeypatch.setattr(mod, "BacktestEngine", DummyEngine)

    mod.main()

    out = json.loads((tmp_path / "audit_output" / "5m_walk_forward_results.json").read_text(encoding="utf-8"))
    assert "metrics_table" in out
    assert "baseline_1m" in out["metrics_table"]
    assert "candidate_5m" in out["metrics_table"]


def test_missing_metrics_stay_explicit(monkeypatch, tmp_path: Path):
    mod = _import_validation_module_with_pandas_stub([{"timestamp": i * 1000, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(200)])

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    _write_csv(
        tmp_path / "data" / "ohlcv_1m.csv",
        ["timestamp", "open", "high", "low", "close", "volume"],
        [[i * 1000, 1, 1, 1, 1, 1] for i in range(200)],
    )

    monkeypatch.setattr(mod, "resample_bars", lambda bars, minutes: [[i * 1000, 1, 1, 1, 1, 1] for i in range(700)])
    monkeypatch.setattr(mod, "calibrate_5m_artifacts", lambda **kwargs: {"output_path": "weights/mock.npz"})
    monkeypatch.setattr(mod, "_select_threshold", lambda *args, **kwargs: {"threshold": 0.5, "threshold_selection_mode": "research_only", "selection_cost_basis": {"fee_bps": 0.0, "slippage_bps": 0.0, "round_trip_cost_bps": 0.0, "formula": "x"}, "production_parity": False})

    class DummyEngine:
        def __init__(self, config=None, weight_path=None):
            pass

        def _run_single_pass(self, bars, label=""):
            return {"total_trades": 0, "win_rate": 0.0, "expectancy": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "trade_log": []}

    monkeypatch.setattr(mod, "BacktestEngine", DummyEngine)
    mod.main()
    out = json.loads((tmp_path / "audit_output" / "5m_walk_forward_results.json").read_text(encoding="utf-8"))
    assert out["metrics_table"]["baseline_1m"]["signals"]["macro_f1"] is None
    assert out["metrics_table"]["candidate_5m"]["signals"]["macro_f1"] is None
    assert out["metrics_table"]["comparison"]["delta_macro_f1"] is None
    assert any("macro_f1" in b["reason"] for b in out["blockers"])

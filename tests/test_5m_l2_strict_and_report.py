import csv
import json
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

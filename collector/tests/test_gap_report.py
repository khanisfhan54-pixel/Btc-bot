import os

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from collector.scripts.gap_report import generate_gap_report


def _write_trades_timestamps(data_dir, date_str, timestamps):
    trades_dir = os.path.join(data_dir, "raw", "trades")
    os.makedirs(trades_dir)
    df = pd.DataFrame({"timestamp": pd.to_datetime(timestamps, unit="ms", utc=True)})
    table = pa.Table.from_pandas(df, preserve_index=False)
    schema = pa.schema([pa.field("timestamp", pa.timestamp("ms", tz="UTC"))])
    pq.write_table(table.cast(schema), os.path.join(trades_dir, f"{date_str}-00.parquet"))


def _trades_gap_stats(output):
    trades_line = next(line for line in output.splitlines() if line.startswith("trades"))
    parts = trades_line.split()
    return int(parts[2]), int(parts[5]), parts[7]


def test_gap_report_detects_datetime_trades_gap_above_threshold(tmp_path, capsys):
    date_str = "2026-06-03"
    start_ts = int(pd.Timestamp(f"{date_str} 00:00:00", tz="UTC").timestamp() * 1000)
    _write_trades_timestamps(str(tmp_path), date_str, [start_ts, start_ts + 1000, start_ts + 7000])

    generate_gap_report(date_str, date_str, data_dir=str(tmp_path))

    num_gaps, longest_gap, coverage = _trades_gap_stats(capsys.readouterr().out)
    assert num_gaps == 1
    assert longest_gap == 6000
    assert coverage == "99.99%"


def test_gap_report_ignores_datetime_trades_gap_below_threshold(tmp_path, capsys):
    date_str = "2026-06-03"
    start_ts = int(pd.Timestamp(f"{date_str} 00:00:00", tz="UTC").timestamp() * 1000)
    _write_trades_timestamps(str(tmp_path), date_str, [start_ts, start_ts + 1000, start_ts + 5000])

    generate_gap_report(date_str, date_str, data_dir=str(tmp_path))

    num_gaps, longest_gap, coverage = _trades_gap_stats(capsys.readouterr().out)
    assert num_gaps == 0
    assert longest_gap == 0
    assert coverage == "100.00%"

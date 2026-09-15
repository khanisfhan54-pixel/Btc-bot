"""Regression coverage for SIGKILL recovery of an open Parquet segment."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from collector.collector.parquet_writer import ParquetWriter


def test_sigkill_discards_open_segment_and_reports_persisted_loss(tmp_path):
    base_dir = str(tmp_path)
    stream_name = "sigkill"
    script = """
import sys
import time
import pyarrow as pa
from collector.collector.parquet_writer import ParquetWriter
writer = ParquetWriter(sys.argv[1], pa.schema([('timestamp', pa.int64()), ('value', pa.float64())]), base_dir=sys.argv[2])
for value in range(3):
    writer.write({'timestamp': value, 'value': float(value)})
writer.flush()
print('flushed', flush=True)
time.sleep(60)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, stream_name, base_dir],
        cwd=str(Path(__file__).resolve().parents[2]), stdout=subprocess.PIPE, text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "flushed"
        stream_dir = tmp_path / "raw" / stream_name
        tmp_file = next(stream_dir.glob("*.seg.tmp"))
        assert next(stream_dir.glob("*.seg.count.json"))

        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=10)

        with pytest.raises(Exception):
            pq.read_table(tmp_file)
        with pytest.raises(Exception):
            pq.ParquetFile(tmp_file)

        events = []
        writer = ParquetWriter(
            stream_name,
            pa.schema([("timestamp", pa.int64()), ("value", pa.float64())]),
            base_dir=base_dir,
            quality_event_sink=events.append,
        )
        writer.close()
        assert not list(stream_dir.glob("*.seg.tmp"))
        assert not list(stream_dir.glob("*.seg.count.json"))
        assert len(events) == 1
        assert events[0]["exchange"] == "BINANCE"
        assert events[0]["stream"] == stream_name
        assert events[0]["event_type"] == "DATA_DROP"
        assert events[0]["reason"] == "crashed_segment_discarded"
        assert events[0]["rows_lost"] == 3
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

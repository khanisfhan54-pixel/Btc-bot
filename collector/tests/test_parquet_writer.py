import os
import pytest
import pyarrow as pa
from collector.parquet_writer import HourlyParquetWriter

def test_parquet_writer(tmp_path):
    schema = pa.schema([
        ("exchange_timestamp", pa.int64()),
        ("value", pa.float64())
    ])

    writer = HourlyParquetWriter("test", schema, str(tmp_path))

    record = {"exchange_timestamp": 1234567890, "value": 1.0}

    # Write one record
    writer.write_record(record)
    assert len(writer.buffer) == 1

    # Simulate an hour passing
    writer.current_hour = "2024-01-01-00"
    writer.flush()

    assert len(writer.buffer) == 0

    # Verify file created
    files = list(tmp_path.glob("*.parquet"))
    assert len(files) == 1

    # Verify schema metadata
    table = pa.parquet.read_table(files[0])
    assert b"schema_version" in table.schema.metadata

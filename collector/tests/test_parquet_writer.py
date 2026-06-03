import pytest
import os
import shutil
import pyarrow as pa
import pandas as pd
from collector.parquet_writer import ParquetWriter

@pytest.fixture
def temp_dir(tmp_path):
    yield str(tmp_path)

def test_parquet_writer(temp_dir):
    schema = pa.schema([
        ("timestamp", pa.int64()),
        ("value", pa.float64())
    ], metadata={"schema_version": "1.0"})

    writer = ParquetWriter("test_stream", schema, base_dir=temp_dir)

    writer.write({"timestamp": 1000, "value": 1.5})
    writer.write({"timestamp": 2000, "value": 2.5})

    assert len(writer.buffer) == 2

    writer.flush()
    assert len(writer.buffer) == 0

    writer.close()

    file_path = writer._get_filename(writer.current_hour)
    assert os.path.exists(file_path)

    df = pd.read_parquet(file_path)
    assert len(df) == 2
    assert df["value"].iloc[0] == 1.5
    assert df["value"].iloc[1] == 2.5

def test_parquet_writer_rotation(temp_dir, monkeypatch):
    schema = pa.schema([
        ("timestamp", pa.int64()),
        ("value", pa.float64())
    ])

    # Mock datetime to control time
    import datetime

    class MockDatetime:
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime(2026, 6, 3, 10, tzinfo=datetime.timezone.utc)
        @classmethod
        def utcnow(cls):
            return datetime.datetime(2026, 6, 3, 10)

    # Init writer at hour 10
    monkeypatch.setattr("collector.parquet_writer.datetime", MockDatetime)
    writer = ParquetWriter("test_stream2", schema, base_dir=temp_dir)

    assert writer.current_hour == "2026-06-03-10"

    writer.write({"timestamp": 1000, "value": 1.5})

    class MockDatetime11:
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime(2026, 6, 3, 11, tzinfo=datetime.timezone.utc)
        @classmethod
        def utcnow(cls):
            return datetime.datetime(2026, 6, 3, 11)

    monkeypatch.setattr("collector.parquet_writer.datetime", MockDatetime11)

    writer.write({"timestamp": 2000, "value": 2.5})

    assert writer.current_hour == "2026-06-03-11"

    writer.close()

    files = os.listdir(os.path.join(temp_dir, "raw", "test_stream2"))
    assert len(files) == 2

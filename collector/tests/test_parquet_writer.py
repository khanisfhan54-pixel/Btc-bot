import pytest
import json
import os
import shutil
import pyarrow as pa
import pyarrow.parquet as pa_parquet
import pandas as pd
from collector.collector.parquet_writer import ParquetWriter

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
    monkeypatch.setattr("collector.collector.parquet_writer.datetime", MockDatetime)
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

    monkeypatch.setattr("collector.collector.parquet_writer.datetime", MockDatetime11)

    writer.write({"timestamp": 2000, "value": 2.5})

    assert writer.current_hour == "2026-06-03-11"

    writer.close()

    files = os.listdir(os.path.join(temp_dir, "raw", "test_stream2"))
    parquet_files = [name for name in files if name.endswith(".parquet")]
    sidecar_files = [name for name in files if name.endswith(".parquet.meta.json")]
    assert len(parquet_files) == 2
    assert len(sidecar_files) == 2


def _trade_record(timestamp_ms=1770000000000):
    return {
        "timestamp": timestamp_ms,
        "exchange_timestamp": timestamp_ms + 1,
        "local_timestamp": timestamp_ms + 2,
        "trade_id": 123,
        "price": 50000.0,
        "quantity": 0.25,
        "is_buyer_maker": False,
        "side_sign": 1,
        "signed_qty": 0.25,
    }


def test_trades_schema_casts_integer_ms_to_utc_timestamps():
    from collector.collector.config import TRADES_SCHEMA

    table = pa.Table.from_pydict(
        {key: [value] for key, value in _trade_record().items()},
        schema=TRADES_SCHEMA,
    )

    expected_type = pa.timestamp("ms", tz="UTC")
    assert table.schema.field("timestamp").type == expected_type
    assert table.schema.field("exchange_timestamp").type == expected_type
    assert table.schema.field("local_timestamp").type == expected_type
    assert table.column("timestamp").type == expected_type


def test_trades_parquet_reads_timestamps_as_timezone_aware_datetimes(temp_dir):
    from collector.collector.config import TRADES_SCHEMA

    writer = ParquetWriter("trades", TRADES_SCHEMA, base_dir=temp_dir)
    writer.write(_trade_record())
    writer.close()

    file_path = writer._get_filename(writer.current_hour)
    df = pd.read_parquet(file_path)

    assert str(df["timestamp"].dtype) == "datetime64[ms, UTC]"
    assert isinstance(df["timestamp"].dtype, pd.DatetimeTZDtype)

    decoded = pd.to_datetime(df["timestamp"])
    assert decoded.dt.year.iloc[0] == 2026
    assert decoded.dt.year.iloc[0] != 1970



def _openinterest_record(timestamp_ms=1770000000000):
    return {
        "timestamp": timestamp_ms,
        "exchange_timestamp": timestamp_ms + 1,
        "local_timestamp": timestamp_ms + 2,
        "open_interest": 123.45,
    }


def _liquidation_record(timestamp_ms=1770000000000):
    return {
        "timestamp": timestamp_ms,
        "exchange_timestamp": timestamp_ms + 1,
        "local_timestamp": timestamp_ms + 2,
        "side": 1,
        "price": 50000.0,
        "quantity": 0.5,
        "signed_qty": 0.5,
        "order_status": "FILLED",
        "time_in_force": "IOC",
    }


def test_openinterest_schema_casts_integer_ms_to_utc_timestamps():
    from collector.collector.config import OPENINTEREST_SCHEMA

    table = pa.Table.from_pydict(
        {key: [value] for key, value in _openinterest_record().items()},
        schema=OPENINTEREST_SCHEMA,
    )

    expected_type = pa.timestamp("ms", tz="UTC")
    assert table.schema.field("timestamp").type == expected_type
    assert table.schema.field("exchange_timestamp").type == expected_type
    assert table.schema.field("local_timestamp").type == expected_type
    assert table.column("timestamp").type == expected_type


def test_liquidation_schema_casts_integer_ms_to_utc_timestamps():
    from collector.collector.config import LIQUIDATION_SCHEMA

    table = pa.Table.from_pydict(
        {key: [value] for key, value in _liquidation_record().items()},
        schema=LIQUIDATION_SCHEMA,
    )

    expected_type = pa.timestamp("ms", tz="UTC")
    assert table.schema.field("timestamp").type == expected_type
    assert table.schema.field("exchange_timestamp").type == expected_type
    assert table.schema.field("local_timestamp").type == expected_type
    assert table.column("timestamp").type == expected_type


def test_openinterest_parquet_reads_timestamps_as_timezone_aware_datetimes(temp_dir):
    from collector.collector.config import OPENINTEREST_SCHEMA

    writer = ParquetWriter("openinterest", OPENINTEREST_SCHEMA, base_dir=temp_dir)
    writer.write(_openinterest_record())
    writer.close()

    file_path = writer._get_filename(writer.current_hour)
    df = pd.read_parquet(file_path)

    assert str(df["timestamp"].dtype) == "datetime64[ms, UTC]"
    assert isinstance(df["timestamp"].dtype, pd.DatetimeTZDtype)

    decoded = pd.to_datetime(df["timestamp"])
    assert decoded.dt.year.iloc[0] == 2026
    assert decoded.dt.year.iloc[0] != 1970

def test_parquet_sidecar_uses_first_record_timestamp(temp_dir, monkeypatch):
    schema = pa.schema([
        ("timestamp", pa.int64()),
        ("value", pa.float64())
    ])

    import datetime

    class MockDatetime:
        @classmethod
        def utcnow(cls):
            return datetime.datetime(2026, 6, 3, 13)

        @classmethod
        def utcfromtimestamp(cls, timestamp):
            return datetime.datetime.utcfromtimestamp(timestamp)

    monkeypatch.setattr("collector.collector.parquet_writer.datetime", MockDatetime)

    writer = ParquetWriter("test_stream_sidecar", schema, base_dir=temp_dir)
    first_timestamp = int(datetime.datetime(2026, 6, 3, 13, 31, 11).timestamp() * 1000)
    writer.write({"timestamp": first_timestamp, "value": 1.5})
    writer.write({"timestamp": first_timestamp + 1000, "value": 2.5})
    writer.close()

    file_path = writer._get_filename(writer.current_hour)
    sidecar_path = file_path + ".meta.json"

    assert os.path.exists(sidecar_path)
    with open(sidecar_path, encoding="utf-8") as f:
        sidecar = json.load(f)

    assert sidecar["interval_start_declared"] == "2026-06-03T13:00:00Z"
    assert sidecar["interval_start_actual"] == "2026-06-03T13:31:11Z"
    assert sidecar["interval_end_actual"] == "2026-06-03T13:31:12Z"
    assert sidecar["record_count"] == 2

    parquet_metadata = pa_parquet.read_metadata(file_path).metadata
    assert parquet_metadata[b"interval_start"] == b"2026-06-03T13:00:00Z"
    assert parquet_metadata[b"interval_end"] == b"2026-06-03T14:00:00Z"


def test_empty_parquet_sidecar_falls_back_to_declared_start(temp_dir, monkeypatch):
    schema = pa.schema([
        ("timestamp", pa.int64()),
        ("value", pa.float64())
    ])

    import datetime

    class MockDatetime:
        @classmethod
        def utcnow(cls):
            return datetime.datetime(2026, 6, 3, 13)

        @classmethod
        def utcfromtimestamp(cls, timestamp):
            return datetime.datetime.utcfromtimestamp(timestamp)

    monkeypatch.setattr("collector.collector.parquet_writer.datetime", MockDatetime)

    writer = ParquetWriter("test_stream_empty_sidecar", schema, base_dir=temp_dir)
    writer.close()

    file_path = writer._get_filename(writer.current_hour)
    sidecar_path = file_path + ".meta.json"

    assert os.path.exists(sidecar_path)
    with open(sidecar_path, encoding="utf-8") as f:
        sidecar = json.load(f)

    assert sidecar["interval_start_declared"] == "2026-06-03T13:00:00Z"
    assert sidecar["interval_start_actual"] == "2026-06-03T13:00:00Z"
    assert sidecar["interval_end_actual"] == "2026-06-03T14:00:00Z"
    assert sidecar["record_count"] == 0


def test_parquet_sidecar_created_next_to_parquet_file(temp_dir, monkeypatch):
    schema = pa.schema([
        ("timestamp", pa.int64()),
        ("value", pa.float64())
    ])

    import datetime

    class MockDatetime:
        @classmethod
        def utcnow(cls):
            return datetime.datetime(2026, 6, 3, 13)

        @classmethod
        def utcfromtimestamp(cls, timestamp):
            return datetime.datetime.utcfromtimestamp(timestamp)

    monkeypatch.setattr("collector.collector.parquet_writer.datetime", MockDatetime)

    writer = ParquetWriter("test_stream_sidecar_location", schema, base_dir=temp_dir)
    timestamp = int(datetime.datetime(2026, 6, 3, 13, 31, 11).timestamp() * 1000)
    writer.write({"timestamp": timestamp, "value": 1.5})
    writer.close()

    file_path = writer._get_filename(writer.current_hour)
    sidecar_path = file_path + ".meta.json"

    assert os.path.exists(file_path)
    assert os.path.exists(sidecar_path)
    assert os.path.dirname(sidecar_path) == os.path.dirname(file_path)

import time

import pyarrow as pa

from collector.collector.parquet_writer import ParquetWriter
from collector.collector.storage_layout import iter_segments, parse_segment_name, segment_path


def test_storage_layout_roundtrip(tmp_path):
    schema = pa.schema([("timestamp", pa.timestamp("ms", tz="UTC")), ("value", pa.int64())])
    writer = ParquetWriter("example", schema, base_dir=str(tmp_path), segment_rows=1)
    writer.write({"timestamp": int(time.time() * 1000), "value": 7})
    writer.close()

    paths = list(iter_segments(tmp_path, "example"))
    assert len(paths) == 1
    parsed = parse_segment_name(paths[0])
    assert parsed is not None
    date, hour, sequence = parsed
    assert paths[0] == segment_path(tmp_path, "example", f"{date}-{hour:02d}", sequence)


def test_storage_layout_reads_legacy_and_never_temporary_files(tmp_path):
    raw = tmp_path / "raw" / "example"
    raw.mkdir(parents=True)
    (raw / "2026-06-03-01.parquet").touch()
    (raw / "2026-06-03-00-000002.seg").touch()
    (raw / "2026-06-03-00-000003.seg.tmp").touch()
    assert [path.name for path in iter_segments(tmp_path, "example", date="2026-06-03")] == [
        "2026-06-03-00-000002.seg", "2026-06-03-01.parquet"
    ]

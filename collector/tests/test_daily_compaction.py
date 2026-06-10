import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from collector.collector.config import (
    LIQUIDATION_SCHEMA,
    MARKPRICE_SCHEMA,
    OPENINTEREST_SCHEMA,
    ORDERBOOK_SCHEMA,
    TRADES_SCHEMA,
)
from collector.scripts import compact_daily as cd

DATE = "2026-06-10"
SCHEMAS = {
    "orderbook": ORDERBOOK_SCHEMA,
    "trades": TRADES_SCHEMA,
    "markprice": MARKPRICE_SCHEMA,
    "openinterest": OPENINTEREST_SCHEMA,
    "liquidation": LIQUIDATION_SCHEMA,
}


def _dt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _base_ms(hour=0):
    return int((datetime(2026, 6, 10, hour, tzinfo=UTC)).timestamp() * 1000)


def _values_for_field(stream, field, timestamps, offset=0):
    n = len(timestamps)
    name = field.name
    if pa.types.is_timestamp(field.type):
        return [_dt(ts if name == "timestamp" else ts + 10) for ts in timestamps]
    if pa.types.is_list(field.type):
        return [[100000.0 + offset, 99999.0 + offset] for _ in range(n)]
    if pa.types.is_boolean(field.type):
        return [False for _ in range(n)]
    if pa.types.is_string(field.type):
        return ["FILLED" if name == "order_status" else "GTC" for _ in range(n)]
    if pa.types.is_int64(field.type):
        if name == "trade_id":
            return [offset * 1000 + i for i in range(n)]
        return [int(ts + 3600000) for ts in timestamps]
    if pa.types.is_int8(field.type):
        return [1 for _ in range(n)]
    if pa.types.is_float64(field.type):
        if stream == "orderbook":
            mapping = {
                "best_bid": 100.0 + offset,
                "best_ask": 101.0 + offset,
                "mid_price": 100.5 + offset,
                "micro_price": 100.5 + offset,
                "spread": 1.0,
                "spread_bps": 10.0,
                "total_bid_qty": 5.0,
                "total_ask_qty": 6.0,
                "obi": 0.1,
                "obi_level_1": 0.1,
                "obi_level_3": 0.1,
                "obi_level_5": 0.1,
            }
            return [mapping[name] for _ in range(n)]
        return [1.0 + offset for _ in range(n)]
    raise AssertionError(f"Unhandled field {field}")


def _table(stream, timestamps, offset=0, overrides=None):
    schema = SCHEMAS[stream]
    data = {}
    for field in schema:
        data[field.name] = _values_for_field(stream, field, timestamps, offset)
    for key, value in (overrides or {}).items():
        data[key] = value
    arrays = [pa.array(data[field.name], type=field.type) for field in schema]
    return pa.Table.from_arrays(arrays, schema=schema)


def _write_hour(data_dir, stream, hour, timestamps=None, rows=2, overrides=None):
    raw_dir = data_dir / "raw" / stream
    raw_dir.mkdir(parents=True, exist_ok=True)
    ts = timestamps or [_base_ms(hour) + i * 1000 for i in range(rows)]
    table = _table(stream, ts, offset=hour, overrides=overrides)
    path = raw_dir / f"{DATE}-{hour:02d}.parquet"
    pq.write_table(table, path, compression="snappy")
    old_mtime = time.time() - 3600
    os.utime(path, (old_mtime, old_mtime))
    return table


def _write_hours(data_dir, stream, hours=range(24), rows=2):
    for hour in hours:
        _write_hour(data_dir, stream, hour, rows=rows)


def _meta(data_dir, stream):
    return json.loads((data_dir / "daily" / stream / f"{DATE}.meta.json").read_text())


def test_timestamp_stat_datetime():
    from datetime import UTC, datetime as dt

    value = dt(2026, 6, 10, 0, 0, 0, tzinfo=UTC)
    result = cd._timestamp_stat_to_ms(value)
    assert result == int(value.timestamp() * 1000)
    assert isinstance(result, int)


def test_timestamp_stat_pyarrow_timestamp_scalar():
    value = pa.scalar(datetime(2026, 6, 10, 0, 0, 0, tzinfo=UTC), type=cd.TIMESTAMP_TYPE)
    result = cd._timestamp_stat_to_ms(value)
    assert isinstance(result, int)
    assert result == _base_ms(0)


def test_timestamp_stat_numpy_datetime64():
    import numpy as np

    value = np.datetime64("2026-06-10T00:00:00.000", "ms")
    result = cd._timestamp_stat_to_ms(value)
    assert isinstance(result, int)
    assert result == int(value.astype("datetime64[ms]").astype("int64"))


def test_timestamp_stat_pandas_timestamp():
    import pandas as pd

    value = pd.Timestamp("2026-06-10T00:00:00", tz="UTC")
    result = cd._timestamp_stat_to_ms(value)
    assert isinstance(result, int)
    assert result == int(value.timestamp() * 1000)


def test_timestamp_stat_none():
    assert cd._timestamp_stat_to_ms(None) is None


def test_single_date_all_streams(tmp_path):
    for stream in SCHEMAS:
        _write_hours(tmp_path, stream, rows=1)
    assert cd.main(["--date", DATE, "--data-dir", str(tmp_path)]) == 0
    for stream in SCHEMAS:
        out = tmp_path / "daily" / stream / f"{DATE}.parquet"
        assert out.exists()
        assert pq.read_table(out).num_rows == 24
        meta = _meta(tmp_path, stream)
        assert meta["date"] == DATE
        assert meta["stream"] == stream
        assert meta["record_count"] == 24
        assert len(meta["source_hourly_files"]) == 24
        assert meta["missing_hours"] == []


def test_missing_hours_warns_not_aborts(tmp_path):
    _write_hours(tmp_path, "orderbook", hours=[h for h in range(24) if h not in {3, 7, 17}], rows=1)
    assert cd.compact_daily(DATE, "orderbook", tmp_path)
    assert _meta(tmp_path, "orderbook")["missing_hours"] == [3, 7, 17]


def test_idempotent_skip(tmp_path):
    _write_hours(tmp_path, "trades", rows=1)
    assert cd.compact_daily(DATE, "trades", tmp_path)
    out = tmp_path / "daily" / "trades" / f"{DATE}.parquet"
    first_mtime = out.stat().st_mtime_ns
    time.sleep(0.01)
    assert cd.compact_daily(DATE, "trades", tmp_path)
    assert out.stat().st_mtime_ns == first_mtime


def test_force_overwrite(tmp_path):
    _write_hours(tmp_path, "trades", rows=1)
    assert cd.compact_daily(DATE, "trades", tmp_path)
    out = tmp_path / "daily" / "trades" / f"{DATE}.parquet"
    first_mtime = out.stat().st_mtime_ns
    time.sleep(0.01)
    assert cd.compact_daily(DATE, "trades", tmp_path, force=True)
    assert out.stat().st_mtime_ns > first_mtime


def test_duplicate_timestamp_raises(tmp_path):
    duplicate = [_base_ms(0), _base_ms(0)]
    _write_hour(tmp_path, "orderbook", 0, timestamps=duplicate)
    with pytest.raises(cd.CompactionError):
        cd.compact_daily(DATE, "orderbook", tmp_path)


def test_duplicate_trade_id_raises(tmp_path):
    ts = [_base_ms(0), _base_ms(0)]
    _write_hour(tmp_path, "trades", 0, timestamps=ts, overrides={"trade_id": [42, 42]})
    with pytest.raises(cd.CompactionError):
        cd.compact_daily(DATE, "trades", tmp_path)


def test_tmp_files_skipped(tmp_path):
    _write_hours(tmp_path, "markprice", hours=[0], rows=1)
    tmp_file = tmp_path / "raw" / "markprice" / f"{DATE}-05.parquet.tmp"
    tmp_file.write_text("not parquet")
    assert cd.compact_daily(DATE, "markprice", tmp_path)
    meta = _meta(tmp_path, "markprice")
    assert f"{DATE}-05.parquet.tmp" not in meta["source_hourly_files"]
    assert meta["missing_hours"] == [h for h in range(1, 24) if h != 5]
    assert meta["skipped_hours"] == [5]


def test_timestamp_sort_order(tmp_path):
    _write_hour(tmp_path, "trades", 9, timestamps=[_base_ms(10)])
    _write_hour(tmp_path, "trades", 10, timestamps=[_base_ms(9)])
    assert cd.compact_daily(DATE, "trades", tmp_path)
    table = pq.read_table(tmp_path / "daily" / "trades" / f"{DATE}.parquet")
    timestamps = table.column("timestamp").cast(pa.int64()).combine_chunks().to_pylist()
    assert timestamps == sorted(timestamps)


def test_atomic_write(tmp_path, monkeypatch):
    _write_hours(tmp_path, "openinterest", hours=[0], rows=1)

    def fail_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(cd.os, "replace", fail_replace)
    with pytest.raises(OSError):
        cd.compact_daily(DATE, "openinterest", tmp_path)
    assert not (tmp_path / "daily" / "openinterest" / f"{DATE}.parquet").exists()


def test_meta_json_fields(tmp_path):
    _write_hours(tmp_path, "liquidation", hours=[0], rows=1)
    assert cd.compact_daily(DATE, "liquidation", tmp_path)
    meta = _meta(tmp_path, "liquidation")
    expected = {
        "date": str,
        "stream": str,
        "record_count": int,
        "file_size_bytes": int,
        "source_hourly_files": list,
        "missing_hours": list,
        "start_timestamp_ms": int,
        "end_timestamp_ms": int,
        "start_timestamp_iso": str,
        "end_timestamp_iso": str,
        "compacted_at_utc": str,
    }
    for key, typ in expected.items():
        assert key in meta
        assert isinstance(meta[key], typ)


def test_schema_preserved(tmp_path):
    for stream, schema in SCHEMAS.items():
        _write_hours(tmp_path, stream, hours=[0], rows=1)
        assert cd.compact_daily(DATE, stream, tmp_path)
        table = pq.read_table(tmp_path / "daily" / stream / f"{DATE}.parquet")
        assert table.schema == schema


def test_directory_fsync_execution(tmp_path, monkeypatch):
    _write_hours(tmp_path, "trades", hours=[0], rows=1)
    opened_dirs = []
    fsynced = []
    closed = []

    def fake_open(path, flags):
        opened_dirs.append(path)
        return 12345 + len(opened_dirs)

    def fake_fsync(fd):
        fsynced.append(fd)

    def fake_close(fd):
        closed.append(fd)

    monkeypatch.setattr(cd.os, "open", fake_open)
    monkeypatch.setattr(cd.os, "fsync", fake_fsync)
    monkeypatch.setattr(cd.os, "close", fake_close)
    assert cd.compact_daily(DATE, "trades", tmp_path)
    assert opened_dirs == [str(tmp_path / "daily" / "trades"), str(tmp_path / "daily" / "trades")]
    assert [12346, 12347] == closed
    assert 12346 in fsynced and 12347 in fsynced


def test_matching_tmp_detection_records_skipped_hour(tmp_path):
    _write_hour(tmp_path, "markprice", 0, rows=1)
    _write_hour(tmp_path, "markprice", 1, rows=1)
    (tmp_path / "raw" / "markprice" / f"{DATE}-01.parquet.tmp").write_text("in progress")
    assert cd.compact_daily(DATE, "markprice", tmp_path)
    meta = _meta(tmp_path, "markprice")
    assert meta["record_count"] == 1
    assert meta["skipped_hours"] == [1]
    assert f"{DATE}-01.parquet" not in meta["source_hourly_files"]


def test_stale_tmp_cleanup_on_force(tmp_path):
    _write_hours(tmp_path, "openinterest", hours=[0], rows=1)
    out_dir = tmp_path / "daily" / "openinterest"
    out_dir.mkdir(parents=True)
    parquet_tmp = out_dir / f"{DATE}.parquet.tmp"
    meta_tmp = out_dir / f"{DATE}.meta.json.tmp"
    parquet_tmp.write_text("stale")
    meta_tmp.write_text("stale")
    assert cd.compact_daily(DATE, "openinterest", tmp_path, force=True)
    assert not parquet_tmp.exists()
    assert not meta_tmp.exists()


def test_schema_mismatch_rejection(tmp_path):
    raw_dir = tmp_path / "raw" / "orderbook"
    raw_dir.mkdir(parents=True)
    path = raw_dir / f"{DATE}-00.parquet"
    pq.write_table(pa.table({"timestamp": pa.array([_dt(_base_ms(0))], type=pa.timestamp("ms", tz="UTC"))}), path)
    old_mtime = time.time() - 3600
    os.utime(path, (old_mtime, old_mtime))
    with pytest.raises(cd.CompactionError):
        cd.compact_daily(DATE, "orderbook", tmp_path)


def test_corrupt_parquet_rejection(tmp_path):
    raw_dir = tmp_path / "raw" / "trades"
    raw_dir.mkdir(parents=True)
    path = raw_dir / f"{DATE}-00.parquet"
    path.write_text("not a parquet file")
    old_mtime = time.time() - 3600
    os.utime(path, (old_mtime, old_mtime))
    assert cd.main(["--date", DATE, "--streams", "trades", "--data-dir", str(tmp_path)]) == 1
    assert not (tmp_path / "daily" / "trades" / f"{DATE}.parquet").exists()


def test_current_hour_guard_behavior(tmp_path):
    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")
    current_hour = now.hour
    other_hour = 1 if current_hour == 0 else 0
    old_date = globals()["DATE"]
    globals()["DATE"] = today
    try:
        _write_hour(tmp_path, "trades", other_hour, rows=1)
        _write_hour(tmp_path, "trades", current_hour, rows=1)
        guarded = tmp_path / "raw" / "trades" / f"{today}-{current_hour:02d}.parquet"
        fresh_mtime = time.time()
        os.utime(guarded, (fresh_mtime, fresh_mtime))
        assert cd.compact_daily(today, "trades", tmp_path, guard_seconds=3600)
        meta = json.loads((tmp_path / "daily" / "trades" / f"{today}.meta.json").read_text())
        assert current_hour in meta["skipped_hours"]
        assert f"{today}-{current_hour:02d}.parquet" not in meta["source_hourly_files"]
    finally:
        globals()["DATE"] = old_date


def test_large_multi_hour_compaction_path_without_concat(tmp_path, monkeypatch):
    for hour in range(6):
        _write_hour(tmp_path, "trades", hour, rows=50)

    def fail_concat(*args, **kwargs):
        raise AssertionError("concat_tables should not be used by streaming compaction")

    monkeypatch.setattr(cd.pa, "concat_tables", fail_concat, raising=False)
    assert cd.compact_daily(DATE, "trades", tmp_path)
    assert pq.read_table(tmp_path / "daily" / "trades" / f"{DATE}.parquet").num_rows == 300


def test_verify_command(tmp_path):
    _write_hours(tmp_path, "liquidation", hours=[0], rows=1)
    assert cd.compact_daily(DATE, "liquidation", tmp_path)
    assert cd.main(["--verify", "--date", DATE, "--streams", "liquidation", "--data-dir", str(tmp_path)]) == 0
    meta_path = tmp_path / "daily" / "liquidation" / f"{DATE}.meta.json"
    meta = json.loads(meta_path.read_text())
    meta["record_count"] = 999
    meta_path.write_text(json.dumps(meta))
    assert cd.main(["--verify", "--date", DATE, "--streams", "liquidation", "--data-dir", str(tmp_path)]) == 1


def _write_hour_for_date(data_dir, stream, date, hour, timestamps=None, rows=2, overrides=None):
    old_date = globals()["DATE"]
    globals()["DATE"] = date
    try:
        return _write_hour(data_dir, stream, hour, timestamps=timestamps, rows=rows, overrides=overrides)
    finally:
        globals()["DATE"] = old_date


def test_historical_recent_file_not_skipped(tmp_path):
    yesterday = "2026-06-09"
    _write_hour_for_date(tmp_path, "trades", yesterday, 12, rows=1)
    path = tmp_path / "raw" / "trades" / f"{yesterday}-12.parquet"
    fresh_mtime = time.time()
    os.utime(path, (fresh_mtime, fresh_mtime))

    assert cd.compact_daily(yesterday, "trades", tmp_path, guard_seconds=3600)

    meta = json.loads((tmp_path / "daily" / "trades" / f"{yesterday}.meta.json").read_text())
    assert meta["source_hourly_files"] == [f"{yesterday}-12.parquet"]
    assert meta["skipped_hours"] == []


def test_current_hour_guard_only_applies_today(tmp_path):
    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")
    current_hour = now.hour
    previous_hour = (current_hour - 1) % 24
    _write_hour_for_date(tmp_path, "trades", today, previous_hour, rows=1)
    _write_hour_for_date(tmp_path, "trades", today, current_hour, rows=1)
    fresh_mtime = time.time()
    for hour in (previous_hour, current_hour):
        path = tmp_path / "raw" / "trades" / f"{today}-{hour:02d}.parquet"
        os.utime(path, (fresh_mtime, fresh_mtime))

    assert cd.compact_daily(today, "trades", tmp_path, guard_seconds=3600)

    meta = json.loads((tmp_path / "daily" / "trades" / f"{today}.meta.json").read_text())
    assert current_hour in meta["skipped_hours"]
    assert previous_hour not in meta["skipped_hours"]
    assert f"{today}-{previous_hour:02d}.parquet" in meta["source_hourly_files"]
    assert f"{today}-{current_hour:02d}.parquet" not in meta["source_hourly_files"]


def test_hourly_file_read_once(tmp_path, monkeypatch):
    source_hours = [0, 1, 2]
    _write_hours(tmp_path, "trades", hours=source_hours, rows=2)

    real_read_table = cd.pq.read_table
    # Track reads per source path only; ignore reads of other paths
    # (e.g. any internal parquet verification reads).
    source_paths = {
        tmp_path / "raw" / "trades" / f"{DATE}-{hour:02d}.parquet"
        for hour in source_hours
    }
    raw_source_reads: dict = {p: 0 for p in source_paths}

    def tracking_read_table(path, *args, **kwargs):
        p = Path(path) if not isinstance(path, Path) else path
        if p in raw_source_reads:
            raw_source_reads[p] += 1
        return real_read_table(path, *args, **kwargs)

    monkeypatch.setattr(cd.pq, "read_table", tracking_read_table)
    assert cd.compact_daily(DATE, "trades", tmp_path)

    # Every source file must have been read exactly once.
    for p, count in raw_source_reads.items():
        assert count == 1, f"{p.name} was read {count} times, expected 1"


def test_no_set_or_to_pylist_in_duplicate_detection():
    source = (Path(__file__).parents[1] / "scripts" / "compact_daily.py").read_text()
    duplicate_section = source[source.index("def _raise_if_duplicate_groups") : source.index("def _warn_orderbook_quality")]
    assert "to_pylist" not in duplicate_section
    assert "set(" not in duplicate_section


def test_cross_boundary_arrow_duplicate_detected(tmp_path):
    boundary_ts = _base_ms(0) + 1000
    _write_hour(tmp_path, "trades", 0, timestamps=[_base_ms(0), boundary_ts], overrides={"trade_id": [1, 42]})
    _write_hour(tmp_path, "trades", 1, timestamps=[boundary_ts, _base_ms(1)], overrides={"trade_id": [42, 43]})

    with pytest.raises(cd.CompactionError, match="duplicate"):
        cd.compact_daily(DATE, "trades", tmp_path)


def test_verify_uses_parquet_statistics_not_full_read(tmp_path, monkeypatch):
    _write_hours(tmp_path, "trades", hours=[0, 1], rows=2)
    assert cd.compact_daily(DATE, "trades", tmp_path)

    def fail_read_table(*args, **kwargs):
        raise AssertionError("pq.read_table must not be called during output verification")

    monkeypatch.setattr(cd.pq, "read_table", fail_read_table)
    stats = cd._verify_parquet_output(tmp_path / "daily" / "trades" / f"{DATE}.parquet", TRADES_SCHEMA, 4)
    assert stats.rows == 4
    assert stats.start_timestamp_ms == _base_ms(0)
    assert stats.end_timestamp_ms == _base_ms(1) + 1000


def test_schema_metadata_mismatch_accepted():
    actual = TRADES_SCHEMA.with_metadata({b"extra": b"metadata"})
    cd._validate_schema_compatible(actual, TRADES_SCHEMA)


def test_schema_column_mismatch_rejected():
    fields = [pa.field("wrong_name", TRADES_SCHEMA[0].type), *list(TRADES_SCHEMA)[1:]]
    actual = pa.schema(fields)
    with pytest.raises(cd.CompactionError, match="name mismatch"):
        cd._validate_schema_compatible(actual, TRADES_SCHEMA)


def test_schema_type_mismatch_rejected():
    fields = [pa.field(TRADES_SCHEMA[0].name, pa.int64()), *list(TRADES_SCHEMA)[1:]]
    actual = pa.schema(fields)
    with pytest.raises(cd.CompactionError, match="type mismatch"):
        cd._validate_schema_compatible(actual, TRADES_SCHEMA)


def test_tmp_removed_after_replace_failure(tmp_path, monkeypatch):
    final_path = tmp_path / "metadata.json"

    def fail_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(cd.os, "replace", fail_replace)
    with pytest.raises(OSError):
        cd._write_json_atomic({"ok": True}, final_path)
    assert not Path(str(final_path) + ".tmp").exists()


def test_directory_fsync_after_replace(tmp_path, monkeypatch):
    # Verify that for both _stream_write_parquet and _write_json_atomic:
    #   os.replace() fires before the directory os.fsync().
    _write_hours(tmp_path, "trades", hours=[0], rows=1)
    final_parent = tmp_path / "daily" / "trades"

    events = []
    next_fd = [20000]
    directory_fds = set()
    real_replace = cd.os.replace

    def fake_open(path, flags):
        next_fd[0] += 1
        fd = next_fd[0]
        if Path(path) == final_parent:
            directory_fds.add(fd)
        return fd

    def fake_fsync(fd):
        if fd in directory_fds:
            events.append(("dir_fsync", fd))
        # File fsyncs use real fds from Python open(); ignore them here.

    def fake_replace(src, dst):
        events.append(("replace", str(dst)))
        # Perform the actual replace so the file is really written.
        real_replace(src, dst)

    monkeypatch.setattr(cd.os, "open", fake_open)
    monkeypatch.setattr(cd.os, "close", lambda fd: None)
    monkeypatch.setattr(cd.os, "fsync", fake_fsync)
    monkeypatch.setattr(cd.os, "replace", fake_replace)

    assert cd.compact_daily(DATE, "trades", tmp_path)

    # Must have at least one replace and one dir_fsync event.
    replace_events = [i for i, (kind, _) in enumerate(events) if kind == "replace"]
    dir_fsync_events = [i for i, (kind, _) in enumerate(events) if kind == "dir_fsync"]
    assert replace_events, "os.replace never called"
    assert dir_fsync_events, "directory fsync never called"

    # Every dir_fsync must come after at least one replace; i.e.
    # min(dir_fsync indices) > min(replace indices).
    assert min(dir_fsync_events) > min(replace_events), (
        f"directory fsync at event {min(dir_fsync_events)} "
        f"occurred before replace at event {min(replace_events)}"
    )

    # Both parquet and json replace events must precede their dir_fsync
    # (there are 2 replace+fsync pairs for one compaction run).
    assert len(replace_events) >= 2, "expected replace for both parquet and metadata"
    assert len(dir_fsync_events) >= 2, "expected dir_fsync for both parquet and metadata"

    # Verify interleaving: each replace_i < its matching dir_fsync_i.
    for replace_idx, dir_fsync_idx in zip(
        sorted(replace_events), sorted(dir_fsync_events), strict=False
    ):
        assert replace_idx < dir_fsync_idx


def test_verify_detects_wrong_file_size(tmp_path):
    _write_hours(tmp_path, "liquidation", hours=[0], rows=1)
    assert cd.compact_daily(DATE, "liquidation", tmp_path)
    meta_path = tmp_path / "daily" / "liquidation" / f"{DATE}.meta.json"
    meta = json.loads(meta_path.read_text())
    meta["file_size_bytes"] = -1
    meta_path.write_text(json.dumps(meta))

    with pytest.raises(cd.CompactionError, match="file_size_bytes mismatch"):
        cd.verify_daily(DATE, "liquidation", tmp_path)


def test_verify_detects_bad_metadata(tmp_path):
    _write_hours(tmp_path, "liquidation", hours=[0], rows=1)
    assert cd.compact_daily(DATE, "liquidation", tmp_path)
    meta_path = tmp_path / "daily" / "liquidation" / f"{DATE}.meta.json"
    meta = json.loads(meta_path.read_text())
    meta["missing_hours"] = "bad"
    meta_path.write_text(json.dumps(meta))

    with pytest.raises(cd.CompactionError, match="missing_hours must be a list"):
        cd.verify_daily(DATE, "liquidation", tmp_path)


def test_no_concat_tables_called(tmp_path, monkeypatch):
    # Normal compaction with non-overlapping hours must never call
    # concat_tables on the full-day write path.
    for hour in range(3):
        _write_hour(tmp_path, "trades", hour, rows=3)

    concat_calls = []
    real_concat = cd.pa.concat_tables

    def tracking_concat(*args, **kwargs):
        concat_calls.append(args[0])
        return real_concat(*args, **kwargs)

    monkeypatch.setattr(cd.pa, "concat_tables", tracking_concat)
    assert cd.compact_daily(DATE, "trades", tmp_path)
    # Non-overlapping hours produce no boundary timestamp match, so
    # _check_boundary_duplicates is never called and concat_tables is
    # never invoked.
    assert len(concat_calls) == 0


def test_boundary_duplicate_detection_uses_small_concat_only(tmp_path):
    # When two adjacent hours share a boundary timestamp, concat_tables
    # must be called exactly once (on tiny boundary subsets only) and
    # must raise CompactionError for actual duplicates.
    boundary_ts = _base_ms(0) + 3500  # inside hour 0 range
    _write_hour(
        tmp_path,
        "trades",
        0,
        timestamps=[_base_ms(0), boundary_ts],
        overrides={"trade_id": [1, 42]},
    )
    _write_hour(
        tmp_path,
        "trades",
        1,
        timestamps=[boundary_ts, _base_ms(1)],
        overrides={"trade_id": [42, 43]},
    )

    concat_calls = []
    real_concat = cd.pa.concat_tables

    def tracking_concat(*args, **kwargs):
        concat_calls.append(args[0])
        return real_concat(*args, **kwargs)

    import collector.scripts.compact_daily as cd_module

    # Patch at module level so _check_boundary_duplicates picks it up.
    orig = cd_module.pa.concat_tables
    cd_module.pa.concat_tables = tracking_concat
    try:
        with pytest.raises(cd.CompactionError, match="duplicate"):
            cd.compact_daily(DATE, "trades", tmp_path)
    finally:
        cd_module.pa.concat_tables = orig

    # concat_tables called exactly once (the boundary subset only).
    assert len(concat_calls) == 1
    # Each boundary subset table must be tiny (boundary rows only, not
    # full hourly tables).
    for tables in concat_calls:
        for table in tables:
            assert table.num_rows <= 2

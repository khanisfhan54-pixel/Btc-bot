"""Offline daily compaction for raw hourly collector Parquet files."""
from __future__ import annotations
import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from collector.collector.config import (
    LIQUIDATION_SCHEMA,
    MARKPRICE_SCHEMA,
    OPENINTEREST_SCHEMA,
    ORDERBOOK_SCHEMA,
    TRADES_SCHEMA,
)
from collector.collector.utils import logger
STREAM_SCHEMAS: dict[str, pa.Schema] = {
    "orderbook": ORDERBOOK_SCHEMA,
    "trades": TRADES_SCHEMA,
    "markprice": MARKPRICE_SCHEMA,
    "openinterest": OPENINTEREST_SCHEMA,
    "liquidation": LIQUIDATION_SCHEMA,
}
ALL_STREAMS = tuple(STREAM_SCHEMAS.keys())
TIMESTAMP_TYPE = pa.timestamp("ms", tz="UTC")
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-\d{2}\.parquet$")
DEFAULT_GUARD_SECONDS = 90
class CompactionError(RuntimeError):
    """Raised when a daily compaction input fails validation.
    Args:
        RuntimeError: Base exception type for compaction validation failures.
    """
@dataclass(frozen=True)
class _HourSource:
    path: Path
    hour: int
    rows: int
    min_ts_ms: int
    max_ts_ms: int
    table: pa.Table
@dataclass(frozen=True)
class _DiscoveredFiles:
    present_files: list[Path]
    missing_hours: list[int]
    skipped_hours: list[int]
@dataclass(frozen=True)
class _OutputStats:
    rows: int
    file_size_bytes: int
    start_timestamp_ms: int | None
    end_timestamp_ms: int | None
def compact_daily(
    date: str,
    stream: str,
    data_dir: Path | str = Path("data"),
    *,
    force: bool = False,
    guard_seconds: int = DEFAULT_GUARD_SECONDS,
) -> bool:
    """Compact one stream/date pair into a daily Parquet file.
    Args:
        date: Date to compact in YYYY-MM-DD form.
        stream: Stream name to compact.
        data_dir: Root data directory containing raw/ and daily/.
        force: Whether to overwrite an existing daily file.
        guard_seconds: File modification-time guard window in seconds.
    Returns:
        True when compaction wrote a file or safely skipped; False on no sources.
    """
    data_root = Path(data_dir)
    schema = _schema_for_stream(stream)
    raw_dir = data_root / "raw" / stream
    out_dir = data_root / "daily" / stream
    final_path = out_dir / f"{date}.parquet"
    meta_path = final_path.with_suffix(".meta.json")
    if final_path.exists() and not force:
        logger.info("compact_daily_skip", stream=stream, date=date, reason="exists")
        return True
    if force:
        _remove_stale_tmp(final_path, stream, date)
        _remove_stale_tmp(meta_path, stream, date)
    discovered = _discover_hourly_files(raw_dir, date, guard_seconds=guard_seconds)
    if not discovered.present_files:
        logger.warning("compact_daily_no_sources", stream=stream, date=date)
        return False
    sources = _inspect_sources(discovered.present_files, stream, schema)
    sources = sorted(sources, key=lambda source: (source.min_ts_ms, source.hour))
    _validate_cross_source_order(sources, stream, schema)
    expected_rows = sum(source.rows for source in sources)
    out_dir.mkdir(parents=True, exist_ok=True)
    _stream_write_parquet(sources, final_path, schema)
    stats = _verify_parquet_output(final_path, schema, expected_rows)
    metadata = _build_metadata(
        stats=stats,
        stream=stream,
        date=date,
        source_files=[source.path for source in sources],
        missing_hours=discovered.missing_hours,
        skipped_hours=discovered.skipped_hours,
    )
    _write_json_atomic(metadata, meta_path)
    logger.info("compact_daily", stream=stream, date=date, rows=stats.rows)
    return True
def verify_daily(
    date: str,
    stream: str,
    data_dir: Path | str = Path("data"),
) -> bool:
    """Verify one compacted daily Parquet file and its metadata sidecar.
    Args:
        date: Date to verify in YYYY-MM-DD form.
        stream: Stream name to verify.
        data_dir: Root data directory containing daily/.
    Returns:
        True when the compacted output and metadata are consistent.
    """
    schema = _schema_for_stream(stream)
    final_path = Path(data_dir) / "daily" / stream / f"{date}.parquet"
    meta_path = final_path.with_suffix(".meta.json")
    if not meta_path.exists():
        raise CompactionError(f"missing metadata sidecar: {meta_path}")
    with meta_path.open("r", encoding="utf-8") as handle:
        metadata = json.load(handle)
    stats = _verify_parquet_output(final_path, schema, int(metadata["record_count"]))
    if metadata.get("start_timestamp_ms") != stats.start_timestamp_ms:
        raise CompactionError("metadata start timestamp does not match parquet")
    if metadata.get("end_timestamp_ms") != stats.end_timestamp_ms:
        raise CompactionError("metadata end timestamp does not match parquet")
    actual_size = final_path.stat().st_size
    if metadata.get("file_size_bytes") != actual_size:
        raise CompactionError(f"file_size_bytes mismatch: {metadata.get('file_size_bytes')} != {actual_size}")
    if not isinstance(metadata.get("source_hourly_files"), list):
        raise CompactionError("source_hourly_files must be a list")
    missing_hours = metadata.get("missing_hours")
    if not isinstance(missing_hours, list):
        raise CompactionError("missing_hours must be a list")
    if not all(isinstance(hour, int) for hour in missing_hours):
        raise CompactionError("missing_hours must be a list of ints")
    skipped_hours = metadata.get("skipped_hours")
    if not isinstance(skipped_hours, list):
        raise CompactionError("skipped_hours must be a list")
    if not all(isinstance(hour, int) for hour in skipped_hours):
        raise CompactionError("skipped_hours must be a list of ints")
    logger.info("compact_daily_verify", stream=stream, date=date, rows=stats.rows)
    return True
def main(argv: Sequence[str] | None = None) -> int:
    """Run the daily compaction command-line interface.
    Args:
        argv: Optional argument vector. Uses sys.argv when omitted.
    Returns:
        Process exit code: 0 on success, 1 if any compaction or verification failed.
    """
    args = _parse_args(argv)
    streams = list(args.streams or ALL_STREAMS)
    data_dir = Path(args.data_dir)
    dates = [args.date] if args.date else sorted(_discover_all_dates(data_dir, streams))
    failed = False
    for date in dates:
        for stream in streams:
            try:
                if args.verify:
                    verify_daily(date, stream, data_dir)
                elif not compact_daily(date, stream, data_dir, force=args.force, guard_seconds=args.guard_seconds):
                    failed = True
            except (CompactionError, OSError, pa.ArrowException, ValueError, json.JSONDecodeError) as exc:
                logger.error("compact_daily_failed", stream=stream, date=date, error=str(exc))
                failed = True
    return 1 if failed else 0
def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="compact exactly one date (YYYY-MM-DD)")
    group.add_argument("--all", action="store_true", help="compact every discovered date")
    parser.add_argument("--verify", action="store_true", help="verify existing daily outputs")
    parser.add_argument("--force", action="store_true", help="overwrite existing daily files")
    parser.add_argument("--guard-seconds", type=int, default=DEFAULT_GUARD_SECONDS)
    parser.add_argument("--streams", nargs="+", choices=ALL_STREAMS, default=list(ALL_STREAMS))
    parser.add_argument("--data-dir", default="data", help="data directory root")
    args = parser.parse_args(argv)
    if args.date:
        _parse_date(args.date)
    if args.verify and args.force:
        parser.error("--verify cannot be combined with --force")
    if args.guard_seconds < 0:
        parser.error("--guard-seconds must be non-negative")
    return args
def _schema_for_stream(stream: str) -> pa.Schema:
    try:
        return STREAM_SCHEMAS[stream]
    except KeyError as exc:
        raise ValueError(f"unknown stream: {stream}") from exc
def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
def _discover_hourly_files(raw_dir: Path, date: str, *, guard_seconds: int) -> _DiscoveredFiles:
    present: list[Path] = []
    missing: list[int] = []
    skipped: list[int] = []
    now = datetime.now(UTC)
    current_date = now.strftime("%Y-%m-%d")
    current_hour = now.hour
    for hour in range(24):
        path = raw_dir / f"{date}-{hour:02d}.parquet"
        tmp_path = Path(str(path) + ".tmp")
        if tmp_path.exists():
            logger.warning("compact_daily_skip_matching_tmp", stream=raw_dir.name, date=date, hour=hour)
            skipped.append(hour)
            continue
        if not path.exists():
            logger.warning("compact_daily_missing_hour", stream=raw_dir.name, date=date, hour=hour)
            missing.append(hour)
            continue
        if path.name.endswith((".tmp", ".bak")):
            skipped.append(hour)
            continue
        is_current_hour = date == current_date and hour == current_hour
        if is_current_hour:
            age_seconds = datetime.now(UTC).timestamp() - path.stat().st_mtime
            if age_seconds < guard_seconds:
                logger.warning(
                    "compact_daily_skip_current_hour",
                    stream=raw_dir.name,
                    date=date,
                    hour=hour,
                    age_seconds=age_seconds,
                    guard_seconds=guard_seconds,
                )
                skipped.append(hour)
                continue
        present.append(path)
    return _DiscoveredFiles(present, missing, skipped)
def _discover_all_dates(data_dir: Path, streams: Sequence[str]) -> set[str]:
    dates: set[str] = set()
    for stream in streams:
        raw_dir = data_dir / "raw" / stream
        if not raw_dir.exists():
            continue
        for path in raw_dir.glob("*.parquet"):
            if path.name.endswith((".tmp", ".bak")) or Path(str(path) + ".tmp").exists():
                continue
            match = DATE_RE.match(path.name)
            if match:
                dates.add(match.group(1))
    return dates
def _inspect_sources(paths: Sequence[Path], stream: str, schema: pa.Schema) -> list[_HourSource]:
    sources: list[_HourSource] = []
    for path in paths:
        table = _read_hourly_table(path, schema)
        table = table.sort_by([("timestamp", "ascending")])
        _validate_table(table, stream)
        timestamp_ms = table.column("timestamp").cast(pa.int64())
        min_ts = pc.min(timestamp_ms).as_py()
        max_ts = pc.max(timestamp_ms).as_py()
        if min_ts is None or max_ts is None:
            raise CompactionError(f"empty or invalid timestamp range in {path.name}")
        sources.append(_HourSource(path, _hour_from_path(path), table.num_rows, int(min_ts), int(max_ts), table))
    return sources
def _read_hourly_table(path: Path, schema: pa.Schema) -> pa.Table:
    table = pq.read_table(path)
    if table.schema.field("timestamp").type != TIMESTAMP_TYPE:
        table = table.set_column(
            table.schema.get_field_index("timestamp"),
            "timestamp",
            table.column("timestamp").cast(TIMESTAMP_TYPE),
        )
    return _align_to_schema(table, schema)
def _align_to_schema(table: pa.Table, schema: pa.Schema) -> pa.Table:
    arrays = []
    for field in schema:
        if field.name not in table.column_names:
            raise CompactionError(f"missing required column: {field.name}")
        arrays.append(table.column(field.name).cast(field.type))
    return pa.Table.from_arrays(arrays, schema=schema)
def _validate_table(table: pa.Table, stream: str) -> None:
    timestamps = table.column("timestamp")
    if table.num_rows == 0:
        raise CompactionError("hourly file contains no rows")
    if timestamps.null_count:
        raise CompactionError("timestamp column contains null values")
    timestamp_ms = timestamps.cast(pa.int64())
    left = timestamp_ms.slice(0, table.num_rows - 1)
    right = timestamp_ms.slice(1)
    if stream == "trades":
        if table.num_rows > 1 and pc.any(pc.greater(left, right)).as_py():
            raise CompactionError("trades timestamps are not monotonically non-decreasing")
        _raise_if_duplicate_groups(table, ["timestamp", "trade_id"], "duplicate (timestamp, trade_id) pairs found")
    else:
        if table.num_rows > 1 and pc.any(pc.greater_equal(left, right)).as_py():
            raise CompactionError(f"{stream} timestamps are not strictly increasing")
        _raise_if_duplicate_groups(table, ["timestamp"], f"duplicate timestamp values found in {stream}")
    if stream == "orderbook":
        _warn_orderbook_quality(table)
def _raise_if_duplicate_groups(table: pa.Table, keys: list[str], message: str) -> None:
    grouped = table.select(keys).group_by(keys).aggregate([([], "count_all")])
    counts = grouped.column("count_all")
    max_count = pc.max(counts).as_py()
    if max_count is not None and max_count > 1:
        raise CompactionError(message)
def _validate_cross_source_order(sources: Sequence[_HourSource], stream: str, schema: pa.Schema) -> None:
    del schema
    previous: _HourSource | None = None
    for source in sources:
        if previous is not None:
            if stream == "trades":
                if source.min_ts_ms < previous.max_ts_ms:
                    raise CompactionError("trades source timestamp ranges overlap out of order")
                if source.min_ts_ms == previous.max_ts_ms:
                    _check_boundary_duplicates(previous, source)
            elif source.min_ts_ms <= previous.max_ts_ms:
                raise CompactionError(f"duplicate or overlapping timestamp values found across {stream} hourly files")
        previous = source
def _check_boundary_duplicates(prev_source: _HourSource, cur_source: _HourSource) -> None:
    prev_timestamp = prev_source.table.column("timestamp").cast(pa.int64())
    cur_timestamp = cur_source.table.column("timestamp").cast(pa.int64())
    prev_boundary = prev_source.table.filter(pc.equal(prev_timestamp, pa.scalar(prev_source.max_ts_ms, type=pa.int64())))
    cur_boundary = cur_source.table.filter(pc.equal(cur_timestamp, pa.scalar(cur_source.min_ts_ms, type=pa.int64())))
    boundary = pa.concat_tables((prev_boundary, cur_boundary), promote_options="none")
    duplicate_groups = boundary.group_by(["timestamp", "trade_id"]).aggregate([([], "count_all")])
    max_count = pc.max(duplicate_groups.column("count_all")).as_py()
    if max_count is not None and max_count > 1:
        raise CompactionError("duplicate (timestamp, trade_id) pairs found across hourly files")
def _warn_orderbook_quality(table: pa.Table) -> None:
    bad_spread = pc.greater_equal(table.column("best_bid"), table.column("best_ask"))
    if pc.any(bad_spread).as_py():
        logger.warning("compact_daily_orderbook_bad_spread")
    obi = table.column("obi")
    bad_obi = pc.or_(pc.less(obi, pa.scalar(-1.0)), pc.greater(obi, pa.scalar(1.0)))
    if pc.any(bad_obi).as_py():
        logger.warning("compact_daily_orderbook_bad_obi")
def _stream_write_parquet(sources: Sequence[_HourSource], final_path: Path, schema: pa.Schema) -> None:
    tmp_path = Path(str(final_path) + ".tmp")
    writer = pq.ParquetWriter(str(tmp_path), schema, compression="snappy")
    try:
        for source in sources:
            writer.write_table(source.table)
    finally:
        writer.close()
    with tmp_path.open("rb") as handle:
        os.fsync(handle.fileno())
    try:
        os.replace(tmp_path, final_path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    _fsync_parent_dir(final_path)
def _write_json_atomic(metadata: dict[str, Any], final_path: Path) -> None:
    tmp_path = Path(str(final_path) + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(tmp_path, final_path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    _fsync_parent_dir(final_path)
def _verify_parquet_output(final_path: Path, schema: pa.Schema, expected_rows: int) -> _OutputStats:
    if not final_path.exists():
        raise CompactionError(f"daily parquet was not created: {final_path}")
    actual_schema = pq.read_schema(final_path)
    _validate_schema_compatible(actual_schema, schema)
    parquet_file = pq.ParquetFile(final_path)
    metadata = parquet_file.metadata
    row_count = metadata.num_rows
    if row_count != expected_rows:
        raise CompactionError(f"daily parquet row count mismatch: {row_count} != {expected_rows}")
    start_ts, end_ts = _timestamp_range_from_metadata(parquet_file, schema) if row_count else (None, None)
    return _OutputStats(row_count, final_path.stat().st_size, start_ts, end_ts)
def _validate_schema_compatible(actual: pa.Schema, expected: pa.Schema) -> None:
    if len(actual) != len(expected):
        raise CompactionError("daily parquet schema field count does not match expected stream schema")
    for index, (actual_field, expected_field) in enumerate(zip(actual, expected, strict=True)):
        if actual_field.name != expected_field.name:
            raise CompactionError(
                f"daily parquet schema field {index} name mismatch: "
                f"{actual_field.name} != {expected_field.name}"
            )
        if actual_field.type != expected_field.type:
            raise CompactionError(
                f"daily parquet schema field {actual_field.name} type mismatch: "
                f"{actual_field.type} != {expected_field.type}"
            )
def _timestamp_range_from_metadata(parquet_file: pq.ParquetFile, schema: pa.Schema) -> tuple[int | None, int | None]:
    metadata = parquet_file.metadata
    start_ts: int | None = None
    end_ts: int | None = None
    ts_col_idx = schema.get_field_index("timestamp")
    for row_group_index in range(metadata.num_row_groups):
        row_group = metadata.row_group(row_group_index)
        column = row_group.column(ts_col_idx)
        stats = column.statistics
        if stats is None or not stats.has_min_max:
            table = parquet_file.read_row_group(row_group_index, columns=["timestamp"])
            timestamp_col = table.column("timestamp").cast(pa.int64())
            row_group_min = pc.min(timestamp_col).as_py()
            row_group_max = pc.max(timestamp_col).as_py()
        else:
            row_group_min = _timestamp_stat_to_ms(stats.min)
            row_group_max = _timestamp_stat_to_ms(stats.max)
        if row_group_min is not None:
            start_ts = row_group_min if start_ts is None else min(start_ts, row_group_min)
        if row_group_max is not None:
            end_ts = row_group_max if end_ts is None else max(end_ts, row_group_max)
    return start_ts, end_ts
def _timestamp_stat_to_ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    return int(value)
def _build_metadata(
    *,
    stats: _OutputStats,
    stream: str,
    date: str,
    source_files: Sequence[Path],
    missing_hours: Sequence[int],
    skipped_hours: Sequence[int],
) -> dict[str, Any]:
    return {
        "date": date,
        "stream": stream,
        "record_count": stats.rows,
        "file_size_bytes": stats.file_size_bytes,
        "source_hourly_files": [path.name for path in source_files],
        "missing_hours": list(missing_hours),
        "skipped_hours": list(skipped_hours),
        "start_timestamp_ms": stats.start_timestamp_ms,
        "end_timestamp_ms": stats.end_timestamp_ms,
        "start_timestamp_iso": _format_ms_iso(stats.start_timestamp_ms),
        "end_timestamp_iso": _format_ms_iso(stats.end_timestamp_ms),
        "compacted_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
def _format_ms_iso(value: int | None) -> str | None:
    if value is None:
        return None
    dt = datetime.fromtimestamp(value / 1000, tz=UTC)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
def _remove_stale_tmp(final_path: Path, stream: str, date: str) -> None:
    tmp_path = Path(str(final_path) + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()
        logger.info("compact_daily_cleanup_tmp", stream=stream, date=date, path=str(tmp_path))
def _fsync_parent_dir(final_path: Path) -> None:
    parent_fd = os.open(str(final_path.parent), os.O_RDONLY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
def _hour_from_path(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[1])
if __name__ == "__main__":
    raise SystemExit(main())

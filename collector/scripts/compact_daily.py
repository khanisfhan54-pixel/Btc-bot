"""Offline daily compaction for raw hourly collector Parquet files."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
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
CURRENT_HOUR_MTIME_GUARD_SECONDS = 90


class CompactionError(RuntimeError):
    """Raised when a daily compaction input fails validation.

    Args:
        RuntimeError: Base exception type for compaction validation failures.
    """


def compact_daily(
    date: str,
    stream: str,
    data_dir: Path | str = Path("data"),
    *,
    force: bool = False,
) -> bool:
    """Compact one stream/date pair into a daily Parquet file.

    Args:
        date: Date to compact in YYYY-MM-DD form.
        stream: Stream name to compact.
        data_dir: Root data directory containing raw/ and daily/.
        force: Whether to overwrite an existing daily file.

    Returns:
        True when compaction wrote a file or safely skipped; False on failure.
    """
    data_root = Path(data_dir)
    schema = _schema_for_stream(stream)
    raw_dir = data_root / "raw" / stream
    out_dir = data_root / "daily" / stream
    final_path = out_dir / f"{date}.parquet"

    if final_path.exists() and not force:
        logger.info("compact_daily_skip", stream=stream, date=date, reason="exists")
        return True

    discovered = _discover_hourly_files(raw_dir, date)
    if not discovered.present_files:
        logger.warning("compact_daily_no_sources", stream=stream, date=date)
        return True

    tables: list[pa.Table] = []
    row_counts: list[int] = []
    for source_path in discovered.present_files:
        table = _read_hourly_table(source_path, schema)
        tables.append(table)
        row_counts.append(table.num_rows)

    combined = pa.concat_tables(tables, promote_options="default")
    expected_rows = sum(row_counts)
    if combined.num_rows != expected_rows:
        raise CompactionError(
            f"row count mismatch for {stream} {date}: "
            f"combined={combined.num_rows} expected={expected_rows}"
        )

    combined = combined.sort_by([("timestamp", "ascending")])
    combined = _align_to_schema(combined, schema)
    _validate_table(combined, stream, expected_rows)

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_parquet_atomic(combined, final_path, schema)
    metadata = _build_metadata(
        table=combined,
        stream=stream,
        date=date,
        final_path=final_path,
        source_files=discovered.present_files,
        missing_hours=discovered.missing_hours,
    )
    _write_json_atomic(metadata, final_path.with_suffix(".meta.json"))
    logger.info("compact_daily", stream=stream, date=date, rows=combined.num_rows)
    return True


def main(argv: Sequence[str] | None = None) -> int:
    """Run the daily compaction command-line interface.

    Args:
        argv: Optional argument vector. Uses sys.argv when omitted.

    Returns:
        Process exit code: 0 on success, 1 if any compaction failed.
    """
    args = _parse_args(argv)
    streams = list(args.streams or ALL_STREAMS)
    data_dir = Path(args.data_dir)
    dates = [args.date] if args.date else sorted(_discover_all_dates(data_dir, streams))

    failed = False
    for date in dates:
        for stream in streams:
            try:
                if not compact_daily(date, stream, data_dir, force=args.force):
                    failed = True
            except (CompactionError, OSError, pa.ArrowException, ValueError) as exc:
                logger.error("compact_daily_failed", stream=stream, date=date, error=str(exc))
                failed = True
    return 1 if failed else 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="compact exactly one date (YYYY-MM-DD)")
    group.add_argument("--all", action="store_true", help="compact every discovered date")
    parser.add_argument("--force", action="store_true", help="overwrite existing daily files")
    parser.add_argument("--streams", nargs="+", choices=ALL_STREAMS, default=list(ALL_STREAMS))
    parser.add_argument("--data-dir", default="data", help="data directory root")
    args = parser.parse_args(argv)
    if args.date:
        _parse_date(args.date)
    return args


def _schema_for_stream(stream: str) -> pa.Schema:
    try:
        return STREAM_SCHEMAS[stream]
    except KeyError as exc:
        raise ValueError(f"unknown stream: {stream}") from exc


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)


class _DiscoveredFiles(tuple):
    @property
    def present_files(self) -> list[Path]:
        return self[0]

    @property
    def missing_hours(self) -> list[int]:
        return self[1]


def _discover_hourly_files(raw_dir: Path, date: str) -> _DiscoveredFiles:
    present: list[Path] = []
    missing: list[int] = []
    now = datetime.now(UTC)
    current_date = now.strftime("%Y-%m-%d")
    current_hour = now.hour

    for hour in range(24):
        path = raw_dir / f"{date}-{hour:02d}.parquet"
        if not path.exists():
            logger.warning("compact_daily_missing_hour", stream=raw_dir.name, date=date, hour=hour)
            missing.append(hour)
            continue
        if path.name.endswith((".tmp", ".bak")):
            continue
        if date == current_date and hour == current_hour:
            age_seconds = datetime.now(UTC).timestamp() - path.stat().st_mtime
            if age_seconds < CURRENT_HOUR_MTIME_GUARD_SECONDS:
                logger.warning(
                    "compact_daily_skip_current_hour",
                    stream=raw_dir.name,
                    date=date,
                    hour=hour,
                    age_seconds=age_seconds,
                )
                missing.append(hour)
                continue
        present.append(path)
    return _DiscoveredFiles((present, missing))


def _discover_all_dates(data_dir: Path, streams: Sequence[str]) -> set[str]:
    dates: set[str] = set()
    for stream in streams:
        raw_dir = data_dir / "raw" / stream
        if not raw_dir.exists():
            continue
        for path in raw_dir.glob("*.parquet"):
            if path.name.endswith((".tmp", ".bak")):
                continue
            match = DATE_RE.match(path.name)
            if match:
                dates.add(match.group(1))
    return dates


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


def _validate_table(table: pa.Table, stream: str, expected_rows: int) -> None:
    if table.num_rows != expected_rows:
        raise CompactionError(f"row count mismatch after schema alignment: {table.num_rows} != {expected_rows}")
    timestamps = table.column("timestamp")
    if timestamps.null_count:
        raise CompactionError("timestamp column contains null values")
    timestamp_ms = timestamps.cast(pa.int64()).combine_chunks().to_pylist()
    if any(value is None or (isinstance(value, float) and math.isnan(value)) for value in timestamp_ms):
        raise CompactionError("timestamp column contains invalid values")

    if stream == "trades":
        if any(left > right for left, right in zip(timestamp_ms, timestamp_ms[1:])):
            raise CompactionError("trades timestamps are not monotonically non-decreasing")
        trade_ids = table.column("trade_id").combine_chunks().to_pylist()
        pairs = list(zip(timestamp_ms, trade_ids))
        if len(pairs) != len(set(pairs)):
            raise CompactionError("duplicate (timestamp, trade_id) pairs found")
    else:
        if any(left >= right for left, right in zip(timestamp_ms, timestamp_ms[1:])):
            raise CompactionError(f"{stream} timestamps are not strictly increasing")
        if len(timestamp_ms) != len(set(timestamp_ms)):
            raise CompactionError(f"duplicate timestamp values found in {stream}")

    if stream == "orderbook":
        _warn_orderbook_quality(table)


def _warn_orderbook_quality(table: pa.Table) -> None:
    bad_spread = pc.greater_equal(table.column("best_bid"), table.column("best_ask"))
    if pc.any(bad_spread).as_py():
        logger.warning("compact_daily_orderbook_bad_spread")
    obi = table.column("obi")
    bad_obi = pc.or_(pc.less(obi, pa.scalar(-1.0)), pc.greater(obi, pa.scalar(1.0)))
    if pc.any(bad_obi).as_py():
        logger.warning("compact_daily_orderbook_bad_obi")


def _write_parquet_atomic(table: pa.Table, final_path: Path, schema: pa.Schema) -> None:
    tmp_path = Path(str(final_path) + ".tmp")
    writer = pq.ParquetWriter(str(tmp_path), schema, compression="snappy")
    try:
        writer.write_table(table)
    finally:
        writer.close()
    with tmp_path.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(tmp_path, final_path)


def _write_json_atomic(metadata: dict[str, Any], final_path: Path) -> None:
    tmp_path = Path(str(final_path) + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, final_path)


def _build_metadata(
    *,
    table: pa.Table,
    stream: str,
    date: str,
    final_path: Path,
    source_files: Sequence[Path],
    missing_hours: Sequence[int],
) -> dict[str, Any]:
    timestamp_ms = table.column("timestamp").cast(pa.int64()).combine_chunks().to_pylist()
    start_ms = int(timestamp_ms[0]) if timestamp_ms else None
    end_ms = int(timestamp_ms[-1]) if timestamp_ms else None
    return {
        "date": date,
        "stream": stream,
        "record_count": table.num_rows,
        "file_size_bytes": final_path.stat().st_size,
        "source_hourly_files": [path.name for path in source_files],
        "missing_hours": list(missing_hours),
        "start_timestamp_ms": start_ms,
        "end_timestamp_ms": end_ms,
        "start_timestamp_iso": _format_ms_iso(start_ms),
        "end_timestamp_iso": _format_ms_iso(end_ms),
        "compacted_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def _format_ms_iso(value: int | None) -> str | None:
    if value is None:
        return None
    dt = datetime.fromtimestamp(value / 1000, tz=UTC)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())

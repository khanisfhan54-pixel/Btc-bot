# Daily Compaction

`collector/scripts/compact_daily.py` compacts raw hourly Parquet files into one daily Parquet file per stream. It is an offline maintenance command: it does not modify raw hourly naming, collector startup, or collector recovery behavior.

## File Layout

```text
data/
  raw/
    orderbook/       YYYY-MM-DD-HH.parquet
    trades/          YYYY-MM-DD-HH.parquet
    markprice/       YYYY-MM-DD-HH.parquet
    openinterest/    YYYY-MM-DD-HH.parquet
    liquidation/     YYYY-MM-DD-HH.parquet
  daily/
    orderbook/       YYYY-MM-DD.parquet
                     YYYY-MM-DD.meta.json
    trades/          YYYY-MM-DD.parquet
                     YYYY-MM-DD.meta.json
    markprice/       YYYY-MM-DD.parquet
                     YYYY-MM-DD.meta.json
    openinterest/    YYYY-MM-DD.parquet
                     YYYY-MM-DD.meta.json
    liquidation/     YYYY-MM-DD.parquet
                     YYYY-MM-DD.meta.json
```

Only `data/daily/{stream}/` is created by daily compaction. The raw hourly inputs remain unchanged.

## Guard Behavior

The mtime guard is intentionally narrow:

- Historical hourly files are never skipped due to mtime, regardless of when they were last modified or copied into place.
- The mtime guard is applied only when the hourly file belongs to the current UTC date and the current UTC hour.
- When `date == current UTC date` and `hour == current UTC hour`, files modified within `guard_seconds` are skipped and the hour is recorded in `skipped_hours`.
- The default guard window is 90 seconds.
- Any hour with a matching `YYYY-MM-DD-HH.parquet.tmp` file is skipped unconditionally and recorded in `skipped_hours`.

This allows backfills and historical repairs to compact recently-copied hourly files while still avoiding races with the live writer for the active current-hour file.

## Streaming Architecture

Daily compaction streams inputs by hourly source:

1. `_inspect_sources()` reads each hourly Parquet file exactly once.
2. The hourly table is aligned to the expected stream schema, sorted by `timestamp`, and validated.
3. The validated table is stored on `HourSource.table`.
4. `_stream_write_parquet()` writes each `HourSource.table` directly to the daily output as a row group.

The compactor does not build a full-day in-memory table. It does not use `concat_tables` for the full-day write path.

The only use of Arrow table concatenation is the tiny cross-boundary duplicate check for `trades`, where the final timestamp rows from the previous source and the first timestamp rows from the current source are grouped to detect duplicate `(timestamp, trade_id)` pairs.

## Validation Checks

Before writing a daily file, compaction validates:

- Required columns are present and castable to the configured stream schema.
- Hourly files are non-empty.
- The `timestamp` column contains no null values.
- `trades` timestamps are monotonically non-decreasing within each hourly file and across ordered hourly ranges.
- `orderbook`, `markprice`, `openinterest`, and `liquidation` timestamps are strictly increasing within each hourly file and across ordered hourly ranges.
- `trades` has no duplicate `(timestamp, trade_id)` pairs within an hourly file or across adjacent equal-timestamp boundaries.
- Non-trade streams have no duplicate `timestamp` values.
- `orderbook` rows where `best_bid >= best_ask` or `obi` is outside `[-1.0, 1.0]` are logged as warnings but do not abort compaction.

Duplicate detection uses Arrow grouping and compute kernels rather than Python trade-id sets or timestamp-list materialization.

## Atomic Writes and Tmp Cleanup

Daily Parquet and metadata sidecar writes use `.tmp` files followed by `os.replace` and a parent-directory fsync. Readers either see the previous complete file or the new complete file; they do not see a partially written final file.

Both `_stream_write_parquet()` and `_write_json_atomic()` wrap `os.replace` in `try`/`except`. If `os.replace` raises, the corresponding `.tmp` file is deleted before the exception is re-raised.

A forced compaction run (`--force`) also removes stale Parquet and metadata `.tmp` files for the requested output before rewriting.

## Schema Compatibility

Daily output verification uses `_validate_schema_compatible()` rather than strict schema object equality. Compatibility checks require:

- The same field count.
- Matching field names in order.
- Matching field types in order.

Schema-level metadata differences, such as `interval_start` or `interval_end`, are ignored and do not cause false validation failures.

## Metadata Verification

Each daily file has a sidecar `YYYY-MM-DD.meta.json`. `verify_daily()` checks the Parquet file against that metadata and raises `CompactionError` on any mismatch.

Verification checks:

- `record_count` matches the Parquet row count.
- `file_size_bytes` matches the actual daily Parquet file size.
- `start_timestamp_ms` matches the Parquet timestamp minimum.
- `end_timestamp_ms` matches the Parquet timestamp maximum.
- `source_hourly_files` is a list.
- `missing_hours` is a list of integers.
- `skipped_hours` is a list of integers.

The Parquet timestamp range is read from row-group statistics when available. If a row group lacks timestamp min/max statistics, verification falls back to reading only that row group's `timestamp` column.

## Usage

Run from the repository root or any environment where the package is importable:

```bash
python -m collector.scripts.compact_daily --date 2026-06-10
python -m collector.scripts.compact_daily --all
python -m collector.scripts.compact_daily --date 2026-06-10 --streams orderbook trades
python -m collector.scripts.compact_daily --all --force --data-dir /mnt/data
python -m collector.scripts.compact_daily --date 2026-06-10 --guard-seconds 180
python -m collector.scripts.compact_daily --verify --date 2026-06-10
```

By default, compaction processes all five streams: `orderbook`, `trades`, `markprice`, `openinterest`, and `liquidation`. Without `--force`, an existing `data/daily/{stream}/{date}.parquet` file is skipped so repeated runs are idempotent.

## Retention Policy Integration

After a daily Parquet file and its `.meta.json` sidecar verify successfully, raw hourly files for that date can be archived or deleted according to the deployment retention policy. Do not remove raw files before verifying the daily row count, timestamp range, file size, and sidecar metadata.

Example archive flow:

```bash
stream=orderbook date=2026-06-10
python -m collector.scripts.compact_daily --verify --date "$date" --streams "$stream" \
  && mkdir -p data/archive/raw/$stream \
  && tar -czf data/archive/raw/$stream/$date-hours.tar.gz data/raw/$stream/$date-*.parquet \
  && rm data/raw/$stream/$date-*.parquet
```

## Running Tests

```bash
pytest collector/tests/test_daily_compaction.py -v
pytest collector/tests/ -v
ruff check collector/scripts/compact_daily.py
python -m collector.scripts.compact_daily --help
```

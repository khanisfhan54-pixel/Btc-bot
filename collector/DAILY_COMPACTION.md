# Daily Compaction Pipeline

## Architecture

Daily compaction is an offline, read-only step over the live collector's hourly raw Parquet outputs. It never imports or changes the live `ParquetWriter`, never writes under `data/raw/`, and never changes the hourly filename contract (`YYYY-MM-DD-HH.parquet`). The compactor reads existing hourly files from `data/raw/{stream}/` and writes additive daily outputs under `data/daily/{stream}/`.

Daily Parquet and metadata files use the same atomic write shape as the collector: write the complete payload to a sibling `.tmp` file, fsync it, publish it with `os.replace`, and fsync the output directory. Because `os.replace` is atomic on the same filesystem and the parent directory is flushed after rename, readers either see the previous complete file or the new complete file; they do not see a partially-written daily output.

The compactor guards the current UTC hour and any recently-modified hourly file. If the current-hour file was modified inside the configurable guard window (90 seconds by default), or if a matching `YYYY-MM-DD-HH.parquet.tmp` exists, the hour is skipped and recorded in the sidecar `skipped_hours` list. This prevents racing the live writer while it may still be flushing or recovering a file.

The implementation streams hourly inputs into the daily Parquet writer one source at a time. It inspects and validates each hourly file independently, orders the sources by timestamp range, then appends row groups directly to the output writer instead of concatenating all 24 hourly tables in memory.

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
  aligned/
  splits/
  stats/
```

Only `data/daily/{stream}/` is created by this pipeline. The assembler's `data/raw/` inputs remain unchanged.

## Recovery Behaviour

If a run is aborted mid-stream, an unpublished `.tmp` file may remain next to the intended daily output. The next run with `--force` removes stale Parquet and metadata `.tmp` files, logs the cleanup, writes fresh `.tmp` files, fsyncs them, atomically replaces the final files with `os.replace`, and fsyncs the parent directory.

Missing hourly files are not fatal. The compactor logs a warning for each absent `HH` file and records those integer hours in the sidecar `missing_hours` field. Hours skipped because a matching `.tmp` exists or because the current-hour guard fired are recorded in `skipped_hours`.

If an hourly file is corrupt or unreadable as Parquet, PyArrow raises during the read. The CLI logs the stream/date failure as an error, skips that stream/date output, continues with remaining work, and exits with code `1` after all requested streams and dates have been attempted.

After writing a daily Parquet file, the compactor explicitly verifies that the final file exists, is readable, has the expected stream schema, and has the expected row count before collecting `file_size_bytes` for the metadata sidecar.

## Usage

Run from the `collector/` repository root or with an environment where the package is importable:

```bash
python -m collector.scripts.compact_daily --date 2026-06-10
python -m collector.scripts.compact_daily --all
python -m collector.scripts.compact_daily --date 2026-06-10 --streams orderbook trades
python -m collector.scripts.compact_daily --all --force --data-dir /mnt/data
python -m collector.scripts.compact_daily --date 2026-06-10 --guard-seconds 180
python -m collector.scripts.compact_daily --verify --date 2026-06-10
```

By default, compaction processes all five streams: `orderbook`, `trades`, `markprice`, `openinterest`, and `liquidation`. Without `--force`, existing `data/daily/{stream}/{date}.parquet` files are skipped so repeated runs are idempotent. `--verify` reads existing daily outputs and checks that the Parquet file, schema, metadata row count, and metadata timestamp range are consistent.

## Validation Checks

Before writing a daily file, the compactor validates:

- Concatenated row count expectation equals the sum of per-file row counts for every stream.
- The `timestamp` column contains no null values.
- `trades` timestamps are monotonically non-decreasing within each hourly file and across ordered hourly ranges.
- `orderbook`, `markprice`, `openinterest`, and `liquidation` timestamps are strictly increasing within each hourly file and across ordered hourly ranges.
- `trades` has no duplicate `(timestamp, trade_id)` pairs within an hourly file or across adjacent equal-timestamp boundaries.
- The non-trade streams have no duplicate `timestamp` values.
- `orderbook` rows where `best_bid >= best_ask` are logged as warnings but do not abort compaction.
- `orderbook` rows where `obi` is outside `[-1.0, 1.0]` are logged as warnings but do not abort compaction.

Validation uses PyArrow compute kernels for column-wide checks and avoids materializing production-sized timestamp columns or trade key sets into Python memory. Daily outputs are written using the imported stream schemas, preserving column types and schema metadata.

## Retention Policy Integration

After a daily compacted Parquet file and its `.meta.json` sidecar have been verified, the raw hourly files for that date can be archived or deleted to reclaim disk, consistent with the storage policy discussed in `README.md`. Do not remove raw files before verifying the daily file row count, timestamp range, and sidecar metadata.

Suggested archive one-liner after verification:

```bash
stream=orderbook date=2026-06-10; python -m collector.scripts.compact_daily --verify --date "$date" --streams "$stream" && mkdir -p data/archive/raw/$stream && tar -czf data/archive/raw/$stream/$date-hours.tar.gz data/raw/$stream/$date-*.parquet && rm data/raw/$stream/$date-*.parquet
```

Repeat for each stream only after confirming `data/daily/{stream}/{date}.parquet` and `data/daily/{stream}/{date}.meta.json` are present and valid.

## Running the Test Suite

```bash
pytest collector/tests/test_daily_compaction.py -v
pytest collector/tests/
```

# Daily Compaction Pipeline

## Architecture

Daily compaction is an offline, read-only step over the live collector's hourly raw Parquet outputs. It never imports or changes the live `ParquetWriter`, never writes under `data/raw/`, and never changes the hourly filename contract (`YYYY-MM-DD-HH.parquet`). The compactor reads existing hourly files from `data/raw/{stream}/` and writes additive daily outputs under `data/daily/{stream}/`.

Daily Parquet files use the same atomic write pattern as the collector: write the complete payload to a sibling `.tmp` file, fsync it, and then publish it with `os.replace`. Because `os.replace` is atomic on the same filesystem, readers either see the previous complete daily file or the new complete daily file; they do not see a partially-written Parquet file.

The compactor also guards the current UTC hour. If the expected current-hour file was modified within the last 90 seconds, the compactor skips that file and records the hour as missing. This prevents racing the live writer while it may still be flushing the hourly Parquet file.

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

If a run is aborted mid-stream, an unpublished `.tmp` file may remain next to the intended daily output. The next run with `--force` writes a fresh `.tmp`, fsyncs it, and atomically replaces the final daily file with `os.replace`; stale temporary files are not treated as valid daily files.

Missing hourly files are not fatal. The compactor logs a warning for each absent `HH` file and records those integer hours in the sidecar `missing_hours` field.

If an hourly file is corrupt or unreadable as Parquet, PyArrow raises during the read. The CLI logs the stream/date failure as an error, skips that stream/date output, continues with remaining work, and exits with code `1` after all requested streams and dates have been attempted.

## Usage

Run from the `collector/` repository root or with an environment where the package is importable:

```bash
python -m collector.scripts.compact_daily --date 2026-06-10
python -m collector.scripts.compact_daily --all
python -m collector.scripts.compact_daily --date 2026-06-10 --streams orderbook trades
python -m collector.scripts.compact_daily --all --force --data-dir /mnt/data
```

By default, compaction processes all five streams: `orderbook`, `trades`, `markprice`, `openinterest`, and `liquidation`. Without `--force`, existing `data/daily/{stream}/{date}.parquet` files are skipped so repeated runs are idempotent.

## Validation Checks

Before writing a daily file, the compactor validates:

- Concatenated row count equals the sum of per-file row counts for every stream.
- The `timestamp` column contains no null values.
- `trades` timestamps are monotonically non-decreasing.
- `orderbook`, `markprice`, `openinterest`, and `liquidation` timestamps are strictly increasing.
- `trades` has no duplicate `(timestamp, trade_id)` pairs.
- The non-trade streams have no duplicate `timestamp` values.
- `orderbook` rows where `best_bid >= best_ask` are logged as warnings but do not abort compaction.
- `orderbook` rows where `obi` is outside `[-1.0, 1.0]` are logged as warnings but do not abort compaction.

Daily outputs are written using the imported stream schemas, preserving column types and schema metadata.

## Retention Policy Integration

After a daily compacted Parquet file and its `.meta.json` sidecar have been verified, the raw hourly files for that date can be archived or deleted to reclaim disk, consistent with the storage policy discussed in `README.md`. Do not remove raw files before verifying the daily file row count, timestamp range, and sidecar metadata.

Suggested archive one-liner after verification:

```bash
stream=orderbook date=2026-06-10; mkdir -p data/archive/raw/$stream && tar -czf data/archive/raw/$stream/$date-hours.tar.gz data/raw/$stream/$date-*.parquet && rm data/raw/$stream/$date-*.parquet
```

Repeat for each stream only after confirming `data/daily/{stream}/{date}.parquet` and `data/daily/{stream}/{date}.meta.json` are present and valid.

## Running the Test Suite

```bash
pytest collector/tests/test_daily_compaction.py -v
pytest collector/tests/
```

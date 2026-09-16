# BTCUSDT Market-Data Collector

This directory contains the standalone Binance USD-M BTCUSDT collector and its
research-data pipeline. It is **not production-ready**: replay parity,
collector-level crash testing, multi-venue runtimes, durable quality-event
coverage, and split-leakage controls remain incomplete.

## Storage contract

Raw output is atomically published under `data/raw/<stream>/` as immutable
`YYYY-MM-DD-HH-NNNNNN.seg` Parquet segments. Legacy
`YYYY-MM-DD-HH.parquet` files remain supported for reading. All raw-data
readers use `collector.storage_layout.iter_segments`; temporary files are never
read.

## Commands

From the repository root after installing `collector/requirements.txt`:

```bash
python -m collector.pipeline.dataset_assembler 2026-06-03
python -m collector.scripts.verify_dataset 2026-06-03
python -m collector.scripts.gap_report 2026-06-03
python -m collector.pipeline.stats_computer
python -m collector.pipeline.label_generator 2026-06-03
python -m collector.pipeline.split_generator
python -m collector.run_collector
```

`verify_dataset.py` and `gap_report.py` fail when a requested stream has no
published source segments. An absence of data is not reported as healthy.

## Operations

Use `btc-collector.service` as a deployment template. Put notification
configuration in `/etc/btc-collector.env`; no chat identifier is committed to
the repository. The service has a 60-second stop budget to allow segment
publication, but the collector-level SIGKILL-loss boundary is not yet proven.

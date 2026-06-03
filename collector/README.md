# BTCUSDT Perpetual ML Training Data Collector

A standalone BTCUSDT Perpetual Futures market data collection system that produces ML-ready, temporally-aligned datasets suitable for feature engineering, model training, and event-driven backtesting.

## Architecture

This system has three primary responsibilities, strictly separated into modules:

1. **Collector (`collector/`)**: Connects to Binance WebSocket, computes ML-relevant derived features at ingestion time, validates records, monitors system health (disk, gaps, connection), and writes hourly compressed Parquet files.
2. **Pipeline (`pipeline/`)**: Assembles raw hourly streams onto a common temporal grid (e.g., 100ms), aggregates trades, computes z-score normalization stats, generates forward return labels for supervised learning, and produces chronological train/val/test splits.
3. **Scripts (`scripts/`)**: Provides utilities to verify dataset integrity, run replay tests, and generate gap reports.

### Features

- Auto-reconnect with exponential backoff on WebSocket disconnects.
- Defensive record-level validation to prevent corruption.
- Real-time disk monitoring with emergency halt at < 2GB free.
- Telegram alerting for critical events (connection lost, disk warning, validation spikes, gaps).
- Temporal alignment of orderbook, trade aggregates, and mark price.
- Strict chronological splitting (70/15/15) to prevent lookahead leakage.

## Setup & Deployment

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Set environment variables (e.g., in `.env` or systemd service):
   ```
   TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ123456789"
   TELEGRAM_CHAT_ID="987654321"
   ```

3. Deploy as a systemd service (see `btc-collector.service`).

## Runbook

### Start / Stop / Restart
- Start: `systemctl start btc-collector`
- Stop: `systemctl stop btc-collector`
- Restart: `systemctl restart btc-collector`
- Status: `systemctl status btc-collector`

### Check Health
- Check logs: `tail -f logs/collector.log` (Look for the "Health check summary" every 60s)
- Check Telegram alerts.
- Use `scripts/gap_report.py` to verify gap coverage.

### Recover from a Gap or Crash
- The system automatically resets state on reconnect or restart.
- For gaps, the `dataset_assembler.py` will automatically flag missing orderbook or markprice data with `orderbook_gap=True` and `markprice_gap=True`. ML training must exclude these rows.

### Run Pipeline
To run the full pipeline for a specific date:
```bash
python pipeline/dataset_assembler.py 2026-06-03
python pipeline/stats_computer.py
python pipeline/label_generator.py 2026-06-03
python pipeline/split_generator.py
```

### Verify Data Quality
```bash
python scripts/verify_dataset.py 2026-06-01 2026-06-30
python scripts/replay_test.py 2026-06-03
```

## Disk Usage & Storage Management

| Stream     | Rate          | Raw Size / day   | 30-day total |
|------------|---------------|------------------|--------------|
| Orderbook  | 10 rows/sec   | ~600 MB          | ~18 GB       |
| Trades     | ~5–50/sec avg | ~200 MB          | ~6 GB        |
| Mark Price | 1 row/sec     | ~15 MB           | ~450 MB      |
| Aligned    | 10 rows/sec   | ~250 MB          | ~7.5 GB      |
| Labeled    | 10 rows/sec   | ~300 MB          | ~9 GB        |
| Logs       | —             | ~5 MB            | ~150 MB      |
| **Total**  |               |                  | **~41 GB**   |

**Policy**: The 30 days of raw + aligned + labeled data will exceed the 17 GB constraint of the VPS. You must implement an auto-archive policy: after 30 days (or sooner), raw hourly files (`data/raw/`) should be deleted or moved off-server once aligned and labeled files (`data/aligned/`) are verified. The collector guards against disk exhaustion by halting completely if free space drops below 2 GB.

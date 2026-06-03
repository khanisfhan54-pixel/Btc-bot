# BTCUSDT Perpetual ML Training Data Collector

Standalone data collection system to produce ML-ready, temporally-aligned datasets.

## Architecture

1. **Collector (`run_collector.py`)**: Runs constantly. Connects to Binance websocket, validates raw data, computes streaming features, saves to hourly snappy-compressed Parquet.
2. **Pipeline (`pipeline/`)**: Runs offline. Assembles raw data into 100ms grid-aligned datasets, computes z-score normalizations, labels, and train/val/test splits.

## Disk Usage Estimate

| Stream     | Rate          | Raw Size / day   | 30-day total |
|------------|---------------|------------------|--------------|
| Orderbook  | 10 rows/sec   | ~600 MB          | ~18 GB       |
| Trades     | ~5–50/sec avg | ~200 MB          | ~6 GB        |
| Mark Price | 1 row/sec     | ~15 MB           | ~450 MB      |
| Aligned    | 10 rows/sec   | ~250 MB          | ~7.5 GB      |
| Labeled    | 10 rows/sec   | ~300 MB          | ~9 GB        |
| Logs       | —             | ~5 MB            | ~150 MB      |
| **Total**  |               |                  | **~41 GB**   |

**Auto-Archive Policy:** Due to the 17 GB free disk limit on the VPS, raw data should be deleted after running the pipeline and verifying aligned/labeled datasets using `scripts/verify_dataset.py`.

## Deployment

1. Install requirements: `pip install -r requirements.txt`
2. Set env variables in `.env` or systemd:
   ```
   TELEGRAM_BOT_TOKEN="1234:ABCDef"
   TELEGRAM_CHAT_ID="987654321"
   ```
3. Run as service using `btc-collector.service`.

## Runbooks

**Start/Stop:**
`sudo systemctl start btc-collector`
`sudo systemctl stop btc-collector`

**Health check:**
Monitor `logs/collector.log` or check Telegram alerts.

**Pipeline Execution:**
1. Align: `python -m pipeline.dataset_assembler 2024-01-01`
2. Label: `python -m pipeline.label_generator 2024-01-01`
3. Split: `python -m pipeline.split_generator`
4. Stats: `python -m pipeline.stats_computer`

**Verify Quality:**
1. Run `python -m scripts.verify_dataset 2024-01-01 2024-01-01`
2. Run `python -m scripts.replay_test 2024-01-01`
3. Check gaps: `python -m scripts.gap_report 2024-01-01 2024-01-01`

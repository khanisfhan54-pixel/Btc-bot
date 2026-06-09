# Telegram Alert Integration Audit

## Telegram message senders and alert call paths

- `telegram_bot.py`
  - `send_telegram_message(message, parse_mode="Markdown")` posts to Telegram `sendMessage`.
  - `send_test_telegram_alert()` sends the fixed integration-test message: `✅ BTC Bot Telegram Alert Test`.
- `main.py`
  - Imports `send_telegram_message` and sends boot, degraded-mode, circuit-breaker, fatal, shutdown, and trading lifecycle alerts through the shared helper.
  - `send_telegram(message)` is a legacy wrapper routed through `send_telegram_message`.
- `execution.py`
  - Imports `send_telegram_message` and sends order execution, order failure, protective-order, and emergency execution alerts through the shared helper.
- `engine.py`
  - `TelegramAlertSystem.send_message()` posts directly to Telegram `sendMessage` when `BTCBOT_LIVE_MODE=1`.
  - `TelegramAlertSystem.send_signal()` serializes a signal and routes it through `send_message()`.
- `collector/collector/utils.py`
  - `send_telegram_alert(message)` now routes collector alerts through the shared Telegram helper.
- Collector caller modules that trigger `send_telegram_alert()`:
  - `collector/run_collector.py`
  - `collector/collector/parquet_writer.py`
  - `collector/collector/gap_detector.py`
  - `collector/collector/health_monitor.py`
  - `collector/collector/disk_monitor.py`

## Environment variable loading

- `telegram_bot.py` loads `.env` via `python-dotenv` when available and reads:
  - `TELEGRAM_BOT_TOKEN`
  - `TELEGRAM_CHAT_ID`
- `main.py` also reads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as startup module constants, preserving the existing environment-based design.
- `engine.py` and `collector/collector/utils.py` now delegate Telegram credential loading to `telegram_bot.py`.

## Files modified

- `telegram_bot.py`
- `main.py`
- `engine.py`
- `collector/collector/utils.py`
- `collector/run_collector.py`
- `tests/test_telegram_bot.py`
- `TELEGRAM_ALERT_AUDIT.md`

## Functions modified or added

- Added `TelegramConfigError` in `telegram_bot.py`.
- Added `TelegramConfig` in `telegram_bot.py`.
- Added `load_telegram_config()` in `telegram_bot.py`.
- Added `validate_telegram_startup()` in `telegram_bot.py`.
- Modified `send_telegram_message()` in `telegram_bot.py`.
- Added/updated `send_test_telegram_alert()` in `telegram_bot.py` with message `✅ BTC Bot Telegram Alert Test`.
- Modified `run_live()` in `main.py` to validate Telegram startup before continuing.
- Modified `TelegramAlertSystem.__init__()` in `engine.py` to load and validate Telegram credentials from the shared helper.
- Modified `send_telegram_alert()` in `collector/collector/utils.py` to use the shared helper.
- Modified the `collector/run_collector.py` entry point to validate Telegram startup before starting the collector.

## Startup commands

Trading bot:

```bash
TELEGRAM_BOT_TOKEN=<BOT_TOKEN> TELEGRAM_CHAT_ID=<CHAT_ID> python main.py
```

Collector:

```bash
TELEGRAM_BOT_TOKEN=<BOT_TOKEN> TELEGRAM_CHAT_ID=<CHAT_ID> python collector/run_collector.py
```

Backtest mode remains available and does not start the live Telegram-validated path:

```bash
python main.py backtest
```

## Required environment variables

```bash
TELEGRAM_BOT_TOKEN=<BOT_TOKEN>
TELEGRAM_CHAT_ID=<CHAT_ID>
```

These may be exported in the shell or provided in `.env`.

## Test results

- `pytest -q tests/test_telegram_bot.py`: passed.
- `python -m py_compile telegram_bot.py main.py engine.py execution.py collector/collector/utils.py collector/run_collector.py`: passed.
- `send_test_telegram_alert()` with supplied test credentials: startup printed `Telegram Bot Config Loaded` and `Telegram Chat ID: 93372553`; no exception was thrown, but Telegram API success/message delivery could not be verified from this environment because outbound access to `api.telegram.org` was blocked by the configured proxy (`ProxyError`).

## Security note for requested hardcoding

Real Telegram bot tokens were not committed to source code. Telegram credentials remain loaded from environment variables or `.env` so the bot can be tested with the supplied values without leaking secrets in git history.

## Lines modified in follow-up

- `telegram_bot.py`: updated `TELEGRAM_TEST_MESSAGE` and startup validation output.
- `tests/test_telegram_bot.py`: updated assertions for the new test message and startup print behavior.
- `TELEGRAM_ALERT_AUDIT.md`: updated the report with the new test message and hardcoding/security note.

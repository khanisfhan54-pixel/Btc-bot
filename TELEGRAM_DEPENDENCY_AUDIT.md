# Telegram Dependency Audit

## Scope

This audit covers every production Telegram dependency found in the BTC bot after hardening. Telegram is now classified as the lowest-priority optional monitoring layer and must never block startup or runtime trading paths.

## Priority Model

1. Execution
2. Risk
3. Market Data
4. Signal Engine
5. Monitoring
6. Telegram

## Dependency Graph

```text
Signal Engine
  └─ no direct Telegram import found

Risk Engine
  └─ no direct Telegram import found

Execution Engine (execution.py)
  └─ imports telegram_bot.send_telegram_message
      └─ queues optional execution/protective-order/emergency alerts
      └─ fallback import shim returns False if Telegram module is unavailable

Collector (collector/run_collector.py, collector/collector/*.py)
  └─ imports collector.utils.send_telegram_alert
      └─ imports telegram_bot.send_telegram_message
      └─ imports telegram_bot.validate_telegram_startup for collector entrypoint diagnostics
      └─ queue/full/API/config failures are logged and swallowed

Health Monitor (collector/collector/health_monitor.py)
  └─ imports collector.utils.send_telegram_alert
      └─ queues optional health alerts

Lifecycle Manager / Main Entrypoint (main.py)
  └─ imports telegram_bot.send_telegram_message
  └─ imports telegram_bot.validate_telegram_startup
      └─ import fallback logs and continues
      └─ startup validation logs diagnostics and never raises

Engine Utility Layer (engine.py)
  └─ TelegramAlertSystem lazily imports telegram_bot helpers only inside methods
      └─ initialization disables Telegram instead of raising when config/network/module is unavailable
```

## Components

### Signal Engine

- **Imports:** No direct Telegram imports found.
- **Startup dependencies:** None.
- **Runtime dependencies:** None.
- **Failure propagation paths:** No Telegram failure path into signal generation.

### Risk Engine

- **Imports:** No direct Telegram imports found in risk-control code paths.
- **Startup dependencies:** None.
- **Runtime dependencies:** None.
- **Failure propagation paths:** No Telegram failure path into risk controls or position sizing.

### Execution Engine

- **Imports:** `execution.py` imports `send_telegram_message` from `telegram_bot` for optional order lifecycle notifications.
- **Startup dependencies:** Import failure is converted to a local no-op fallback returning `False`.
- **Runtime dependencies:** Runtime sends enqueue messages through `telegram_bot.send_telegram_message`; trading threads do not perform Telegram HTTP calls.
- **Failure propagation paths:** Telegram send failures return `False`, are logged by Telegram infrastructure, and do not raise into execution.

### Collector

- **Imports:** `collector/run_collector.py` imports `logger`, `send_telegram_alert`, and `validate_telegram_startup` from `collector.utils`; collector support modules import `send_telegram_alert` from `collector.utils`.
- **Startup dependencies:** Collector entrypoint calls `validate_telegram_startup()`, which now returns config/disabled state and never raises because of Telegram config or API failures.
- **Runtime dependencies:** Collector alerts flow through `send_telegram_alert()` to the Telegram queue.
- **Failure propagation paths:** `send_telegram_alert()` catches all Telegram exceptions, logs a warning, returns `False`, and allows collection to continue.

### Health Monitor

- **Imports:** `collector/collector/health_monitor.py` imports `send_telegram_alert` from collector utilities.
- **Startup dependencies:** None beyond collector utility import.
- **Runtime dependencies:** Optional health alerts enter the same queue and worker.
- **Failure propagation paths:** Queue/API/config failures do not propagate to health monitoring or collector loops.

### Lifecycle Manager

- **Imports:** `main.py` imports `send_telegram_message` and `validate_telegram_startup` with an import fallback.
- **Startup dependencies:** `run_live()` calls Telegram startup validation for diagnostics only. It cannot stop startup.
- **Runtime dependencies:** Boot, shutdown, fatal, and degraded-mode notifications enqueue optional alerts.
- **Failure propagation paths:** Telegram failures are caught around call sites and additionally contained inside the Telegram queue/worker/circuit breaker.

### Main Entrypoint

- **Imports:** Same as lifecycle manager.
- **Startup dependencies:** None that can raise due to Telegram. Telegram constants in `main.py` are compatibility defaults and are not used to gate startup.
- **Runtime dependencies:** Optional alert sends only.
- **Failure propagation paths:** Import fallback and fail-open startup validation prevent Telegram from aborting process startup.

### Telegram Infrastructure

- **Config:** `TelegramConfigManager` loads `.env`/environment once, uses hardcoded `BOT_TOKEN`/`CHAT_ID` defaults when environment variables are absent, permits environment overrides when present, and caches token/chat ID under a re-entrant lock.
- **Circuit breaker:** `TelegramCircuitBreaker` opens after five consecutive failures and cools down for 300 seconds.
- **Queue:** Bounded queue of 1000 alerts; when full, the oldest alert is dropped and trading continues.
- **Worker:** Daemon worker thread performs HTTP requests out-of-band from trading threads.
- **Timeouts/retries:** HTTP calls use 3s connect timeout, 5s read timeout, at most two retries, and 1s/2s backoff.
- **Health:** `get_telegram_health()` exposes sent/failed counts, queue depth, circuit state, last success, and last failure.

## Startup Failure Propagation Summary

| Failure | Startup effect | Runtime effect |
| --- | --- | --- |
| Missing token | Telegram disabled; process continues | Alert calls return `False` |
| Missing chat ID | Telegram disabled; process continues | Alert calls return `False` |
| Invalid token/chat ID | No startup probe blocks startup | Worker records failure and circuit state |
| Telegram 4xx/5xx | No startup block | Failure recorded; 5xx retries bounded |
| DNS/network/proxy/timeout | No startup block | Failure recorded; request bounded by timeout/retry policy |
| Rate limits | Treated as non-200 failure; no propagation | Failure recorded and circuit breaker can open |
| Queue overflow | No startup impact | Oldest alert dropped; warning logged |

## Conclusion

Telegram has no required dependency edge into signal generation, risk controls, position management, market data collection, or execution. Remaining dependencies are optional alerting edges protected by fail-open config handling, queue isolation, bounded HTTP behavior, and a circuit breaker.

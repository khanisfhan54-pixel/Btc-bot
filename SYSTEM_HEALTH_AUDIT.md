# System Health Audit (Phase 14)

| Check | Status | Hardening |
| --- | --- | --- |
| ARE health | Existing backtest path skips bars when ARE reports invalid signals, degraded engine status, uncalibrated feed, halt, fail-safe, or circuit-breaker modes. | Preserved fail-closed gating. |
| SHPE health | Startup previously allowed degraded ambiguity. | `LIVE_TRADING=true` or `SHPE_REQUIRE_TRAINED=true` now requires a valid trained, versioned, calibrated artifact. |
| Model artifact checks | SHPE artifact now validates existence, loadability, classifier, model version, feature names, and required calibrator. | Missing/corrupt/unversioned artifacts raise in required contexts. |
| Data feed checks | Existing feature staleness, snapshot age, regime feed status, and execution filters remain unchanged. | No threshold changes. |
| Telegram alert path | Main bootstrap emits critical SHPE artifact failures through `send_telegram_message` before fail-closed startup. | Alert failures are logged. |
| Startup validation | Critical SHPE artifact failures can no longer be silently treated as non-fatal in live/required mode. | Explicit startup failure. |

## Remaining Risk
Telegram delivery still depends on external network/API availability; local startup failure is not dependent on Telegram success.

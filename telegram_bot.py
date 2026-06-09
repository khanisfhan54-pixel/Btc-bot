# telegram_bot.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError as _req_err:
    raise ImportError(
        "requests is required for telegram_bot.py. Install it with: pip install requests"
    ) from _req_err

logger = logging.getLogger(__name__)

BOT_TOKEN = "8719899776:AAHj7Tl-SuUU0CecRYU3sRyssCIRwThv3yY"
CHAT_ID = "93372553"
TELEGRAM_TEST_MESSAGE = "✅ BTC Bot Telegram Test Message"
TELEGRAM_MODULE_FILE_PATH = str(Path(__file__).resolve())
TELEGRAM_FUNCTION_NAMES = (
    "load_telegram_config",
    "validate_telegram_startup",
    "send_telegram_message",
    "send_test_telegram_alert",
    "run_telegram_startup_test",
)


class TelegramConfigError(RuntimeError):
    """Raised when the hardcoded Telegram alerting configuration is invalid."""


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    chat_id: str


def load_telegram_config(
    *,
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
    validate: bool = True,
) -> TelegramConfig:
    """Return the single hardcoded Telegram configuration used by all alerts."""
    config = TelegramConfig(token=BOT_TOKEN, chat_id=CHAT_ID)
    if validate and (not config.token.strip() or not config.chat_id.strip()):
        raise TelegramConfigError("Hardcoded Telegram BOT_TOKEN and CHAT_ID must be non-empty.")
    return config


def send_telegram_message(message: str, *, parse_mode: Optional[str] = "Markdown") -> bool:
    config = load_telegram_config(validate=True)
    url = f"https://api.telegram.org/bot{config.token}/sendMessage"
    payload = {
        "chat_id": config.chat_id,
        "text": message,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        response = requests.post(url, json=payload, timeout=10)
    except requests.RequestException as exc:
        logger.error("Telegram API request failed: %s", type(exc).__name__)
        return False

    if response.status_code != 200:
        logger.error(
            "Telegram API returned non-200 status",
            extra={"status_code": response.status_code, "response_text": response.text[:500]},
        )
        return False

    try:
        body = response.json()
    except ValueError:
        logger.error("Telegram API returned non-JSON response", extra={"response_text": response.text[:500]})
        return False

    ok = bool(body.get("ok"))
    if not ok:
        logger.error("Telegram API response was not successful", extra={"response_json": body})
    return ok


def send_test_telegram_alert() -> bool:
    """Send the fixed Telegram integration-test alert."""
    return send_telegram_message(TELEGRAM_TEST_MESSAGE)


def run_telegram_startup_test() -> bool:
    """Send and print the startup Telegram test status."""
    success = send_test_telegram_alert()
    status = "success" if success else "failure"
    print(f"Telegram module file path: {TELEGRAM_MODULE_FILE_PATH}")
    print("Telegram function names: " + ", ".join(TELEGRAM_FUNCTION_NAMES))
    print(f"Telegram startup test status: {status}")
    return success


def validate_telegram_startup() -> TelegramConfig:
    """Validate hardcoded Telegram config and send the startup test message."""
    config = load_telegram_config(validate=True)
    print("Telegram Bot Config Loaded")
    print(f"Telegram Chat ID: {config.chat_id}")
    logger.info("[BOOT] Telegram integration configured for chat_id=%s", config.chat_id)
    if not run_telegram_startup_test():
        raise TelegramConfigError("Telegram startup test failed.")
    return config

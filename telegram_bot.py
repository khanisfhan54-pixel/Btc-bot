# telegram_bot.py
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

try:
    import requests
except ImportError as _req_err:
    raise ImportError(
        "requests is required for telegram_bot.py. Install it with: pip install requests"
    ) from _req_err

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs): pass  # type: ignore[misc]

load_dotenv()

logger = logging.getLogger(__name__)

TELEGRAM_TEST_MESSAGE = "✅ BTC Bot Telegram Alert Test"


class TelegramConfigError(RuntimeError):
    """Raised when Telegram alerting is not configured correctly."""


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    chat_id: str


def _clean_env_value(value: Optional[str]) -> str:
    """Normalize environment/.env values without accepting blank secrets."""
    return (value or "").strip().strip('"').strip("'").strip()


def load_telegram_config(
    *,
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
    validate: bool = True,
) -> TelegramConfig:
    """Load Telegram credentials from explicit overrides or environment/.env."""
    load_dotenv()
    resolved_token = _clean_env_value(token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN"))
    resolved_chat_id = _clean_env_value(chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID"))

    if validate:
        missing = []
        if not resolved_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not resolved_chat_id:
            missing.append("TELEGRAM_CHAT_ID")
        if missing:
            raise TelegramConfigError(
                "Missing required Telegram environment variable(s): "
                + ", ".join(missing)
                + ". Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment or .env."
            )

    return TelegramConfig(token=resolved_token, chat_id=resolved_chat_id)


def validate_telegram_startup() -> TelegramConfig:
    """Fail fast on missing Telegram config and emit a clear startup log."""
    config = load_telegram_config(validate=True)
    print("Telegram Bot Config Loaded")
    print(f"Telegram Chat ID: {config.chat_id}")
    logger.info("[BOOT] Telegram integration configured for chat_id=%s", config.chat_id)
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
    return True


def send_test_telegram_alert() -> bool:
    """Send a fixed Telegram integration-test alert."""
    return send_telegram_message(TELEGRAM_TEST_MESSAGE)

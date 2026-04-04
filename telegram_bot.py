# telegram_bot.py
# telegram_bot.py
from __future__ import annotations

import os
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

TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")


def send_telegram_message(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        return bool(response.ok)
    except Exception:
        return False
import os
import requests
import structlog
import logging
from typing import Any

# Configure structlog
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

def setup_logging(log_file_path: str):
    logging.basicConfig(
        format="%(message)s",
        stream=open(log_file_path, "a"),
        level=logging.INFO,
    )
    # Add a stream handler to also log to stdout
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(console_handler)

def get_logger(name: str):
    return structlog.get_logger(name)

def send_telegram_alert(message: str) -> None:
    """Send an alert to a Telegram chat."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        get_logger("telegram_alert").warning("Telegram credentials missing, alert not sent", message=message)
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"🚨 *BTCUSDT Collector Alert*\n\n{message}",
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
    except Exception as e:
        get_logger("telegram_alert").error("Failed to send telegram alert", error=str(e))

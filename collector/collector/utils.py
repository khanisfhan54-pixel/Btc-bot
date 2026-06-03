import os
import structlog
import logging
import logging.handlers
import requests
import re

def setup_logging(log_dir="logs"):
    os.makedirs(log_dir, exist_ok=True)

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = logging.Formatter("%(message)s")

    # File handler with daily rotation, keep 7 days
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=os.path.join(log_dir, "collector.log"),
        when="midnight",
        interval=1,
        backupCount=7
    )
    file_handler.setFormatter(formatter)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    return structlog.get_logger()

logger = setup_logging()

def send_telegram_alert(message: str):
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        logger.warning("Telegram credentials missing, skipping alert", message=message)
        return

    # Validate bot token defensively
    if not re.match(r"^\d+:[a-zA-Z0-9_-]{30,50}$", bot_token):
        logger.error("Invalid Telegram bot token format", message=message)
        return

    try:
        chat_id_int = int(chat_id)
    except ValueError:
        logger.error("Invalid Telegram chat ID format", message=message)
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id_int,
        "text": message
    }
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
    except Exception as e:
        logger.error("Failed to send Telegram alert", error=str(e), message=message)

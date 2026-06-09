import os
import structlog
import logging
import logging.handlers
from telegram_bot import (
    TelegramConfigError,
    send_telegram_message,
    validate_telegram_startup,
)

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

def send_telegram_alert(message: str) -> bool:
    try:
        return send_telegram_message(message, parse_mode=None)
    except TelegramConfigError:
        logger.error("Telegram credentials missing; alert delivery cannot continue", message=message)
        raise
    except Exception as e:
        logger.error("Failed to send Telegram alert", error=str(e), message=message)
        return False

# telegram_bot.py
from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    requests = importlib.import_module("requests")
except Exception as _requests_import_error:  # Telegram is optional/fail-open.
    requests = None  # type: ignore[assignment]
    logger.warning("requests unavailable; Telegram disabled: %s", _requests_import_error)

TELEGRAM_BOT_TOKEN = "8719899776:AAHj7Tl-SuUU0CecRYU3sRyssCIRwThv3yY"
TELEGRAM_CHAT_ID = "8578163822"
BOT_TOKEN = TELEGRAM_BOT_TOKEN
CHAT_ID = TELEGRAM_CHAT_ID
TELEGRAM_TEST_MESSAGE = "✅ BTC Bot Telegram Test Message"
TELEGRAM_MODULE_FILE_PATH = str(Path(__file__).resolve())
TELEGRAM_FUNCTION_NAMES = (
    "TelegramConfigManager",
    "TelegramCircuitBreaker",
    "load_telegram_config",
    "validate_telegram_startup",
    "send_telegram_message",
    "send_test_telegram_alert",
    "get_telegram_health",
)

TELEGRAM_CONNECT_TIMEOUT_SECONDS = 3
TELEGRAM_READ_TIMEOUT_SECONDS = 5
TELEGRAM_MAX_RETRIES = 2
TELEGRAM_BACKOFF_SECONDS = (1.0, 2.0)
TELEGRAM_QUEUE_SIZE = 1000
TELEGRAM_CIRCUIT_FAILURE_THRESHOLD = 5
TELEGRAM_CIRCUIT_COOLDOWN_SECONDS = 300


class TelegramConfigError(RuntimeError):
    """Backward-compatible exception type; no longer raised on startup paths."""


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    chat_id: str
    enabled: bool = False


class TelegramConfigManager:
    """Thread-safe, load-once Telegram config cache.

    Telegram is the lowest-priority subsystem. Missing or invalid-looking config
    disables Telegram locally instead of raising into trading, collection, risk,
    signal, or execution code.
    """

    _lock = threading.RLock()
    _config: Optional[TelegramConfig] = None
    _loaded = False
    _dotenv_loaded = False

    @classmethod
    def reset_for_tests(cls) -> None:
        with cls._lock:
            cls._config = None
            cls._loaded = False
            cls._dotenv_loaded = False

    @classmethod
    def load(cls, *, token: Optional[str] = None, chat_id: Optional[str] = None, validate: bool = True) -> TelegramConfig:
        del validate  # Kept for API compatibility; validation never raises.
        with cls._lock:
            if cls._loaded and token is None and chat_id is None:
                return cls._config or TelegramConfig(token="", chat_id="", enabled=False)

            cls._load_dotenv_once()
            resolved_token = token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
            resolved_chat_id = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", CHAT_ID)
            resolved_token = (resolved_token or "").strip()
            resolved_chat_id = (resolved_chat_id or "").strip()
            enabled = bool(resolved_token and resolved_chat_id and requests is not None)
            config = TelegramConfig(token=resolved_token, chat_id=resolved_chat_id, enabled=enabled)

            if not enabled:
                reason = []
                if not resolved_token:
                    reason.append("missing token")
                if not resolved_chat_id:
                    reason.append("missing chat_id")
                if requests is None:
                    reason.append("requests unavailable")
                logger.warning("Telegram disabled (%s); trading continues", ", ".join(reason) or "unknown reason")

            if token is None and chat_id is None:
                cls._config = config
                cls._loaded = True
            return config

    @classmethod
    def _load_dotenv_once(cls) -> None:
        if cls._dotenv_loaded:
            return
        cls._dotenv_loaded = True
        dotenv_spec = importlib.util.find_spec("dotenv")
        if dotenv_spec is None:
            return
        try:
            dotenv_module = importlib.import_module("dotenv")
            load_dotenv = getattr(dotenv_module, "load_dotenv", None)
            if callable(load_dotenv):
                load_dotenv()
        except Exception as exc:
            logger.warning("Telegram dotenv load failed; continuing without .env: %s", exc)


class TelegramCircuitBreaker:
    def __init__(self, failure_threshold: int = TELEGRAM_CIRCUIT_FAILURE_THRESHOLD, cooldown_seconds: int = TELEGRAM_CIRCUIT_COOLDOWN_SECONDS) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.consecutive_failures = 0
        self.last_failure_time: Optional[float] = None
        self._state = "CLOSED"
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == "OPEN" and self.last_failure_time is not None:
                if time.monotonic() - self.last_failure_time >= self.cooldown_seconds:
                    return "HALF_OPEN"
            return self._state

    def allow_request(self) -> bool:
        return self.state != "OPEN"

    def record_success(self) -> None:
        with self._lock:
            self.consecutive_failures = 0
            self._state = "CLOSED"

    def record_failure(self) -> None:
        with self._lock:
            self.consecutive_failures += 1
            self.last_failure_time = time.monotonic()
            if self.consecutive_failures >= self.failure_threshold:
                self._state = "OPEN"


_circuit_breaker = TelegramCircuitBreaker()
_alert_queue: "queue.Queue[tuple[str, Optional[str]]]" = queue.Queue(maxsize=TELEGRAM_QUEUE_SIZE)
_worker_lock = threading.Lock()
_worker_thread: Optional[threading.Thread] = None
_health_lock = threading.Lock()
_health: Dict[str, Any] = {
    "alerts_sent": 0,
    "alerts_failed": 0,
    "last_success": None,
    "last_failure": None,
}


def _record_success() -> None:
    _circuit_breaker.record_success()
    with _health_lock:
        _health["alerts_sent"] += 1
        _health["last_success"] = time.time()


def _record_failure() -> None:
    _circuit_breaker.record_failure()
    with _health_lock:
        _health["alerts_failed"] += 1
        _health["last_failure"] = time.time()


def load_telegram_config(*, token: Optional[str] = None, chat_id: Optional[str] = None, validate: bool = True) -> TelegramConfig:
    return TelegramConfigManager.load(token=token, chat_id=chat_id, validate=validate)


def _post_telegram_once(message: str, *, parse_mode: Optional[str] = "Markdown") -> bool:
    config = load_telegram_config(validate=False)
    if not config.enabled:
        return False
    if not _circuit_breaker.allow_request():
        logger.warning("Telegram circuit OPEN; skipping alert")
        return False
    if requests is None:
        logger.warning("Telegram requests dependency unavailable; skipping alert")
        return False

    url = f"https://api.telegram.org/bot{config.token}/sendMessage"
    payload = {"chat_id": config.chat_id, "text": message}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    for attempt in range(TELEGRAM_MAX_RETRIES + 1):
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=(TELEGRAM_CONNECT_TIMEOUT_SECONDS, TELEGRAM_READ_TIMEOUT_SECONDS),
            )
            if response.status_code != 200:
                logger.warning("Telegram API returned status %s", response.status_code)
                if 400 <= response.status_code < 500:
                    _record_failure()
                    return False
                raise RuntimeError(f"Telegram status {response.status_code}")
            body = response.json()
            if bool(body.get("ok")):
                _record_success()
                return True
            logger.warning("Telegram API response was not ok: %s", body)
            _record_failure()
            return False
        except Exception as exc:
            if attempt >= TELEGRAM_MAX_RETRIES:
                logger.warning("Telegram request failed after retries: %s", exc)
                _record_failure()
                return False
            time.sleep(TELEGRAM_BACKOFF_SECONDS[min(attempt, len(TELEGRAM_BACKOFF_SECONDS) - 1)])
    return False


def _telegram_worker() -> None:
    while True:
        message, parse_mode = _alert_queue.get()
        try:
            _post_telegram_once(message, parse_mode=parse_mode)
        except Exception as exc:
            logger.warning("Telegram worker swallowed alert failure: %s", exc)
            _record_failure()
        finally:
            _alert_queue.task_done()


def _ensure_worker_started() -> None:
    global _worker_thread
    with _worker_lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _worker_thread = threading.Thread(target=_telegram_worker, name="telegram-alert-worker", daemon=True)
        _worker_thread.start()


def send_telegram_message(message: str, *, parse_mode: Optional[str] = "Markdown") -> bool:
    """Enqueue a Telegram alert without blocking trading threads on the API."""
    config = load_telegram_config(validate=False)
    if not config.enabled:
        return False
    if not _circuit_breaker.allow_request():
        logger.warning("Telegram circuit OPEN; dropping alert before enqueue")
        return False
    _ensure_worker_started()
    item = (str(message), parse_mode)
    try:
        _alert_queue.put_nowait(item)
        return True
    except queue.Full:
        try:
            _alert_queue.get_nowait()
            _alert_queue.task_done()
        except queue.Empty:
            pass
        logger.warning("Telegram alert queue full; dropped oldest alert")
        try:
            _alert_queue.put_nowait(item)
            return True
        except queue.Full:
            _record_failure()
            return False


def send_test_telegram_alert() -> bool:
    """Send the fixed Telegram integration-test alert synchronously for diagnostics."""
    return _post_telegram_once(TELEGRAM_TEST_MESSAGE)


def run_telegram_startup_test() -> bool:
    """Run a non-blocking, fail-open startup diagnostic without raising."""
    success = False
    try:
        # Enqueue instead of synchronously probing the network during startup.
        success = send_telegram_message(TELEGRAM_TEST_MESSAGE)
    except Exception as exc:
        logger.warning("Telegram startup test skipped after failure: %s", exc)
    status = "queued" if success else "disabled"
    print(f"Telegram module file path: {TELEGRAM_MODULE_FILE_PATH}")
    print("Telegram function names: " + ", ".join(TELEGRAM_FUNCTION_NAMES))
    print(f"Telegram startup test status: {status}")
    return success


def validate_telegram_startup() -> TelegramConfig:
    """Load Telegram config once, warn on failure, and always allow startup."""
    config = load_telegram_config(validate=False)
    print("Telegram Bot Config Loaded" if config.enabled else "Telegram Disabled; continuing startup")
    print(f"Telegram Chat ID: {config.chat_id or '<missing>'}")
    logger.info("[BOOT] Telegram optional subsystem enabled=%s", config.enabled)
    try:
        run_telegram_startup_test()
    except Exception as exc:
        logger.warning("Telegram startup validation failed open: %s", exc)
    return config


def get_telegram_health() -> Dict[str, Any]:
    with _health_lock:
        health = dict(_health)
    health.update(
        {
            "queue_depth": _alert_queue.qsize(),
            "circuit_state": _circuit_breaker.state,
            "consecutive_failures": _circuit_breaker.consecutive_failures,
            "last_failure_time": _circuit_breaker.last_failure_time,
            "enabled": load_telegram_config(validate=False).enabled,
        }
    )
    return health

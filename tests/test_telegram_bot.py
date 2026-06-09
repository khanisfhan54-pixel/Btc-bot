import queue

import telegram_bot


def _reset(monkeypatch):
    telegram_bot.TelegramConfigManager.reset_for_tests()
    monkeypatch.setattr(telegram_bot, "_circuit_breaker", telegram_bot.TelegramCircuitBreaker())
    while True:
        try:
            telegram_bot._alert_queue.get_nowait()
            telegram_bot._alert_queue.task_done()
        except queue.Empty:
            break
    with telegram_bot._health_lock:
        telegram_bot._health.update({"alerts_sent": 0, "alerts_failed": 0, "last_success": None, "last_failure": None})


def test_load_telegram_config_uses_hardcoded_defaults_without_env(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    config = telegram_bot.load_telegram_config()

    assert config.token == telegram_bot.BOT_TOKEN
    assert config.chat_id == telegram_bot.CHAT_ID
    assert config.enabled is True


def test_load_telegram_config_env_overrides_hardcoded_and_caches_once(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-one")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-one")

    config = telegram_bot.load_telegram_config()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-two")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-two")

    cached = telegram_bot.load_telegram_config()
    assert config == cached
    assert cached.token == "token-one"
    assert cached.chat_id == "chat-one"
    assert cached.enabled is True


def test_missing_token_is_fail_open(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(telegram_bot, "BOT_TOKEN", "")
    monkeypatch.setattr(telegram_bot, "CHAT_ID", "chat")

    config = telegram_bot.validate_telegram_startup()

    assert config.enabled is False
    assert telegram_bot.send_telegram_message("hello") is False


def test_missing_chat_id_is_fail_open(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(telegram_bot, "BOT_TOKEN", "token")
    monkeypatch.setattr(telegram_bot, "CHAT_ID", "")

    config = telegram_bot.validate_telegram_startup()

    assert config.enabled is False
    assert telegram_bot.send_telegram_message("hello") is False


def test_send_test_telegram_alert_posts_expected_message_with_timeouts(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    captured = {}

    class Response:
        status_code = 200
        text = '{"ok": true}'

        def json(self):
            return {"ok": True, "result": {"message_id": 1}}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(telegram_bot.requests, "post", fake_post)

    assert telegram_bot.send_test_telegram_alert() is True
    assert captured["url"] == "https://api.telegram.org/bottoken/sendMessage"
    assert captured["json"]["chat_id"] == "chat"
    assert captured["json"]["text"] == telegram_bot.TELEGRAM_TEST_MESSAGE
    assert captured["timeout"] == (3, 5)


def test_invalid_token_4xx_fails_open(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "invalid")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")

    class Response:
        status_code = 401
        text = "unauthorized"

        def json(self):
            return {"ok": False}

    monkeypatch.setattr(telegram_bot.requests, "post", lambda *args, **kwargs: Response())

    assert telegram_bot.send_test_telegram_alert() is False
    assert telegram_bot.get_telegram_health()["alerts_failed"] == 1


def test_invalid_chat_id_not_ok_fails_open(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "bad-chat")

    class Response:
        status_code = 200
        text = '{"ok": false}'

        def json(self):
            return {"ok": False, "description": "chat not found"}

    monkeypatch.setattr(telegram_bot.requests, "post", lambda *args, **kwargs: Response())

    assert telegram_bot.send_test_telegram_alert() is False


def test_telegram_500_retries_and_opens_circuit(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setattr(telegram_bot.time, "sleep", lambda _: None)

    class Response:
        status_code = 500
        text = "server error"

        def json(self):
            return {"ok": False}

    monkeypatch.setattr(telegram_bot.requests, "post", lambda *args, **kwargs: Response())

    for _ in range(5):
        assert telegram_bot.send_test_telegram_alert() is False

    health = telegram_bot.get_telegram_health()
    assert health["circuit_state"] == "OPEN"
    assert health["consecutive_failures"] == 5


def test_timeout_dns_proxy_network_failures_fail_open(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setattr(telegram_bot.time, "sleep", lambda _: None)

    failures = [TimeoutError("timeout"), OSError("dns"), RuntimeError("proxy"), ConnectionError("network")]
    calls = {"count": 0}

    def fake_post(*args, **kwargs):
        idx = min(calls["count"], len(failures) - 1)
        calls["count"] += 1
        raise failures[idx]

    monkeypatch.setattr(telegram_bot.requests, "post", fake_post)

    assert telegram_bot.send_test_telegram_alert() is False
    assert calls["count"] == telegram_bot.TELEGRAM_MAX_RETRIES + 1


def test_queue_overflow_drops_oldest_and_continues(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    small_queue = queue.Queue(maxsize=2)
    monkeypatch.setattr(telegram_bot, "_alert_queue", small_queue)
    monkeypatch.setattr(telegram_bot, "_ensure_worker_started", lambda: None)

    assert telegram_bot.send_telegram_message("one") is True
    assert telegram_bot.send_telegram_message("two") is True
    assert telegram_bot.send_telegram_message("three") is True

    assert small_queue.qsize() == 2
    assert small_queue.get_nowait()[0] == "two"
    assert small_queue.get_nowait()[0] == "three"


def test_get_telegram_health_exposes_metrics(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")

    health = telegram_bot.get_telegram_health()

    assert {"alerts_sent", "alerts_failed", "queue_depth", "circuit_state", "last_success", "last_failure"} <= set(health)

import telegram_bot


def test_load_telegram_config_uses_hardcoded_values(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "ignored-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "ignored-chat")

    config = telegram_bot.load_telegram_config()

    assert config.token == telegram_bot.BOT_TOKEN
    assert config.chat_id == telegram_bot.CHAT_ID


def test_send_test_telegram_alert_posts_expected_message(monkeypatch):
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
    assert captured["url"] == f"https://api.telegram.org/bot{telegram_bot.BOT_TOKEN}/sendMessage"
    assert captured["json"]["chat_id"] == telegram_bot.CHAT_ID
    assert captured["json"]["text"] == telegram_bot.TELEGRAM_TEST_MESSAGE
    assert captured["timeout"] == 10


def test_send_telegram_message_requires_ok_response(monkeypatch):
    class Response:
        status_code = 200
        text = '{"ok": false}'

        def json(self):
            return {"ok": False, "description": "failed"}

    monkeypatch.setattr(telegram_bot.requests, "post", lambda *args, **kwargs: Response())

    assert telegram_bot.send_telegram_message("hello") is False


def test_validate_telegram_startup_prints_status_without_token(monkeypatch, capsys):
    monkeypatch.setattr(telegram_bot, "send_test_telegram_alert", lambda: True)

    config = telegram_bot.validate_telegram_startup()

    captured = capsys.readouterr()
    assert config.chat_id == telegram_bot.CHAT_ID
    assert "Telegram Bot Config Loaded" in captured.out
    assert f"Telegram Chat ID: {telegram_bot.CHAT_ID}" in captured.out
    assert "Telegram module file path:" in captured.out
    assert "Telegram function names:" in captured.out
    assert "Telegram startup test status: success" in captured.out
    assert telegram_bot.BOT_TOKEN not in captured.out

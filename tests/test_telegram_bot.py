import pytest

import telegram_bot


def test_load_telegram_config_fails_fast_when_token_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "93372553")

    with pytest.raises(telegram_bot.TelegramConfigError, match="TELEGRAM_BOT_TOKEN"):
        telegram_bot.load_telegram_config()


def test_load_telegram_config_fails_fast_when_chat_id_missing(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:abcdef")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    with pytest.raises(telegram_bot.TelegramConfigError, match="TELEGRAM_CHAT_ID"):
        telegram_bot.load_telegram_config()


def test_send_test_telegram_alert_posts_expected_message(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:abcdef")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "93372553")

    captured = {}

    class Response:
        status_code = 200
        text = '{"ok": true}'

        def raise_for_status(self):
            return None

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(telegram_bot.requests, "post", fake_post)

    assert telegram_bot.send_test_telegram_alert() is True
    assert captured["url"] == "https://api.telegram.org/bot123456:abcdef/sendMessage"
    assert captured["json"]["chat_id"] == "93372553"
    assert captured["json"]["text"] == telegram_bot.TELEGRAM_TEST_MESSAGE
    assert captured["timeout"] == 10


def test_validate_telegram_startup_prints_chat_id_without_token(monkeypatch, capsys):
    token = "123456:abcdef"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "93372553")

    config = telegram_bot.validate_telegram_startup()

    captured = capsys.readouterr()
    assert config.chat_id == "93372553"
    assert "Telegram Bot Config Loaded" in captured.out
    assert "Telegram Chat ID: 93372553" in captured.out
    assert token not in captured.out

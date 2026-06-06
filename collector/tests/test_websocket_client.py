import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from collector.collector.websocket_client import WebSocketClient


@pytest.mark.asyncio
async def test_websocket_stop_sets_running_false():
    client = WebSocketClient("ws://localhost:9999", AsyncMock())
    client.running = True
    client.stop()
    assert not client.running
    assert not client.connected


@pytest.mark.asyncio
async def test_websocket_reconnect_retries_on_failure():
    """Verify exponential backoff retries when connection fails."""
    connect_attempts = 0

    async def fake_connect(url, **kwargs):
        nonlocal connect_attempts
        connect_attempts += 1
        raise ConnectionRefusedError("Simulated connection failure")

    reconnect_called = MagicMock()
    client = WebSocketClient("ws://localhost:9999", AsyncMock(), on_reconnect=reconnect_called)
    client.running = True

    sleep_calls = []

    async def fake_sleep(delay):
        sleep_calls.append(delay)
        if connect_attempts >= 3:
            client.running = False  # Stop after 3 attempts

    with patch("collector.collector.websocket_client.websockets.connect", new=fake_connect):
        with patch("collector.collector.websocket_client.asyncio.sleep", new=fake_sleep):
            await client.start()

    assert connect_attempts >= 3, "Expected at least 3 reconnect attempts"
    assert len(sleep_calls) >= 2, "Expected exponential backoff sleep calls"
    # Verify backoff is increasing
    assert sleep_calls[1] > sleep_calls[0], "Expected increasing backoff delay"

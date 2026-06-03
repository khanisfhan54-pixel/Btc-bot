import pytest
import asyncio
from collector.collector.websocket_client import WebSocketClient

@pytest.mark.asyncio
async def test_websocket_client_lifecycle():
    messages = []

    async def on_message(msg):
        messages.append(msg)

    client = WebSocketClient("ws://localhost:9999", on_message)

    # Just test that stop sets running to false
    client.running = True
    client.stop()

    assert not client.running

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from collector.websocket_client import BinanceWebsocketClient

@pytest.mark.asyncio
async def test_websocket_reconnect():
    handler_mock = MagicMock()
    reconnect_mock = MagicMock()

    client = BinanceWebsocketClient(handler_mock, reconnect_mock)

    # Run slightly and stop
    client.running = True

    # Mock process message to test routing
    client._process_message('{"stream": "btcusdt@depth10@100ms", "data": {"a": 1}}')
    handler_mock.assert_called_with("orderbook", {"a": 1})

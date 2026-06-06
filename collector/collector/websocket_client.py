import asyncio
import json
import websockets
from typing import Callable, Awaitable
from .utils import logger

class WebSocketClient:
    def __init__(self, url: str, on_message: Callable[[dict], Awaitable[None]], on_reconnect: Callable[[], None] = None):
        self.url = url
        self.on_message = on_message
        self.on_reconnect = on_reconnect
        self.running = False
        self.connected = False
        self.retry_delay = 1.0
        self.attempt = 0

    async def start(self):
        self.running = True
        logger.info("Starting WebSocket client", url=self.url)

        while self.running:
            try:
                connection = websockets.connect(self.url)
                if hasattr(connection, "__await__"):
                    connection = await connection

                async with connection as ws:
                    self.connected = True
                    self.retry_delay = 1.0
                    self.attempt = 0
                    logger.info("WebSocket connected")

                    if self.on_reconnect:
                        self.on_reconnect()

                    async for msg in ws:
                        if not self.running:
                            break
                        try:
                            data = json.loads(msg)
                            await self.on_message(data)
                        except json.JSONDecodeError:
                            logger.error("Failed to parse JSON from WebSocket", msg=msg)

            except websockets.ConnectionClosed as e:
                self.connected = False
                logger.warning("WebSocket connection closed", error=str(e))
            except Exception as e:
                self.connected = False
                logger.error("WebSocket error", error=str(e))

            if self.running:
                self.attempt += 1
                logger.info("Reconnecting WebSocket", attempt=self.attempt, delay=self.retry_delay)
                await asyncio.sleep(self.retry_delay)
                self.retry_delay = min(self.retry_delay * 2, 60.0)

    def stop(self):
        self.running = False
        self.connected = False

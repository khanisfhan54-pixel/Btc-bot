import asyncio
import json
import time
import websockets
from typing import Callable, Dict, Any
from collector import config
from collector.utils import get_logger, send_telegram_alert

logger = get_logger("websocket_client")

class BinanceWebsocketClient:
    def __init__(self, message_handler: Callable[[str, Dict[str, Any]], None], reconnect_handler: Callable[[], None]):
        self.url = config.WS_URL
        self.message_handler = message_handler
        self.reconnect_handler = reconnect_handler
        self.running = False

    async def connect_and_run(self):
        self.running = True
        retry_delay = 1
        attempt = 0

        while self.running:
            try:
                attempt += 1
                logger.info("ws_connecting", attempt=attempt, delay=retry_delay)

                async with websockets.connect(self.url) as ws:
                    logger.info("ws_connected")
                    self.reconnect_handler() # Reset state on successful connect
                    retry_delay = 1 # Reset delay

                    async for message in ws:
                        if not self.running:
                            break
                        self._process_message(message)

            except Exception as e:
                logger.error("ws_error", error=str(e), attempt=attempt)
                if self.running:
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 60) # Exponential backoff up to 60s

    def _process_message(self, message: str):
        try:
            data = json.loads(message)
            stream = data.get("stream")
            payload = data.get("data")

            if not stream or not payload:
                return

            stream_name = ""
            if "depth10" in stream:
                stream_name = "orderbook"
            elif "aggTrade" in stream:
                stream_name = "trades"
            elif "markPrice" in stream:
                stream_name = "markprice"

            if stream_name:
                self.message_handler(stream_name, payload)

        except json.JSONDecodeError:
            logger.error("ws_json_error", message=message)
        except Exception as e:
            logger.error("ws_processing_error", error=str(e))

    def stop(self):
        self.running = False

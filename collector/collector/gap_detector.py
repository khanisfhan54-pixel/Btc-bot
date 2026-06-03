from typing import Dict, Any
from collector.utils import logger, send_telegram_alert

class GapDetector:
    def __init__(self):
        self.last_seen: Dict[str, int] = {
            "orderbook": 0,
            "trades": 0,
            "markprice": 0
        }
        self.thresholds = {
            "orderbook": 500,
            "trades": 30000,
            "markprice": 5000
        }

    def check_gap(self, stream_name: str, current_ts: int):
        last_ts = self.last_seen.get(stream_name, 0)

        if last_ts > 0:
            gap_duration = current_ts - last_ts
            if gap_duration > self.thresholds[stream_name]:
                logger.warning("Gap detected",
                               stream=stream_name,
                               gap_start=last_ts,
                               gap_end=current_ts,
                               duration_ms=gap_duration)
                if gap_duration > 2000:
                    send_telegram_alert(f"Gap > 2s detected in {stream_name}: {gap_duration}ms")

        self.last_seen[stream_name] = current_ts

    def reset(self):
        self.last_seen = {"orderbook": 0, "trades": 0, "markprice": 0}

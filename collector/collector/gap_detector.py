from typing import Dict
from collector import config
from collector.utils import get_logger

logger = get_logger("gap_detector")

class GapDetector:
    def __init__(self):
        self.last_ts: Dict[str, int] = {"orderbook": 0, "trades": 0, "markprice": 0}

    def check_gap(self, stream_name: str, current_ts: int):
        last = self.last_ts.get(stream_name, 0)

        if last > 0:
            diff = current_ts - last
            threshold = config.GAP_THRESHOLDS_MS.get(stream_name, 0)

            if threshold > 0 and diff > threshold:
                logger.warning("gap_detected",
                               stream=stream_name,
                               gap_start=last,
                               gap_end=current_ts,
                               duration_ms=diff)

        self.last_ts[stream_name] = current_ts

    def reset(self):
        self.last_ts = {"orderbook": 0, "trades": 0, "markprice": 0}

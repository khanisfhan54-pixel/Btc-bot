import shutil
import sys
from typing import Callable, Optional
from collector.utils import logger, send_telegram_alert

class DiskMonitor:
    def __init__(self, data_dir: str = "data", shutdown_callback: Optional[Callable[[], None]] = None):
        self.data_dir = data_dir
        self.shutdown_callback = shutdown_callback

    def get_free_gb(self) -> float:
        total, used, free = shutil.disk_usage(self.data_dir)
        return free / (1024 ** 3)

    def check_disk_space(self):
        free_gb = self.get_free_gb()

        if free_gb < 2.0:
            msg = f"EMERGENCY: Free disk < 2GB ({free_gb:.2f}GB). Halting collection."
            logger.critical(msg)
            send_telegram_alert(msg)
            if self.shutdown_callback:
                self.shutdown_callback()
            sys.exit(1)
        elif free_gb < 4.0:
            msg = f"CRITICAL: Free disk < 4GB ({free_gb:.2f}GB)."
            logger.error(msg)
            send_telegram_alert(msg)
        elif free_gb < 7.0:
            msg = f"WARNING: Free disk < 7GB ({free_gb:.2f}GB)."
            logger.warning(msg)
            send_telegram_alert(msg)

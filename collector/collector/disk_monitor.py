import os
import shutil
import sys
from collector import config
from collector.utils import get_logger, send_telegram_alert

logger = get_logger("disk_monitor")

def get_free_space_gb(path: str = "/") -> float:
    """Returns free disk space in GB for the given path."""
    total, used, free = shutil.disk_usage(path)
    return free / (1024 ** 3)

def check_disk_space():
    """Checks disk space and triggers alerts/shutdown if needed.
    Returns: (free_gb, should_shutdown)
    """
    free_gb = get_free_space_gb(config.DATA_DIR)

    if free_gb < config.DISK_EMERGENCY_GB:
        msg = f"EMERGENCY: Disk space extremely low ({free_gb:.2f} GB free). Halting collection."
        logger.critical("disk_emergency", free_gb=free_gb)
        send_telegram_alert(msg)
        return free_gb, True
    elif free_gb < config.DISK_CRITICAL_GB:
        msg = f"CRITICAL: Disk space critically low ({free_gb:.2f} GB free)."
        logger.error("disk_critical", free_gb=free_gb)
        send_telegram_alert(msg)
    elif free_gb < config.DISK_WARNING_GB:
        msg = f"WARNING: Disk space getting low ({free_gb:.2f} GB free)."
        logger.warning("disk_warning", free_gb=free_gb)
        send_telegram_alert(msg)

    return free_gb, False

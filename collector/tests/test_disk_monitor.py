import pytest
import sys
from collector.collector.disk_monitor import DiskMonitor

def test_disk_monitor_get_free_gb(monkeypatch):
    class MockShutil:
        @staticmethod
        def disk_usage(path):
            return (1000 * 1024**3, 500 * 1024**3, 500 * 1024**3)

    monkeypatch.setattr("collector.collector.disk_monitor.shutil", MockShutil)

    monitor = DiskMonitor()
    assert monitor.get_free_gb() == 500.0

def test_disk_monitor_emergency(monkeypatch):
    class MockShutil:
        @staticmethod
        def disk_usage(path):
            return (1000 * 1024**3, 999 * 1024**3, 1 * 1024**3) # 1GB free

    monkeypatch.setattr("collector.collector.disk_monitor.shutil", MockShutil)

    # Mock sys.exit
    exited = False
    shutdown_called = False
    def mock_exit(code):
        nonlocal exited
        exited = True

    def mock_shutdown():
        nonlocal shutdown_called
        shutdown_called = True

    monkeypatch.setattr(sys, "exit", mock_exit)

    # Mock telegram
    monkeypatch.setattr("collector.collector.disk_monitor.send_telegram_alert", lambda msg: None)

    monitor = DiskMonitor(shutdown_callback=mock_shutdown)
    monitor.check_disk_space()

    assert shutdown_called
    assert exited

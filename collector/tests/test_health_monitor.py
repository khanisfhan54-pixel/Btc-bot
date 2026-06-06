import pytest
import time
from collector.collector.health_monitor import HealthMonitor

class MockDiskMonitor:
    def get_free_gb(self):
        return 10.0

class MockValidator:
    failures_in_window = 0

class MockWSClient:
    connected = True

@pytest.fixture
def monitor():
    return HealthMonitor(MockDiskMonitor(), MockValidator(), MockWSClient())

def test_health_monitor_record(monitor):
    monitor.record_message("orderbook", 1000)
    monitor.record_message("trades", 2000)
    monitor.record_message("markprice", 3000)

    assert monitor.messages_per_minute["orderbook"] == 1
    assert monitor.messages_per_minute["trades"] == 1
    assert monitor.messages_per_minute["markprice"] == 1

    assert monitor.last_book_ts == 1000
    assert monitor.last_trade_ts == 2000
    assert monitor.last_mark_ts == 3000

def test_health_monitor_check_health(monitor, monkeypatch):
    monitor.last_book_ts = int(time.time() * 1000)
    monitor.last_trade_ts = int(time.time() * 1000)
    monitor.last_mark_ts = int(time.time() * 1000) - 70000 # Stale markprice

    alert_sent = None
    def mock_alert(msg):
        nonlocal alert_sent
        alert_sent = msg

    monkeypatch.setattr("collector.collector.health_monitor.send_telegram_alert", mock_alert)

    monitor._check_health()

    assert alert_sent is not None
    assert "markprice" in alert_sent

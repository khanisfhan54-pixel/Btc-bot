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


def test_health_monitor_records_liquidation_without_keyerror(monitor):
    monitor.record_message("liquidation", 4000)

    assert monitor.messages_per_minute["liquidation"] == 1
    assert monitor.last_liq_ts == 4000


def test_health_monitor_liquidation_staleness_alert(monkeypatch, monitor):
    from collector.collector.health_monitor import LIQUIDATION_STALE_MS

    now = 10_000_000
    monitor.last_book_ts = now
    monitor.last_trade_ts = now
    monitor.last_mark_ts = now
    monitor.last_oi_ts = now
    monitor.last_liq_ts = now - LIQUIDATION_STALE_MS - 1
    alerts = []

    monkeypatch.setattr("collector.collector.health_monitor.time.time", lambda: now / 1000)
    monkeypatch.setattr("collector.collector.health_monitor.send_telegram_alert", alerts.append)

    monitor._check_health()

    assert alerts
    assert "liquidation" in alerts[0]


@pytest.mark.parametrize(
    ("stream_attr", "constant_name"),
    [
        ("last_book_ts", "ORDERBOOK_STALE_MS"),
        ("last_trade_ts", "TRADES_STALE_MS"),
        ("last_mark_ts", "MARKPRICE_STALE_MS"),
        ("last_oi_ts", "OI_STALE_MS"),
    ],
)
def test_health_monitor_uses_config_derived_staleness_thresholds(monkeypatch, monitor, stream_attr, constant_name):
    import collector.collector.health_monitor as health_monitor

    now = 10_000_000
    threshold = getattr(health_monitor, constant_name) * health_monitor.HEALTH_CHECK_STALE_MULTIPLIER
    monitor.last_book_ts = now
    monitor.last_trade_ts = now
    monitor.last_mark_ts = now
    monitor.last_oi_ts = now
    monitor.last_liq_ts = now
    setattr(monitor, stream_attr, now - threshold - 1)
    alerts = []

    monkeypatch.setattr("collector.collector.health_monitor.time.time", lambda: now / 1000)
    monkeypatch.setattr("collector.collector.health_monitor.send_telegram_alert", alerts.append)

    monitor._check_health()

    assert alerts

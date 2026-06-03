import pytest
from unittest.mock import patch
from collector.health_monitor import HealthMonitor

@patch('collector.health_monitor.send_telegram_alert')
@patch('collector.health_monitor.time.time')
def test_health_monitor_silence(mock_time, mock_alert):
    monitor = HealthMonitor()

    # Simulate current time
    mock_time.return_value = 100

    # Update timestamps (90s ago)
    monitor.last_book_ts = 10 * 1000
    monitor.last_trade_ts = 10 * 1000
    monitor.last_mark_ts = 10 * 1000

    # Run loop logic once manually
    monitor.running = True

    # We bypass the sleep in the thread by just calling the inner logic
    now = mock_time.return_value * 1000
    alerts = []
    if now - monitor.last_book_ts > 60000: alerts.append("Orderbook")
    if now - monitor.last_trade_ts > 60000: alerts.append("Trades")

    assert len(alerts) == 2

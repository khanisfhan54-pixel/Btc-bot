import pytest
from unittest.mock import patch
from collector.disk_monitor import check_disk_space

@patch('collector.disk_monitor.shutil.disk_usage')
@patch('collector.disk_monitor.send_telegram_alert')
def test_disk_monitor_emergency(mock_alert, mock_disk):
    # Mock returning 1GB free
    mock_disk.return_value = (0, 0, 1 * 1024**3)

    free_gb, should_shutdown = check_disk_space()

    mock_alert.assert_called_once()
    assert should_shutdown is True

@patch('collector.disk_monitor.shutil.disk_usage')
@patch('collector.disk_monitor.send_telegram_alert')
def test_disk_monitor_warning(mock_alert, mock_disk):
    # Mock returning 6GB free
    mock_disk.return_value = (0, 0, 6 * 1024**3)

    free_gb, should_shutdown = check_disk_space()

    mock_alert.assert_called_once()
    assert should_shutdown is False

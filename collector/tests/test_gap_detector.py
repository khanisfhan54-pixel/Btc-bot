import pytest
from unittest.mock import MagicMock

from collector.collector.gap_detector import GapDetector


@pytest.fixture
def detector():
    return GapDetector()


def test_gap_detector_no_gap(detector, monkeypatch):
    alerted = False
    def mock_alert(msg):
        nonlocal alerted
        alerted = True

    monkeypatch.setattr("collector.collector.gap_detector.send_telegram_alert", mock_alert)

    detector.check_gap("orderbook", 1000)
    detector.check_gap("orderbook", 1400) # 400ms gap, threshold is 500

    assert not alerted
    assert detector.last_seen["orderbook"] == 1400


def test_gap_detector_with_gap(detector, monkeypatch):
    alerted = False
    def mock_alert(msg):
        nonlocal alerted
        alerted = True

    monkeypatch.setattr("collector.collector.gap_detector.send_telegram_alert", mock_alert)

    detector.check_gap("orderbook", 1000)
    detector.check_gap("orderbook", 4000) # 3000ms gap, threshold is 500

    assert alerted
    assert detector.last_seen["orderbook"] == 4000


def test_trade_exchange_timestamp_gap_over_5000ms_is_detected(detector, monkeypatch):
    mock_logger = MagicMock()
    monkeypatch.setattr("collector.collector.gap_detector.logger", mock_logger)
    monkeypatch.setattr("collector.collector.gap_detector.send_telegram_alert", MagicMock())

    detector.check_gap("trades", 1_000)
    detector.check_gap("trades", 7_000)

    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args.kwargs["duration_ms"] == 6_000
    assert mock_logger.warning.call_args.kwargs["duration_ms"] > 5_000
    assert detector.last_seen["trades"] == 7_000


def test_trade_exchange_timestamp_gap_under_5000ms_is_not_detected(detector, monkeypatch):
    mock_logger = MagicMock()
    mock_alert = MagicMock()
    monkeypatch.setattr("collector.collector.gap_detector.logger", mock_logger)
    monkeypatch.setattr("collector.collector.gap_detector.send_telegram_alert", mock_alert)

    detector.check_gap("trades", 1_000)
    detector.check_gap("trades", 5_999)

    mock_logger.warning.assert_not_called()
    mock_alert.assert_not_called()
    assert detector.last_seen["trades"] == 5_999


def test_trade_gap_over_telegram_threshold_triggers_alert(detector, monkeypatch):
    mock_alert = MagicMock()
    monkeypatch.setattr("collector.collector.gap_detector.send_telegram_alert", mock_alert)

    detector.check_gap("trades", 1_000)
    detector.check_gap("trades", 21_000)

    mock_alert.assert_called_once_with("Gap > 2s detected in trades: 20000ms")


def test_reset_stream_resets_only_requested_gap_state(detector):
    detector.last_seen = {"orderbook": 1000, "trades": 2000, "markprice": 3000}

    detector.reset_stream("trades")

    assert detector.last_seen == {"orderbook": 1000, "trades": 0, "markprice": 3000}

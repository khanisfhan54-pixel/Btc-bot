import pytest
from collector.gap_detector import GapDetector
from unittest.mock import patch

@patch('collector.gap_detector.logger')
def test_gap_detector(mock_logger):
    detector = GapDetector()

    # First entry
    detector.check_gap("orderbook", 1000)
    mock_logger.warning.assert_not_called()

    # Valid gap (< 500)
    detector.check_gap("orderbook", 1400)
    mock_logger.warning.assert_not_called()

    # Invalid gap (> 500)
    detector.check_gap("orderbook", 2000)
    mock_logger.warning.assert_called_once()

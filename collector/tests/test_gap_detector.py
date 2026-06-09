import pytest
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


def test_reset_stream_resets_only_requested_gap_state(detector):
    detector.last_seen = {"orderbook": 1000, "trades": 2000, "markprice": 3000}

    detector.reset_stream("trades")

    assert detector.last_seen == {"orderbook": 1000, "trades": 0, "markprice": 3000}

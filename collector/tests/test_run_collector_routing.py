import importlib.util
import sys
import time
from pathlib import Path
from unittest.mock import ANY, MagicMock

import pytest

_outer_collector = sys.modules.get("collector")
_collector_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_collector_dir))
sys.modules.pop("collector", None)
_spec = importlib.util.spec_from_file_location("_run_collector_under_test", _collector_dir / "run_collector.py")
_run_collector = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_run_collector)
CollectorApp = _run_collector.CollectorApp
if _outer_collector is not None:
    sys.modules["collector"] = _outer_collector


def _valid_depth_msg():
    return {
        "E": int(time.time() * 1000),
        "b": [[str(100.0 - i * 0.1), "1.0"] for i in range(10)],
        "a": [[str(101.0 + i * 0.1), "1.0"] for i in range(10)],
    }


def _valid_trade_msg(trade_id=123):
    return {
        "E": int(time.time() * 1000),
        "a": trade_id,
        "p": "100.5",
        "q": "1.0",
        "m": False,
    }


def _valid_mark_msg():
    now = int(time.time() * 1000)
    return {
        "E": now,
        "p": "100.5",
        "r": "0.0001",
        "T": now + 3600000,
    }


def _app_without_init():
    app = CollectorApp.__new__(CollectorApp)
    app.raw_messages_logged = 20
    app.stream_counters = {
        "orderbook": {"received": 0, "computed": 0, "empty_features": 0, "validated": 0, "rejected": 0, "written": 0},
        "trades": {"received": 0, "computed": 0, "empty_features": 0, "validated": 0, "rejected": 0, "written": 0},
        "markprice": {"received": 0, "computed": 0, "empty_features": 0, "validated": 0, "rejected": 0, "written": 0},
        "unrouted": {"received": 0},
    }
    app.validation_fail_reasons = {"orderbook": {}, "trades": {}, "markprice": {}}
    app.validator = MagicMock()
    app.validator.validate_orderbook.return_value = (True, "")
    app.validator.validate_trade.return_value = (True, "")
    app.validator.validate_markprice.return_value = (True, "")
    app.validator.check_failure_rate.return_value = False
    app.validator.failures_in_window = 0
    app.gap_detector = MagicMock()
    app.ob_writer = MagicMock()
    app.trades_writer = MagicMock()
    app.mark_writer = MagicMock()
    app.health_monitor = MagicMock()
    app.health_monitor.messages_per_minute = {"orderbook": 0, "trades": 0, "markprice": 0}
    return app


def test_route_stream_matches_case_insensitive_required_streams():
    app = CollectorApp.__new__(CollectorApp)

    assert app._route_stream("btcusdt@depth10@100ms") == "orderbook"
    assert app._route_stream("btcusdt@aggTrade") == "trades"
    assert app._route_stream("btcusdt@aggtrade") == "trades"
    assert app._route_stream("btcusdt@markPrice@1s") == "markprice"
    assert app._route_stream("btcusdt@markprice@1s") == "markprice"
    assert app._route_stream("ethusdt@aggtrade") is None


@pytest.mark.asyncio
async def test_handle_message_routes_lowercase_trade_and_markprice_to_health_monitor():
    app = _app_without_init()

    await app.handle_message({"stream": "btcusdt@depth10@100ms", "data": _valid_depth_msg()})
    await app.handle_message({"stream": "btcusdt@aggtrade", "data": _valid_trade_msg()})
    await app.handle_message({"stream": "btcusdt@markprice@1s", "data": _valid_mark_msg()})

    app.health_monitor.record_message.assert_any_call("orderbook", ANY)
    app.health_monitor.record_message.assert_any_call("trades", ANY)
    app.health_monitor.record_message.assert_any_call("markprice", ANY)
    assert app.stream_counters["trades"]["validated"] == 1
    assert app.stream_counters["markprice"]["validated"] == 1
    assert app.stream_counters["unrouted"]["received"] == 0

@pytest.mark.asyncio
async def test_startup_verification_uses_cumulative_received_counters(monkeypatch):
    app = _app_without_init()
    app.stream_counters["orderbook"]["received"] = 533
    app.stream_counters["trades"]["received"] = 651
    app.stream_counters["markprice"]["received"] = 59
    app.health_monitor.messages_per_minute = {"orderbook": 0, "trades": 0, "markprice": 0}

    monkeypatch.setattr(_run_collector, "STREAM_INACTIVE_STARTUP_SECONDS", 0)

    await app._verify_startup_streams()


def test_trade_validation_rejections_are_grouped_by_reason():
    app = _app_without_init()
    app.validator.validate_trade.return_value = (False, "Timestamp regression")

    app._handle_trades(_valid_trade_msg(), "btcusdt@aggtrade")
    app._handle_trades(_valid_trade_msg(trade_id=124), "btcusdt@aggtrade")

    assert app.stream_counters["trades"]["rejected"] == 2
    assert app.validation_fail_reasons["trades"] == {"Timestamp regression": 2}


def test_handlers_use_exchange_timestamp_for_gap_detection(monkeypatch):
    app = _app_without_init()

    trade_features = {
        "timestamp": 11_000,
        "exchange_timestamp": 1_200,
        "trade_id": 123,
        "price": 100.5,
        "quantity": 1.0,
        "is_buyer_maker": False,
    }
    orderbook_features = {"timestamp": 11_000, "exchange_timestamp": 1_300}
    markprice_features = {"timestamp": 11_000, "exchange_timestamp": 1_400}
    monkeypatch.setattr(_run_collector, "compute_trades_features", lambda _: trade_features)
    monkeypatch.setattr(_run_collector, "compute_orderbook_features", lambda _: orderbook_features)
    monkeypatch.setattr(_run_collector, "compute_markprice_features", lambda _: markprice_features)

    app._handle_trades({}, "btcusdt@aggtrade")
    app._handle_orderbook({}, "btcusdt@depth10@100ms")
    app._handle_markprice({}, "btcusdt@markprice@1s")

    app.gap_detector.check_gap.assert_any_call("trades", 1_200)
    app.gap_detector.check_gap.assert_any_call("orderbook", 1_300)
    app.gap_detector.check_gap.assert_any_call("markprice", 1_400)


def test_trade_processing_backlog_local_timestamp_gap_does_not_create_gap(monkeypatch):
    app = _app_without_init()
    app.gap_detector = _run_collector.GapDetector()
    mock_logger = MagicMock()
    mock_alert = MagicMock()
    monkeypatch.setattr(_run_collector, "logger", mock_logger)
    monkeypatch.setattr(_run_collector, "send_telegram_alert", mock_alert)
    monkeypatch.setitem(_run_collector.GapDetector.check_gap.__globals__, "logger", mock_logger)
    monkeypatch.setitem(_run_collector.GapDetector.check_gap.__globals__, "send_telegram_alert", mock_alert)

    feature_records = iter([
        {
            "timestamp": 1_000,
            "exchange_timestamp": 1_000,
            "trade_id": 123,
            "price": 100.5,
            "quantity": 1.0,
            "is_buyer_maker": False,
        },
        {
            "timestamp": 11_000,
            "exchange_timestamp": 1_200,
            "trade_id": 124,
            "price": 100.5,
            "quantity": 1.0,
            "is_buyer_maker": False,
        },
    ])
    monkeypatch.setattr(_run_collector, "compute_trades_features", lambda _: next(feature_records))

    app._handle_trades({}, "btcusdt@aggtrade")
    app._handle_trades({}, "btcusdt@aggtrade")

    assert app.gap_detector.last_seen["trades"] == 1_200
    mock_logger.warning.assert_not_called()
    mock_alert.assert_not_called()


def test_orderbook_reconnect_preserves_trade_mid_price_validation_context():
    app = CollectorApp.__new__(CollectorApp)
    app.validator = _run_collector.Validator()
    app.gap_detector = _run_collector.GapDetector()
    app.validator.last_timestamps = {"orderbook": 1000, "trades": 2000, "markprice": 3000}
    app.gap_detector.last_seen = {"orderbook": 1000, "trades": 2000, "markprice": 3000}
    app.validator.last_trade_id = 12345
    app.validator.last_mid_price = 100.0

    reconnect_handler = app._make_reconnect_handler(_run_collector.BINANCE_PUBLIC_WS_URL)
    reconnect_handler()

    assert app.validator.last_timestamps["orderbook"] == 0
    assert app.gap_detector.last_seen["orderbook"] == 0
    assert app.validator.last_timestamps["trades"] == 2000
    assert app.gap_detector.last_seen["trades"] == 2000
    assert app.validator.last_trade_id == 12345
    assert app.validator.last_mid_price == 100.0

    valid, reason = app.validator.validate_trade(
        {
            "timestamp": int(time.time() * 1000),
            "exchange_timestamp": int(time.time() * 1000),
            "trade_id": 12346,
            "price": 106.0,
            "quantity": 1.0,
        }
    )

    assert not valid
    assert reason == "Price > 5% from mid_price"


def test_market_reconnect_resets_trades_and_markprice_without_orderbook_state():
    app = CollectorApp.__new__(CollectorApp)
    app.validator = _run_collector.Validator()
    app.gap_detector = _run_collector.GapDetector()
    app.validator.last_timestamps = {"orderbook": 1000, "trades": 2000, "markprice": 3000}
    app.gap_detector.last_seen = {"orderbook": 1000, "trades": 2000, "markprice": 3000}
    app.validator.last_trade_id = 12345
    app.validator.last_mid_price = 100.0

    reconnect_handler = app._make_reconnect_handler(_run_collector.BINANCE_MARKET_WS_URL)
    reconnect_handler()

    assert app.validator.last_timestamps == {"orderbook": 1000, "trades": 0, "markprice": 0}
    assert app.gap_detector.last_seen == {"orderbook": 1000, "trades": 0, "markprice": 0}
    assert app.validator.last_trade_id == -1
    assert app.validator.last_mid_price == 100.0

@pytest.mark.asyncio
async def test_poll_openinterest_writes_valid_rest_response(monkeypatch):
    app = _app_without_init()
    app.running = True
    app.stream_counters["openinterest"] = {"received": 0, "computed": 0, "empty_features": 0, "validated": 0, "rejected": 0, "written": 0}
    app.oi_writer = MagicMock()

    class FakeResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {"openInterest": "2.5", "time": str(int(time.time() * 1000)), "price": "100.0"}

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            return FakeResponse()

    class FakeAiohttp:
        ClientSession = FakeSession

        class ClientTimeout:
            def __init__(self, total):
                self.total = total

    async def stop_after_poll(seconds):
        app.running = False

    monkeypatch.setitem(sys.modules, "aiohttp", FakeAiohttp)
    monkeypatch.setattr(_run_collector.asyncio, "sleep", stop_after_poll)

    await app._poll_openinterest()

    app.oi_writer.write.assert_called_once()
    app.health_monitor.record_message.assert_any_call("openinterest", ANY)
    assert app.stream_counters["openinterest"]["written"] == 1
